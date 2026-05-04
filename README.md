# NASA EnAACT Walk Dashboard

Monitoring and self-scheduling system for the NYC EnAACT air quality field campaign. Manages collector slot claims, weather refreshes, Drive sync, and team coordination via an interactive web dashboard deployed on Google Cloud Run.

---

## Table of Contents

- [How the System Works](#how-the-system-works)
- [Repository Structure](#repository-structure)
- [Data Flow](#data-flow)
- [Subsystems](#subsystems)
  - [Web Server](#web-server--appserver)
  - [Retired Pipelines](#retired-pipelines--pipelines_retired)
  - [Weather Pipeline](#weather-pipeline--pipelinesweather)
  - [Dashboard Pipeline](#dashboard-pipeline--pipelinesdashboard)
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
5. The server runs: `build_weather.py` → `build_dashboard.py` (no scheduler in the active runtime path)
6. Browsers hitting `/dashboard.html` get the freshly generated file served from `data/outputs/site/`

**Container lifecycle (Dockerfile CMD):**

```
python app/server/serve.py --restore-only   # pull runtime state from GCS
python pipelines/dashboard/build_dashboard.py  # bake schedule + weather into dashboard.html
python pipelines/_retired/maps/build_collector_map.py   # best-effort legacy map build; failures are tolerated
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
│   ├── _retired/
│   │   ├── scheduling/
│   │   │   ├── walk_scheduler.py  # Retired scheduler algorithm
│   │   │   └── transit_matrix.py  # Retired transit helper
│   │   └── maps/
│   │       └── build_collector_map.py  # Retired collector map builder
│   ├── weather/
│   │   ├── build_weather.py     # Reads Google Sheets forecast → weather.json
│   │   └── forecast_monitor.py  # Standalone poller: watches Sheets, triggers pipeline
│   ├── dashboard/
│   │   ├── build_dashboard.py   # Generates dashboard.html (schedule + weather + walk log)
│   │   └── build_availability_heatmap.py  # Generates availability_heatmap.html
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
    ▼ build_dashboard.py  ◄── data/inputs/routes/routes_data.json
                          ◄── data/inputs/routes/Route_Groups.xlsx
                          ◄── data/runtime/persisted/Walks_Log.txt
                          ◄── data/outputs/site/weather.json
                          ◄── data/outputs/site/schedule_output.json
                          ◄── Availability.xlsx (via load_availability import)
    │
    ▼
data/outputs/site/dashboard.html   (served at /)
    │
    ▼ GCS upload (dashboard.html)
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
- Streams subprocess output to the browser for `/api/rebuild` and `/api/forecast-stability`

Key design: all subprocess calls pass `cwd=REPO_ROOT` so scripts can find `shared/paths.py`.

### Retired Pipelines — `pipelines/_retired/`

These scripts are preserved for history/fallback but are not part of the active default runtime loop.

**`walk_scheduler.py`** (retired):
- Reads `weather.json`, `Walks_Log.txt`, `Availability.xlsx`, `V2_Preferred_Routes.xlsx`, collector schedule PDFs (via Claude vision API), and `transit_matrix.json`
- Generates a ranked top-8 walk recommendation list and weekly calendar assignment
- Enforces constraints: weather thresholds, collector availability, backpack continuity, transit time
- Writes `data/outputs/site/schedule_output.json`

**`transit_matrix.py`** (retired):
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
- When the sheet changes, runs: `build_weather.py` → `build_dashboard.py`
- Used for local development; in production this is replaced by GAS push triggers to `/api/force-rebuild`

### Dashboard Pipeline — `pipelines/dashboard/`

**`build_dashboard.py`**:
- Module-level code (not a function) — runs top-to-bottom when invoked
- Bakes `schedule_output.json`, `weather.json`, `Walks_Log.txt`, route KML data, and collector info into a single self-contained HTML file
- Imports `load_availability()` from `build_availability_heatmap.py` and bakes availability data directly into the dashboard
- Output: `data/outputs/site/dashboard.html`

**`build_availability_heatmap.py`**:
- Reads `data/inputs/availability/Availability.xlsx`
- Generates a color-coded grid showing which collectors are available for each Day × TOD slot
- Output: `data/outputs/site/availability_heatmap.html`
- Not called by `build_dashboard.py`; run manually when you specifically want to refresh the standalone page

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
| `Recal_Log.txt` | `data/runtime/persisted/Recal_Log.txt` | Calibration history |
| `notification_dispatch_log.jsonl` | `data/runtime/persisted/notification_dispatch_log.jsonl` | Notification send audit log |

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

To run the active rebuild path locally (requires Google Sheets access):

```bash
python pipelines/weather/build_weather.py          # refresh weather.json
python pipelines/dashboard/build_dashboard.py      # rebuild dashboard
```

Retired scripts are still runnable manually when needed:

```bash
python pipelines/_retired/scheduling/transit_matrix.py
python pipelines/_retired/scheduling/walk_scheduler.py
python pipelines/dashboard/build_availability_heatmap.py
```

Self-scheduling ops scripts:

```bash
python scripts/ops/self_schedule_regression.py
python scripts/ops/backfill_assignment_ids.py            # dry-run (default)
python scripts/ops/backfill_assignment_ids.py --apply    # persist ID backfill
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
python app/server/serve.py --restore-only   # download runtime state from GCS
python pipelines/dashboard/build_dashboard.py
python pipelines/_retired/maps/build_collector_map.py  # best-effort legacy path (errors are tolerated)
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
| `ANTHROPIC_API_KEY` | Secret Manager | Legacy/retired scheduler workflows that use Claude |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Secret Manager | Service account JSON string for Drive/Sheets auth |
| `GOOGLE_DRIVE_WALKS_FOLDER_ID` | Secret Manager | Drive folder ID containing walk log files |
| `GCS_BUCKET` | Cloud Run env var | GCS bucket name for persisted state |
| `GAS_SECRET` | Secret Manager | Bearer token for GAS → server webhooks |
| `SCHEDULER_PIN` | Secret Manager | PIN for browser-triggered rebuilds |
| `DRIVE_POLL_INTERVAL` | Cloud Run env var | Background Drive poll interval in seconds (0 = disabled, use GAS push) |
| `SMTP_HOST` | Secret Manager/env var | SMTP server for email notifications |
| `SMTP_PORT` | Secret Manager/env var | SMTP port, default `587` |
| `SMTP_USERNAME` | Secret Manager/env var | Optional SMTP username |
| `SMTP_PASSWORD` | Secret Manager | Optional SMTP password/app password |
| `SMTP_USE_TLS` | Secret Manager/env var | Use STARTTLS for SMTP, default `1` |
| `NOTIFICATION_FROM_EMAIL` | Secret Manager/env var | Sender address for email notifications |
| `NOTIFICATION_PREFERENCES_JSON` | Secret Manager | Collector email opt-ins as JSON; overrides local `notification_preferences.json` |

In production, `DRIVE_POLL_INTERVAL=0` — Drive sync is push-triggered by the GAS `drive_watcher.js` script rather than polled.

---

## GCS Persistence Model

The container is stateless. Durable state is stored in GCS and restored on startup.

**Restore flow** (`--restore-only` on startup):
1. Download `Walks_Log.txt` from GCS → `data/runtime/persisted/Walks_Log.txt`
2. Download `weather.json` from GCS → `data/outputs/site/weather.json`
3. Download `schedule_output.json`, `Recal_Log.txt`, and `dashboard.html`
4. Exit — `build_dashboard.py` runs next with fresh data

**Persist flow** (after any pipeline run):
- After `build_weather.py`: upload `weather.json`
- After schedule claim/unclaim/update APIs: upload `schedule_output.json`
- After `build_dashboard.py`: upload `dashboard.html` (in weather-triggered rebuild path)
- After Drive poll: upload `Walks_Log.txt`, `drive_seen_files.json`

GCS blob names intentionally match the original filenames (e.g. `Walks_Log.txt`, not `data/runtime/persisted/Walks_Log.txt`) for backward compatibility with any external tooling.

---

## Adding / Updating Data

**New route KMLs**: drop into `data/inputs/routes/kml/`, then regenerate `routes_data.json` and `transit_matrix.json` and commit both.

**Collector availability update**: replace `data/inputs/availability/Availability.xlsx` and commit. The embedded dashboard availability grid updates on next rebuild; regenerate `availability_heatmap.html` manually with `python pipelines/dashboard/build_availability_heatmap.py` when needed.

**New collector**: add their metadata in `shared/registry.py` (display name, groups, and any role-specific sets).

**Notification opt-ins**: copy `data/inputs/collectors/notification_preferences.example.json` to
`data/inputs/collectors/notification_preferences.json`, then add opted-in collector emails.
The real preferences file is git-ignored because it contains contact info.
Email is the active transport; Slack preferences can be recorded for later but are not sent yet.

For Cloud Run, store SMTP settings and opt-ins in Secret Manager instead of committing them:

```bash
printf '%s' 'smtp.gmail.com' | gcloud secrets versions add SMTP_HOST --data-file=-
printf '%s' '587' | gcloud secrets versions add SMTP_PORT --data-file=-
printf '%s' '<sender-email>' | gcloud secrets versions add SMTP_USERNAME --data-file=-
printf '%s' '<gmail-app-password>' | gcloud secrets versions add SMTP_PASSWORD --data-file=-
printf '%s' '<sender-email>' | gcloud secrets versions add NOTIFICATION_FROM_EMAIL --data-file=-
gcloud secrets versions add NOTIFICATION_PREFERENCES_JSON --data-file=data/inputs/collectors/notification_preferences.json
```

If a secret does not exist yet, create it first with `gcloud secrets create <NAME> --replication-policy=automatic`.

**MTA GTFS update**: replace files in `data/inputs/transit/gtfs/`, run `python pipelines/_retired/scheduling/transit_matrix.py`, commit the updated `data/outputs/site/transit_matrix.json`.

**Forecast update**: update the Google Sheets tab → GAS triggers `/api/force-rebuild` automatically.

**Walk completions**: Drive sync handles this. Alternatively, manually append a line to `data/runtime/persisted/Walks_Log.txt` in format `A_SOT_MN_MT_20260314_AM`.

---

## API Reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Redirect to `/dashboard.html` |
| `GET` | `/<filename>` | — | Serve file from `data/outputs/site/` |
| `GET` | `/api/status` | — | JSON: file mod times, Drive status, GCS health |
| `GET` | `/api/schedule` | — | Read schedule document |
| `GET` | `/api/schedule/slots` | — | Slot-oriented schedule view (optional `week_start=YYYY-MM-DD`) |
| `POST` | `/api/rebuild` | — | Rebuild dashboards only (no scheduler), stream output |
| `POST` | `/api/forecast-stability` | — | Run forecast stability analysis, stream output |
| `POST` | `/api/force-rebuild` | GAS_SECRET or PIN | Weather + dashboard rebuild (scheduler-free), runs async |
| `POST` | `/api/schedule/rebuild-site` | GAS_SECRET or PIN | Weather + dashboard rebuild (scheduler-free), runs async |
| `POST` | `/api/schedule/claim` | — | Claim one schedule slot |
| `POST` | `/api/schedule/unclaim` | — | Unclaim one schedule slot |
| `PATCH` | `/api/schedule/assignments/{id}` | — | Update a claimed assignment |
| `DELETE` | `/api/schedule/assignments/{id}` | — | Delete a claimed assignment |
| `POST` | `/api/drive/poll` | GAS_SECRET | Manually trigger one Drive poll cycle |
| `POST` | `/api/upload-walk` | — | Upload walk assets and append walk log entry |
| `POST` | `/api/notifications/preview` | — | Preview scheduled collector notifications |
| `POST` | `/api/notifications/send` | PIN | Send scheduled collector email notifications and record per-channel results |
| `POST` | `/api/confirm` | PIN | Verify SCHEDULER_PIN (used by browser auth modal) |
| `POST` | `/api/record-calibration` | PIN | Append a calibration date to `Recal_Log.txt` |

`GAS_SECRET` auth: `Authorization: Bearer <secret>` header. If `GAS_SECRET` is not set, endpoints are open (local dev mode).

Retired endpoints:
- `POST /api/rerun`, `POST /api/rerun/a`, and `POST /api/rerun/b` now return `410 Gone`.
