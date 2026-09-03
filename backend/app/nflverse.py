"""nflverse data: NFL schedule + weekly player stats -> "defense vs. position" tables.

Sources (public GitHub releases of https://github.com/nflverse/nflverse-data):
  schedules/games.csv
  stats_player/stats_player_week_{season}.csv
  players/players.csv   (optional, for Sportradar-ID -> nflverse-ID mapping)
"""
from __future__ import annotations

import io
import logging
import time
from functools import lru_cache

import httpx
import pandas as pd

from . import settings
from .models import ScoringRule

log = logging.getLogger(__name__)

POSITIONS = ["QB", "RB", "WR", "TE", "K"]

# Fleaflicker -> nflverse team abbreviations
TEAM_MAP = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LA", "HST": "HOU",
            "BLT": "BAL", "CLV": "CLE", "ARZ": "ARI"}


def norm_team(abbr: str | None) -> str:
    if not abbr:
        return ""
    abbr = abbr.upper()
    return TEAM_MAP.get(abbr, abbr)


# Fleaflicker scoring category id -> nflverse stat column(s)
CATEGORY_COLUMNS: dict[int, list[str]] = {
    1: ["attempts"], 2: ["completions"], 3: ["passing_yards"], 4: ["passing_2pt_conversions"],
    5: ["passing_tds"], 7: ["passing_interceptions", "interceptions"],
    21: ["carries"], 22: ["rushing_yards"], 23: ["rushing_2pt_conversions"], 24: ["rushing_tds"],
    41: ["receptions"], 42: ["receiving_yards"], 43: ["receiving_2pt_conversions"], 44: ["receiving_tds"],
    26: ["sack_fumbles", "rushing_fumbles", "receiving_fumbles"],
    27: ["sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"],
    101: ["fg_made"], 104: ["pat_made"],
}
SUM_CATEGORIES = {26, 27}   # columns are summed instead of "first existing"


# ----------------------------------------------------------------- download
def _download(rel_path: str, ttl: int = settings.NFLVERSE_TTL) -> bytes:
    local = settings.NFLVERSE_DIR / rel_path.replace("/", "_")
    fresh = local.exists() and time.time() - local.stat().st_mtime < ttl
    if fresh:
        return local.read_bytes()
    url = f"{settings.NFLVERSE_BASE}/{rel_path}"
    try:
        with httpx.Client(follow_redirects=True, timeout=120) as c:
            r = c.get(url)
            r.raise_for_status()
        local.write_bytes(r.content)
        return r.content
    except Exception as e:  # noqa: BLE001
        if local.exists():
            log.warning("nflverse download failed (%s), using stale file %s", e, local)
            return local.read_bytes()
        raise RuntimeError(f"nflverse download failed for {url}: {e}") from e


def load_schedule(season: int) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(_download("schedules/games.csv")), low_memory=False)
    df = df[(df["season"] == season) & (df["game_type"] == "REG")]
    return df[["season", "week", "gameday", "away_team", "home_team"]].copy()


def load_weekly_stats(season: int) -> pd.DataFrame:
    try:
        raw = _download(f"stats_player/stats_player_week_{season}.csv")
    except RuntimeError:
        # season not published yet (e.g. before week 1) -> empty frame
        return pd.DataFrame()
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"]
    if "team" not in df.columns and "recent_team" in df.columns:
        df = df.rename(columns={"recent_team": "team"})
    df = df[df["position"].isin(POSITIONS)]
    return df.reset_index(drop=True)


def load_players() -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(_download("players/players.csv", ttl=7 * 24 * 3600)), low_memory=False)
    except RuntimeError:
        return pd.DataFrame(columns=["gsis_id", "sportradar_id", "display_name", "position"])
    cols = [c for c in ["gsis_id", "sportradar_id", "display_name", "position", "latest_team"] if c in df.columns]
    return df[cols]


