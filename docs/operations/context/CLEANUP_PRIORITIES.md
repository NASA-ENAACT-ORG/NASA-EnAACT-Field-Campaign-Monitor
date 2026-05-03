# Cleanup Priorities

This document condenses the current cleanup and migration priorities into one
short, practical roadmap.

Last scanned: 2026-05-03 on `feature/self-scheduling-v1`.

## Primary Objective

Reduce repo bloat and runtime complexity without destabilizing operations.

The guiding principles are:

- fewer active runtime subsystems
- fewer duplicate definitions
- clearer boundaries between active code and retired code
- beginner-friendly maintainability

## What Is Already Done

- Shared registry extraction is in place through `shared/registry.py`.
- Scheduler runtime hooks have been retired from the active server flow.
- Legacy scheduler/map/transit scripts have been moved under `pipelines/_retired/`.
- The dashboard now supports direct slot claim/unclaim workflows.
- Assignment-level schedule update/remove APIs are active.
- `README.md`, `Dockerfile`, and operations/architecture indexes have been
  aligned with the self-scheduling runtime and docs history reorg.

## Current Priority Order

### 1. Release validation for merge readiness

The branch is in a strong checkpoint state, but the remaining go/no-go checks
are environment and browser checks.

Highest-value follow-up targets:

- Cloud Run deploy sanity: deploy, trigger one rebuild, and confirm no scheduler
  path regression
- browser UI sanity: claim, conflict rejection, unclaim/delete, and refresh
  persistence
- automation sanity: repo-owned callers are clean; confirm no coworker-owned or
  external caller still depends on `/api/rerun*`

Goal:

- establish merge readiness without taking on more refactor risk

### 2. Clean remaining scheduler-era wording

The known stale Apps Script wording has been aligned with the self-scheduling
runtime path:

- `integrations/gas/forecast_monitor.js` now describes `/api/force-rebuild`
  as a weather + dashboard rebuild
- `integrations/gas/drive_watcher.js` now describes drive polling and
  server-side dashboard rebuilds
- `scripts/ops/create_doc.py` now generates a self-scheduling/current-runtime
  architecture note instead of presenting the retired scheduler as active

Goal:

- keep active setup guidance from sending future operators back to retired
  scheduler endpoints

### 3. Finish availability-heatmap separation

`build_dashboard.py` still imports shared heatmap parsing data directly from
`build_availability_heatmap.py`.

Goal:

- extract shared availability parsing/helpers into a dedicated helper module
- let both builders consume that helper without cross-script coupling

### 4. Demote or clarify `forecast_monitor.py`

The active production story appears to rely on Apps Script triggers rather than
the standalone Python forecast monitor.

Goal:

- either mark `pipelines/weather/forecast_monitor.py` as local-only backup
- or retire/archive it if operations no longer need it

### 5. Confirm and clean unused inputs

The strongest known candidate is:

- `data/inputs/availability/Coordinate Availability.xlsx`

Goal:

- confirm whether it is still operationally needed
- remove it from the active contract if not

### 6. Decide scope of optional subsystems

Still worth confirming:

- upload staging / Drive mover subsystem
- student scheduler subsystem

Goal:

- keep them only if they are still active workflows
- otherwise isolate or retire them cleanly

## Longer-Term Refactors

These are worthwhile, but not the best next move unless the lower-risk items are
already handled:

- break `build_dashboard.py` into smaller data/template/frontend pieces
- continue simplifying scheduler-era code paths that still shape active data flow
- fully separate active runtime docs from historical scheduler docs

## Detailed Source Docs

These documents still contain useful deeper analysis:

- `docs/architecture/plans/SELF_SCHEDULING_PLAN.md`
- `docs/operations/history/architecture/CLEANUP_AUDIT.md`
- `docs/operations/history/architecture/REDUNDANCY_VERDICTS.md`
- `docs/operations/history/architecture/Repo_Reorg_plan_codex.md`
