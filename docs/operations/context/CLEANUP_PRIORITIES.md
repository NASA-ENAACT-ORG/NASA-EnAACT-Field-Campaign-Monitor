# Cleanup Priorities

This document condenses the current cleanup and migration priorities into one
short, practical roadmap.

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

## Current Priority Order

### 1. Docs and operations alignment

Several docs still describe the old scheduler-centered architecture.

Highest-value follow-up targets:

- `README.md`
- `docs/operations/HANDOFF.md`
- `docs/operations/LOCAL_TESTING.md`
- deployment/startup references such as `Dockerfile`

Goal:

- make the written operational story match the current runtime behavior

### 2. Finish availability-heatmap separation

`build_dashboard.py` still imports shared heatmap parsing data directly from
`build_availability_heatmap.py`.

Goal:

- extract shared availability parsing/helpers into a dedicated helper module
- let both builders consume that helper without cross-script coupling

### 3. Demote or clarify `forecast_monitor.py`

The active production story appears to rely on Apps Script triggers rather than
the standalone Python forecast monitor.

Goal:

- either mark `pipelines/weather/forecast_monitor.py` as local-only backup
- or retire/archive it if operations no longer need it

### 4. Confirm and clean unused inputs

The strongest known candidate is:

- `data/inputs/availability/Coordinate Availability.xlsx`

Goal:

- confirm whether it is still operationally needed
- remove it from the active contract if not

### 5. Decide scope of optional subsystems

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

- `docs/architecture/CLEANUP_AUDIT.md`
- `docs/architecture/REDUNDANCY_VERDICTS.md`
- `docs/architecture/SELF_SCHEDULING_PLAN.md`
- `docs/architecture/Repo_Reorg_plan_codex.md`
