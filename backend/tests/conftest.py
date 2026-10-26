import pytest


@pytest.fixture(autouse=True)
def no_espn(monkeypatch):
    from app import injuries
    monkeypatch.setattr(injuries, "espn_injuries", lambda force=False: {})
