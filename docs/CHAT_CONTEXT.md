# Chat Context

Use this file to quickly re-establish context if the chat window loses earlier discussion.

## User Background

- The user is a beginner.
- The user does not know much Git.
- The user does not know much Linux.
- The user only knows a little C++.
- The project is mostly Python.
- Explanations should teach technical terms instead of avoiding them.
- It is okay to use technical jargon, but jargon should be explained clearly.

## Communication Preferences

- Explain things in depth when needed.
- Do not assume prior knowledge of Python project structure, deployment, or web architecture.
- When discussing cleanup/refactoring, explain both:
  - what the code currently does
  - why a proposed simplification helps
- Prefer concrete examples over abstract descriptions.

## Project Goal

The user wants to clean up this repository so there are as few unnecessary files, processes, and moving parts as possible.

The user is especially interested in:

- identifying bloat caused by vibe-coding / ad hoc growth
- removing or demoting genuinely redundant pieces
- reducing runtime complexity
- replacing messy/unstructured inputs with simpler structured inputs where possible
- understanding the codebase well enough to maintain it confidently

## Important Architectural Understanding So Far

- The repo is a scheduling + dashboard system for a NASA NYC field campaign.
- The smallest current production path appears to be:
  - `app/server/serve.py`
  - `pipelines/weather/build_weather.py`
  - `pipelines/scheduling/walk_scheduler.py`
  - `pipelines/dashboard/build_dashboard.py`
  - `pipelines/maps/build_collector_map.py`
  - `shared/paths.py`
  - `shared/gcs.py`
- The biggest complexity hotspots are:
  - `pipelines/dashboard/build_dashboard.py`
  - `pipelines/scheduling/walk_scheduler.py`

## Cleanup Documents Created

- `docs/architecture/CLEANUP_AUDIT.md`
  - broad structural cleanup map
- `docs/architecture/REDUNDANCY_VERDICTS.md`
  - deeper verdicts on suspected redundancies

## Current Redundancy Verdicts

### 1. Forecast monitor overlap

- `pipelines/weather/forecast_monitor.py` appears truly redundant for production if the deployed system uses `integrations/gas/forecast_monitor.js`.
- The Apps Script trigger appears to be the real production trigger path.

### 2. Claude schedule parsing

- Claude-based schedule parsing in `walk_scheduler.py` appears to be a dormant fallback, not the primary path.
- `Availability.xlsx` already covers all 9 current scheduler collectors.
- There is no committed `data/inputs/collectors/` directory in the repo right now.
- Long-term goal: make structured availability the canonical runtime path and remove runtime Claude parsing if operations allow.

### 3. Availability heatmap overlap

- `build_dashboard.py` and `build_availability_heatmap.py` are partially overlapping.
- They are not fully redundant, but they share data-preparation logic in an awkward way.

### 4. Shared registries

- Collector IDs, route labels, backpack membership, and related mappings are duplicated across multiple scripts.
- This is not duplicate behavior, but it is duplicate source-of-truth data.
- A likely cleanup step is creating a shared registry module such as `shared/registry.py`.

### 5. Upload staging vs direct Drive upload

- This overlap is intentional for resilience.
- It is not accidental redundancy.

### 6. Coordinate Availability

- `Coordinate Availability.xlsx` currently appears unused in runtime code.

## Suggested Cleanup Priorities

Low-risk early targets:

1. Centralize shared collector/route registries
2. Remove, archive, or demote `pipelines/weather/forecast_monitor.py`
3. Confirm whether `Coordinate Availability.xlsx` is truly unused

Medium-risk later targets:

4. Refactor `build_dashboard.py` into smaller pieces
5. Refactor `walk_scheduler.py` into smaller modules

Higher-level architectural target:

6. Remove runtime Claude schedule parsing from the normal scheduler path once structured inputs are fully trusted

## Recommended Teaching Style For Future Turns

When resuming from this file:

- start by briefly restating where we left off
- explain proposed changes in beginner-friendly terms
- define important jargon
- prefer small, safe cleanup steps over big rewrites
- call out risk level for any change before making it

## If Picking Up Cleanup Work

Best likely next step:

- inspect and centralize the shared collector/route registry into one module

Good alternative next step:

- verify and demote/remove `pipelines/weather/forecast_monitor.py` from the active architecture
