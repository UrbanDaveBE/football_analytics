"""Matchup analysis: combines the Fleaflicker player pool with nflverse defense data.

Score of a player for week w:
    score_w = baseline * factor(opponent_w, position)
baseline  = per-game fantasy points (current season blended with last season, league scoring)
            or Fleaflicker projection if there is no game log yet
factor    = points the opponent allows to that position / league average (1.0 = neutral)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict

import pandas as pd

from . import nflverse, store
from .fleaflicker import FleaflickerClient, Player, parse_player, parse_rosters, parse_team_roster
from .models import LeagueConfig

log = logging.getLogger(__name__)
_ctx_lock = threading.Lock()
_contexts: dict[int, tuple[float, "Context"]] = {}
CONTEXT_TTL = 1800


def _norm_name(n: str) -> str:
    n = re.sub(r"[^a-z ]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


class Context:
    """Everything needed for one league's analysis, cached for CONTEXT_TTL seconds."""

    def __init__(self, cfg: LeagueConfig, client: FleaflickerClient):
        self.cfg = cfg
        self.client = client
        self.season = cfg.season or pd.Timestamp.utcnow().year
        self.week = nflverse.current_week(self.season)
        rules = cfg.scoring or nflverse.default_rules()
        self.rules = rules

        self.cur = nflverse.load_weekly_stats(self.season)
        self.prior = nflverse.load_weekly_stats(self.season - 1)
        self.defense = nflverse.defense_vs_position(self.cur, self.prior, rules, cfg.prior_season_weight)
        self.defense_by_team = {d["team"]: d for d in self.defense}
        try:
            self.schedule = nflverse.schedule_lookup(nflverse.load_schedule(self.season))
        except RuntimeError as e:
            log.warning("schedule unavailable: %s", e)
            self.schedule = {}

        # game logs for baselines (current season if >=3 games, else blended with prior)
        self.log_cur = nflverse.player_game_log(self.cur, rules).set_index("player_id") if not self.cur.empty else pd.DataFrame()
        self.log_pri = nflverse.player_game_log(self.prior, rules).set_index("player_id") if not self.prior.empty else pd.DataFrame()

        players = nflverse.load_players()
        self.sr_to_gsis = {}
        self.name_to_gsis = {}
        if not players.empty:
            for r in players.dropna(subset=["gsis_id"]).itertuples(index=False):
                if getattr(r, "sportradar_id", None) and isinstance(r.sportradar_id, str):
                    self.sr_to_gsis[r.sportradar_id] = r.gsis_id
                self.name_to_gsis[(_norm_name(getattr(r, "display_name", "")), getattr(r, "position", ""))] = r.gsis_id
        for lg in (self.log_cur, self.log_pri):
            for pid, r in lg.iterrows():
                self.name_to_gsis.setdefault((_norm_name(r["name"]), r["position"]), pid)

        # league rosters / ownership
        self.teams, self.owned = [], {}
        try:
            self.teams, self.owned = parse_rosters(client.rosters(cfg.id, cfg.sport, self.season))
        except Exception as e:  # noqa: BLE001
            log.warning("rosters unavailable: %s", e)

    # ------------------------------------------------------------ helpers
    def gsis(self, p: Player) -> str | None:
        if p.sportradar_id and p.sportradar_id in self.sr_to_gsis:
            return self.sr_to_gsis[p.sportradar_id]
        return self.name_to_gsis.get((_norm_name(p.name), p.position))

    def baseline(self, p: Player) -> tuple[float | None, str]:
        """(points per game, source)"""
        gid = self.gsis(p)
        cur = self.log_cur.loc[gid] if gid is not None and not self.log_cur.empty and gid in self.log_cur.index else None
        pri = self.log_pri.loc[gid] if gid is not None and not self.log_pri.empty and gid in self.log_pri.index else None
        if cur is not None and cur["games"] >= 6:
            return float(cur["avg"]), f"{self.season} avg ({int(cur['games'])} G)"
        if cur is not None and pri is not None:
            g = float(cur["games"])
            w = g / 6.0
            return float(w * cur["avg"] + (1 - w) * pri["avg"]), f"blend {self.season}/{self.season - 1}"
        if cur is not None:
            return float(cur["avg"]), f"{self.season} avg ({int(cur['games'])} G)"
        if pri is not None and pri["games"] >= 4:
            return float(pri["avg"]), f"{self.season - 1} avg ({int(pri['games'])} G)"
        if p.projected:
            return float(p.projected), "Fleaflicker projection"
        if pri is not None:
            return float(pri["avg"]), f"{self.season - 1} avg ({int(pri['games'])} G)"
        return None, "n/a"

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

    def analyze_player(self, p: Player, weeks: list[int]) -> dict:
        base, source = self.baseline(p)
        rows = []
        for w in weeks:
            m = self.matchup(p.team, p.position, w)
            m["score"] = round((base or 0) * m["factor"], 2) if base is not None and not m["bye"] else 0.0
            rows.append(m)
        played = [r for r in rows if not r["bye"]]
        total = round(sum(r["score"] for r in rows), 2)
        avg = round(total / len(played), 2) if played else 0.0
        d = asdict(p)
        if p.id in self.owned:
            d["owner_team_id"], d["owner_team_name"] = self.owned[p.id]
        d.update(baseline=round(base, 2) if base is not None else None, baseline_source=source,
                 weeks=rows, outlook_total=total, outlook_avg=avg,
                 best_week=max(played, key=lambda r: r["score"])["week"] if played else None,
                 worst_week=min(played, key=lambda r: r["score"])["week"] if played else None,
                 bye_in_range=any(r["bye"] for r in rows))
        return d

    # ------------------------------------------------------------ queries
    def player_pool(self, positions: list[str] | None = None, force: bool = False) -> list[Player]:
        pos = positions or self.cfg.enabled_positions()
        pos = [p for p in pos if p in nflverse.POSITIONS] or nflverse.POSITIONS
        raw = self.client.player_pool(self.cfg.id, season=self.season, period=self.week, positions=pos,
                                      max_pages=self.cfg.player_pool_pages, force=force)
        seen, out = set(), []
        for e in raw:
            p = parse_player(e)
            if p.id in seen or p.position not in pos:
                continue
            seen.add(p.id)
            out.append(p)
        return out

    def team_players(self, team_id: int) -> list[Player]:
        data = self.client.team_roster(self.cfg.id, team_id, self.cfg.sport, self.season, self.week)
        out = []
        for e in parse_team_roster(data):
            p = parse_player(e)
            p.owner_team_id = team_id
            out.append(p)
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
