# Repo Reorganization Plan for Stability-First Cleanup

## Summary

Reorganize the repo into one deployable app with clear internal boundaries, while aggressively cleaning names and paths. The target structure should separate:

- deployable web app code
- scheduling/weather/build pipelines
- static/site assets
- durable runtime state
- local-only raw inputs
- generated outputs
- operational docs

The reorg should optimize first for troubleshooting and feature stabilization, not backward compatibility. Existing internal filenames, imports, and local commands can change freely. Generated artifacts should be moved out of source directories by default.

## Target Structure and Implementation Changes

### 1. Establish a top-level layout with explicit ownership

Adopt this target layout:

```text
/
  app/
    server/
    api/
    services/
    templates_or_static/
  pipelines/
    scheduling/
    weather/
    dashboard/
    maps/
    students/
  data/
    inputs/
      routes/
      transit/
      availability/
      forecasts/
    runtime/
      local/
      persisted/
    outputs/
      site/
      reports/
      logs/
  scripts/
    dev/
    deploy/
    ops/
  integrations/
    gdrive/
    gsheet/
    gas/
    gcs/
  infra/
    docker/
    github/
    cloudrun/
  docs/
    architecture/
    operations/
    handoff/
```

Implementation defaults:

- `app/` contains only runtime web-server code and server-adjacent helpers.
- `pipelines/` contains all build/generation/scheduling logic.
- `data/inputs/` contains raw human-managed project inputs committed to the repo when appropriate.
- `data/runtime/persisted/` is the canonical location for files mirrored to GCS.
- `data/runtime/local/` is for ephemeral non-persisted state.
- `data/outputs/site/` is for generated HTML/JSON consumed by the site.
- `data/outputs/logs/` is for logs and debug exports.
- `integrations/gas/` contains Apps Script sources only.
- `infra/` contains Docker, GitHub Actions, and Cloud Run deployment config/docs.

### 2. Split the current mixed responsibilities into stable subsystems

Refactor the repo mentally and physically into these subsystems:

- Web app subsystem:
  - `serve.py` becomes the web app entrypoint under `app/server/`.
  - Request routing, auth checks, API endpoints, GCS restore/upload helpers, and server startup stay here.
  - Drive/GCS helpers that are runtime concerns can live under `app/services/` or `integrations/`.

- Pipeline subsystem:
  - `walk_scheduler.py`, `build_weather.py`, `build_dashboard.py`, `build_collector_map.py`, `build_availability_heatmap.py`, `student_scheduler.py`, `transit_matrix.py` move under `pipelines/` grouped by function.
  - Each pipeline should expose one clear CLI entrypoint and one clear input/output contract.

- Data contract subsystem:
  - Define canonical paths for:
    - walk log
    - recal log
    - seen-file cache
    - schedule output
    - weather output
    - confirmation state
    - student schedule output
  - Replace ad hoc `BASE_DIR / "filename"` scattering with a single shared path config module.

- Integration subsystem:
  - Move Google Apps Script files into `integrations/gas/`.
  - Move Drive/Sheets/GCS client logic into named modules so server and pipelines do not each reinvent auth/path handling.

### 3. Separate raw inputs from generated artifacts

Move current files into categories:

- Raw/project inputs:
  - route KMLs
  - GTFS source files
  - route grouping spreadsheets
  - preferred-routes spreadsheets
  - availability spreadsheets
  - collector schedule source files
  - forecast source PDFs only if still truly used
- Generated outputs:
  - `dashboard.html`
  - `collector_map.html`
  - `availability_heatmap.html`
  - `schedule_map.html`
  - `schedule_output.json`
  - `student_schedule_output.json`
  - `weather.json`
  - generated logs/reports

Defaults:

- Generated site files should live under `data/outputs/site/`.
- Generated JSON consumed at runtime should also live under `data/outputs/site/` unless it is true runtime state.
- Durable operational state mirrored to GCS should live under `data/runtime/persisted/`.
- Logs should move to `data/outputs/logs/`.
- Repo root should no longer contain generated HTML/JSON/log files.

### 4. Normalize runtime persistence and cloud boundaries

Make the persistence model explicit:

- GCS-backed persisted files:
  - walk log
  - drive seen-file state
  - schedule output
  - weather output
  - any site artifacts you intentionally restore/share across revisions
- Ephemeral local-only files:
  - temporary rebuild intermediates
  - transient debug files
- Committed seed/reference files:
  - source spreadsheets/KML/GTFS/docs

