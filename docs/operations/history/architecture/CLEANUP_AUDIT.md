# Cleanup Audit

## Goal

Reduce the repo to the smallest set of runtime-critical files and processes, then simplify the highest-cost complexity without breaking operations.

This audit classifies the current code into:

- core runtime paths
- optional/supporting tools
- duplicate or scattered logic
- likely cleanup targets

## 1. Core Runtime Path

These files are on the main production path and should be treated as the current source of truth:

- `app/server/serve.py`
- `shared/paths.py`
- `shared/gcs.py`
- `pipelines/weather/build_weather.py`
- `pipelines/scheduling/walk_scheduler.py`
- `pipelines/dashboard/build_dashboard.py`
- `pipelines/maps/build_collector_map.py`

These supporting inputs are also core:

- `data/runtime/persisted/Walks_Log.txt`
- `data/runtime/persisted/Recal_Log.txt`
- `data/inputs/availability/Availability.xlsx`
- `data/inputs/routes/V2_Preferred_Routes.xlsx`
- `data/inputs/routes/Preferred_Routes.xlsx`
- `data/inputs/routes/routes_data.json`
- `data/inputs/routes/kml/*`

The current production flow is:

1. `build_weather.py` writes `weather.json`
2. `walk_scheduler.py` reads weather, walk log, availability, preferences, and transit data, then writes `schedule_output.json`
3. `build_dashboard.py` bakes those artifacts into `dashboard.html`
4. `build_collector_map.py` builds `collector_map.html`
5. `serve.py` serves the generated output and triggers rebuilds

## 2. Optional Or Sidecar Pieces

These are not part of the smallest possible runtime system:

- `pipelines/weather/forecast_monitor.py`
  - appears redundant with `integrations/gas/forecast_monitor.js`
  - both watch forecast changes and trigger rebuilds
  - likely keep one mechanism, not both

- `app/server/serve_upload_test.py`
  - local upload UI test server only
  - useful for dev, not production-critical

- `pipelines/students/student_scheduler.py`
  - separate scheduling domain
  - should be kept only if the EFD bag-passing schedule is still actively used

- `app/server/upload_buffer.py`
- `app/server/drive_mover.py`
  - reliability layer for staged uploads to GCS before Drive sync
  - useful, but not required for the minimum scheduling/dashboard product
  - could be isolated as an optional upload subsystem

## 3. Clear Complexity Hotspots

### 3.1 `build_dashboard.py` is oversized and multi-purpose

`pipelines/dashboard/build_dashboard.py` is over 3,100 lines and currently does all of the following:

- loads runtime data
- embeds schedule JSON
- embeds weather JSON
- parses route groups
- loads availability heatmap data
- contains a very large inline HTML/JS application
- patches generated template text
- shells out to rebuild `build_availability_heatmap.py`

This file is both:

- a build script
- a giant front-end bundle stored inside Python

That makes it the strongest candidate for decomposition.

### 3.2 `walk_scheduler.py` is doing too many jobs

`pipelines/scheduling/walk_scheduler.py` is over 2,300 lines and currently handles:

- walk log parsing
- forecast interpretation
- preferred-route parsing
- availability parsing
- Claude-based schedule parsing fallback
- route geometry loading
- collector home loading
- transit continuity scoring
- route ranking
- weekly calendar assignment
- schedule map generation
- JSON output persistence

This is the core scheduling brain, but it is also absorbing too many unrelated responsibilities.

### 3.3 Runtime AI parsing is the highest-cost complexity

The scheduler already prefers structured availability from `Availability.xlsx`.
Claude vision is only a fallback when a collector is missing from the spreadsheet.

That means the code itself already hints at the cleaner architecture:

- primary path: structured spreadsheet input
- fallback path: unstructured PDF/image parsing

If all collectors can be normalized into one structured availability source, the Claude vision path can likely be removed from production scheduling.

## 4. Duplicate Or Scattered Logic

