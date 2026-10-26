import { useState } from "react";
import { api, League, SlimPlayer } from "../api";
import { AvailBadge, CompareBox, Err, RoleBadge, useAsync, WeekCell } from "../ui";

function Name({ p }: { p: SlimPlayer | null }) {
  if (!p) return <span className="muted">– leer –</span>;
  return <><b>{p.name}</b> <span className="muted small">{p.position} {p.team}</span> <AvailBadge a={p.availability} /> <RoleBadge flag={p.role_flag} /></>;
}

/** Was soll ich tun? Aufstellung, Warnungen, Waiver-Upgrades, Streamer. */
export function RecommendationsPage({ league, week }: { league: League; week: number }) {
  const [from, setFrom] = useState(week);
  const [n, setN] = useState(league.lookahead_weeks);
  const [tab, setTab] = useState(0);
  const { data, error, loading } = useAsync(() => api.recommendations(league.id, { from_week: from, weeks: n }), [league.id, from, n]);

  if (!league.my_team_id) return <div className="card">Bitte unter „Ligen / Stammdaten“ zuerst „Mein Team“ wählen.</div>;
  const lu = data?.lineups[tab];

  return (
    <div>
      <div className="toolbar">
        <label>ab Woche <select value={from} onChange={(e) => { setFrom(Number(e.target.value)); setTab(0); }}>{Array.from({ length: 18 }, (_, i) => i + 1).map((w) => <option key={w} value={w}>{w}</option>)}</select></label>
        <label>Wochen <select value={n} onChange={(e) => { setN(Number(e.target.value)); setTab(0); }}>{[1, 2, 3, 4, 5, 6, 8].map((w) => <option key={w} value={w}>{w}</option>)}</select></label>
        {data && <span className="muted small">Projektion optimale Aufstellung: <b>{data.projected_total}</b> Punkte über {data.weeks.length} Wochen</span>}
      </div>
      <Err msg={error} />
      {loading && <div className="spinner">Rechne Aufstellungen und Waiver-Kombinationen durch…</div>}
      {data && (
        <>
          {data.alerts.length > 0 && (
            <div className="card">
              <h2>⚠ Mein Roster – Handlungsbedarf</h2>
              {data.alerts.map((a, i) => <div key={i} className={`alert ${a.level}`}><b>{a.player}</b>: {a.text}</div>)}
            </div>
          )}

          <div className="card">
            <h2>Optimale Aufstellung</h2>
            <div className="tabs">{data.lineups.map((l, i) => <button key={l.week} className={i === tab ? "active" : ""} onClick={() => setTab(i)}>Woche {l.week} · {l.total}</button>)}</div>
            {lu && (
              <div className="grid">
                <table>
                  <thead><tr><th>Slot</th><th>Starter</th><th>Gegner</th><th className="num">Score</th></tr></thead>
                  <tbody>{lu.starters.map((s, i) => (
                    <tr key={i}><td>{s.slot}</td><td><Name p={s.player} /></td>
                      <td>{s.player?.bye ? "BYE" : s.player ? `${s.player.home ? "vs" : "@"} ${s.player.opponent}` : ""}</td>
                      <td className="num"><b>{s.score.toFixed(1)}</b></td></tr>))}</tbody>
                </table>
                <table>
                  <thead><tr><th>Bank</th><th>Gegner</th><th className="num">Score</th></tr></thead>
                  <tbody>{lu.bench.map((b) => (
                    <tr key={b.id}><td><Name p={b} /></td><td>{b.bye ? "BYE" : `${b.home ? "vs" : "@"} ${b.opponent}`}</td>
                      <td className="num">{(b.score ?? 0).toFixed(1)}</td></tr>))}</tbody>
                </table>
              </div>
            )}
            <p className="small muted">Starter werden pro Woche nach Score gewählt, eingeschränkte Slots (QB, RB, WR, TE, K) zuerst, dann Flex und Superflex.</p>
          </div>

          <div className="card">
            <h2>Waiver-Upgrades (Free Agent rein, wer raus, Punkte-Gewinn in der optimalen Aufstellung)</h2>
            {data.upgrades.length === 0 ? <span className="muted">Kein Free Agent verbessert deine Aufstellung im gewählten Zeitraum.</span> : (
              <div className="tablewrap"><table>
                <thead><tr><th>⚖</th><th>Holen</th><th>Ø Ausblick</th><th>Abgeben</th><th>Ø Ausblick</th><th className="num">+ Punkte gesamt</th><th className="num">+ pro Woche</th>{data.weeks.map((w) => <th key={w} className="num">W{w} neu</th>)}</tr></thead>
                <tbody>{data.upgrades.map((u, i) => (
                  <tr key={i}>
                    <td><CompareBox id={u.add.id} /></td>
                    <td><Name p={u.add} /></td><td className="num">{u.add.outlook_avg.toFixed(1)}</td>
                    <td><Name p={u.drop} /></td><td className="num">{u.drop.outlook_avg.toFixed(1)}</td>
                    <td className="num gain">+{u.gain}</td><td className="num">+{u.gain_per_week}</td>
                    {u.add.weeks.map((w) => <td key={w.week} className="num"><WeekCell w={w} /></td>)}
                  </tr>))}</tbody>
              </table></div>
            )}
            <p className="small muted">Gewinn = Summe der optimalen Aufstellung mit dem Tausch minus ohne. Ein Spieler, der nur auf der Bank landen würde, bringt hier 0 – dafür gibt es „Tiefe“ unten.</p>
          </div>

          {data.depth.length > 0 && (
            <div className="card">
              <h2>Tiefe: Free Agents besser als dein schwächster Spieler der Position</h2>
              {data.depth.map((d) => (
                <div key={d.position} style={{ marginBottom: 8 }}>
                  <b>{d.position}</b>: schwächster bei dir <Name p={d.weakest} /> (Σ {d.weakest.outlook_total.toFixed(1)}) → besser:{" "}
                  {d.candidates.map((c) => <span key={c.id} className="chip"><CompareBox id={c.id} /> {c.name} <span className="muted small">{c.team} Σ {c.outlook_total.toFixed(1)}</span></span>)}
                </div>
              ))}
            </div>
          )}

          <div className="card">
            <h2>Hochkaräter / Streamer pro Woche (beste Free Agents)</h2>
            <div className="tablewrap"><table>
              <thead><tr><th>Woche</th>{Object.keys(data.streamers[0]?.positions || {}).map((p) => <th key={p}>{p}</th>)}</tr></thead>
              <tbody>{data.streamers.map((s) => (
                <tr key={s.week}><td>W{s.week}</td>
                  {Object.entries(s.positions).map(([pos, ps]) => (
                    <td key={pos} style={{ whiteSpace: "normal", verticalAlign: "top" }}>
                      {ps.map((p) => <div key={p.id} className="small"><b>{p.name}</b> {p.score?.toFixed(1)} <span className="muted">{p.home ? "vs" : "@"} {p.opponent}</span> <RoleBadge flag={p.role_flag} /></div>)}
                    </td>))}
                </tr>))}</tbody>
            </table></div>
          </div>
        </>
      )}
    </div>
  );
}
