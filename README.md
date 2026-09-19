# Exposure-Aware Fleet Routing — Delhi NCR

Built for **Ignite-with-Delhi** (Sept 19, 2026, Neo4j track, PS-1). Routes
delivery riders around Delhi NCR's worst winter AQI instead of just
optimizing for time/distance — using a Neo4j knowledge graph of real sensor
data, live rerouting around pollution spikes, and a Slack digest for
managers.

## How it works

Two layers in one Neo4j graph, joined through `Zone`:

- **Operational layer** — `Hub`, `Rider`, `Order`, `Route`, `Place`, `Alias`,
  `VehicleType`, `MaskType` — the actual delivery-dispatch data.
- **Knowledge layer** — `Zone` (H3 resolution-8 hexes, ~0.74 km² each),
  `ZoneReading` (interpolated PM2.5 per zone per 15-min bucket), `GRAPStage`,
  `Rule` — what the city's air actually looked like, everywhere, all day.

Real sensor readings only exist at ~13 fixed points, so every zone's PM2.5
is filled in with inverse-distance-weighted interpolation (3km radius,
confidence marked High/Medium by sensor coverage). Each candidate route is
scored by how much pollution a rider would actually breathe along it
(`dose = Σ PM2.5(zone, time) × time_in_zone`, adjusted for vehicle type and
mask), not just distance or time. A rider's live position is checked
against the graph as they move, and if a severe pollution spike shows up in
a zone they're headed into, `core/reroute.py` runs Dijkstra over the zone
graph to find a clean path around it.

## Project layout

| Folder / file | What it is |
|---|---|
| `pipeline/` | Offline: clean & aggregate raw sensor CSVs → H3 zones → IDW-interpolated `ZoneReading`s → load into Neo4j → seed demo data. Also the Slack EOD poster and every test script. |
| `core/` | The live package: place resolution, Google Routes candidates, exposure scoring + dispatch, AQI-avoidance rerouting, alert logging, grounded route explanations, daily summary. |
| `bot/slack_bot.py` | Live Slack bot (Socket Mode) — replies with the daily summary when @mentioned. |
| `data/cleaned/` | Output of the pipeline: `aggregated_15min.csv` → `zoned_readings.csv` → `zone_readings_interpolated.csv`. |
| `sheild-source/` | Reference copy of the original SHEild.AI codebase this project extends. |
| `app.py`, `test_connections.py`, `render.yaml` | Leftover from the initial sponsor-scaffold — not part of the current build. |

## 1. Setup

```powershell
py -m pip install -r requirements.txt
copy .env.example .env
```

Then open `.env` and fill in (see the comments in `.env.example` for where
to get each one):

- `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` — free instance at
  console.neo4j.io (Aura Free)
- `LLM_API_KEY` — a Gemini key from aistudio.google.com/apikey (used for
  place-name resolution, route explanations, and Cognee)
- `GOOGLE_MAPS_API_KEY` — needs Geocoding API + Routes API enabled, from
  console.cloud.google.com
- `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` / `SLACK_CHANNEL_ID` — from your
  Slack app at api.slack.com/apps (Socket Mode + `app_mention` event)

## 2. Build the knowledge graph (one-time)

```powershell
py pipeline\profile_aqi.py
py pipeline\clean_aggregate.py
py pipeline\assign_zones.py
py pipeline\interpolate_zones.py
py pipeline\neo4j_load.py setup
py pipeline\neo4j_load.py readings
py pipeline\seed_demo.py
```

The first four turn the raw sensor CSVs in `data/` into
`data/cleaned/zone_readings_interpolated.csv`. `neo4j_load.py setup` creates
constraints/indexes and loads `Zone`/`Sensor` nodes; `readings` loads all the
`ZoneReading`s (batched, safe to re-run if it's interrupted). `seed_demo.py`
adds the demo `Hub`/`Rider`/`Order`/`Place` data.

## 3. Verify it end-to-end

```powershell
py pipeline\test_window_filter.py
py pipeline\test_scoring.py
py pipeline\test_explain.py
py pipeline\test_reroute.py
py pipeline\test_full_loop.py
```

Each one exercises a real piece of the pipeline against the loaded graph and
prints what it found — worth running after any change to `core/`.

## 4. Slack digest

```powershell
py pipeline\test_alerts.py          # logs one real reroute alert to Neo4j
py pipeline\send_slack_summary.py   # posts the daily summary to Slack
```

For the live bot (separate terminal, keeps running):

```powershell
py bot\slack_bot.py
```

Then in Slack: `@your-bot-name give summary`.
