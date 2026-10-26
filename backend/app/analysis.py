"""Matchup analysis: combines the Fleaflicker player pool with nflverse defense data.

Score of a player for week w:
    score_w = base * matchup_factor(opponent_w, position) * availability_factor(w)

base       per-game fantasy points in the league scoring:
             history  = current season avg (blended with last season early on) + recent form (last 3)
             role     = Fleaflicker projection for the current week (knows depth chart / starters)
             base     = 0.6 * history + 0.4 * role, or mostly role if the projection says "backup"
matchup    points the opponent allows to that position / league average (1.0 = neutral)
availability  0 for OUT/IR/season-ending, 0.8 for Q, … (see injuries.py)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict

import pandas as pd

from . import injuries, nflverse, store
from .fleaflicker import FleaflickerClient, Player, parse_player, parse_rosters, parse_team_roster
from .models import LeagueConfig

log = logging.getLogger(__name__)
_ctx_lock = threading.Lock()
_contexts: dict[int, tuple[float, "Context"]] = {}
CONTEXT_TTL = 1800
BACKUP_RATIO = 0.35   # projection below 35 % of history -> probably backup / lost role


def _norm_name(n: str) -> str:
    n = re.sub(r"[^a-z ]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _row(df: pd.DataFrame, key):
    if key is None or df is None or df.empty or key not in df.index:
        return None
    return df.loc[key]


class Context:
    """Everything needed for one league's analysis, cached for CONTEXT_TTL seconds."""

    def __init__(self, cfg: LeagueConfig, client: FleaflickerClient):
        self.cfg = cfg
        self.client = client
        self.season = cfg.season or pd.Timestamp.utcnow().year
        self.week = nflverse.current_week(self.season)
        self.rules = cfg.scoring or nflverse.default_rules()

        self.cur = nflverse.load_weekly_stats(self.season)
        self.prior = nflverse.load_weekly_stats(self.season - 1)
        self.defense = nflverse.defense_vs_position(self.cur, self.prior, self.rules, cfg.prior_season_weight)
        self.defense_by_team = {d["team"]: d for d in self.defense}
        try:
            self.schedule = nflverse.schedule_lookup(nflverse.load_schedule(self.season))
        except RuntimeError as e:
            log.warning("schedule unavailable: %s", e)
            self.schedule = {}
        self.week_starts = nflverse.week_starts(self.season)
        self.espn = injuries.espn_injuries()

        self.log_cur = nflverse.player_game_log(self.cur, self.rules).set_index("player_id") if not self.cur.empty else pd.DataFrame()
        self.log_pri = nflverse.player_game_log(self.prior, self.rules).set_index("player_id") if not self.prior.empty else pd.DataFrame()

        players = nflverse.load_players()
        self.sr_to_gsis, self.name_to_gsis = {}, {}
        if not players.empty:
            for r in players.dropna(subset=["gsis_id"]).itertuples(index=False):
                if isinstance(getattr(r, "sportradar_id", None), str):
                    self.sr_to_gsis[r.sportradar_id] = r.gsis_id
                self.name_to_gsis[(_norm_name(getattr(r, "display_name", "")), getattr(r, "position", ""))] = r.gsis_id
        for lg in (self.log_cur, self.log_pri):
            for pid, r in lg.iterrows():
                self.name_to_gsis.setdefault((_norm_name(r["name"]), r["position"]), pid)

        self.teams, self.owned = [], {}
        try:
            self.teams, self.owned = parse_rosters(self.client.rosters(cfg.id, cfg.sport, self.season))
        except Exception as e:  # noqa: BLE001
            log.warning("rosters unavailable: %s", e)
        self._pool: list[Player] | None = None
        self._analyzed: dict[tuple, dict] = {}

    # ------------------------------------------------------------ helpers
    def gsis(self, p: Player) -> str | None:
        if p.sportradar_id and p.sportradar_id in self.sr_to_gsis:
            return self.sr_to_gsis[p.sportradar_id]
        return self.name_to_gsis.get((_norm_name(p.name), p.position))

    def availability(self, p: Player) -> injuries.Availability:
        return injuries.assess(p.name, p.injury_raw, p.news, self.espn, self.week_starts)

    def matchup(self, team: str, position: str, week: int) -> dict:
        t = nflverse.norm_team(team)
        game = self.schedule.get(week, {}).get(t)
        if not game:
            return {"week": week, "opponent": None, "bye": True, "factor": 0.0}
        opp = game["opponent"]
        d = self.defense_by_team.get(opp)
        out = {"week": week, "opponent": opp, "home": game["home"], "bye": False, "factor": 1.0,
               "date": game.get("date")}
        if d:
            vs = d["vs"].get(position, {})
            out.update(factor=vs.get("factor", 1.0), pos_rank=vs.get("rank"), pts_allowed=vs.get("pts_pg"),
                       run_rank=d["run"]["rank"], pass_rank=d["pass"]["rank"])
        return out

    def baseline(self, p: Player, avail: injuries.Availability) -> tuple[float | None, str, dict]:
        """(points per game, source, extra info)"""
        gid = self.gsis(p)
        cur, pri = _row(self.log_cur, gid), _row(self.log_pri, gid)
        info: dict = {"role_flag": None}
        hist, src = None, "n/a"
        if cur is not None and pri is not None and cur["games"] < 6:
            w = float(cur["games"]) / 6.0
            hist, src = float(w * cur["avg"] + (1 - w) * pri["avg"]), f"Mix {self.season}/{self.season - 1}"
        elif cur is not None:
            hist, src = float(cur["avg"]), f"Ø {self.season} ({int(cur['games'])} Sp.)"
        elif pri is not None:
            hist, src = float(pri["avg"]), f"Ø {self.season - 1} ({int(pri['games'])} Sp.)"
        if cur is not None and cur["games"] >= 3 and hist is not None:
            hist = 0.7 * hist + 0.3 * float(cur["last3"])
        if cur is not None:
            info.update(last3=round(float(cur["last3"]), 1), std=round(float(cur["std"]), 1),
                        games=int(cur["games"]), max=round(float(cur["max"]), 1), min=round(float(cur["min"]), 1),
                        touches=round(float(cur["touches_avg"]), 1), touches_last3=round(float(cur["touches_last3"]), 1),
                        targets=round(float(cur["targets_avg"]), 1), carries=round(float(cur["carries_avg"]), 1),
                        target_share=round(float(cur["target_share"] or 0) * 100, 1), log=cur["log"],
                        trend=round(float(cur["last3"]) - float(cur["avg"]), 1))

        proj = p.projected
        playing_now = avail.factor(self.week, self.week) > 0.5
        m_now = self.matchup(p.team, p.position, self.week)
        if hist is None and proj:
            return proj, "Fleaflicker-Projektion", info
        if proj is not None and playing_now and not m_now["bye"]:
            neutral = proj / m_now["factor"] if m_now["factor"] > 0.5 else proj
            if hist is None:
                return neutral, "Fleaflicker-Projektion", info
            if neutral < BACKUP_RATIO * hist and neutral < 8:
                info["role_flag"] = f"Rolle? FF projiziert nur {proj:.1f}"
                return 0.15 * hist + 0.85 * neutral, src + " → Rolle (FF)", info
            return 0.6 * hist + 0.4 * neutral, src + " + FF", info
        return hist, src, info

    def analyze_player(self, p: Player, weeks: list[int]) -> dict:
        key = (p.id, tuple(weeks))
        if key in self._analyzed:
            return self._analyzed[key]
        avail = self.availability(p)
        base, source, info = self.baseline(p, avail)
        rows = []
        for w in weeks:
            m = self.matchup(p.team, p.position, w)
            af = avail.factor(w, self.week)
            m["avail"] = round(af, 2)
            m["score"] = round((base or 0) * m["factor"] * af, 2) if base is not None and not m["bye"] else 0.0
            rows.append(m)
        played = [r for r in rows if not r["bye"] and r["avail"] > 0]
        total = round(sum(r["score"] for r in rows), 2)
        d = asdict(p)
        d.pop("news", None)
        d["news"] = p.news[:2]
        if p.id in self.owned:
            d["owner_team_id"], d["owner_team_name"] = self.owned[p.id]
        d.update(baseline=round(base, 2) if base is not None else None, baseline_source=source,
                 weeks=rows, outlook_total=total,
                 outlook_avg=round(total / len(weeks), 2) if weeks else 0.0,
                 best_week=max(played, key=lambda r: r["score"])["week"] if played else None,
                 worst_week=min(played, key=lambda r: r["score"])["week"] if played else None,
                 bye_in_range=any(r["bye"] for r in rows), availability=avail.to_dict(), stats=info,
                 role_flag=info.get("role_flag"))
        self._analyzed[key] = d
        return d

    # ------------------------------------------------------------ queries
    def player_pool(self, positions: list[str] | None = None, force: bool = False) -> list[Player]:
        if self._pool is not None and positions is None and not force:
            return self._pool
        pos = positions or self.cfg.enabled_positions()
        pos = [x for x in pos if x in nflverse.POSITIONS] or nflverse.POSITIONS
        raw = self.client.player_pool(self.cfg.id, season=self.season, period=self.week, positions=pos,
                                      max_pages=self.cfg.player_pool_pages, force=force)
        seen, out = set(), []
        for e in raw:
            p = parse_player(e)
            if p.id in seen or p.position not in pos:
                continue
            seen.add(p.id)
            if p.id in self.owned:
                p.owner_team_id, p.owner_team_name = self.owned[p.id]
            out.append(p)
        if positions is None:
            self._pool = out
        return out

    def team_players(self, team_id: int) -> list[Player]:
        data = self.client.team_roster(self.cfg.id, team_id, self.cfg.sport, self.season, self.week)
        pool = {p.id: p for p in self.player_pool()}
        out = []
        for e in parse_team_roster(data):
            p = parse_player(e)
            # roster endpoint has fewer fields; prefer the richer listing entry if present
            if p.id in pool:
                q = pool[p.id]
                p.projected = p.projected if p.projected is not None else q.projected
                p.news = p.news or q.news
                p.injury_raw = p.injury_raw or q.injury_raw
            p.owner_team_id = team_id
            p.slot = e.get("_slot")
            out.append(p)
        return out

    def players_by_id(self, ids: list[int]) -> list[Player]:
        pool = {p.id: p for p in self.player_pool()}
        missing = [i for i in ids if i not in pool]
        if missing:
            data = self.client.player_listing(self.cfg.id, season=self.season, period=self.week, player_ids=missing)
            for e in data.get("players", []):
                p = parse_player(e)
                if p.id in self.owned:
                    p.owner_team_id, p.owner_team_name = self.owned[p.id]
                pool[p.id] = p
        return [pool[i] for i in ids if i in pool]

    # ------------------------------------------------------- lineup logic
    def starting_slots(self) -> list[tuple[str, list[str]]]:
        """Slots expanded by starter count, most restrictive first (greedy is then near-optimal)."""
        out = []
        for s in self.cfg.slots:
            if s.enabled and s.start > 0:
                elig = [e for e in s.eligibility if e in nflverse.POSITIONS]
                if elig:
                    out.extend([(s.label, elig)] * s.start)
        return sorted(out, key=lambda x: len(x[1]))

    def best_lineup(self, rows: list[dict], week_idx: int) -> tuple[float, list[dict], list[dict]]:
        """(total, starters [{slot, player}], bench) for one week."""
        avail = sorted(rows, key=lambda r: -r["weeks"][week_idx]["score"])
        used, starters, total = set(), [], 0.0
        for label, elig in self.starting_slots():
            pick = next((r for r in avail if r["id"] not in used and r["position"] in elig), None)
            if pick is None:
                starters.append({"slot": label, "player": None, "score": 0})
                continue
            used.add(pick["id"])
            sc = pick["weeks"][week_idx]["score"]
            total += sc
            starters.append({"slot": label, "player": pick, "score": sc})
        bench = [r for r in rows if r["id"] not in used]
        return total, starters, bench

    def lineup_total(self, rows: list[dict], n_weeks: int) -> float:
        return sum(self.best_lineup(rows, i)[0] for i in range(n_weeks))

    def recommendations(self, weeks: list[int], max_fa: int = 80) -> dict:
        my_id = self.cfg.my_team_id
        if not my_id:
            raise ValueError("Bitte unter Ligen / Stammdaten zuerst „Mein Team“ wählen.")
        mine = [self.analyze_player(p, weeks) for p in self.team_players(my_id)]
        n = len(weeks)

        # lineup per week
        lineups = []
        for i, w in enumerate(weeks):
            total, starters, bench = self.best_lineup(mine, i)
            lineups.append({
                "week": w, "total": round(total, 1),
                "starters": [{"slot": s["slot"], "score": s["score"], "player": _slim(s["player"], i)} for s in starters],
                "bench": [_slim(b, i) for b in sorted(bench, key=lambda r: -r["weeks"][i]["score"])],
            })
        base_total = sum(l["total"] for l in lineups)

        # alerts for my roster
        alerts = []
        for r in mine:
            a = r["availability"]
            if a["season_ending"]:
                alerts.append({"level": "season", "player": r["name"], "text": "Saisonende – kann gedroppt werden. " + a["note"]})
            elif a["level"] == "out":
                rw = f", zurück ca. Woche {a['return_week']}" if a["return_week"] else ""
                ir = " → in IR-Slot verschieben" if a["status"] in injuries.LONG_TERM else ""
                alerts.append({"level": "out", "player": r["name"], "text": f"{a['status']}{rw}{ir}. {a['note']}"})
            elif a["level"] == "warn":
                alerts.append({"level": "warn", "player": r["name"], "text": f"{a['status']}: {a['note']}"})
            if r.get("role_flag"):
                alerts.append({"level": "warn", "player": r["name"], "text": r["role_flag"]})

        # waiver upgrades: FA in, best drop out, gain in optimal lineup points over the window
        fas = [self.analyze_player(p, weeks) for p in self.player_pool() if not p.owner_team_id]
        fas = [f for f in fas if f["availability"]["level"] not in ("out", "season")]
        fas = sorted(fas, key=lambda f: -f["outlook_total"])[:max_fa]
        droppable = sorted(mine, key=lambda r: r["outlook_total"])
        upgrades = []
        for fa in fas:
            best = None
            for d in droppable:
                roster = [r for r in mine if r["id"] != d["id"]] + [fa]
                gain = self.lineup_total(roster, n) - base_total
                if best is None or gain > best[0]:
                    best = (gain, d)
            if best and best[0] > 0.5:
                upgrades.append({"add": _slim(fa), "drop": _slim(best[1]), "gain": round(best[0], 1),
                                 "gain_per_week": round(best[0] / n, 1)})
        upgrades.sort(key=lambda u: -u["gain"])

        # depth: FA better than my weakest healthy player at the same position
        depth = []
        for pos in sorted({r["position"] for r in mine}):
            my_pos = [r for r in mine if r["position"] == pos]
            weakest = min(my_pos, key=lambda r: r["outlook_total"])
            better = [f for f in fas if f["position"] == pos and f["outlook_total"] > weakest["outlook_total"] + 2]
            if better:
                depth.append({"position": pos, "weakest": _slim(weakest),
                              "candidates": [_slim(b) for b in sorted(better, key=lambda f: -f["outlook_total"])[:5]]})

        # streamers: best free agents per week and position
        streamers = []
        for i, w in enumerate(weeks):
            per_pos = {}
            for pos in self.cfg.enabled_positions():
                cands = sorted((f for f in fas if f["position"] == pos), key=lambda f: -f["weeks"][i]["score"])[:3]
                if cands:
                    per_pos[pos] = [_slim(c, i) for c in cands]
            streamers.append({"week": w, "positions": per_pos})

        return {"weeks": weeks, "lineups": lineups, "projected_total": round(base_total, 1),
                "alerts": alerts, "upgrades": upgrades[:25], "depth": depth, "streamers": streamers}


