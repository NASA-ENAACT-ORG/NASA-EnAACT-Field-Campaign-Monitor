# Current State

This document is the quickest way to re-establish the current project direction.

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
- uniqueness is enforced per `backpack + route + date + tod`
- collector double-booking is blocked within the same `date + tod`
- claim/unclaim writes go through `shared/schedule_store.py`

Important active APIs:

- `GET /api/schedule`
- `GET /api/schedule/slots`
- `POST /api/schedule/claim`
- `POST /api/schedule/unclaim`
- `POST /api/rebuild`
- `POST /api/schedule/rebuild-site`

## Cleanup Status

Recently completed:

- shared collector/route/backpack registry extraction into `shared/registry.py`
- scheduler runtime hooks retired from the active server flow
- scheduler/map/transit scripts moved under `pipelines/_retired/`
- self-scheduling smoke test added at `scripts/ops/self_schedule_smoke.py`

Still true:

- `build_dashboard.py` remains the biggest active complexity hotspot
- some docs still describe the older scheduler-centered architecture
- some sidecar scripts are still present even though the active runtime is slimmer

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
- `docs/retired/history/architecture/CLEANUP_AUDIT.md`
- `docs/retired/history/architecture/REDUNDANCY_VERDICTS.md`
