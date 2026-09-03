"""Thin client for the public Fleaflicker API (no auth needed for public leagues).

Docs: https://www.fleaflicker.com/api-docs/index.html
Responses are cached on disk (DATA_DIR/cache) for FLEAFLICKER_TTL seconds.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from . import settings
from .models import RosterSlot, ScoringRule


class FleaflickerError(RuntimeError):
    pass


class FleaflickerClient:
    def __init__(self, base: str = settings.FLEAFLICKER_BASE, ttl: int = settings.FLEAFLICKER_TTL):
        self.base = base
        self.ttl = ttl
        self._http = httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 football-analytics/0.1", "Accept": "application/json"})

    # ---------------------------------------------------------------- caching
    def _cache_path(self, endpoint: str, params: list[tuple[str, Any]]):
        key = hashlib.sha1(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
        return settings.CACHE_DIR / f"ff_{endpoint}_{key}.json"

    def get(self, endpoint: str, params: Iterable[tuple[str, Any]], force: bool = False) -> dict:
        params = [(k, v) for k, v in params if v is not None]
        path = self._cache_path(endpoint, params)
        if not force and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return json.loads(path.read_text("utf-8"))
        r = self._http.get(f"{self.base}/{endpoint}", params=params)
        if r.status_code != 200:
            raise FleaflickerError(f"{endpoint} -> HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as e:  # HTML error page
            raise FleaflickerError(f"{endpoint}: non-JSON response") from e
        try:
            path.write_text(json.dumps(data), "utf-8")
        except OSError:
            pass
        return data

    def clear_cache(self, league_id: int | None = None) -> int:
        n = 0
        for p in settings.CACHE_DIR.glob("ff_*.json"):
            p.unlink(missing_ok=True)
            n += 1
        return n

    # -------------------------------------------------------------- endpoints
    def rules(self, league_id: int, sport: str = "NFL") -> dict:
        return self.get("FetchLeagueRules", [("sport", sport), ("league_id", league_id)])

    def standings(self, league_id: int, sport: str = "NFL", season: int | None = None) -> dict:
        return self.get("FetchLeagueStandings", [("sport", sport), ("league_id", league_id), ("season", season)])

    def scoreboard(self, league_id: int, sport: str = "NFL", season: int | None = None,
                   scoring_period: int | None = None) -> dict:
        return self.get("FetchLeagueScoreboard", [("sport", sport), ("league_id", league_id),
                                                  ("season", season), ("scoring_period", scoring_period)])

    def rosters(self, league_id: int, sport: str = "NFL", season: int | None = None,
                scoring_period: int | None = None, force: bool = False) -> dict:
        return self.get("FetchLeagueRosters", [("sport", sport), ("league_id", league_id), ("season", season),
                                               ("scoring_period", scoring_period),
                                               ("external_id_type", "SPORTRADAR")], force=force)

    def team_roster(self, league_id: int, team_id: int, sport: str = "NFL", season: int | None = None,
                    scoring_period: int | None = None) -> dict:
        return self.get("FetchRoster", [("sport", sport), ("league_id", league_id), ("team_id", team_id),
                                        ("season", season), ("scoring_period", scoring_period),
                                        ("external_id_type", "SPORTRADAR")])

    def player_listing(self, league_id: int, *, sport: str = "NFL", sort: str = "SORT_PROJECTIONS",
                       season: int | None = None, period: int | None = None, offset: int = 0,
                       positions: list[str] | None = None, free_agent_only: bool | None = None,
                       query: str | None = None, force: bool = False) -> dict:
        params: list[tuple[str, Any]] = [("sport", sport), ("league_id", league_id), ("sort", sort),
                                         ("sort_season", season), ("sort_period", period),
                                         ("result_offset", offset or None),
                                         ("filter.free_agent_only", "true" if free_agent_only else None),
                                         ("filter.query", query),
                                         ("external_id_type", "SPORTRADAR")]
        for p in positions or []:
            params.append(("filter.position.eligibility", p))
        return self.get("FetchPlayerListing", params, force=force)

    def player_pool(self, league_id: int, *, season: int | None, period: int | None,
                    positions: list[str], max_pages: int = 12, sort: str = "SORT_PROJECTIONS",
                    free_agent_only: bool | None = None, force: bool = False) -> list[dict]:
        """Fetch up to `max_pages` pages (30 players each) of the league player listing."""
        players: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            data = self.player_listing(league_id, sort=sort, season=season, period=period, offset=offset,
                                       positions=positions, free_agent_only=free_agent_only, force=force)
            page = data.get("players", [])
            players.extend(page)
            nxt = data.get("resultOffsetNext")
            if not page or not nxt or nxt <= offset:
                break
            offset = nxt
        return players


# ----------------------------------------------------------------- parsers
def parse_slots(rules: dict) -> list[RosterSlot]:
    slots = []
    for rp in rules.get("rosterPositions", []):
        if rp.get("group") != "START":
            continue
        slots.append(RosterSlot(label=rp["label"], eligibility=rp.get("eligibility", []),
                                start=int(rp.get("start", 0) or 0), enabled=bool(rp.get("start"))))
    return slots


def parse_scoring(rules: dict) -> list[ScoringRule]:
    out = []
    for g in rules.get("groups", []):
        for s in g.get("scoringRules", []) or []:
            cat = s.get("category", {})
            out.append(ScoringRule(
                category_id=int(cat.get("id", 0)), name=cat.get("nameSingular", ""),
                points=float((s.get("points") or {}).get("value", 0)),
                for_every=s.get("forEvery"), bound_lower=s.get("boundLower"), bound_upper=s.get("boundUpper"),
                is_bonus=bool(s.get("isBonus"))))
    return out


@dataclass
class Player:
    id: int
    name: str
    position: str
    team: str                       # NFL team abbreviation
    bye_week: int | None
    injury: str | None
    owner_team_id: int | None = None
    owner_team_name: str | None = None
    sportradar_id: str | None = None
    pct_owned: float | None = None
    projected: float | None = None          # projection for the requested week
    season_avg: float | None = None
    draft_rank: int | None = None
    opponent: str | None = None             # opponent for requested week
    home: bool | None = None
    ff_matchup_rank: int | None = None      # Fleaflicker: opponent rank vs. position (32 = softest)
    ff_matchup_rating: str | None = None
    ff_category_ranks: dict = field(default_factory=dict)


def _val(v: Any) -> float | None:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_player(entry: dict) -> Player:
    pp = entry.get("proPlayer", entry)
    inj = pp.get("injury") or {}
    ext = pp.get("externalIds") or []
    p = Player(
        id=int(pp["id"]), name=pp.get("nameFull", ""), position=pp.get("position", ""),
        team=pp.get("proTeamAbbreviation", "") or "", bye_week=pp.get("nflByeWeek"),
        injury=inj.get("typeAbbreviaition") or inj.get("typeFull"),
        sportradar_id=ext[0].get("id") if ext else None,
        pct_owned=pp.get("percentOwnedRatio"),
        projected=_val(entry.get("viewingProjectedPoints")),
        season_avg=_val(entry.get("seasonAverage")) or _val(entry.get("viewingActualPointsAverage")),
        draft_rank=(entry.get("rankDraft") or {}).get("ordinal"),
    )
    owner = entry.get("owner")
    if owner:
        p.owner_team_id = owner.get("id")
        p.owner_team_name = owner.get("name")
    games = entry.get("requestedGames") or []
    if games:
        g = games[0]
        game = g.get("game", {})
        home = (game.get("home") or {}).get("abbreviation")
        away = (game.get("away") or {}).get("abbreviation")
        if home and away:
            p.home = p.team == home
            p.opponent = away if p.home else home
        if g.get("pointsProjected"):
            p.projected = _val(g["pointsProjected"])
        ranks = g.get("ranks") or {}
        dp = ranks.get("defaultPoints") or {}
        p.ff_matchup_rank = dp.get("rank")
        p.ff_matchup_rating = dp.get("rating")
        for c in ranks.get("categories", []) or []:
            p.ff_category_ranks[(c.get("category") or {}).get("nameSingular", "?")] = (c.get("rank") or {}).get("rank")
    return p


def parse_rosters(data: dict) -> tuple[list[dict], dict[int, tuple[int, str]]]:
    """Return (teams, {player_id: (team_id, team_name)})."""
    teams, owned = [], {}
    for r in data.get("rosters", []):
        t = r.get("team", {})
        teams.append({"id": t.get("id"), "name": t.get("name"), "logo": t.get("logoUrl"),
                      "record": (t.get("recordOverall") or {}).get("formatted")})
        for pl in r.get("players", []) or []:
            pp = pl.get("proPlayer") or {}
            if pp.get("id"):
                owned[int(pp["id"])] = (t.get("id"), t.get("name"))
    return teams, owned


def parse_team_roster(data: dict) -> list[dict]:
    """FetchRoster response -> list of player listing entries (with slot label)."""
    out = []
    for g in data.get("groups", []) or []:
        for s in g.get("slots", []) or []:
            lp = s.get("leaguePlayer")
            if lp:
                lp = dict(lp)
                lp["_slot"] = (s.get("position") or {}).get("label")
                out.append(lp)
    return out
