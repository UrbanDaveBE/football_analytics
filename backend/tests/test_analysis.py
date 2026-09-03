"""Unit tests with recorded Fleaflicker responses and synthetic nflverse data (no network)."""
import json
import os
import random
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("DATA_DIR", "/tmp/fa_test_data")

from app import nflverse, store  # noqa: E402
from app.fleaflicker import parse_player, parse_scoring, parse_slots  # noqa: E402
from app.models import LeagueConfig  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TEAMS = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
         "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"]


@pytest.fixture
def rules():
    return json.loads((FIX / "rules.json").read_text())


@pytest.fixture
def listing():
    return json.loads((FIX / "player_listing.json").read_text())


def synthetic_stats(season: int, weeks: int, seed: int = 1) -> pd.DataFrame:
    rnd = random.Random(seed)
    rows = []
    # DAL has a terrible run defense, DET a terrible pass defense
    soft = {"DAL": ("rushing_yards", 2.0), "DET": ("passing_yards", 1.6)}
    for wk in range(1, weeks + 1):
        order = TEAMS[:]
        rnd.shuffle(order)
        for i in range(0, 32, 2):
            a, b = order[i], order[i + 1]
            for team, opp in ((a, b), (b, a)):
                for pos, n in (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1), ("K", 1)):
                    for k in range(n):
                        r = {"player_id": f"{team}-{pos}{k}", "player_display_name": f"{team} {pos}{k}",
                             "position": pos, "team": team, "season": season, "week": wk, "season_type": "REG",
                             "opponent_team": opp, "passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
                             "rushing_yards": 0, "rushing_tds": 0, "receptions": 0, "receiving_yards": 0,
                             "receiving_tds": 0, "rushing_fumbles_lost": 0}
                        if pos == "QB":
                            r.update(passing_yards=rnd.gauss(240, 50), passing_tds=rnd.choice([0, 1, 2, 2, 3]))
                        elif pos == "RB":
                            r.update(rushing_yards=rnd.gauss(50, 25), rushing_tds=rnd.choice([0, 0, 1]),
                                     receptions=rnd.randint(0, 5), receiving_yards=rnd.gauss(20, 10))
                        elif pos == "K":
                            r.update()
                        else:
                            r.update(receptions=rnd.randint(1, 8), receiving_yards=rnd.gauss(50, 25),
                                     receiving_tds=rnd.choice([0, 0, 0, 1]))
                        if opp in soft:
                            col, f = soft[opp]
                            r[col] = r[col] * f
                        rows.append(r)
    return pd.DataFrame(rows)


def test_parse_rules(rules):
    slots = parse_slots(rules)
    labels = [s.label for s in slots]
    assert "QB" in labels and "RB/WR/TE" in labels and "K" in labels
    assert next(s for s in slots if s.label == "RB/WR/TE").start == 4
    sc = parse_scoring(rules)
    assert any(r.category_id == 41 and r.points == 1 for r in sc)      # PPR
    assert any(r.category_id == 3 and r.for_every == 20 for r in sc)   # 0.05/pass yd


def test_parse_player(listing):
    p = parse_player(listing["players"][0])
    assert p.name == "Jalen Hurts" and p.position == "QB" and p.team == "PHI"
    assert p.opponent == "WAS" and p.home is True
    assert p.projected == pytest.approx(27.94, abs=0.01)
    assert p.ff_matchup_rank == 30 and p.sportradar_id
    assert p.ff_category_ranks["Passing Yard"] == 28


def test_scoring_matches_league(rules):
    sc = parse_scoring(rules)
    df = pd.DataFrame([{"passing_yards": 300, "passing_tds": 2, "passing_interceptions": 1, "rushing_yards": 150,
                        "rushing_tds": 1, "receptions": 9, "receiving_yards": 0, "receiving_tds": 0, "completions": 25,
                        "carries": 30}])
    pts = nflverse.apply_scoring(df, sc).iloc[0]
    # pass: 300/20=15 +1 bonus + 12 TD -2 INT +2 cmp bonus; rush: 15 +1 bonus + 6 TD + 2 att bonus; rec: 9 + 2 bonus
    assert pts == pytest.approx(15 + 1 + 12 - 2 + 2 + 15 + 1 + 6 + 2 + 9 + 2)


