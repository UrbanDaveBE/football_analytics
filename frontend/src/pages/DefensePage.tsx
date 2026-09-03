import { useState } from "react";
import { api, League } from "../api";
import { Err, rankColor, useAsync, useSort } from "../ui";

const POS = ["QB", "RB", "WR", "TE", "K"];

export function DefensePage({ league, week }: { league: League; week: number }) {
  const [wk, setWk] = useState<number>(week);
  const { data, error, loading } = useAsync(() => api.defense(league.id, wk), [league.id, wk]);
  const { sorted, Th } = useSort(data?.defenses, "run.rank", "desc");
  const positions = POS.filter((p) => league.positions.includes(p) || true);

  return (
    <div>
      <div className="toolbar">
        <label>Woche <select value={wk} onChange={(e) => setWk(Number(e.target.value))}>{Array.from({ length: 18 }, (_, i) => i + 1).map((w) => <option key={w} value={w}>{w}</option>)}</select></label>
        <span className="muted small">Rang 32 = schwächste Defense (bestes Matchup), Rang 1 = stärkste. Werte pro Spiel, Punkte nach Liga-Scoring.</span>
      </div>
      <Err msg={error} />
      {loading && <div className="spinner">Lade…</div>}
      {data && (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <Th k="team">Defense</Th>
                <Th k="opponent_in_week">Gegner W{data.week}</Th>
                <Th k="run.rank" num>Run-D Rang</Th>
                <Th k="run.yds_pg" num>Rush Yds/G</Th>
                <Th k="run.tds_pg" num>Rush TD/G</Th>
                <Th k="pass.rank" num>Pass-D Rang</Th>
                <Th k="pass.yds_pg" num>Pass Yds/G</Th>
                <Th k="pass.tds_pg" num>Pass TD/G</Th>
                {positions.map((p) => <Th key={p} k={`vs.${p}.rank`} num>Pkt an {p}</Th>)}
                <Th k="games_current" num>Spiele</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((d) => (
                <tr key={d.team}>
                  <td><b>{d.team}</b></td>
                  <td>{d.opponent_in_week ? `${d.home_in_week ? "vs" : "@"} ${d.opponent_in_week}` : <span className="muted">BYE</span>}</td>
                  <td className="num"><span className="cell" style={{ background: rankColor(d.run.rank) }}>{d.run.rank}</span></td>
                  <td className="num">{d.run.yds_pg.toFixed(0)}</td>
                  <td className="num">{d.run.tds_pg.toFixed(2)}</td>
                  <td className="num"><span className="cell" style={{ background: rankColor(d.pass.rank) }}>{d.pass.rank}</span></td>
                  <td className="num">{d.pass.yds_pg.toFixed(0)}</td>
                  <td className="num">{d.pass.tds_pg.toFixed(2)}</td>
                  {positions.map((p) => {
                    const v = d.vs[p];
                    return <td key={p} className="num" title={v ? `Rang ${v.rank}/32 · Ø Liga ${v.league_avg}` : ""}>
                      {v ? <span className="cell" style={{ background: rankColor(v.rank) }}>{v.pts_pg.toFixed(1)} <span className="small muted">#{v.rank}</span></span> : "–"}
                    </td>;
                  })}
                  <td className="num muted small">{d.games_current}{d.games_prior ? ` (+${d.games_prior}·${d.prior_weight})` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
