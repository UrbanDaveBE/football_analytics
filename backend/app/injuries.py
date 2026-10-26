"""Injury / availability model.

Sources (all best effort, the tool keeps working if one fails):
  1. Fleaflicker  proPlayer.injury  -> Q / D / OUT / IR / IRD / PUP / SUSP (always present)
  2. Fleaflicker  proPlayer.news    -> keywords like "season-ending", "out for the season", "torn ACL"
  3. ESPN         site.api.espn.com/.../nfl/injuries -> status, returnDate, long comment

Result per player: an `Availability` with
  status        short code shown in the GUI
  season_ending True if any source says the season is over
  return_week   first NFL week the player is expected back (None = unknown)
  note          human readable explanation
and `factor(week)` = multiplier applied to the projected score of that week.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from . import settings

log = logging.getLogger(__name__)

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
ESPN_TTL = 3 * 3600

SEASON_ENDING_PATTERNS = [
    r"season[- ]ending", r"out for (the )?(rest of the )?season", r"miss(es)? the (rest|remainder) of the season",
    r"remainder of the (\d{4} )?season", r"torn (acl|achilles)", r"(acl|achilles) (tear|rupture)",
    r"ruptured achilles", r"placed on (season[- ]ending )?ir.*season", r"done for the (year|season)",
]
_SEASON_RE = re.compile("|".join(SEASON_ENDING_PATTERNS), re.I)
_WEEKS_RE = re.compile(r"(?:out|sidelined|miss)\D{0,30}?(\d+)(?:\s*(?:-|to)\s*(\d+))?\s*weeks", re.I)

# multiplier for the current week by status
STATUS_FACTOR = {"Q": 0.8, "D": 0.35, "OUT": 0.0, "IR": 0.0, "IRD": 0.0, "PUP": 0.0, "SUSP": 0.0, "NFI": 0.0}
LONG_TERM = {"IR", "IRD", "PUP", "NFI"}     # minimum 4 games on IR


@dataclass
class Availability:
    status: str | None = None
    season_ending: bool = False
    return_week: int | None = None
    note: str = ""
    source: str = ""

    def factor(self, week: int, current_week: int) -> float:
        if self.season_ending:
            return 0.0
        if self.return_week is not None:
            return 0.0 if week < self.return_week else (0.85 if week == self.return_week else 1.0)
        if not self.status:
            return 1.0
        if self.status in LONG_TERM:
            # no return date known: IR = at least 4 games out from now
            return 0.0 if week < current_week + 4 else 0.7
        if week == current_week:
            return STATUS_FACTOR.get(self.status, 1.0)
        if self.status in ("OUT", "SUSP"):
            return 0.6 if week == current_week + 1 else 0.9
        return 1.0

    @property
    def level(self) -> str:
        """ok | warn | out | season for color coding"""
        if self.season_ending:
            return "season"
        if self.status in ("OUT", "SUSP") or self.status in LONG_TERM:
            return "out"
        if self.status in ("Q", "D"):
            return "warn"
        return "ok"

    def to_dict(self) -> dict:
        return {"status": self.status, "season_ending": self.season_ending, "return_week": self.return_week,
                "note": self.note, "source": self.source, "level": self.level}


def _norm(n: str) -> str:
    n = re.sub(r"[^a-z ]", "", (n or "").lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


# ------------------------------------------------------------------- ESPN
_espn_cache: tuple[float, dict] | None = None


def espn_injuries(force: bool = False) -> dict[str, dict]:
    """{normalized name: {status, return_date, comment, team}}; empty dict on any failure."""
    global _espn_cache
    if _espn_cache and not force and time.time() - _espn_cache[0] < ESPN_TTL:
        return _espn_cache[1]
    path = settings.CACHE_DIR / "espn_injuries.json"
    raw = None
    try:
        if not force and path.exists() and time.time() - path.stat().st_mtime < ESPN_TTL:
            raw = json.loads(path.read_text("utf-8"))
        else:
            r = httpx.get(ESPN_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0 football-analytics"})
            r.raise_for_status()
            raw = r.json()
            path.write_text(json.dumps(raw), "utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("ESPN injuries unavailable: %s", e)
        if path.exists():
            try:
                raw = json.loads(path.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                raw = None
    out = parse_espn(raw or {})
    _espn_cache = (time.time(), out)
    return out


def parse_espn(raw: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for team in raw.get("injuries", []) or []:
        for i in team.get("injuries", []) or []:
            ath = i.get("athlete") or {}
            name = ath.get("displayName") or ath.get("fullName")
            if not name:
                continue
            det = i.get("details") or {}
            fant = (det.get("fantasyStatus") or {})
            out[_norm(name)] = {
                "status": i.get("status") or fant.get("description"),
                "fantasy": fant.get("abbreviation"),
                "return_date": det.get("returnDate"),
                "comment": i.get("longComment") or i.get("shortComment") or "",
                "type": " ".join(x for x in [det.get("side"), det.get("type"), det.get("detail")] if x),
            }
    return out


def _date_to_week(d: str | None, week_starts: dict[int, date]) -> int | None:
    if not d or not week_starts:
        return None
    try:
        dt = datetime.fromisoformat(d.replace("Z", "+00:00")).date()
    except ValueError:
        return None
    for wk in sorted(week_starts):
        # week w is the first whose last game day (start + 6) is on/after the return date
        if week_starts[wk] + timedelta(days=6) >= dt:
            return wk
    return 99


# --------------------------------------------------------------- combine
def assess(name: str, ff_injury: dict | None, news: list[dict] | None, espn: dict[str, dict],
           week_starts: dict[int, date]) -> Availability:
    a = Availability()
    ff_injury = ff_injury or {}
    code = ff_injury.get("typeAbbreviaition") or ff_injury.get("typeAbbreviation")
    if code:
        a.status = code.upper()
        a.note = " · ".join(x for x in [ff_injury.get("typeFull"), ff_injury.get("description")] if x)
        a.source = "Fleaflicker"

    texts = []
    for n in news or []:
        texts.append(f"{n.get('title', '')} {n.get('contents', '')}")
    e = espn.get(_norm(name))
    if e:
        texts.append(e.get("comment", ""))
        st = (e.get("status") or "").lower()
        if not a.status and st:
            a.status = {"questionable": "Q", "doubtful": "D", "out": "OUT", "injured reserve": "IR",
                        "suspension": "SUSP"}.get(st, a.status)
        rw = _date_to_week(e.get("return_date"), week_starts)
        if rw:
            a.return_week = rw
        if e.get("type"):
            a.note = a.note or e["type"]
        a.source = "+".join(x for x in [a.source, "ESPN"] if x)

    blob = " ".join(texts)
    m = _SEASON_RE.search(blob)
    if m:
        a.season_ending = True
        a.note = (a.note + " · " if a.note else "") + f"Saisonende ({m.group(0)})"
    elif a.return_week is None:
        w = _WEEKS_RE.search(blob)
        if w and week_starts:
            weeks_out = int(w.group(2) or w.group(1))
            cur = min((wk for wk, s in week_starts.items() if s + timedelta(days=6) >= date.today()), default=None)
            if cur:
                a.return_week = cur + weeks_out
                a.note = (a.note + " · " if a.note else "") + f"~{weeks_out} Wochen out"
    if e and e.get("comment") and not a.season_ending:
        a.note = (a.note + " · " if a.note else "") + e["comment"][:160]
    return a