def test_defense_vs_position_ranks(rules):
    sc = parse_scoring(rules)
    prior = synthetic_stats(2025, 17)
    cur = synthetic_stats(2026, 2, seed=2)
    rows = nflverse.defense_vs_position(cur, prior, sc, prior_weight=0.5)
    assert len(rows) == 32
    by = {r["team"]: r for r in rows}
    assert by["DAL"]["run"]["rank"] == 32          # softest run defense
    assert by["DET"]["pass"]["rank"] == 32         # softest pass defense
    assert by["DAL"]["vs"]["RB"]["rank"] >= 30
    assert by["DAL"]["vs"]["RB"]["factor"] > 1.2
    assert by["DAL"]["games_current"] == 2 and by["DAL"]["games_prior"] == 17
    assert 0 < by["DAL"]["prior_weight"] <= 0.5
    # without prior data the current season alone is used
    rows2 = nflverse.defense_vs_position(cur, pd.DataFrame(), sc)
    assert len(rows2) == 32 and all(r["games_prior"] == 0 for r in rows2)


def test_game_log(rules):
    sc = parse_scoring(rules)
    log = nflverse.player_game_log(synthetic_stats(2025, 5), sc)
    row = log[log["player_id"] == "DAL-RB0"].iloc[0]
    assert row["games"] == 5 and row["avg"] > 0 and row["position"] == "RB"


def test_store_roundtrip(tmp_path, monkeypatch):
    from app import settings
    monkeypatch.setattr(settings, "LEAGUES_FILE", tmp_path / "leagues.json")
    cfg = LeagueConfig(id=354024, name="Test", my_team_id=1838651)
    store.save_league(cfg)
    assert store.get_league(354024).my_team_id == 1838651
    assert [l.id for l in store.list_leagues()] == [354024]
    assert store.delete_league(354024) and store.get_league(354024) is None


def test_analysis_context_offline(rules, listing, monkeypatch, tmp_path):
    """Full analysis path with patched data sources."""
    from app import analysis, settings
    from app.fleaflicker import FleaflickerClient

    monkeypatch.setattr(settings, "LEAGUES_FILE", tmp_path / "leagues.json")
    cfg = LeagueConfig(id=1, name="T", season=2026, slots=parse_slots(rules), scoring=parse_scoring(rules))
    store.save_league(cfg)

    prior = synthetic_stats(2025, 17)
    prior.loc[prior["player_id"] == "PHI-QB0", "player_display_name"] = "Jalen Hurts"
    monkeypatch.setattr(nflverse, "load_weekly_stats", lambda s: prior if s == 2025 else pd.DataFrame())
    monkeypatch.setattr(nflverse, "load_players", lambda: pd.DataFrame())
    monkeypatch.setattr(nflverse, "current_week", lambda s: 1)
    sched = pd.DataFrame([{"season": 2026, "week": w, "gameday": "2026-09-10", "away_team": "WAS", "home_team": "PHI"}
                          for w in (1, 2)] +
                         [{"season": 2026, "week": 3, "gameday": "2026-09-24", "away_team": "PHI", "home_team": "DET"}])
    monkeypatch.setattr(nflverse, "load_schedule", lambda s: sched)

    class FakeClient(FleaflickerClient):
        def rosters(self, *a, **k):
            return {"rosters": [{"team": {"id": 7, "name": "Other"}, "players": [{"proPlayer": {"id": 13775}}]}]}

        def player_listing(self, *a, **k):
            return listing

    ctx = analysis.get_context(1, FakeClient(), force=True)
    pool = ctx.player_pool()
    assert [p.name for p in pool][:2] == ["Jalen Hurts", "Lamar Jackson"]
    rows = [ctx.analyze_player(p, [1, 2, 3, 4]) for p in pool]
    hurts = rows[0]
    assert hurts["baseline_source"].startswith("2025 avg")          # matched by name to synthetic log
    assert [w["opponent"] for w in hurts["weeks"]] == ["WAS", "WAS", "DET", None]
    assert hurts["weeks"][3]["bye"] is True
    assert hurts["weeks"][2]["factor"] > hurts["weeks"][0]["factor"]  # DET soft vs pass
    assert hurts["weeks"][2]["pass_rank"] == 32
    lamar = next(r for r in rows if r["name"] == "Lamar Jackson")
    assert lamar["owner_team_id"] == 7 and lamar["baseline_source"] == "Fleaflicker projection"
