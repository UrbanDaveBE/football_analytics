import { useEffect, useState } from "react";
import { api, League, PlayerRow } from "../api";
import { AvailBadge, Err, OwnerBadge, RoleBadge, useAsync, useCompare, WeekCell } from "../ui";

const COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#39c5cf"];

type Metric = { label: string; get: (p: PlayerRow) => number | undefined | null; higher?: boolean; fmt?: (v: number) => string };
const METRICS: Metric[] = [
  { label: "Ø Ausblick", get: (p) => p.outlook_avg, higher: true },
  { label: "Σ Ausblick", get: (p) => p.outlook_total, higher: true },
  { label: "Basis (Pkt/Spiel)", get: (p) => p.baseline, higher: true },
  { label: "FF-Projektion diese Woche", get: (p) => p.projected, higher: true },
  { label: "Letzte 3 Spiele", get: (p) => p.stats?.last3, higher: true },
  { label: "Trend (L3 − Ø)", get: (p) => p.stats?.trend, higher: true },
  { label: "Konstanz (Std.-Abw.)", get: (p) => p.stats?.std, higher: false },
  { label: "Bestes / schlechtestes Spiel", get: (p) => p.stats?.max, higher: true, fmt: (v) => v.toFixed(1) },
  { label: "Touches / Spiel (Targets+Carries)", get: (p) => p.stats?.touches, higher: true },
  { label: "Touches letzte 3", get: (p) => p.stats?.touches_last3, higher: true },
  { label: "Target-Share %", get: (p) => p.stats?.target_share, higher: true },
  { label: "Spiele", get: (p) => p.stats?.games, higher: true, fmt: (v) => String(v) },
];

export function ComparePage({ league, week }: { league: League; week: number }) {
  const { ids, toggle, set } = useCompare();
  const [n, setN] = useState(league.lookahead_weeks);
  const { data, error, loading } = useAsync(
    () => (ids.length ? api.compare(league.id, ids, { from_week: week, weeks: n }) : Promise.resolve({ weeks: [], players: [] })),
    [league.id, ids.join(","), n]);
  const players = data?.players ?? [];

  return (
    <div>
      <div className="toolbar">
        <PlayerSearch leagueId={league.id} onPick={(id) => set([...ids, id])} />
        <label>Wochen <select value={n} onChange={(e) => setN(Number(e.target.value))}>{[1, 2, 3, 4, 5, 6, 8].map((w) => <option key={w} value={w}>{w}</option>)}</select></label>
        {ids.length > 0 && <button className="btn" onClick={() => set([])}>Auswahl leeren</button>}
        <span className="muted small">Spieler auch über ⚖ in den anderen Tabellen hinzufügen (max. 6).</span>
      </div>
      <Err msg={error} />
      {loading && <div className="spinner">Lade…</div>}
      {ids.length === 0 && <div className="card">Noch keine Spieler ausgewählt. Oben suchen oder in „Spieler & Ausblick“ / „Empfehlungen“ das ⚖-Häkchen setzen.</div>}
      {players.length > 0 && (
        <>
          <div className="tablewrap card" style={{ padding: 0 }}>
            <table className="cmp">
              <thead>
                <tr><th></th>{players.map((p, i) => (
                  <th key={p.id} style={{ color: COLORS[i], textTransform: "none", fontSize: 14 }}>
                    {p.name} <button className="btn" style={{ padding: "0 6px" }} onClick={() => toggle(p.id)}>×</button>
                    <div className="small muted">{p.position} · {p.team}</div>
                  </th>))}</tr>
              </thead>
              <tbody>
                <tr><td>Besitzer</td>{players.map((p) => <td key={p.id}><OwnerBadge p={p} myTeamId={league.my_team_id} /></td>)}</tr>
                <tr><td>Status</td>{players.map((p) => <td key={p.id} style={{ whiteSpace: "normal" }}><AvailBadge a={p.availability} /> <RoleBadge flag={p.role_flag} /><div className="small muted">{p.availability?.note}</div></td>)}</tr>
                {METRICS.map((m) => {
                  const vals = players.map((p) => m.get(p));
                  const nums = vals.filter((v): v is number => typeof v === "number");
                  const best = nums.length > 1 ? (m.higher === false ? Math.min(...nums) : Math.max(...nums)) : null;
                  return (
                    <tr key={m.label}><td>{m.label}</td>
                      {players.map((p, i) => {
                        const v = vals[i];
                        const txt = typeof v === "number" ? (m.fmt ? m.fmt(v) : v.toFixed(1)) : "–";
                        const extra = m.label.startsWith("Bestes") && p.stats?.min !== undefined ? ` / ${p.stats.min.toFixed(1)}` : "";
                        return <td key={p.id} className={v === best ? "best" : ""}>{txt}{extra}</td>;
                      })}
                    </tr>);
                })}
                {data!.weeks.map((w, wi) => {
                  const scores = players.map((p) => p.weeks[wi]?.score ?? 0);
                  const best = Math.max(...scores);
                  return (
                    <tr key={w}><td>Woche {w}</td>
                      {players.map((p, i) => <td key={p.id} className={scores[i] === best && best > 0 ? "best" : ""}><WeekCell w={p.weeks[wi]} /></td>)}
                    </tr>);
                })}
                <tr><td>Bye</td>{players.map((p) => <td key={p.id}>{p.bye_week ?? "–"}</td>)}</tr>
                <tr><td>News</td>{players.map((p) => <td key={p.id} style={{ whiteSpace: "normal", maxWidth: 260 }} className="small">{p.news?.[0] ? (p.news[0].url ? <a href={p.news[0].url} target="_blank" rel="noreferrer">{p.news[0].title}</a> : p.news[0].title) : "–"}</td>)}</tr>
              </tbody>
            </table>
          </div>
          <div className="card">
            <h2>Punkte pro Spiel {league.season ?? ""} (Liga-Scoring)</h2>
            <GameLogChart players={players} />
          </div>
        </>
      )}
    </div>
  );
}