def _slim(r: dict | None, week_idx: int | None = None) -> dict | None:
    if r is None:
        return None
    out = {k: r.get(k) for k in ("id", "name", "position", "team", "owner_team_name", "outlook_total", "outlook_avg",
                                 "baseline", "role_flag")}
    out["availability"] = r.get("availability")
    out["weeks"] = [{k: w.get(k) for k in ("week", "opponent", "home", "bye", "score", "factor", "avail")} for w in r["weeks"]]
    if week_idx is not None:
        w = r["weeks"][week_idx]
        out.update(score=w["score"], opponent=w.get("opponent"), home=w.get("home"), bye=w.get("bye"))
    return out


def get_context(league_id: int, client: FleaflickerClient, force: bool = False) -> Context:
    cfg = store.get_league(league_id)
    if cfg is None:
        raise KeyError(league_id)
    with _ctx_lock:
        hit = _contexts.get(league_id)
        if hit and not force and time.time() - hit[0] < CONTEXT_TTL:
            return hit[1]
        ctx = Context(cfg, client)
        _contexts[league_id] = (time.time(), ctx)
        return ctx


def invalidate(league_id: int | None = None) -> None:
    with _ctx_lock:
        if league_id is None:
            _contexts.clear()
        else:
            _contexts.pop(league_id, None)
