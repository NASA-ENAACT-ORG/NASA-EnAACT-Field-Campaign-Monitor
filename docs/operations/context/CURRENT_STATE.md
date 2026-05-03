# Current State

This document is the quickest way to re-establish the current project direction.

Last scanned: 2026-05-03 on `feature/self-scheduling-v1`.

## Project Goal

The current goal is to simplify the repo and runtime so the system is easier to
operate and maintain, with fewer moving parts and less scheduler-era complexity.

## Current Architecture Direction

The repository should no longer be treated as primarily an algorithmic
"scheduler + dashboard" system.

The active direction is:

- keep runtime behavior simple and maintainable
- use direct calendar-integrated slot workflows instead of algorithmic schedule generation
- keep weather advisory-only for self-scheduling
- centralize stable metadata in shared modules
- retire scheduler-era code from the active runtime path

## Active Runtime Path

The smallest current active path is centered on:

- `app/server/serve.py`
- `pipelines/weather/build_weather.py`
- `pipelines/dashboard/build_dashboard.py`
- `shared/paths.py`
- `shared/gcs.py`
- `shared/registry.py`
- `shared/schedule_store.py`

Related runtime artifacts:

- `data/runtime/persisted/Walks_Log.txt`
- `data/runtime/persisted/Recal_Log.txt`
- `data/outputs/site/schedule_output.json`
- `data/outputs/site/weather.json`
- `data/outputs/site/dashboard.html`

## Self-Scheduling Status

Self-scheduling is implemented in the dashboard and server:

- calendar slot -> modal -> claim/unclaim workflow exists
- weather is advisory only
- uniqueness is enforced per `backpack + date + tod`
- collector double-booking is blocked within the same `date + tod`
- claim/unclaim writes go through `shared/schedule_store.py`

Important active APIs:

- `GET /api/schedule`
- `GET /api/schedule/slots`
- `POST /api/schedule/claim`
- `POST /api/schedule/unclaim`
- `PATCH /api/schedule/assignments/{id}`
- `DELETE /api/schedule/assignments/{id}`
- `POST /api/rebuild`
- `POST /api/force-rebuild`
- `POST /api/schedule/rebuild-site`

Retired scheduler endpoints:

- `POST /api/rerun`
- `POST /api/rerun/a`
- `POST /api/rerun/b`

These now return `410 Gone` and should not be used by active callers.

## Cleanup Status

Recently completed:

- shared collector/route/backpack registry extraction into `shared/registry.py`
- scheduler runtime hooks retired from the active server flow
- scheduler/map/transit scripts moved under `pipelines/_retired/`
- self-scheduling smoke test added at `scripts/ops/self_schedule_smoke.py`
- assignment-level update/remove APIs are active for schedule records
- docs history now lives under `docs/operations/history/`
- repo-owned caller audit found no active `/api/rerun*` callers outside the
  intentional `410 Gone` handlers and historical/context documentation

Still true:

- `build_dashboard.py` remains the biggest active complexity hotspot
- some sidecar scripts are still present even though the active runtime is slimmer
- external automation callers still need one final confirmation pass to ensure
  no active dependency on `/api/rerun*`

## Release Validation Status

Code-verifiable checks now pass in user-local execution:

- `python scripts/ops/self_schedule_regression.py` -> PASS
- `python scripts/ops/self_schedule_smoke.py --schedule ".tmp/schedule_output.test.json" --in-place` -> PASS

Note: the agent shell remains path/permission-isolated from the user-local
Python environment.

Manual checks still worth doing before merge:

- Cloud Run deploy sanity: deploy, trigger one rebuild, and confirm no scheduler
  path regression
- browser UI sanity: claim, conflict rejection, unclaim/delete, and refresh
  persistence
- automation sanity: confirm no coworker-owned/external caller still depends on
  `/api/rerun*`

## How To Resume Work

When picking work back up:

- assume the scheduling algorithm is deprecated unless explicitly requested
- prioritize low-risk simplifications before refactors
- prefer one source of truth for metadata and path definitions
- explain code and tradeoffs in beginner-friendly language

## Detailed References

Use these for deeper background after reading this file:

- `docs/operations/context/CLEANUP_PRIORITIES.md`
- `docs/operations/context/CONTEXT_HISTORY.md`
- `docs/architecture/plans/SELF_SCHEDULING_PLAN.md`
- `docs/operations/history/architecture/CLEANUP_AUDIT.md`
- `docs/operations/history/architecture/REDUNDANCY_VERDICTS.md`
