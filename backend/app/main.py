from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, nflverse, settings, store
from .fleaflicker import FleaflickerClient, FleaflickerError, parse_scoring, parse_slots
from .models import LeagueConfig, LeagueUpdate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Football Analytics", version="0.1.0")
client = FleaflickerClient()


class LeagueCreate(BaseModel):
    id: int
    sport: str = "NFL"
    my_team_id: Optional[int] = None


def _cfg(league_id: int) -> LeagueConfig:
    cfg = store.get_league(league_id)
    if cfg is None:
        raise HTTPException(404, f"league {league_id} not configured")
    return cfg


def _ctx(league_id: int, force: bool = False) -> analysis.Context:
    _cfg(league_id)
    try:
        return analysis.get_context(league_id, client, force=force)
    except FleaflickerError as e:
        raise HTTPException(502, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


def _import_from_fleaflicker(cfg: LeagueConfig, keep_slots: bool = False) -> LeagueConfig:
    try:
        rules = client.rules(cfg.id, cfg.sport)
        standings = client.standings(cfg.id, cfg.sport)
    except FleaflickerError as e:
        raise HTTPException(502, str(e))
    cfg.scoring = parse_scoring(rules)
    new_slots = parse_slots(rules)
    if keep_slots and cfg.slots:
        old = {s.label: s for s in cfg.slots}
        for s in new_slots:
            if s.label in old:
                s.enabled, s.start = old[s.label].enabled, old[s.label].start
    cfg.slots = new_slots
    cfg.season = standings.get("season") or cfg.season
    lg = standings.get("league") or {}
    cfg.name = lg.get("name") or cfg.name or f"League {cfg.id}"
    return cfg


# ------------------------------------------------------------------ leagues
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/leagues")
def list_leagues():
    return [l.model_dump() for l in store.list_leagues()]


@app.post("/api/leagues", status_code=201)
def create_league(body: LeagueCreate):
    cfg = store.get_league(body.id) or LeagueConfig(id=body.id, sport=body.sport)
    if body.my_team_id is not None:
        cfg.my_team_id = body.my_team_id
    cfg = _import_from_fleaflicker(cfg, keep_slots=True)
    store.save_league(cfg)
    analysis.invalidate(cfg.id)
    return cfg.model_dump()


@app.get("/api/leagues/{league_id}")
def get_league(league_id: int):
    return _cfg(league_id).model_dump()


@app.put("/api/leagues/{league_id}")
def update_league(league_id: int, body: LeagueUpdate):
    cfg = _cfg(league_id)
    cfg = LeagueConfig.model_validate({**cfg.model_dump(), **body.model_dump(exclude_unset=True)})
    store.save_league(cfg)
    analysis.invalidate(league_id)
    return cfg.model_dump()


@app.delete("/api/leagues/{league_id}", status_code=204)
def delete_league(league_id: int):
    if not store.delete_league(league_id):
        raise HTTPException(404)
    analysis.invalidate(league_id)


@app.post("/api/leagues/{league_id}/sync")
def sync_league(league_id: int):
    """Re-import slots and scoring rules from Fleaflicker (keeps your enabled/start edits)."""
    cfg = _import_from_fleaflicker(_cfg(league_id), keep_slots=True)
    store.save_league(cfg)
    analysis.invalidate(league_id)
    return cfg.model_dump()


@app.post("/api/leagues/{league_id}/refresh")
def refresh(league_id: int):
    """Drop all caches (Fleaflicker + analysis context) and rebuild."""
    client.clear_cache()
    analysis.invalidate(league_id)
    ctx = _ctx(league_id, force=True)
    return {"ok": True, "week": ctx.week}


@app.get("/api/leagues/{league_id}/teams")
def teams(league_id: int):
    cfg = _cfg(league_id)
    try:
        st = client.standings(cfg.id, cfg.sport)
    except FleaflickerError as e:
        raise HTTPException(502, str(e))
    out = []
    for d in st.get("divisions", []) or []:
        for t in d.get("teams", []) or []:
            out.append({"id": t["id"], "name": t.get("name"), "division": d.get("name"),
                        "record": (t.get("recordOverall") or {}).get("formatted"),
                        "owners": [o.get("displayName") for o in t.get("owners", []) or []]})
    return out


@app.get("/api/leagues/{league_id}/status")
def status(league_id: int):
    ctx = _ctx(league_id)
    return {"season": ctx.season, "week": ctx.week, "positions": ctx.cfg.enabled_positions(),
            "stats_current_rows": int(len(ctx.cur)), "stats_prior_rows": int(len(ctx.prior)),
            "defense_teams": len(ctx.defense), "schedule_weeks": len(ctx.schedule),
            "league_teams": ctx.teams, "owned_players": len(ctx.owned), "scoring_rules": len(ctx.rules)}


# ----------------------------------------------------------------- analysis
def _weeks(ctx: analysis.Context, from_week: int | None, n: int | None) -> list[int]:
    start = from_week or ctx.week
    n = n or ctx.cfg.lookahead_weeks
    return [w for w in range(start, start + n) if w <= 18]


@app.get("/api/leagues/{league_id}/defense")
def defense(league_id: int, week: Optional[int] = None):
    """Defense-vs-position table (rank 32 = softest) plus the opponent of every team in `week`."""
    ctx = _ctx(league_id)
    wk = week or ctx.week
    rows = []
    for d in ctx.defense:
        r = dict(d)
        g = ctx.schedule.get(wk, {}).get(d["team"])
        r["opponent_in_week"] = g["opponent"] if g else None
        r["home_in_week"] = g["home"] if g else None
        rows.append(r)
    return {"season": ctx.season, "week": wk, "defenses": rows}


@app.get("/api/leagues/{league_id}/players")
def players(league_id: int,
            owner: str = Query("all", description="all | free | mine | <team_id>"),
            position: Optional[str] = None, from_week: Optional[int] = None, weeks: Optional[int] = None,
            sort: str = "outlook_avg", limit: int = 150, force: bool = False):
    ctx = _ctx(league_id)
    wks = _weeks(ctx, from_week, weeks)
    pool = ctx.player_pool(force=force)
    if position:
        pool = [p for p in pool if p.position == position.upper()]
    rows = [ctx.analyze_player(p, wks) for p in pool]
    if owner == "free":
        rows = [r for r in rows if not r["owner_team_id"]]
    elif owner == "mine":
        rows = [r for r in rows if r["owner_team_id"] == ctx.cfg.my_team_id]
    elif owner not in ("all", "", None):
        rows = [r for r in rows if str(r["owner_team_id"]) == str(owner)]
    key = sort if sort in ("outlook_avg", "outlook_total", "baseline", "projected", "ff_matchup_rank") else "outlook_avg"
    rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0)))
    return {"season": ctx.season, "week": ctx.week, "weeks": wks, "count": len(rows), "players": rows[:limit]}


