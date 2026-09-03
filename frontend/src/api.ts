export interface RosterSlot { label: string; eligibility: string[]; start: number; enabled: boolean }
export interface League {
  id: number; name: string; sport: string; my_team_id: number | null; season: number | null;
  slots: RosterSlot[]; scoring: unknown[]; lookahead_weeks: number; player_pool_pages: number;
  positions: string[]; prior_season_weight: number;
}
export interface Team { id: number; name: string; record?: string; owners?: string[] }
export interface Status {
  season: number; week: number; positions: string[]; stats_current_rows: number; stats_prior_rows: number;
  defense_teams: number; schedule_weeks: number; league_teams: Team[]; owned_players: number; scoring_rules: number;
}
export interface WeekMatchup {
  week: number; opponent: string | null; home?: boolean; bye: boolean; factor: number; score: number;
  pos_rank?: number; pts_allowed?: number; run_rank?: number; pass_rank?: number; date?: string;
}
export interface PlayerRow {
  id: number; name: string; position: string; team: string; bye_week: number | null; injury: string | null;
  owner_team_id: number | null; owner_team_name: string | null; pct_owned: number | null;
  projected: number | null; draft_rank: number | null; opponent: string | null;
  ff_matchup_rank: number | null; ff_matchup_rating: string | null; ff_category_ranks: Record<string, number>;
  baseline: number | null; baseline_source: string; weeks: WeekMatchup[];
  outlook_total: number; outlook_avg: number; best_week: number | null; worst_week: number | null; bye_in_range: boolean;
}
export interface DefenseRow {
  team: string; games_current: number; games_prior: number; prior_weight: number;
  run: { yds_pg: number; tds_pg: number; rank: number }; pass: { yds_pg: number; tds_pg: number; rank: number };
  vs: Record<string, { pts_pg: number; rank: number; factor: number; league_avg: number }>;
  opponent_in_week: string | null; home_in_week: boolean | null;
}
export interface MatchupDefense {
  team: string; rank: number; run: DefenseRow["run"]; pass: DefenseRow["pass"];
  vs: { pts_pg: number; rank: number; factor: number } | null; opponent_in_week: string | null; players: PlayerRow[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    throw new Error(msg);
  }
  if (r.status === 204) return undefined as T;
  return r.json();
}

export const api = {
  leagues: () => req<League[]>("/api/leagues"),
  addLeague: (id: number, my_team_id?: number) => req<League>("/api/leagues", { method: "POST", body: JSON.stringify({ id, my_team_id }) }),
  updateLeague: (id: number, body: Partial<League>) => req<League>(`/api/leagues/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteLeague: (id: number) => req<void>(`/api/leagues/${id}`, { method: "DELETE" }),
  sync: (id: number) => req<League>(`/api/leagues/${id}/sync`, { method: "POST" }),
  refresh: (id: number) => req<{ ok: boolean; week: number }>(`/api/leagues/${id}/refresh`, { method: "POST" }),
  teams: (id: number) => req<Team[]>(`/api/leagues/${id}/teams`),
  status: (id: number) => req<Status>(`/api/leagues/${id}/status`),
  defense: (id: number, week?: number) => req<{ season: number; week: number; defenses: DefenseRow[] }>(`/api/leagues/${id}/defense?${qs({ week })}`),
  players: (id: number, p: { owner?: string; position?: string; from_week?: number; weeks?: number; sort?: string; limit?: number }) =>
    req<{ season: number; week: number; weeks: number[]; count: number; players: PlayerRow[] }>(`/api/leagues/${id}/players?${qs(p)}`),
  matchups: (id: number, p: { week?: number; position: string; top?: number }) =>
    req<{ week: number; position: string; sorted_by: string; defenses: MatchupDefense[] }>(`/api/leagues/${id}/matchups?${qs(p)}`),
  roster: (id: number, teamId: number, p: { from_week?: number; weeks?: number }) =>
    req<{ weeks: number[]; players: PlayerRow[] }>(`/api/leagues/${id}/roster/${teamId}?${qs(p)}`),
};

function qs(p: Record<string, string | number | undefined>): string {
  return Object.entries(p).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
}
