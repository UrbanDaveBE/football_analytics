"""Persistence of league master data (Stammdaten) as a JSON file in DATA_DIR."""
import json
import threading

from .models import LeagueConfig
from . import settings

_lock = threading.Lock()


def _load() -> dict[int, LeagueConfig]:
    if not settings.LEAGUES_FILE.exists():
        return {}
    raw = json.loads(settings.LEAGUES_FILE.read_text("utf-8"))
    return {int(k): LeagueConfig.model_validate(v) for k, v in raw.items()}


def _save(data: dict[int, LeagueConfig]) -> None:
    settings.LEAGUES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings.LEAGUES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({str(k): v.model_dump() for k, v in data.items()}, indent=2), "utf-8")
    tmp.replace(settings.LEAGUES_FILE)


def list_leagues() -> list[LeagueConfig]:
    with _lock:
        return list(_load().values())


def get_league(league_id: int) -> LeagueConfig | None:
    with _lock:
        return _load().get(league_id)


def save_league(cfg: LeagueConfig) -> LeagueConfig:
    with _lock:
        data = _load()
        data[cfg.id] = cfg
        _save(data)
    return cfg


def delete_league(league_id: int) -> bool:
    with _lock:
        data = _load()
        if league_id not in data:
            return False
        del data[league_id]
        _save(data)
    return True
