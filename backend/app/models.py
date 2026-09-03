from typing import Optional
from pydantic import BaseModel, Field


class RosterSlot(BaseModel):
    """One lineup slot of a league (Stammdaten). Editable in the GUI."""
    label: str                      # e.g. "QB", "RB/WR/TE", "K"
    eligibility: list[str]          # e.g. ["RB", "WR", "TE"]
    start: int = 0                  # number of starters in that slot
    enabled: bool = True


class ScoringRule(BaseModel):
    category_id: int
    name: str
    points: float
    for_every: Optional[float] = None     # points per `for_every` units
    bound_lower: Optional[float] = None   # bonus if stat >= bound_lower
    bound_upper: Optional[float] = None
    is_bonus: bool = False


class LeagueConfig(BaseModel):
    id: int
    name: str = ""
    sport: str = "NFL"
    my_team_id: Optional[int] = None
    season: Optional[int] = None
    slots: list[RosterSlot] = Field(default_factory=list)
    scoring: list[ScoringRule] = Field(default_factory=list)
    # analysis settings
    lookahead_weeks: int = 5
    player_pool_pages: int = 12           # Fleaflicker pages à 30 players
    positions: list[str] = Field(default_factory=lambda: ["QB", "RB", "WR", "TE", "K"])
    # weight of last season when blending defense data early in the season
    prior_season_weight: float = 0.5

    def enabled_positions(self) -> list[str]:
        pos: list[str] = []
        for s in self.slots:
            if not s.enabled or s.start <= 0:
                continue
            for e in s.eligibility:
                if e not in pos:
                    pos.append(e)
        return pos or self.positions


class LeagueUpdate(BaseModel):
    name: Optional[str] = None
    my_team_id: Optional[int] = None
    slots: Optional[list[RosterSlot]] = None
    lookahead_weeks: Optional[int] = None
    player_pool_pages: Optional[int] = None
    positions: Optional[list[str]] = None
    prior_season_weight: Optional[float] = None