function GameLogChart({ players }: { players: PlayerRow[] }) {
  const logs = players.map((p) => p.stats?.log ?? []);
  const weeks = Array.from(new Set(logs.flat().map(([w]) => w))).sort((a, b) => a - b);
  if (!weeks.length) return <span className="muted small">Keine Spiele in dieser Saison.</span>;
  const max = Math.max(10, ...logs.flat().map(([, v]) => v));
  const W = 640, H = 220, pad = 30;
  const x = (w: number) => pad + ((w - weeks[0]) / Math.max(1, weeks[weeks.length - 1] - weeks[0])) * (W - 2 * pad);
  const y = (v: number) => H - pad - (v / max) * (H - 2 * pad);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 800 }}>
      {[0, 0.5, 1].map((f) => <g key={f}><line x1={pad} x2={W - pad} y1={y(max * f)} y2={y(max * f)} stroke="var(--border)" />
        <text x={4} y={y(max * f) + 4} fill="var(--muted)" fontSize="10">{(max * f).toFixed(0)}</text></g>)}
      {weeks.map((w) => <text key={w} x={x(w)} y={H - 8} fill="var(--muted)" fontSize="10" textAnchor="middle">W{w}</text>)}
      {logs.map((lg, i) => (
        <g key={i}>
          <polyline fill="none" stroke={COLORS[i]} strokeWidth={2} points={lg.map(([w, v]) => `${x(w)},${y(v)}`).join(" ")} />
          {lg.map(([w, v]) => <circle key={w} cx={x(w)} cy={y(v)} r={3} fill={COLORS[i]}><title>{`${players[i].name} W${w}: ${v}`}</title></circle>)}
        </g>
      ))}
    </svg>
  );
}

function PlayerSearch({ leagueId, onPick }: { leagueId: number; onPick: (id: number) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ id: number; name: string; position: string; team: string; owner: string }[]>([]);
  useEffect(() => {
    if (q.length < 2) { setHits([]); return; }
    const t = setTimeout(() => api.search(leagueId, q).then(setHits).catch(() => setHits([])), 250);
    return () => clearTimeout(t);
  }, [q, leagueId]);
  return (
    <div style={{ position: "relative" }}>
      <input placeholder="Spieler suchen und hinzufügen…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 280 }} />
      {hits.length > 0 && (
        <div className="searchlist">
          {hits.map((h) => <div key={h.id} onClick={() => { onPick(h.id); setQ(""); setHits([]); }}>
            <b>{h.name}</b> <span className="muted small">{h.position} {h.team} · {h.owner}</span></div>)}
        </div>
      )}
    </div>
  );
}