# ----------------------------------------------------------------- scoring
def apply_scoring(df: pd.DataFrame, rules: list[ScoringRule]) -> pd.Series:
    """Fantasy points per player-game according to Fleaflicker scoring rules."""
    pts = pd.Series(0.0, index=df.index)
    if df.empty:
        return pts
    for r in rules:
        cols = CATEGORY_COLUMNS.get(r.category_id)
        if not cols:
            continue
        present = [c for c in cols if c in df.columns]
        if not present:
            continue
        if r.category_id in SUM_CATEGORIES:
            stat = df[present].fillna(0).sum(axis=1)
        else:
            stat = df[present[0]].fillna(0)
        if r.is_bonus or r.bound_lower is not None:
            cond = stat >= (r.bound_lower or 0)
            if r.bound_upper is not None:
                cond &= stat <= r.bound_upper
            pts += cond.astype(float) * r.points
        elif r.for_every:
            pts += stat / float(r.for_every) * r.points
        else:
            pts += stat * r.points
    return pts


def default_rules() -> list[ScoringRule]:
    """Standard PPR fallback if a league has no parsed scoring rules."""
    return [ScoringRule(category_id=3, name="Passing Yard", points=1, for_every=25),
            ScoringRule(category_id=5, name="Passing TD", points=4),
            ScoringRule(category_id=7, name="Interception", points=-2),
            ScoringRule(category_id=22, name="Rushing Yard", points=1, for_every=10),
            ScoringRule(category_id=24, name="Rushing TD", points=6),
            ScoringRule(category_id=41, name="Catch", points=1),
            ScoringRule(category_id=42, name="Receiving Yard", points=1, for_every=10),
            ScoringRule(category_id=44, name="Receiving TD", points=6),
            ScoringRule(category_id=27, name="Fumble Lost", points=-2)]


# ------------------------------------------------------- defense vs position
def _aggregate(df: pd.DataFrame, rules: list[ScoringRule]) -> pd.DataFrame:
    """Per (defense, position): total points/yards allowed and games played."""
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    d["fpts"] = apply_scoring(d, rules)
    for c in ["rushing_yards", "rushing_tds", "passing_yards", "passing_tds", "receiving_yards", "receiving_tds"]:
        if c not in d.columns:
            d[c] = 0.0
    games = d.groupby("opponent_team")["week"].nunique().rename("games")
    by_pos = d.groupby(["opponent_team", "position"]).agg(
        fpts=("fpts", "sum"), rush_yds=("rushing_yards", "sum"), rush_tds=("rushing_tds", "sum"),
        pass_yds=("passing_yards", "sum"), pass_tds=("passing_tds", "sum"),
        rec_yds=("receiving_yards", "sum"), rec_tds=("receiving_tds", "sum")).reset_index()
    by_pos = by_pos.merge(games, left_on="opponent_team", right_index=True)
    return by_pos


