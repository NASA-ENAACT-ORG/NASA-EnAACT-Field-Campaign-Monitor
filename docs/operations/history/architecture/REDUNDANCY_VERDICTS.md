# Redundancy Verdicts

This document follows up on `CLEANUP_AUDIT.md` with narrower verdicts about overlaps that looked redundant on a structural pass.

Verdict labels:

- **Truly redundant**: same job is already covered elsewhere in the deployed system
- **Partially overlapping**: overlap exists, but each piece still has a distinct operational role
- **Not redundant**: messy or duplicated internally, but not safe to treat as duplicate behavior
- **Dormant fallback**: code path exists but current repo state suggests it is rarely or never exercised

## 1. `pipelines/weather/forecast_monitor.py` vs `integrations/gas/forecast_monitor.js`

### Verdict

**Truly redundant for production**

### Evidence

- The deployed Cloud Run workflow in `.github/workflows/gcp-deploy.yml` only runs the web server container; it does not run `pipelines/weather/forecast_monitor.py`.
- `serve.py` exposes `/api/force-rebuild`, which is exactly what the Apps Script monitor calls.
- `integrations/gas/forecast_monitor.js` is a complete external trigger mechanism for forecast changes.
- `README.md` describes the Python forecast monitor as an **optional standalone poller**.
- `docs/operations/HANDOFF.md` documents Apps Script push-trigger behavior as the operating model and does not position `forecast_monitor.py` as part of the deployed runtime.

### Implication

If the production environment uses the Apps Script trigger, `pipelines/weather/forecast_monitor.py` is a backup or legacy local-only approach, not a needed second production mechanism.

### Cleanup Recommendation

- Keep **one** forecast trigger strategy as the supported path.
- Prefer the Apps Script version if current operations already depend on it.
- Move the Python monitor to `scripts/dev/` or archive/remove it after confirming nobody runs it manually.

## 2. Claude schedule parsing vs `Availability.xlsx`

### Verdict

**Dormant fallback**

### Evidence

- In `walk_scheduler.py`, the primary path is `parse_availability_xlsx()`.
- Claude-based schedule parsing only runs for collectors missing from the spreadsheet:
  - `missing = [c for c in COLLECTORS if c not in availability]`
- The committed `Availability.xlsx` workbook includes sheets for all 9 current scheduler collectors:
  - `SOT, AYA, ALX, TAH, ANG, JAM, JEN, SCT, TER`
- There is no committed `data/inputs/collectors/` directory in the current repo state, so there are no schedule PDFs/images present for the fallback to parse anyway.

### Implication

The runtime AI parsing path is not the normal operational path right now. It appears to exist as insurance for incomplete spreadsheet coverage or future ad hoc inputs.

### Cleanup Recommendation

- Treat Claude schedule parsing as removable from the runtime path once the team agrees the spreadsheet is canonical.
- Best long-term replacement:
  - structured availability only in `Availability.xlsx` or a CSV/JSON equivalent
  - optional one-time migration tool for converting ad hoc schedules into that format

## 3. `build_availability_heatmap.py` vs heatmap logic inside `build_dashboard.py`

### Verdict

**Partially overlapping**

### Evidence

- `build_dashboard.py` imports `load_availability` and group/name constants from `build_availability_heatmap.py`.
- `build_dashboard.py` uses that imported availability data to bake heatmap-related JSON into `dashboard.html`.
- At the end of `build_dashboard.py`, it also shells out to run `build_availability_heatmap.py` as a separate script, which writes `availability_heatmap.html`.

### What is actually duplicated

- Availability parsing and group metadata are shared across both pages.
- The heatmap **data preparation** overlaps.

### What is not actually duplicated

- `build_dashboard.py` generates `dashboard.html`.
- `build_availability_heatmap.py` generates a separate standalone page: `availability_heatmap.html`.

So this is not “two files doing the exact same job.” It is “one dataset powering two outputs, implemented with an awkward interface.”

### Cleanup Recommendation