@app.get("/api/leagues/{league_id}/matchups")
def matchups(league_id: int, week: Optional[int] = None, position: str = "RB", top: int = 12):
    """Softest defenses against `position` in `week` and the players who face them.

    RB -> sorted by run defense, QB/WR/TE -> by pass defense, K -> by points allowed to K.
    """
    ctx = _ctx(league_id)
    pos = position.upper()
    wk = week or ctx.week
    pool = [p for p in ctx.player_pool() if p.position == pos]
    by_opp: dict[str, list] = {}
    for p in pool:
        m = ctx.matchup(p.team, pos, wk)
        if m.get("opponent"):
            by_opp.setdefault(m["opponent"], []).append(ctx.analyze_player(p, [wk]))
    keyname = "run" if pos == "RB" else "pass"
    rows = []
    for d in ctx.defense:
        rank = d["vs"].get(pos, {}).get("rank") if pos == "K" else d[keyname]["rank"]
        facing = sorted(by_opp.get(d["team"], []), key=lambda r: -(r["weeks"][0]["score"] or 0))
        rows.append({"team": d["team"], "rank": rank, "run": d["run"], "pass": d["pass"], "vs": d["vs"].get(pos),
                     "opponent_in_week": (ctx.schedule.get(wk, {}).get(d["team"]) or {}).get("opponent"),
                     "players": facing})
    rows.sort(key=lambda r: -(r["rank"] or 0))
    return {"season": ctx.season, "week": wk, "position": pos, "sorted_by": keyname, "defenses": rows[:top]}


@app.get("/api/leagues/{league_id}/roster/{team_id}")
def roster(league_id: int, team_id: int, from_week: Optional[int] = None, weeks: Optional[int] = None):
    ctx = _ctx(league_id)
    wks = _weeks(ctx, from_week, weeks)
    try:
        pl = ctx.team_players(team_id)
    except FleaflickerError as e:
        raise HTTPException(502, str(e))
    return {"weeks": wks, "players": [ctx.analyze_player(p, wks) for p in pl]}


# ------------------------------------------------------------------ static
if settings.STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=settings.STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = settings.STATIC_DIR / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(settings.STATIC_DIR / "index.html")
