import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CACHE_DIR = DATA_DIR / "cache"
NFLVERSE_DIR = DATA_DIR / "nflverse"
LEAGUES_FILE = DATA_DIR / "leagues.json"

# Fleaflicker responses are cached on disk for this many seconds
FLEAFLICKER_TTL = int(os.environ.get("FLEAFLICKER_TTL", "1800"))
# nflverse files are refreshed after this many seconds
NFLVERSE_TTL = int(os.environ.get("NFLVERSE_TTL", str(6 * 3600)))

FLEAFLICKER_BASE = "https://www.fleaflicker.com/api"
NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"

# Static frontend build (served by FastAPI in the Docker image)
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))

for d in (DATA_DIR, CACHE_DIR, NFLVERSE_DIR):
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