- Extract shared availability parsing into a separate helper module.
- Let:
  - `build_dashboard.py` consume the helper for dashboard-embedded data
  - `build_availability_heatmap.py` consume the same helper for standalone page generation
- Remove the current cross-file entanglement where one script imports from and shells out to the other.

## 4. Duplicate collector / route registries across scripts

### Verdict

**Not redundant behavior, but redundant definitions**

### Evidence

The same conceptual metadata is repeated across:

- `walk_scheduler.py`
- `build_collector_map.py`
- `build_availability_heatmap.py`
- `transit_matrix.py`
- parts of `build_dashboard.py`

Repeated definitions include:

- collector IDs and names
- backpack team membership
- route labels
- KML route-name mappings
- collector KML-name mappings

### Implication

This is not duplicate functionality in the sense of “two code paths doing the same runtime job.” But it is absolutely duplicate source-of-truth data, which is a major maintenance smell.

### Cleanup Recommendation

- Create a shared registry module, for example:
  - `shared/registry.py`
- Move all stable metadata there.
- Have all pipelines import from that one source.

## 5. Upload staging system vs direct Drive upload path

### Verdict

**Partially overlapping**

### Evidence

The `/api/upload-walk` handler in `serve.py` has two paths:

1. Preferred path:
   - stage to holding bucket via `upload_buffer.py`
   - async mover thread in `drive_mover.py` eventually writes to Drive

2. Fallback path:
   - direct synchronous Drive write in `serve.py`

`docs/operations/WALK_UPLOAD_TOOL.md` explicitly documents this as intentional degraded-mode behavior.

### What overlaps

- Both paths ultimately create the Drive folder structure and upload walk files.

### What differs

- The staged path decouples the browser response from Drive reliability.
- The direct path exists so local development and degraded deployments still work when the holding bucket is unavailable.

### Implication

This is not accidental redundancy. It is resilience-oriented overlap.

### Cleanup Recommendation

- Keep both only if the upload tool is still a supported feature and Drive flakiness is a real operational problem.
- If the upload tool is not important, this whole subsystem can be isolated or removed.
- If the upload tool is important, keep the two-tier design but move it behind a cleaner module boundary.

## 6. `serve_upload_test.py` vs `serve.py`

### Verdict

**Not redundant, but dev-only**

### Evidence

- `serve_upload_test.py` is a minimal local stub server just for upload-UI testing.
- It does not provide the production app behavior.
- `docs/operations/WALK_UPLOAD_TOOL.md` documents it as a testing convenience.

### Cleanup Recommendation

- Keep only if the upload UI is still actively maintained.
- Otherwise archive it with the upload subsystem.

## 7. `Coordinate Availability.xlsx`

### Verdict

**Appears unused**

### Evidence

- `shared/paths.py` defines `COORD_AVAIL_XLSX`.
- No Python or JS runtime code references it elsewhere.
- The only mention outside `shared/paths.py` is descriptive text in `README.md`.

### Implication

This is the strongest current candidate for a genuinely unused input artifact.

### Cleanup Recommendation

- Confirm with the team whether this file is still needed operationally.
- If not, remove it from the active data contract and docs.

## 8. `student_scheduler.py`

### Verdict

**Separate subsystem, not redundant**

### Evidence

- `walk_scheduler.py` optionally merges `student_schedule_output.json` if it exists.
- No other file generates that output.
- The student scheduler solves a different scheduling problem than the main field-walk scheduler.

### Implication

This file is optional in the overall product, but it is not redundant relative to the main scheduler.

### Cleanup Recommendation

- Decide whether the EFD student workflow is still active.
- If inactive, archive/remove the entire student scheduling subsystem cleanly.

## Priority Order For Redundancy Cleanup

1. Remove or demote `pipelines/weather/forecast_monitor.py`
2. Make structured availability the only supported runtime path and retire Claude fallback from production scheduling
3. Centralize collector/route registries
4. Refactor availability heatmap shared logic into a helper module
5. Decide whether the upload subsystem and student scheduler are still in scope
6. Confirm and likely remove `Coordinate Availability.xlsx` from the active contract
