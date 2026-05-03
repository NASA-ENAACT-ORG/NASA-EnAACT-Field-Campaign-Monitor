# Next Chat Handoff

## Agent Snapshot

- status: self_scheduling_stabilization_in_progress
- date: 2026-05-03
- branch: feature/self-scheduling-v1
- last_commit: 8e950a5
- runtime_mode: self_scheduling_active
- scheduler_runtime: retired_default_path
- retired_endpoints: /api/rerun, /api/rerun/a, /api/rerun/b (410)
- active_schedule_endpoints:
  - GET /api/schedule
  - GET /api/schedule/slots
  - POST /api/schedule/claim
  - POST /api/schedule/unclaim
  - PATCH /api/schedule/assignments/{id}
  - DELETE /api/schedule/assignments/{id}
- active_rebuild_endpoints:
  - POST /api/rebuild
  - POST /api/force-rebuild
  - POST /api/schedule/rebuild-site
- checklist_state:
  - codex_verifiable_checks: blocked locally because the Windows Python launcher has no installed Python
  - manual_checks_remaining:
    - cloud_run_deploy_sanity
    - browser_ui_claim_conflict_unclaim_refresh
    - confirm_no_external_callers_of_/api/rerun*
- key_docs:
  - docs/operations/context/CURRENT_STATE.md
  - docs/operations/context/CLEANUP_PRIORITIES.md
  - docs/operations/context/CONTEXT_HISTORY.md
  - docs/operations/context/NEXT_CHAT_HANDOFF.md
- note: docs history reorg uses docs/operations/history/* (not docs/retired/history/*)

Date: 2026-05-03
Branch: `feature/self-scheduling-v1`

## What Just Landed

- Stabilization work in the current worktree aligns self-scheduling uniqueness
  across server, shared schedule validation, docs, and local ops scripts:
  `backpack + date + tod` is now the schedule slot uniqueness key.
- `PATCH /api/schedule/assignments/{id}` now applies claim-equivalent
  validation before saving, including collector double-booking rejection.
- Assignment IDs now use explicit IDs when present, with underscore and
  pipe-delimited composite fallbacks for legacy records.
- `integrations/gas/forecast_monitor.js` wording now references
  `/api/force-rebuild` and weather + dashboard rebuilds instead of retired
  scheduler reruns.
- `integrations/gas/drive_watcher.js` wording now references drive polling and
  server-side dashboard rebuilds instead of retired scheduler reruns.
- Two local ops scripts are present for stabilization:
  - `scripts/ops/self_schedule_regression.py`
  - `scripts/ops/backfill_assignment_ids.py`
- Slot scheduler modal now includes assignment-level remove action wired to
  `DELETE /api/schedule/assignments/{id}` (with assignment-id fallback derivation
  for older records missing explicit `id`).
- Self-scheduling migration is now the active runtime path.
- Scheduler rerun endpoints were retired (`/api/rerun*` return `410`).
- Active schedule lifecycle APIs are in place:
  - `GET /api/schedule`
  - `GET /api/schedule/slots`
  - `POST /api/schedule/claim`
  - `POST /api/schedule/unclaim`
  - `PATCH /api/schedule/assignments/{id}`
  - `DELETE /api/schedule/assignments/{id}`
- Scheduler-free rebuild paths are active:
  - `POST /api/rebuild`
  - `POST /api/force-rebuild`
  - `POST /api/schedule/rebuild-site`
- Runtime/docs polish pass landed:
  - `README.md` aligned to current runtime behavior
  - `Dockerfile` startup path corrected to retired map script location
  - `serve.py` internal lock naming clarified (`_rebuild_running`)
- Docs history reorg was applied:
  - obsolete docs moved under `docs/operations/history/`
  - active docs remain under `docs/operations/context/`, `docs/operations/guides/`, `docs/architecture/`
  - path reference fixes were applied in `docs/operations/README.md` and `docs/architecture/README.md`

## Current Repo State

Branch has local stabilization changes that still need Python-backed validation
before merge readiness can be called complete. The shell check found
`C:\WINDOWS\py.exe`, but `py -0p` reports no installed Python and `python` is
not on PATH.

Recent commits from this chat:

- `8e950a5` — "Add assignment remove action to self-scheduling slot modal"
- `f5d5342` — "Finalize docs history reorg and add next-chat handoff snapshot"
- `ec4a87d` — "Polish self-scheduling runtime docs and legacy startup paths"

Most important current context docs:

- `docs/operations/context/CURRENT_STATE.md`
- `docs/operations/context/CLEANUP_PRIORITIES.md`
- `docs/operations/context/CONTEXT_HISTORY.md`
- `docs/operations/context/NEXT_CHAT_HANDOFF.md`

## Best Next Likely Task

Finish quick release validation for merge readiness (fast, high-value checks).

Use this minimal go/no-go set:

- Cloud Run sanity: deploy + one rebuild + no scheduler path regressions
- UI sanity: claim, conflict rejection, unclaim, refresh persistence
- Automation sanity: confirm no active callers still depend on `/api/rerun*`

## Important Architectural Reminder

Do not treat the scheduling algorithm as the main future direction unless
explicitly asked.

The active direction is:

- direct slot claim/unclaim workflows
- scheduler-free rebuild paths
- simpler runtime operations
- clearer boundaries between active code and historical code

## Validation Already Done

- self-scheduling smoke test passed via `scripts/ops/self_schedule_smoke.py`
- compile sanity passed for key Python modules via `python3 -m py_compile`
- checklist evidence pass completed for code-verifiable items
- docs path consistency check completed for history reorg references
- current worktree: `git diff --check` passes
- current worktree: Python tests/regression scripts could not be run because no
  Python interpreter is available in this shell

## Suggested Resume Strategy

1. Read `CURRENT_STATE.md`.
2. Read `CLEANUP_PRIORITIES.md`.
3. Install or expose Python in the tool shell, then run:
   - `py -3 scripts/ops/self_schedule_regression.py`
   - `py -3 scripts/ops/self_schedule_smoke.py`
4. Run quick release validation checks (Cloud Run + UI + rerun endpoint dependency check).
5. If checks pass, merge PR and monitor post-deploy logs briefly.
