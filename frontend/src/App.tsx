import { useEffect, useState } from "react";
import { api, League, Status } from "./api";
import { LeaguesPage } from "./pages/LeaguesPage";
import { DefensePage } from "./pages/DefensePage";
import { MatchupsPage } from "./pages/MatchupsPage";
import { PlayersPage } from "./pages/PlayersPage";
import { Err } from "./ui";

type Page = "leagues" | "matchups" | "players" | "defense";
const PAGES: [Page, string][] = [["matchups", "Matchups (Defense → Spieler)"], ["players", "Spieler & Ausblick"], ["defense", "Defense-Tabelle"], ["leagues", "Ligen / Stammdaten"]];

export default function App() {
  const [page, setPage] = useState<Page>((location.hash.slice(1) as Page) || "matchups");
  const [leagues, setLeagues] = useState<League[]>([]);
  const [leagueId, setLeagueId] = useState<number | null>(Number(localStorage.getItem("leagueId")) || null);
  const [status, setStatus] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => api.leagues().then((ls) => {
    setLeagues(ls);
    if (ls.length && !ls.some((l) => l.id === leagueId)) setLeagueId(ls[0].id);
    if (!ls.length) { setLeagueId(null); setPage("leagues"); }
  }).catch((e) => setErr(String(e.message)));
  useEffect(() => { load(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { location.hash = page; }, [page]);
  useEffect(() => {
    if (!leagueId) { setStatus(null); return; }
    localStorage.setItem("leagueId", String(leagueId));
    setStatus(null); setErr(null);
    api.status(leagueId).then(setStatus).catch((e) => setErr(`Status: ${e.message}`));
  }, [leagueId, leagues]);

  const league = leagues.find((l) => l.id === leagueId) ?? null;
  const week = status?.week ?? 1;

  return (
    <>
      <header>
        <h1>🏈 Football Analytics</h1>
        <nav>{PAGES.map(([p, t]) => <button key={p} className={page === p ? "active" : ""} onClick={() => setPage(p)}>{t}</button>)}</nav>
        <span style={{ marginLeft: "auto" }} className="muted small">
          {league ? <>{league.name} · Saison {status?.season ?? league.season} · Woche {week}
            {status && <> · Stats: {status.stats_current_rows ? `${status.season} geladen` : `noch keine ${status.season}-Daten`}{status.stats_prior_rows ? `, ${status.season - 1} geladen` : ""} · {status.owned_players} Spieler auf Rostern</>}</> : "keine Liga konfiguriert"}
        </span>
      </header>
      <main>
        <Err msg={err} />
        {page === "leagues" && <LeaguesPage leagues={leagues} current={league} onChange={load} onSelect={(id) => setLeagueId(id)} />}
        {page !== "leagues" && !league && <div className="card">Bitte zuerst unter „Ligen / Stammdaten“ eine Liga importieren.</div>}
        {page === "matchups" && league && status && <MatchupsPage league={league} week={week} />}
        {page === "players" && league && status && <PlayersPage league={league} week={week} teams={status.league_teams} />}
        {page === "defense" && league && status && <DefensePage league={league} week={week} />}
        {page !== "leagues" && league && !status && !err && <div className="spinner">Lade Daten (nflverse + Fleaflicker) – beim ersten Start kann das eine Minute dauern…</div>}
      </main>
    </>
  );
}
