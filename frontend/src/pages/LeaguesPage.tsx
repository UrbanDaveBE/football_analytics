import { useEffect, useState } from "react";
import { api, League, RosterSlot, Team } from "../api";
import { Err } from "../ui";

interface Props { leagues: League[]; current: League | null; onChange: () => void; onSelect: (id: number) => void }

export function LeaguesPage({ leagues, current, onChange, onSelect }: Props) {
  const [newId, setNewId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const add = async () => {
    const id = parseInt(newId.replace(/\D/g, ""), 10);
    if (!id) return;
    setBusy(true); setErr(null);
    try { await api.addLeague(id); setNewId(""); onChange(); onSelect(id); } catch (e) { setErr(String((e as Error).message)); }
    setBusy(false);
  };

  return (
    <div>
      <Err msg={err} />
      <div className="card">
        <h2>Liga hinzufügen</h2>
        <div className="toolbar">
          <input placeholder="Fleaflicker Liga-ID oder URL (z. B. 354024)" value={newId} onChange={(e) => setNewId(e.target.value)} style={{ width: 360 }} />
          <button className="btn primary" disabled={busy} onClick={add}>{busy ? "Lade…" : "Importieren"}</button>
          <span className="muted small">Roster-Slots und Scoring werden aus der Fleaflicker-API gelesen.</span>
        </div>
      </div>
      {leagues.length > 0 && (
        <div className="toolbar">
          <label>Liga
            <select value={current?.id ?? ""} onChange={(e) => onSelect(Number(e.target.value))}>
              {leagues.map((l) => <option key={l.id} value={l.id}>{l.name || l.id} ({l.id})</option>)}
            </select>
          </label>
        </div>
      )}
      {current && <LeagueEditor key={current.id} league={current} onChange={onChange} />}
    </div>
  );
}

function LeagueEditor({ league, onChange }: { league: League; onChange: () => void }) {
  const [cfg, setCfg] = useState<League>(league);
  const [teams, setTeams] = useState<Team[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.teams(league.id).then(setTeams).catch((e) => setErr(String(e.message))); }, [league.id]);

  const setSlot = (i: number, patch: Partial<RosterSlot>) =>
    setCfg({ ...cfg, slots: cfg.slots.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); onChange(); } catch (e) { setErr(String((e as Error).message)); }
    setBusy(false);
  };
  const save = () => run(() => api.updateLeague(cfg.id, {
    name: cfg.name, my_team_id: cfg.my_team_id, slots: cfg.slots, lookahead_weeks: cfg.lookahead_weeks,
    player_pool_pages: cfg.player_pool_pages, prior_season_weight: cfg.prior_season_weight,
  }), "Gespeichert.");
  const sync = () => run(async () => setCfg(await api.sync(cfg.id)), "Slots & Scoring neu von Fleaflicker geladen.");
  const refresh = () => run(() => api.refresh(cfg.id), "Caches geleert, Daten neu geladen.");
  const del = () => { if (confirm(`Liga ${cfg.name} entfernen?`)) run(() => api.deleteLeague(cfg.id), "Entfernt."); };

  const enabledPositions = Array.from(new Set(cfg.slots.filter((s) => s.enabled && s.start > 0).flatMap((s) => s.eligibility)));

  return (
    <div>
      <Err msg={err} />
      {msg && <div className="card" style={{ borderColor: "var(--accent)" }}>{msg}</div>}
      <div className="grid">
        <div className="card">
          <h2>Stammdaten</h2>
          <div className="slots" style={{ gridTemplateColumns: "auto 1fr" }}>
            <span className="muted">Name</span>
            <input value={cfg.name} onChange={(e) => setCfg({ ...cfg, name: e.target.value })} />
            <span className="muted">Liga-ID</span><span>{cfg.id} · Saison {cfg.season ?? "?"}</span>
            <span className="muted">Mein Team</span>
            <select value={cfg.my_team_id ?? ""} onChange={(e) => setCfg({ ...cfg, my_team_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">– wählen –</option>
              {teams.map((t) => <option key={t.id} value={t.id}>{t.name}{t.owners?.length ? ` (${t.owners.join(", ")})` : ""}</option>)}
            </select>
            <span className="muted">Ausblick (Wochen)</span>
            <input type="number" min={1} max={10} value={cfg.lookahead_weeks} onChange={(e) => setCfg({ ...cfg, lookahead_weeks: Number(e.target.value) })} />
            <span className="muted">Spieler-Pool (Seiten à 30)</span>
            <input type="number" min={1} max={40} value={cfg.player_pool_pages} onChange={(e) => setCfg({ ...cfg, player_pool_pages: Number(e.target.value) })} />
            <span className="muted">Gewicht Vorsaison (0–1)</span>
            <input type="number" min={0} max={1} step={0.1} value={cfg.prior_season_weight} onChange={(e) => setCfg({ ...cfg, prior_season_weight: Number(e.target.value) })} />
          </div>
          <p className="small muted">Gewicht Vorsaison: wie stark die Defense-Daten der letzten Saison zu Saisonbeginn zählen. Läuft über die ersten 8 Spiele linear auf 0 aus.</p>
        </div>
        <div className="card">
          <h2>Roster-Slots (Positionen)</h2>
          <div className="slots">
            <span className="muted">Aktiv</span><span className="muted">Slot</span><span className="muted">Starter</span><span className="muted">Eligible</span>
            {cfg.slots.map((s, i) => (
              <SlotRow key={s.label + i} s={s} onChange={(p) => setSlot(i, p)} />
            ))}
          </div>
          <p className="small muted">Analysierte Positionen: <b>{enabledPositions.join(", ") || "–"}</b>. Slots ohne Starter (P, D/ST, IDP) sind deaktiviert; nflverse liefert nur Daten für QB/RB/WR/TE/K.</p>
        </div>
      </div>
      <div className="toolbar">
        <button className="btn primary" disabled={busy} onClick={save}>Speichern</button>
        <button className="btn" disabled={busy} onClick={sync}>Slots/Scoring von Fleaflicker neu laden</button>
        <button className="btn" disabled={busy} onClick={refresh}>Daten-Cache leeren</button>
        <button className="btn" disabled={busy} onClick={del} style={{ marginLeft: "auto", color: "var(--bad)" }}>Liga entfernen</button>
      </div>
      <details className="card">
        <summary>Scoring-Regeln ({cfg.scoring.length}) – importiert, wird für die Punkteberechnung aus nflverse-Stats genutzt</summary>
        <ul className="small">
          {(cfg.scoring as { name: string; points: number; for_every?: number; bound_lower?: number; is_bonus: boolean }[]).map((r, i) => (
            <li key={i}>{r.name}: {r.points} {r.for_every ? `pro ${r.for_every}` : ""} {r.is_bonus ? `(Bonus ab ${r.bound_lower})` : ""}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function SlotRow({ s, onChange }: { s: RosterSlot; onChange: (p: Partial<RosterSlot>) => void }) {
  return (
    <>
      <input type="checkbox" checked={s.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} />
      <b>{s.label}</b>
      <input type="number" min={0} max={10} value={s.start} style={{ width: 60 }} onChange={(e) => onChange({ start: Number(e.target.value) })} />
      <span className="muted small">{s.eligibility.join(", ")}</span>
    </>
  );
}
