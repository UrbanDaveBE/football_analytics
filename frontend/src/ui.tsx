import { useEffect, useState } from "react";
import type { PlayerRow, WeekMatchup } from "./api";

/** green (soft matchup) .. red (tough matchup) from a factor around 1.0 */
export function factorColor(f: number | undefined | null): string {
  if (f === undefined || f === null) return "transparent";
  const x = Math.max(-1, Math.min(1, (f - 1) / 0.3));
  const hue = 60 + x * 60; // 0=red, 60=yellow, 120=green
  return `hsla(${hue}, 70%, 45%, 0.35)`;
}

/** rank 1 = strongest defense (red), 32 = softest (green) */
export function rankColor(rank: number | undefined | null): string {
  if (!rank) return "transparent";
  const x = (rank - 16.5) / 15.5;
  return `hsla(${60 + x * 60}, 70%, 45%, 0.35)`;
}

export function OwnerBadge({ p, myTeamId }: { p: PlayerRow; myTeamId: number | null }) {
  if (!p.owner_team_id) return <span className="badge fa">FA</span>;
  if (p.owner_team_id === myTeamId) return <span className="badge mine">Mein Team</span>;
  return <span className="badge other" title={p.owner_team_name ?? ""}>{p.owner_team_name}</span>;
}

export function WeekCell({ w }: { w: WeekMatchup }) {
  if (w.bye) return <span className="cell muted">BYE</span>;
  const rank = w.pos_rank;
  const title = `${w.home ? "vs" : "@"} ${w.opponent} · Rang vs. Position ${rank ?? "?"}/32 · Run-D ${w.run_rank ?? "?"} · Pass-D ${w.pass_rank ?? "?"} · Faktor ${w.factor.toFixed(2)}`;
  return (
    <span className="cell" style={{ background: factorColor(w.factor) }} title={title}>
      <b>{w.score.toFixed(1)}</b>
      <span className="small muted"> {w.home ? "" : "@"}{w.opponent}</span>
    </span>
  );
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    fn().then((d) => { if (alive) setData(d); }).catch((e) => { if (alive) setError(String(e.message || e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export function Err({ msg }: { msg: string | null }) {
  return msg ? <div className="error">{msg}</div> : null;
}

export function useSort<T>(rows: T[] | undefined, initial: string, dir: "asc" | "desc" = "desc") {
  const [key, setKey] = useState(initial);
  const [d, setD] = useState<"asc" | "desc">(dir);
  const get = (r: T, k: string): number | string => {
    const v = k.split(".").reduce<unknown>((o, p) => (o && typeof o === "object" ? (o as Record<string, unknown>)[p] : undefined), r);
    return typeof v === "number" ? v : v == null ? -Infinity : String(v);
  };
  const sorted = rows ? [...rows].sort((a, b) => {
    const x = get(a, key), y = get(b, key);
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
    return d === "asc" ? c : -c;
  }) : [];
  const toggle = (k: string) => { if (k === key) setD(d === "asc" ? "desc" : "asc"); else { setKey(k); setD("desc"); } };
  const Th = ({ k, children, num }: { k: string; children: React.ReactNode; num?: boolean }) => (
    <th className={num ? "num" : ""} onClick={() => toggle(k)}>{children}{key === k ? (d === "asc" ? " ▲" : " ▼") : ""}</th>
  );
  return { sorted, Th };
}
