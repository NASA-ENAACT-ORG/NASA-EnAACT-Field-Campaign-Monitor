# NASA EnAACT Walk Dashboard

Scheduling and monitoring system for the NYC EnAACT air quality field campaign. Manages collector walk assignments, weather-constrained scheduling, real-time Drive sync, and student team coordination via an interactive web dashboard deployed on Google Cloud Run.

---

## Table of Contents

- [How the System Works](#how-the-system-works)
- [Repository Structure](#repository-structure)
- [Data Flow](#data-flow)
- [Subsystems](#subsystems)
  - [Web Server](#web-server--appserver)
  - [Scheduling Pipeline](#scheduling-pipeline--pipelinesscheduling)
  - [Weather Pipeline](#weather-pipeline--pipelinesweather)
  - [Dashboard Pipeline](#dashboard-pipeline--pipelinesdashboard)
  - [Maps Pipeline](#maps-pipeline--pipelinesmaps)
  - [Student Scheduler](#student-scheduler--pipelinesstudents)
- [Shared Path Registry](#shared-path-registry)
- [Data Layout](#data-layout)
- [Running Locally](#running-locally)
- [Deployment](#deployment)
- [Environment Variables & Secrets](#environment-variables--secrets)
- [GCS Persistence Model](#gcs-persistence-model)
- [Adding / Updating Data](#adding--updating-data)
- [API Reference](#api-reference)

---

## How the System Works

The dashboard is a single-container Python HTTP server (no framework) deployed on Cloud Run. It serves pre-built HTML files and exposes a small REST API for triggering rebuilds.

**The main feedback loop:**

1. Collectors complete walks → upload filenames to Google Drive
2. A Google Apps Script (in `integrations/gas/`) detects new files and POSTs to `/api/drive/poll`
3. The server pulls the walk log from Drive, rewrites `data/runtime/persisted/Walks_Log.txt`, uploads it to GCS, and triggers a dashboard rebuild
4. Separately, a team member updates the Google Sheets forecast → GAS POSTs to `/api/force-rebuild`
5. The server runs: `build_weather.py` → `walk_scheduler.py` → `build_dashboard.py`, uploading results to GCS after each step
6. Browsers hitting `/dashboard.html` get the freshly generated file served from `data/outputs/site/`

**Container lifecycle (Dockerfile CMD):**

```
python app/server/serve.py --restore-only   # pull Walks_Log.txt + weather.json from GCS
python pipelines/dashboard/build_dashboard.py  # bake schedule + weather into dashboard.html
python pipelines/maps/build_collector_map.py   # build collector location map
python app/server/serve.py                     # start HTTP server on $PORT (8080)
```

---

## Repository Structure

```
/
├── app/
│   └── server/
│       └── serve.py              # HTTP server — all routes, GCS helpers, Drive polling
│
├── pipelines/
│   ├── scheduling/
│   │   ├── walk_scheduler.py     # Core scheduling algorithm (Claude API + weather + transit)
│   │   └── transit_matrix.py    # Builds route-to-route subway travel-time matrix
│   ├── weather/
│   │   ├── build_weather.py     # Reads Google Sheets forecast → weather.json
│   │   └── forecast_monitor.py  # Standalone poller: watches Sheets, triggers pipeline
│   ├── dashboard/
│   │   ├── build_dashboard.py   # Generates dashboard.html (schedule + weather + walk log)
│   │   └── build_availability_heatmap.py  # Generates availability_heatmap.html
│   ├── maps/
│   │   └── build_collector_map.py   # Generates collector_map.html (Leaflet split-screen)
│   └── students/
│       └── student_scheduler.py     # Generates EFD student bag-passing schedule
│
├── integrations/
│   └── gas/                         # Google Apps Script sources
│       ├── drive_watcher.js         # Watches Drive for new walk files → POSTs to /api/drive/poll
│       └── forecast_monitor.js      # Watches Sheets for changes → POSTs to /api/force-rebuild
│
├── shared/
│   └── paths.py                     # Canonical path registry — all scripts import from here
│
├── data/
│   ├── inputs/
│   │   ├── routes/
│   │   │   ├── kml/               # Route KML files (Manhattan, Brooklyn, Bronx, Queens, collector locs)
│   │   │   ├── routes_data.json   # Pre-parsed route coordinates (consumed by dashboard builder)
│   │   │   ├── Preferred_Routes.xlsx
│   │   │   ├── V2_Preferred_Routes.xlsx
│   │   │   └── Route_Groups.xlsx
│   │   ├── transit/
│   │   │   ├── gtfs/              # MTA GTFS subway data (stops, stop_times, transfers)
│   │   │   └── Route_Subway_stops.xlsx
│   │   ├── availability/
│   │   │   ├── Availability.xlsx          # Collector weekly availability grid
│   │   │   └── Coordinate Availability.xlsx
│   │   ├── forecasts/             # Weekly forecast PDFs (from Drive, git-ignored)
│   │   ├── students/
│   │   │   └── EFD_Google_form.csv        # Student team availability form responses
│   │   └── collectors/            # Individual collector schedule PDFs (git-ignored, privacy)
│   │
│   ├── outputs/
│   │   ├── site/                  # Generated files served by the web server
│   │   │   ├── dashboard.html
│   │   │   ├── collector_map.html
│   │   │   ├── availability_heatmap.html
│   │   │   ├── student_schedule.html
│   │   │   ├── schedule_map.html
│   │   │   ├── weather.json
│   │   │   ├── schedule_output.json
│   │   │   ├── student_schedule_output.json
│   │   │   ├── transit_matrix.json
│   │   │   └── routes_data.json
│   │   └── logs/                  # Runtime logs (git-ignored)
│   │       ├── forecast_monitor.log
│   │       └── scheduler_output.txt
│   │
│   └── runtime/
│       ├── persisted/             # Durable state synced to/from GCS
│       │   ├── Walks_Log.txt          # One line per completed walk
│       │   ├── Recal_Log.txt          # Calibration dates
│       │   └── drive_seen_files.json  # Drive file IDs seen (git-ignored)
│       └── local/                 # Ephemeral state, never committed
│           └── .forecast_state.json
│
├── docs/
│   ├── architecture/              # Algorithm docs, reorg plan
│   ├── operations/                # Deployment guides, handoff docs
│   └── handoff/
│
├── scripts/
│   ├── deploy/
│   │   └── deploy.sh              # Manual Cloud Run deploy script
│   └── ops/
│
├── infra/                         # Deployment config docs/references
├── shared/
│   └── paths.py
├── Dockerfile
├── requirements.txt
└── .github/
    └── workflows/
        └── gcp-deploy.yml         # CI/CD: push to main → build → Cloud Run deploy
```

---

## Data Flow

```
Google Sheets (forecast)
    │
    ▼ build_weather.py
data/outputs/site/weather.json
    │
    ▼ walk_scheduler.py  ◄── data/inputs/routes/kml/
                         ◄── data/inputs/routes/Preferred_Routes.xlsx
                         ◄── data/inputs/availability/Availability.xlsx
                         ◄── data/runtime/persisted/Walks_Log.txt
                         ◄── data/outputs/site/transit_matrix.json
    │
    ▼
data/outputs/site/schedule_output.json
    │
    ▼ build_dashboard.py  ◄── data/inputs/routes/routes_data.json
                          ◄── data/inputs/routes/Route_Groups.xlsx
                          ◄── data/runtime/persisted/Walks_Log.txt
                          ◄── data/outputs/site/weather.json
                          ◄── data/outputs/site/schedule_output.json
                          ◄── build_availability_heatmap.py
    │
    ▼
data/outputs/site/dashboard.html   (served at /)
data/outputs/site/availability_heatmap.html
    │
    ▼ GCS upload (all site artifacts)
```

```
Google Drive (walk files)
    │
    ▼ drive_watcher.js (GAS)
POST /api/drive/poll
    │
    ▼ serve.py polls Drive → rewrites Walks_Log.txt
    │
    ▼ build_dashboard.py (async, fire-and-forget)
```

---

## Subsystems

### Web Server — `app/server/`

`serve.py` is the single entrypoint for the running container. It:

- Serves all files from `data/outputs/site/` as static assets
- Runs pipeline scripts as subprocesses (never imports them directly)
- Manages GCS download/upload for persisted state
- Polls Google Drive for new walk files (or listens for GAS push triggers)
- Streams subprocess output to the browser for `/api/rerun` and `/api/rebuild`

Key design: all subprocess calls pass `cwd=REPO_ROOT` so scripts can find `shared/paths.py`.

### Scheduling Pipeline — `pipelines/scheduling/`

**`walk_scheduler.py`** — the core algorithm:
- Reads `weather.json`, `Walks_Log.txt`, `Availability.xlsx`, `V2_Preferred_Routes.xlsx`, collector schedule PDFs (via Claude vision API), and `transit_matrix.json`
- Generates a ranked top-8 walk recommendation list and weekly calendar assignment
- Enforces constraints: weather thresholds, collector availability, backpack continuity, transit time
- Writes `data/outputs/site/schedule_output.json`

**`transit_matrix.py`** — run once when subway data changes:
- Parses MTA GTFS (`data/inputs/transit/gtfs/`) and route KML endpoints
- Computes Dijkstra shortest-path subway travel times between all route pairs
- Writes `data/outputs/site/transit_matrix.json`

### Weather Pipeline — `pipelines/weather/`

**`build_weather.py`**:
- Authenticates to Google Sheets via service account
- Reads each weekly forecast tab (format: `Apr 7 - Apr 13`)
- Resolves conflicts when tabs overlap (newest "Last Updated" date wins)
- Writes `data/outputs/site/weather.json` with AM/MD/PM cloud cover % for all dates since history floor

**`forecast_monitor.py`** — optional standalone poller:
- Polls the forecast spreadsheet's Drive modification time every 5 minutes
- When the sheet changes, runs: `build_weather.py` → `walk_scheduler.py` → `build_dashboard.py`
- Used for local development; in production this is replaced by GAS push triggers to `/api/force-rebuild`

### Dashboard Pipeline — `pipelines/dashboard/`

**`build_dashboard.py`**:
- Module-level code (not a function) — runs top-to-bottom when invoked
- Bakes `schedule_output.json`, `weather.json`, `Walks_Log.txt`, route KML data, and collector info into a single self-contained HTML file
- Calls `build_availability_heatmap.py` as a subprocess at the end
- Output: `data/outputs/site/dashboard.html`

**`build_availability_heatmap.py`**:
- Reads `data/inputs/availability/Availability.xlsx`
- Generates a color-coded grid showing which collectors are available for each Day × TOD slot
- Output: `data/outputs/site/availability_heatmap.html`
- Also importable as a library by `build_dashboard.py` (same directory, `sys.path` trick)

### Maps Pipeline — `pipelines/maps/`

**`build_collector_map.py`**:
- Reads `routes_data.json`, `Preferred_Routes.xlsx`, `Walks_Log.txt`, `schedule_output.json`, and `Collector_Locs.kml`
- Generates a split-screen Leaflet map: collector pins (colored by backpack) with walk counts, detail sidebar, and route overlays
- Output: `data/outputs/site/collector_map.html`

### Student Scheduler — `pipelines/students/`

**`student_scheduler.py`**:
- Reads `data/inputs/students/EFD_Google_form.csv` (Google Form export of student team availability)
- Assigns each team a block of consecutive TOD slots with gap buffers between teams
- Output: `data/outputs/site/student_schedule_output.json` and `student_schedule.html`

## Shared Path Registry

**`shared/paths.py`** is the single source of truth for every file path in the system. All scripts import from it instead of computing paths from `__file__`.

```python
from shared.paths import WALKS_LOG, WEATHER_JSON, SCHEDULE_OUTPUT_JSON
```

Each script adds the repo root to `sys.path` at startup so the `shared` package is discoverable regardless of where the script lives:

```python
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))
from shared.paths import ...
```

To add a new canonical path, edit only `shared/paths.py`. All consumers pick it up automatically.

---

## Data Layout

### What is committed to git

| Path | Why committed |
|---|---|
| `data/inputs/routes/` | Source route definitions — change rarely, need to be in the Docker image |
| `data/inputs/availability/Availability.xlsx` | Collector availability — updated each semester |
| `data/inputs/transit/gtfs/` | MTA GTFS data — update when subway network changes |
| `data/inputs/students/EFD_Google_form.csv` | Student availability export |
| `data/outputs/site/transit_matrix.json` | Expensive to regenerate; no auth required |
| `data/outputs/site/routes_data.json` | Pre-parsed route geometry |
| `data/runtime/persisted/Walks_Log.txt` | Seed log for a fresh container |

### What is NOT committed (git-ignored)

| Path | Why excluded |
|---|---|
| `data/outputs/` (most) | Rebuilt at deploy time from committed inputs |
| `data/runtime/local/` | Ephemeral, machine-local |
| `data/runtime/persisted/drive_seen_files.json` | Regenerated from GCS on startup |
| `data/inputs/collectors/` | Personal collector schedules — privacy |
| `data/inputs/forecasts/*.pdf` | Large, delivered via Google Drive |
| `drive-service-account.json` | Credential |
| `ANTHROPIC_API_KEY.txt` | Credential |

### What lives in GCS (survives container restarts)

| GCS blob | Local path | Purpose |
|---|---|---|
| `Walks_Log.txt` | `data/runtime/persisted/Walks_Log.txt` | Walk completion history |
| `weather.json` | `data/outputs/site/weather.json` | Latest forecast data |
| `schedule_output.json` | `data/outputs/site/schedule_output.json` | Latest schedule |
| `drive_seen_files.json` | `data/runtime/persisted/drive_seen_files.json` | Drive poll dedup state |
| `dashboard.html` | `data/outputs/site/dashboard.html` | Rebuilt HTML |
| `collector_map.html` | `data/outputs/site/collector_map.html` | Rebuilt HTML |
| `availability_heatmap.html` | `data/outputs/site/availability_heatmap.html` | Rebuilt HTML |

---

## Running Locally

```bash
pip install -r requirements.txt

# Optional: set credentials
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_SERVICE_ACCOUNT_JSON=$(cat drive-service-account.json)
export GOOGLE_DRIVE_WALKS_FOLDER_ID=<folder-id>

# Build the dashboard from whatever data is local
python pipelines/dashboard/build_dashboard.py

# Start the server
python app/server/serve.py
# → http://localhost:8765
```

To run the full pipeline locally (requires Google Sheets access):

```bash
python pipelines/weather/build_weather.py          # refresh weather.json
python pipelines/scheduling/walk_scheduler.py      # regenerate schedule
python pipelines/dashboard/build_dashboard.py      # rebuild dashboard
python pipelines/maps/build_collector_map.py       # rebuild collector map
```

To rebuild the transit matrix (run when KMLs or GTFS data changes):

```bash
python pipelines/scheduling/transit_matrix.py
```

---

## Deployment

Push to `main` → GitHub Actions builds a Docker image → deploys to Cloud Run (`us-east1`, service `enact-walk-dashboard`).

The deploy workflow is at `.github/workflows/gcp-deploy.yml`. It:
1. Builds the Docker image from repo root
2. Pushes to Google Artifact Registry (`us-east1-docker.pkg.dev`)
3. Deploys to Cloud Run with 2 Gi memory, 2 CPU, 3600s request timeout
4. Sets env vars and injects secrets from Google Secret Manager

The container CMD sequence on startup:
```sh
python app/server/serve.py --restore-only   # download Walks_Log.txt + weather.json from GCS
python pipelines/dashboard/build_dashboard.py
python pipelines/maps/build_collector_map.py
python app/server/serve.py                  # listen on $PORT (8080)
```

For a manual deploy without GitHub Actions:
```bash
bash scripts/deploy/deploy.sh
```

---

## Environment Variables & Secrets

| Variable | Where set | Purpose |
|---|---|---|
| `PORT` | Cloud Run (auto) | Server listen port (default 8080) |
| `ANTHROPIC_API_KEY` | Secret Manager | Claude API for collector schedule parsing |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Secret Manager | Service account JSON string for Drive/Sheets auth |
| `GOOGLE_DRIVE_WALKS_FOLDER_ID` | Secret Manager | Drive folder ID containing walk log files |
| `GCS_BUCKET` | Cloud Run env var | GCS bucket name for persisted state |
| `GAS_SECRET` | Secret Manager | Bearer token for GAS → server webhooks |
| `SCHEDULER_PIN` | Secret Manager | PIN for browser-triggered rebuilds |
| `DRIVE_POLL_INTERVAL` | Cloud Run env var | Background Drive poll interval in seconds (0 = disabled, use GAS push) |

In production, `DRIVE_POLL_INTERVAL=0` — Drive sync is push-triggered by the GAS `drive_watcher.js` script rather than polled.

---

## GCS Persistence Model

The container is stateless. Durable state is stored in GCS and restored on startup.

**Restore flow** (`--restore-only` on startup):
1. Download `Walks_Log.txt` from GCS → `data/runtime/persisted/Walks_Log.txt`
2. Download `weather.json` from GCS → `data/outputs/site/weather.json`
3. Exit — `build_dashboard.py` runs next with fresh data

**Persist flow** (after any pipeline run):
- After `build_weather.py`: upload `weather.json`
- After `walk_scheduler.py`: upload `schedule_output.json`
- After `build_dashboard.py`: upload `dashboard.html`, `collector_map.html`, `availability_heatmap.html`, `schedule_map.html`
- After Drive poll: upload `Walks_Log.txt`, `drive_seen_files.json`

GCS blob names intentionally match the original filenames (e.g. `Walks_Log.txt`, not `data/runtime/persisted/Walks_Log.txt`) for backward compatibility with any external tooling.

---

## Adding / Updating Data

**New route KMLs**: drop into `data/inputs/routes/kml/`, then regenerate `routes_data.json` and `transit_matrix.json` and commit both.

**Collector availability update**: replace `data/inputs/availability/Availability.xlsx` and commit. The dashboard will reflect the new grid on next rebuild.

**New collector**: add their ID/name to the registries in `walk_scheduler.py`, `build_collector_map.py`, and `build_availability_heatmap.py`.

**MTA GTFS update**: replace files in `data/inputs/transit/gtfs/`, run `python pipelines/scheduling/transit_matrix.py`, commit the updated `data/outputs/site/transit_matrix.json`.

**Forecast update**: update the Google Sheets tab → GAS triggers `/api/force-rebuild` automatically.

**Walk completions**: Drive sync handles this. Alternatively, manually append a line to `data/runtime/persisted/Walks_Log.txt` in format `A_SOT_MN_MT_20260314_AM`.

---

## API Reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Redirect to `/dashboard.html` |
| `GET` | `/<filename>` | — | Serve file from `data/outputs/site/` |
| `GET` | `/api/status` | — | JSON: file mod times, Drive status, GCS health |
| `POST` | `/api/rerun` | GAS_SECRET | Run scheduler (both backpacks) + rebuild all dashboards, stream output |
| `POST` | `/api/rerun/a` | GAS_SECRET | Run scheduler for Backpack A (CCNY) only |
| `POST` | `/api/rerun/b` | GAS_SECRET | Run scheduler for Backpack B (LaGCC) only |
| `POST` | `/api/rebuild` | — | Rebuild dashboards only (no scheduler), stream output |
| `POST` | `/api/force-rebuild` | GAS_SECRET or PIN | Full pipeline: weather → scheduler → dashboard, runs async |
| `POST` | `/api/drive/poll` | GAS_SECRET | Manually trigger one Drive poll cycle |
| `POST` | `/api/confirm` | PIN | Verify SCHEDULER_PIN (used by browser auth modal) |
| `POST` | `/api/record-calibration` | PIN | Append a calibration date to `Recal_Log.txt` |

`GAS_SECRET` auth: `Authorization: Bearer <secret>` header. If `GAS_SECRET` is not set, endpoints are open (local dev mode).
