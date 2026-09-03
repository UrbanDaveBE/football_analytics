import { useState } from "react";
import { api, League } from "../api";
import { Err, OwnerBadge, rankColor, useAsync } from "../ui";

const POS = ["RB", "WR", "QB", "TE", "K"];

/** "Schwache Defense gegen Lauf/Pass → wer spielt dagegen?" */
export function MatchupsPage({ league, week }: { league: League; week: number }) {
  const [wk, setWk] = useState(week);
  const [pos, setPos] = useState("RB");
  const [top, setTop] = useState(10);
  const [onlyAvailable, setOnlyAvailable] = useState(false);
  const { data, error, loading } = useAsync(() => api.matchups(league.id, { week: wk, position: pos, top }), [league.id, wk, pos, top]);

  return (
    <div>
      <div className="toolbar">
        <label>Woche <select value={wk} onChange={(e) => setWk(Number(e.target.value))}>{Array.from({ length: 18 }, (_, i) => i + 1).map((w) => <option key={w} value={w}>{w}</option>)}</select></label>
        <label>Position <select value={pos} onChange={(e) => setPos(e.target.value)}>{POS.map((p) => <option key={p}>{p}</option>)}</select></label>
        <label>Top <select value={top} onChange={(e) => setTop(Number(e.target.value))}>{[5, 10, 16, 32].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label><input type="checkbox" checked={onlyAvailable} onChange={(e) => setOnlyAvailable(e.target.checked)} /> nur Free Agents + mein Team</label>
      </div>
      <p className="muted small">
        {pos === "RB" ? "Sortiert nach Run-Defense (Rush-Yards + TDs erlaubt)." : pos === "K" ? "Sortiert nach Punkten, die an Kicker abgegeben werden." : "Sortiert nach Pass-Defense (Pass-Yards + TDs erlaubt)."}
        {" "}Die schwächste Defense steht oben; darunter die {pos}s, die in Woche {wk} gegen sie spielen.
      </p>
      <Err msg={error} />
      {loading && <div className="spinner">Lade…</div>}
      {data && data.defenses.map((d) => {
        const players = onlyAvailable ? d.players.filter((p) => !p.owner_team_id || p.owner_team_id === league.my_team_id) : d.players;
        return (
          <div className="card" key={d.team}>
            <h2>
              <span className="cell" style={{ background: rankColor(d.rank) }}>#{d.rank}</span> {d.team}
              <span className="muted small"> · Run-D #{d.run.rank} ({d.run.yds_pg.toFixed(0)} yds, {d.run.tds_pg.toFixed(2)} TD/G) · Pass-D #{d.pass.rank} ({d.pass.yds_pg.toFixed(0)} yds, {d.pass.tds_pg.toFixed(2)} TD/G)
                {d.vs ? ` · gibt ${d.vs.pts_pg.toFixed(1)} Pkt/G an ${pos} ab (#${d.vs.rank}, Faktor ${d.vs.factor.toFixed(2)})` : ""}
                {d.opponent_in_week ? ` · Gegner W${data.week}: ${d.opponent_in_week}` : " · BYE"}</span>
            </h2>
            {players.length === 0 ? <span className="muted small">Keine {pos}s im Spieler-Pool gegen {d.team} in dieser Woche.</span> : (
              <div className="tablewrap"><table>
                <thead><tr><th>Spieler</th><th>Team</th><th>Besitzer</th><th className="num">Score W{data.week}</th><th className="num">Basis</th><th className="num">FF-Proj.</th><th className="num">FF-Matchup</th><th>Status</th></tr></thead>
                <tbody>
                  {players.map((p) => {
                    const w = p.weeks[0];
                    return (
                      <tr key={p.id}>
                        <td><b>{p.name}</b></td>
                        <td>{p.team} <span className="muted small">{w.home ? "vs" : "@"} {w.opponent}</span></td>
                        <td><OwnerBadge p={p} myTeamId={league.my_team_id} /></td>
                        <td className="num"><b>{w.score.toFixed(1)}</b></td>
                        <td className="num" title={p.baseline_source}>{p.baseline?.toFixed(1) ?? "–"} <span className="muted small">{p.baseline_source}</span></td>
                        <td className="num">{p.projected?.toFixed(1) ?? "–"}</td>
                        <td className="num">{p.ff_matchup_rank ? <span className="cell" style={{ background: rankColor(p.ff_matchup_rank) }}>#{p.ff_matchup_rank}</span> : "–"}</td>
                        <td>{p.injury && <span className="badge inj">{p.injury}</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table></div>
            )}
          </div>
        );
      })}
    </div>
  );
}
