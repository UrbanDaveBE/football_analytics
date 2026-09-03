"""End-to-end API tests against the FastAPI app with all external sources patched."""
import json
import os
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("DATA_DIR", "/tmp/fa_test_data")

from fastapi.testclient import TestClient  # noqa: E402

from app import analysis, main, nflverse, settings  # noqa: E402
from tests.test_analysis import synthetic_stats  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LEAGUES_FILE", tmp_path / "leagues.json")
    rules = json.loads((FIX / "rules.json").read_text())
    listing = json.loads((FIX / "player_listing.json").read_text())
    prior = synthetic_stats(2025, 17)
    prior.loc[prior["player_id"] == "PHI-QB0", "player_display_name"] = "Jalen Hurts"
    prior.loc[prior["player_id"] == "DAL-QB0", "player_display_name"] = "Dak Prescott"
    monkeypatch.setattr(nflverse, "load_weekly_stats", lambda s: prior if s == 2025 else pd.DataFrame())
    monkeypatch.setattr(nflverse, "load_players", lambda: pd.DataFrame())
    monkeypatch.setattr(nflverse, "current_week", lambda s: 1)
    sched = pd.DataFrame([
        {"season": 2026, "week": 1, "gameday": "2026-09-10", "away_team": "WAS", "home_team": "PHI"},
        {"season": 2026, "week": 1, "gameday": "2026-09-10", "away_team": "BAL", "home_team": "IND"},
        {"season": 2026, "week": 1, "gameday": "2026-09-10", "away_team": "DAL", "home_team": "NYG"},
        {"season": 2026, "week": 2, "gameday": "2026-09-17", "away_team": "PHI", "home_team": "DET"},
        {"season": 2026, "week": 2, "gameday": "2026-09-17", "away_team": "DAL", "home_team": "DET"},
    ])
    monkeypatch.setattr(nflverse, "load_schedule", lambda s: sched)

    class FakeClient(main.FleaflickerClient):
        def rules(self, *a, **k):
            return rules

        def standings(self, *a, **k):
            return {"season": 2026, "league": {"name": "Test League"},
                    "divisions": [{"name": "Div", "teams": [{"id": 1838651, "name": "NotBrady's Team"}, {"id": 7, "name": "Other"}]}]}

        def rosters(self, *a, **k):
            return {"rosters": [{"team": {"id": 7, "name": "Other"}, "players": [{"proPlayer": {"id": 13775}}]},
                                {"team": {"id": 1838651, "name": "NotBrady's Team"}, "players": [{"proPlayer": {"id": 12159}}]}]}

        def player_listing(self, *a, **k):
            return listing

        def team_roster(self, *a, **k):
            return {"groups": [{"slots": [{"position": {"label": "QB"}, "leaguePlayer": listing["players"][2]}]}]}

    monkeypatch.setattr(main, "client", FakeClient())
    analysis.invalidate()
    return TestClient(main.app)


def test_full_flow(client):
    r = client.post("/api/leagues", json={"id": 354024, "my_team_id": 1838651})
    assert r.status_code == 201
    cfg = r.json()
    assert cfg["name"] == "Test League" and cfg["season"] == 2026
    assert [s["label"] for s in cfg["slots"]][:3] == ["QB", "RB", "WR"]

    # edit master data: disable K, set lookahead
    slots = [dict(s, enabled=(s["label"] != "K")) for s in cfg["slots"]]
    r = client.put("/api/leagues/354024", json={"slots": slots, "lookahead_weeks": 3})
    assert r.status_code == 200 and r.json()["lookahead_weeks"] == 3
    assert "K" not in client.get("/api/leagues/354024/status").json()["positions"]

    st = client.get("/api/leagues/354024/status").json()
    assert st["week"] == 1 and st["defense_teams"] == 32 and st["owned_players"] == 2

    d = client.get("/api/leagues/354024/defense?week=1").json()
    assert len(d["defenses"]) == 32
    dal = next(x for x in d["defenses"] if x["team"] == "DAL")
    assert dal["run"]["rank"] == 32 and dal["opponent_in_week"] == "NYG"

    p = client.get("/api/leagues/354024/players?owner=free").json()
    assert p["weeks"] == [1, 2, 3] and [x["name"] for x in p["players"]] == ["Jalen Hurts"]
    hurts = p["players"][0]
    assert hurts["weeks"][1]["opponent"] == "DET" and hurts["weeks"][1]["pass_rank"] == 32
    assert hurts["weeks"][2]["bye"] is True

    p = client.get("/api/leagues/354024/players?owner=mine").json()
    assert [x["name"] for x in p["players"]] == ["Dak Prescott"]
    p = client.get("/api/leagues/354024/players?owner=7").json()
    assert [x["name"] for x in p["players"]] == ["Lamar Jackson"]

    m = client.get("/api/leagues/354024/matchups?week=2&position=QB&top=3").json()
    assert m["sorted_by"] == "pass" and m["defenses"][0]["team"] == "DET"
    facing = {x["name"] for x in m["defenses"][0]["players"]}
    assert facing == {"Jalen Hurts", "Dak Prescott"}

    r = client.get("/api/leagues/354024/roster/1838651").json()
    assert r["players"][0]["name"] == "Dak Prescott" and r["players"][0]["owner_team_id"] == 1838651

    assert client.post("/api/leagues/354024/sync").status_code == 200
    assert client.get("/api/leagues/354024/teams").json()[0]["id"] == 1838651
    assert client.delete("/api/leagues/354024").status_code == 204
    assert client.get("/api/leagues/354024/status").status_code == 404
