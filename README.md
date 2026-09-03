# Football Analytics

Fantasy-Football-Helfer für Fleaflicker-Ligen. Läuft als ein Docker-Container (FastAPI-Backend + React-Frontend), z. B. auf einem NAS.

## Was es macht

- **Ligen / Stammdaten**: Liga per Fleaflicker-ID importieren. Roster-Slots (QB, RB, WR, TE, Flex, K, …) und Scoring-Regeln werden aus der Fleaflicker-API gelesen und lassen sich im GUI anpassen (Slot aktiv, Anzahl Starter, Ausblick-Wochen, eigenes Team).
- **Matchups (Defense → Spieler)**: Für eine Woche und Position die schwächsten Defenses (Run-Defense für RB, Pass-Defense für QB/WR/TE) und darunter alle Spieler, die gegen sie spielen – mit Besitzer (Free Agent / mein Team / anderer Teilnehmer).
- **Spieler & Ausblick**: Free Agents, eigenes Roster oder Roster eines Gegners mit Score für die aktuelle Woche und die nächsten 3–5+ Wochen (farbcodiert nach Matchup-Stärke), Bye-Weeks, Verletzungsstatus, Fleaflicker-Projektion.
- **Defense-Tabelle**: Alle 32 Defenses mit Rush-/Pass-Yards und -TDs pro Spiel, Rang und Punkten, die an jede Position abgegeben werden.

## Datenquellen

| Quelle | Wofür | Auth |
|---|---|---|
| [Fleaflicker API](https://www.fleaflicker.com/api-docs/index.html) | Liga-Regeln, Roster, Besitzer, Spieler-Pool, Projektionen, Fleaflickers eigener Matchup-Rang | keine (öffentliche Liga) |
| [nflverse](https://github.com/nflverse/nflverse-data) | NFL-Spielplan, wöchentliche Spielerstatistiken → „Defense vs. Position“ | keine |

## Scoring-Modell

```
Score(Spieler, Woche) = Basis × Faktor(Gegner, Position)
```

- **Basis** = Fantasy-Punkte pro Spiel nach *deinem* Liga-Scoring, berechnet aus nflverse-Stats. Laufende Saison, zu Saisonbeginn mit der Vorsaison gemischt; ohne Spielhistorie die Fleaflicker-Projektion.
- **Faktor** = Punkte, die die gegnerische Defense an diese Position abgibt ÷ Liga-Schnitt (1.0 = neutral, 1.2 = 20 % weicher). Auch hier wird die Vorsaison zu Saisonbeginn mit einbezogen (Gewicht in den Stammdaten einstellbar, läuft über 8 Spiele linear aus).
- **Run-D / Pass-D Rang**: 1 = stärkste, 32 = schwächste Defense (Yards + 20 × TDs pro Spiel).

## Starten

```bash
docker compose up -d --build
# → http://<nas-ip>:8000
```

Beim ersten Aufruf: unter *Ligen / Stammdaten* die Liga-ID (z. B. `354024`) importieren, eigenes Team wählen, speichern. Der erste Datenaufbau lädt ~10–20 MB von nflverse und kann eine Minute dauern; danach wird gecacht (`./data`).

Für Synology/QNAP: Ordner auf das NAS kopieren, in Container Manager / Container Station als Compose-Projekt anlegen, oder das Image auf einem Rechner bauen (`docker build -t football-analytics .`) und exportieren.

## Auf GitHub und in die Registry

```bash
cd football_analytics
git init -b main
git add . && git commit -m "Initial: Fleaflicker/nflverse analytics tool"
git remote add origin https://github.com/UrbanDaveBE/football_analytics.git
git push -u origin main
```

Der Workflow `.github/workflows/docker.yml` baut danach bei jedem Push das Image (amd64 + arm64) und legt es unter
`ghcr.io/urbandavebe/football_analytics:latest` ab (Repo → *Packages*). Beim ersten Mal das Package auf GitHub auf
*public* stellen (Package → Settings → Change visibility), dann kann das NAS ohne Login ziehen:

```bash
docker compose -f docker-compose.nas.yml up -d
```

Docker Hub zusätzlich: Secrets `DOCKERHUB_USERNAME` und `DOCKERHUB_TOKEN` im Repo anlegen, dann landet das Image auch
unter `docker.io/<user>/football-analytics`.

## Entwicklung

```bash
# Backend
cd backend && pip install -r requirements.txt
DATA_DIR=../data uvicorn app.main:app --reload            # http://localhost:8000/docs

# Frontend (Proxy auf :8000)
cd frontend && npm install && npm run dev                  # http://localhost:5173

# Tests (offline, mit Fixtures)
cd backend && DATA_DIR=/tmp/fa pytest
```

## API (Auszug)

| Endpoint | Beschreibung |
|---|---|
| `POST /api/leagues` `{id}` | Liga importieren |
| `PUT /api/leagues/{id}` | Stammdaten ändern (Slots, Team, Ausblick …) |
| `POST /api/leagues/{id}/sync` | Slots/Scoring neu von Fleaflicker laden |
| `POST /api/leagues/{id}/refresh` | Caches leeren |
| `GET /api/leagues/{id}/status` | Saison, Woche, Datenlage |
| `GET /api/leagues/{id}/defense?week=` | Defense-vs-Position-Tabelle |
| `GET /api/leagues/{id}/matchups?week=&position=RB` | Schwächste Defenses + Spieler dagegen |
| `GET /api/leagues/{id}/players?owner=free\|mine\|all\|<team_id>&position=&from_week=&weeks=` | Spieler mit Ausblick |
| `GET /api/leagues/{id}/roster/{team_id}` | Roster eines Teams mit Ausblick |

Swagger-UI: `http://<host>:8000/docs`

## Grenzen / Ideen

- nflverse liefert Stats nur für QB/RB/WR/TE/K – D/ST, IDP und Punter werden nicht bewertet.
- Distanz-Boni (TD ab 40 Yards) sind in den Wochenstats nicht enthalten und fließen nicht in die Basis ein.
- Vor Woche 1 gibt es noch keine Daten der laufenden Saison; dann zählt komplett die Vorsaison.
- Mögliche Erweiterungen: Lineup-Optimierer nach Slot-Regeln, Vegas-Totals/Implied Points, Zielanteil (Targets) und Snap-Shares aus nflverse, Trade-Vergleich.