### 4.1 Duplicate cloud auth and client setup

Google auth/client initialization is spread across:

- `app/server/serve.py`
- `shared/gcs.py`
- `pipelines/weather/build_weather.py`
- `pipelines/weather/forecast_monitor.py`
- `app/server/upload_buffer.py`

The repo currently has multiple places that know how to:

- read `GOOGLE_SERVICE_ACCOUNT_JSON`
- build Drive clients
- build GCS clients

This should be centralized.

### 4.2 Duplicate collector and route registries

Collector names, team membership, route labels, and KML mappings are repeated across:

- `pipelines/scheduling/walk_scheduler.py`
- `pipelines/maps/build_collector_map.py`
- `pipelines/dashboard/build_availability_heatmap.py`
- parts of `build_dashboard.py`
- `pipelines/scheduling/transit_matrix.py`

This creates drift risk whenever a new collector or route is added.

These constants should move into one shared registry module.

### 4.3 Duplicate availability heatmap work

`build_dashboard.py` imports availability heatmap helpers from `build_availability_heatmap.py`, uses them for baked data, and then also shells out to run `build_availability_heatmap.py` again.

That is a sign that the heatmap logic has not been given a clean interface yet.

### 4.4 Duplicate forecast-trigger mechanisms

There are two separate forecast-monitor approaches:

- `integrations/gas/forecast_monitor.js`
- `pipelines/weather/forecast_monitor.py`

If the Apps Script path is the deployed one, the Python monitor is probably legacy or backup behavior.

## 5. Likely Legacy Or Cleanup Candidates

These are the best first-pass candidates for removal, isolation, or de-prioritization:

- `pipelines/weather/forecast_monitor.py`
  - likely redundant with Apps Script trigger flow

- `scripts/ops/create_doc.py`
  - document generation helper, not runtime-critical

- `app/server/serve_upload_test.py`
  - keep only if still used regularly

- direct support for collector schedule screenshots/PDFs in the scheduler
  - remove only after structured availability is complete

## 6. Recommended Simplification Order

### Low-risk first

1. Create shared registries for:
   - collectors
   - route labels
   - backpack membership
   - KML name mappings

2. Centralize Google client/auth helpers for:
   - Drive read
   - Drive write
   - GCS
   - Sheets

3. Decide on one forecast trigger mechanism:
   - keep `integrations/gas/forecast_monitor.js` or keep `pipelines/weather/forecast_monitor.py`
   - remove or archive the other

4. Separate dev-only utilities from production code:
   - `serve_upload_test.py`
   - doc generation helpers

### Medium-risk next

5. Refactor `build_dashboard.py` into:
   - data preparation Python
   - HTML template
   - JS asset or embedded JS partials

6. Refactor `walk_scheduler.py` into modules:
   - inputs
   - scoring
   - assignment
   - output writers
   - optional AI ingestion

### Highest-value architectural simplification

7. Remove runtime Claude schedule parsing from the main scheduler path

Target end state:

- all collector availability comes from one structured source
- schedule screenshots/PDFs are converted once, outside the runtime path
- runtime scheduling becomes deterministic and cheaper

## 7. Proposed End State

The minimum clean production system likely looks like this:

- `serve.py`
- `build_weather.py`
- `walk_scheduler.py`
- `build_dashboard.py`
- `build_collector_map.py`
- `shared/paths.py`
- one shared `shared/google_clients.py`
- one shared `shared/registry.py`

Optional systems become clearly separated:

- upload staging / Drive mover
- student scheduler
- local upload test server
- documentation generators
- one-off migration tools

## 8. Suggested First Cleanup Milestone

If doing this incrementally, the best first milestone is:

1. add a shared registry module
2. add shared Google client helpers
3. delete or archive one forecast monitor path
4. make the scheduler use spreadsheet-only availability in normal operation
5. move Claude-based schedule ingestion behind an explicit offline migration script

That milestone would reduce conceptual complexity without requiring a full rewrite.