Implementation rule:

- All GCS download/upload code must reference a shared persistence registry instead of raw blob names scattered through the server.
- The Docker startup flow should restore persisted runtime state first, then run site generation, then start the server.
- The server should serve generated site artifacts from one output directory, not from repo root.

### 5. Cleanly define app-facing interfaces

Public/runtime interfaces to preserve conceptually, even if code paths change:

- Browser-facing routes:
  - `/`
  - `/dashboard.html`
  - `/api/status`
  - `/api/rebuild`
  - `/api/rerun`
  - `/api/rerun/a`
  - `/api/rerun/b`
  - `/api/force-rebuild`
  - `/api/drive/poll`
  - `/api/confirm`
  - `/api/record-calibration`

Internal interfaces to formalize during reorg:

- Pipeline input/output contracts:
  - weather builder writes canonical weather JSON
  - scheduler reads canonical weather + availability + preferences + walk log and writes canonical schedule JSON
  - dashboard builder reads canonical outputs and writes site artifacts
- Shared path/config contract:
  - one module defines all important directories/files and environment-variable mapping
- Integration contract:
  - one module per external system: Drive, Sheets, GCS, GAS-trigger semantics

## Reorganization Phases

### Phase 1. Define the skeleton and contracts

- Create the new directory map and assign every current file to one target home.
- Add a shared path/config module that all code will eventually import.
- Write a file ownership matrix in `docs/architecture/`:
  - source of truth
  - generated by
  - read by
  - persisted or ephemeral
  - committed or ignored

### Phase 2. Move code by subsystem

- Move server/runtime code into `app/`.
- Move builders/schedulers into `pipelines/`.
- Move GAS and cloud integration code into `integrations/`.
- Move infra files into `infra/`.
- Rename modules and imports cleanly rather than preserving old script names internally.

### Phase 3. Move artifacts and data

- Relocate raw inputs into `data/inputs/`.
- Relocate generated outputs into `data/outputs/`.
- Relocate runtime state into `data/runtime/`.
- Update Docker, Cloud Run, and GitHub Actions to reference the new locations.
- Update `.gitignore` so generated outputs and ephemeral runtime files are excluded by default unless intentionally committed.

### Phase 4. Stabilize the runtime behavior

- Replace hard-coded path usage across the codebase with shared path constants.
- Remove duplicate assumptions about credentials and env vars.
- Make one canonical auth path for Google integrations.
- Update server rebuild flows so each pipeline step uses canonical inputs/outputs.
- Update status and health reporting to show canonical persisted/output file states.

### Phase 5. Documentation and onboarding hardening

- Replace stale hosting docs with Cloud Run–accurate docs only.
- Add:
  - architecture overview
  - runtime state map
  - deployment flow
  - troubleshooting guide by subsystem
- Include “how a local file becomes a cloud-served artifact” and “how Drive/Sheets data enters the system.”

## Test Plan

### Structural verification

- Repo root contains only high-signal top-level directories and a minimal set of entry files.
- No generated HTML/JSON/log artifacts remain mixed with source files.
- All moved scripts resolve imports and shared paths correctly.

### Functional workflows

- Local server startup serves the dashboard from the new output location.
- Manual rebuild regenerates site artifacts in `data/outputs/site/`.
- Full rerun regenerates weather, schedule, and site outputs in the new canonical paths.
- Drive poll rewrites the canonical walk log path and triggers downstream rebuild correctly.
- Forecast-triggered rebuild runs weather → scheduler → dashboard pipeline using moved modules.
- GCS restore/upload works against the new persisted runtime paths.
- GitHub Actions deploy builds and deploys successfully after path changes.
- Cloud Run startup restores persisted state, generates site outputs, and serves correctly.

### Regression-focused scenarios

- Missing current-week weather tab still fails with a clear error.
- Missing availability data still follows the intended fallback path.
- Container restart preserves only the files marked durable.
- Dashboard baked data and runtime-fetched data point to the same canonical generated files.

## Assumptions and Defaults

- The repo remains a single deployed Cloud Run app.
- Reorganization is allowed to break internal paths, imports, and local command names in service of a clean structure.
- External browser/API behavior should remain conceptually the same, but internal implementation can be renamed and relocated freely.
- Generated files should not live beside source code.
- Old/stale Fly.io-era documentation should be removed or archived rather than preserved.
- The reorg should include documentation updates as part of the same effort, not as a follow-up.
