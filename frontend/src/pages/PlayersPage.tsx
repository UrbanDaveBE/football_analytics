import { useState } from "react";
import { api, League, Team } from "../api";
import { Err, OwnerBadge, rankColor, useAsync, useSort, WeekCell } from "../ui";

/** Free Agents / Gegner-Roster mit Ausblick über N Wochen */
export function PlayersPage({ league, week, teams }: { league: League; week: number; teams: Team[] }) {
  const [owner, setOwner] = useState("free");
  const [pos, setPos] = useState("");
  const [from, setFrom] = useState(week);
  const [n, setN] = useState(league.lookahead_weeks);
  const [q, setQ] = useState("");
  const { data, error, loading } = useAsync(
    () => api.players(league.id, { owner, position: pos || undefined, from_week: from, weeks: n, limit: 400 }),
    [league.id, owner, pos, from, n]);
  const rows = data?.players.filter((p) => !q || p.name.toLowerCase().includes(q.toLowerCase()) || p.team.toLowerCase() === q.toLowerCase());
  const { sorted, Th } = useSort(rows, "outlook_avg");
  const positions = league.positions;

  return (
    <div>
      <div className="toolbar">
        <label>Besitzer
          <select value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="free">Free Agents</option>
            <option value="mine">Mein Team</option>
            <option value="all">Alle</option>
            {teams.filter((t) => t.id !== league.my_team_id).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <label>Position
          <select value={pos} onChange={(e) => setPos(e.target.value)}><option value="">alle</option>{positions.map((p) => <option key={p}>{p}</option>)}</select>
        </label>
        <label>ab Woche <select value={from} onChange={(e) => setFrom(Number(e.target.value))}>{Array.from({ length: 18 }, (_, i) => i + 1).map((w) => <option key={w} value={w}>{w}</option>)}</select></label>
        <label>Wochen <select value={n} onChange={(e) => setN(Number(e.target.value))}>{[1, 2, 3, 4, 5, 6, 8].map((w) => <option key={w} value={w}>{w}</option>)}</select></label>
        <input placeholder="Suche Name / Team" value={q} onChange={(e) => setQ(e.target.value)} />
        {data && <span className="muted small">{data.count} Spieler</span>}
      </div>
      <div className="legend">
        <span>Score = Basis-Punkte × Matchup-Faktor (Punkte, die die Defense an die Position abgibt ÷ Liga-Schnitt).</span>
        <span><span className="cell" style={{ background: "hsla(120,70%,45%,.35)" }}>grün</span> weiches Matchup</span>
        <span><span className="cell" style={{ background: "hsla(0,70%,45%,.35)" }}>rot</span> hartes Matchup</span>
        <span>FF-Matchup = Fleaflickers Gegner-Rang für die Position (32 = weich)</span>
      </div>
      <Err msg={error} />
      {loading && <div className="spinner">Lade…</div>}
      {data && (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <Th k="name">Spieler</Th><Th k="position">Pos</Th><Th k="team">Team</Th><Th k="owner_team_name">Besitzer</Th>
                <Th k="baseline" num>Basis</Th><Th k="projected" num>FF-Proj. W{data.week}</Th><Th k="ff_matchup_rank" num>FF-Matchup</Th>
                {data.weeks.map((w, i) => <Th key={w} k={`weeks.${i}.score`} num>W{w}</Th>)}
                <Th k="outlook_avg" num>Ø Ausblick</Th><Th k="outlook_total" num>Σ</Th><Th k="bye_week" num>Bye</Th><Th k="injury">Status</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => (
                <tr key={p.id}>
                  <td><b>{p.name}</b></td>
                  <td>{p.position}</td>
                  <td>{p.team}</td>
                  <td><OwnerBadge p={p} myTeamId={league.my_team_id} /></td>
                  <td className="num" title={p.baseline_source}>{p.baseline?.toFixed(1) ?? "–"}<div className="muted small">{p.baseline_source}</div></td>
                  <td className="num">{p.projected?.toFixed(1) ?? "–"}</td>
                  <td className="num">{p.ff_matchup_rank ? <span className="cell" style={{ background: rankColor(p.ff_matchup_rank) }}>#{p.ff_matchup_rank}</span> : "–"}</td>
                  {p.weeks.map((w) => <td key={w.week} className="num"><WeekCell w={w} /></td>)}
                  <td className="num"><b>{p.outlook_avg.toFixed(1)}</b></td>
                  <td className="num">{p.outlook_total.toFixed(1)}</td>
                  <td className="num">{p.bye_week ?? "–"}</td>
                  <td>{p.injury && <span className="badge inj">{p.injury}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
