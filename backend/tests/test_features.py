"""Injury model, lineup optimizer, recommendations."""
import os
from datetime import date, timedelta

os.environ.setdefault("DATA_DIR", "/tmp/fa_test_data")

from app import injuries  # noqa: E402
from app.analysis import Context  # noqa: E402
from app.models import LeagueConfig, RosterSlot  # noqa: E402

WEEKS = {w: date(2026, 9, 10) + timedelta(days=7 * (w - 1)) for w in range(1, 19)}


def test_season_ending_from_news():
    a = injuries.assess("Jalen Hurts", {"typeAbbreviaition": "IR", "typeFull": "Injured Reserve", "description": "Calf"},
                        [{"title": "Hurts out for the season", "contents": "Eagles QB suffered a season-ending calf tear."}],
                        {}, WEEKS)
    assert a.season_ending and a.level == "season"
    assert a.factor(8, 7) == 0 and a.factor(17, 7) == 0


def test_ir_without_date_is_four_weeks():
    a = injuries.assess("X", {"typeAbbreviaition": "IR"}, [], {}, WEEKS)
    assert a.level == "out" and not a.season_ending
    assert [a.factor(w, 7) for w in (7, 8, 10, 11)] == [0, 0, 0, 0.7]


def test_questionable_and_out():
    q = injuries.assess("X", {"typeAbbreviaition": "Q"}, [], {}, WEEKS)
    assert q.factor(7, 7) == 0.8 and q.factor(8, 7) == 1.0 and q.level == "warn"
    o = injuries.assess("X", {"typeAbbreviaition": "OUT"}, [], {}, WEEKS)
    assert o.factor(7, 7) == 0 and o.factor(8, 7) == 0.6 and o.factor(9, 7) == 0.9


def test_espn_return_date_and_weeks_keyword():
    espn = injuries.parse_espn({"injuries": [{"injuries": [{
        "athlete": {"displayName": "Mike Evans"}, "status": "Injured Reserve",
        "longComment": "Evans is expected back after the bye.",
        "details": {"returnDate": "2026-11-15", "type": "Hamstring", "fantasyStatus": {"abbreviation": "OUT"}}}]}]})
    a = injuries.assess("Mike Evans", {}, [], espn, WEEKS)
    assert a.status == "IR" and a.return_week == 10 and "ESPN" in a.source
    assert a.factor(9, 7) == 0 and a.factor(10, 7) == 0.85 and a.factor(11, 7) == 1.0
    b = injuries.assess("Y", {"typeAbbreviaition": "OUT"}, [{"title": "", "contents": "He is expected to miss 4-6 weeks."}], {}, WEEKS)
    assert b.return_week is not None


def _row(i, pos, scores, owner=None):
    return {"id": i, "name": f"P{i}", "position": pos, "team": "X", "owner_team_name": owner,
            "outlook_total": sum(scores), "outlook_avg": sum(scores) / len(scores), "baseline": 0, "role_flag": None,
            "availability": {"level": "ok"},
            "weeks": [{"week": w + 1, "score": s, "bye": False, "opponent": "Y", "home": True, "factor": 1, "avail": 1}
                      for w, s in enumerate(scores)]}


def test_best_lineup_respects_slots():
    cfg = LeagueConfig(id=1, slots=[
        RosterSlot(label="QB", eligibility=["QB"], start=1), RosterSlot(label="RB", eligibility=["RB"], start=1),
        RosterSlot(label="WR", eligibility=["WR"], start=1),
        RosterSlot(label="RB/WR/TE", eligibility=["RB", "WR", "TE"], start=1),
        RosterSlot(label="QB/RB/WR/TE", eligibility=["QB", "RB", "WR", "TE"], start=1),
        RosterSlot(label="K", eligibility=["K"], start=0, enabled=False)])
    ctx = Context.__new__(Context)
    ctx.cfg = cfg
    rows = [_row(1, "QB", [25]), _row(2, "QB", [20]), _row(3, "RB", [15]), _row(4, "RB", [9]),
            _row(5, "WR", [12]), _row(6, "WR", [8]), _row(7, "TE", [3])]
    total, starters, bench = ctx.best_lineup(rows, 0)
    names = {s["slot"]: s["player"]["name"] for s in starters}
    assert names["QB"] == "P1" and names["RB"] == "P3" and names["WR"] == "P5"
    assert names["RB/WR/TE"] == "P4" and names["QB/RB/WR/TE"] == "P2"   # 2nd QB in superflex
    assert total == 25 + 15 + 12 + 9 + 20
    assert {b["name"] for b in bench} == {"P6", "P7"}