def defense_vs_position(current: pd.DataFrame, prior: pd.DataFrame, rules: list[ScoringRule],
                        prior_weight: float = 0.5, decay_games: int = 8) -> list[dict]:
    """Combine current and prior season into per-game "allowed" stats and ranks.

    prior season rows get weight w = prior_weight * max(0, 1 - games_current/decay_games)
    so early in the season last year's data still counts, later it fades out.
    """
    cur = _aggregate(current, rules)
    pri = _aggregate(prior, rules)
    if cur.empty and pri.empty:
        return []
    metrics = ["fpts", "rush_yds", "rush_tds", "pass_yds", "pass_tds", "rec_yds", "rec_tds"]
    teams = sorted(set(cur["opponent_team"]) | set(pri["opponent_team"]) if not cur.empty and not pri.empty
                   else set((cur if not cur.empty else pri)["opponent_team"]))
    rows = []
    for t in teams:
        c = cur[cur["opponent_team"] == t] if not cur.empty else cur
        p = pri[pri["opponent_team"] == t] if not pri.empty else pri
        g_cur = int(c["games"].iloc[0]) if len(c) else 0
        g_pri = int(p["games"].iloc[0]) if len(p) else 0
        w = prior_weight * max(0.0, 1 - g_cur / decay_games) if g_pri else 0.0
        denom = g_cur + w * g_pri
        if denom <= 0:
            continue
        row: dict = {"team": t, "games_current": g_cur, "games_prior": g_pri, "prior_weight": round(w, 2), "vs": {}}
        tot = {m: 0.0 for m in metrics}
        for pos in POSITIONS:
            vals = {}
            for m in metrics:
                cv = float(c.loc[c["position"] == pos, m].sum()) if len(c) else 0.0
                pv = float(p.loc[p["position"] == pos, m].sum()) if len(p) else 0.0
                vals[m] = (cv + w * pv) / denom
                tot[m] += vals[m]
            row["vs"][pos] = {"pts_pg": round(vals["fpts"], 2)}
        row["run"] = {"yds_pg": round(tot["rush_yds"], 1), "tds_pg": round(tot["rush_tds"], 2)}
        row["pass"] = {"yds_pg": round(tot["pass_yds"], 1), "tds_pg": round(tot["pass_tds"], 2)}
        rows.append(row)

    # ranks: 1 = best defense (fewest allowed), 32 = softest matchup
    def rank(key):
        vals = [key(r) for r in rows]
        mean = sum(vals) / len(vals) if vals else 0
        order = sorted(range(len(rows)), key=lambda i: vals[i])
        ranks = {i: k + 1 for k, i in enumerate(order)}
        return ranks, mean

    for pos in POSITIONS:
        ranks, mean = rank(lambda r, pos=pos: r["vs"][pos]["pts_pg"])
        for i, r in enumerate(rows):
            v = r["vs"][pos]
            v["rank"] = ranks[i]
            v["factor"] = round(v["pts_pg"] / mean, 3) if mean else 1.0
            v["league_avg"] = round(mean, 2)
    ranks, mean = rank(lambda r: r["run"]["yds_pg"] + 20 * r["run"]["tds_pg"])
    for i, r in enumerate(rows):
        r["run"]["rank"] = ranks[i]
    ranks, mean = rank(lambda r: r["pass"]["yds_pg"] + 20 * r["pass"]["tds_pg"])
    for i, r in enumerate(rows):
        r["pass"]["rank"] = ranks[i]
    return rows


def schedule_lookup(sched: pd.DataFrame) -> dict[int, dict[str, dict]]:
    """{week: {team: {"opponent": X, "home": bool}}}"""
    out: dict[int, dict[str, dict]] = {}
    for _, g in sched.iterrows():
        wk = int(g["week"])
        out.setdefault(wk, {})
        out[wk][g["home_team"]] = {"opponent": g["away_team"], "home": True, "date": str(g.get("gameday", ""))}
        out[wk][g["away_team"]] = {"opponent": g["home_team"], "home": False, "date": str(g.get("gameday", ""))}
    return out


@lru_cache(maxsize=8)
def current_week(season: int) -> int:
    """First regular-season week whose games are not all in the past."""
    try:
        sched = load_schedule(season)
    except RuntimeError:
        return 1
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    for wk, grp in sched.groupby("week"):
        last = pd.to_datetime(grp["gameday"]).max()
        if last + pd.Timedelta(days=1) >= today:
            return int(wk)
    return int(sched["week"].max()) if len(sched) else 1


def player_game_log(stats: pd.DataFrame, rules: list[ScoringRule]) -> pd.DataFrame:
    """Per player: games, avg fantasy points, last-3 avg, std (consistency)."""
    if stats.empty:
        return pd.DataFrame(columns=["player_id", "games", "avg", "last3", "std"])
    d = stats.copy()
    d["fpts"] = apply_scoring(d, rules)
    d = d.sort_values(["player_id", "week"])
    g = d.groupby("player_id")
    out = pd.DataFrame({
        "games": g["week"].count(),
        "avg": g["fpts"].mean(),
        "std": g["fpts"].std().fillna(0),
        "last3": g["fpts"].apply(lambda s: s.tail(3).mean()),
        "name": g["player_display_name"].last() if "player_display_name" in d.columns else g["player_name"].last(),
        "position": g["position"].last(),
        "team": g["team"].last(),
    }).reset_index()
    return out
