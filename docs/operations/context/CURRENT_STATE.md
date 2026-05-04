# Current State

This document is the quickest way to re-establish the current project direction.

Last scanned: 2026-05-03 on `main` at `db6b422`.

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
- calendar navigation includes current week plus one upcoming claimable empty week
- weather is advisory only
- uniqueness is enforced per `backpack + date + tod`
- collector double-booking is blocked within the same `date + tod`
- claim/unclaim writes go through `shared/schedule_store.py` and persist only
  `schedule_output.json`; they must not write completed-walk entries to
  `Walks_Log.txt`
- backpack holder/location status is shown in the calendar nav and persists to
  `schedule_output.json` under `backpack_status`; when no manual status exists,
  the dashboard defaults to the collector from the most recent completed walk
  for each backpack
- the backpack status control group uses two compact, symmetrical status
  buttons; changing a backpack holder/location opens a confirmation modal before
  showing the dropdown and OK submit action
- Backpack A status options include the BP A team, Angy, and `CCNY`; Backpack B
  status options include the BP B team, `LaGuardia`, and `CCNY`; professor
  accounts are available and ordered at the bottom of relevant dropdowns
- email-first reminder notifications are implemented for next-day assignments
  with SMTP transport, collector opt-ins, dispatch logging, and a dashboard
  Reminders modal; Slack remains a future transport

Important active APIs:

- `GET /api/schedule`
- `GET /api/schedule/slots`
- `POST /api/schedule/claim`
- `POST /api/schedule/unclaim`
- `POST /api/backpack-status`
- `PATCH /api/schedule/assignments/{id}`
- `DELETE /api/schedule/assignments/{id}`
- `POST /api/rebuild`
- `POST /api/force-rebuild`
- `POST /api/schedule/rebuild-site`
- `POST /api/notifications/preview`
- `POST /api/notifications/send`

Retired scheduler endpoints:

- `POST /api/rerun`
- `POST /api/rerun/a`
- `POST /api/rerun/b`

These now return `410 Gone` and should not be used by active callers.

## Cleanup Status

Recently completed:

- PR #8 merged self-scheduling into `main`
- backpack status controls and the follow-up prominence polish are pushed on
  `main`
- shared collector/route/backpack registry extraction into `shared/registry.py`
- scheduler runtime hooks retired from the active server flow
- scheduler/map/transit scripts moved under `pipelines/_retired/`
- self-scheduling smoke test added at `scripts/ops/self_schedule_smoke.py`
- assignment-level update/remove APIs are active for schedule records
- backpack status controls/API are active for self-scheduling coordination
- docs history now lives under `docs/operations/history/`
- active operations context is consolidated to this file; the old cleanup
  priorities file was removed, context history moved to
  `docs/operations/history/architecture/CONTEXT_HISTORY.md`, and the
  Codex handoff moved to local-only `.codex-local/context/NEXT_CHAT_HANDOFF.md`
- repo-owned caller audit found no active `/api/rerun*` callers outside the
  intentional `410 Gone` handlers and historical/context documentation

Still true:

- `build_dashboard.py` remains the biggest active complexity hotspot
- some sidecar scripts are still present even though the active runtime is slimmer
- external automation callers still need one final confirmation pass to ensure
  no active dependency on `/api/rerun*`

## Python Execution

Codex should use the repo-local sandbox Python first:

```powershell
.codex-local\python39\python.exe
```

The older user-profile interpreter outside the workspace is blocked from the
sandbox and should not be used as the default in Codex runs. Plain `python`,
`py -3`, and the checked-in `.venv` shim are also unreliable in the agent shell.
If the repo-local Python copy is missing or broken, then diagnose alternatives
instead of falling back to the blocked user-profile path.

## Release Validation Status

Code-verifiable checks now pass with the repo-local sandbox Python:

- `.codex-local\python39\python.exe -m py_compile app/server/serve.py pipelines/dashboard/build_dashboard.py shared/schedule_store.py` -> PASS
- `.codex-local\python39\python.exe pipelines/dashboard/build_dashboard.py` -> PASS
- `.codex-local\python39\python.exe scripts/ops/self_schedule_regression.py` -> PASS
- `.codex-local\python39\python.exe scripts/ops/self_schedule_smoke.py --schedule ".tmp/schedule_output.test.json" --in-place` -> PASS
- `git diff --check` -> PASS

Manual checks still worth doing before merge:

- Cloud Run deploy sanity: deploy, trigger one rebuild, and confirm no scheduler
  path regression
- browser UI sanity: claim, conflict rejection, unclaim/delete, and refresh
  persistence; also verify Backpack A/B status dropdown persistence after
  refresh/reopen
- production notification setup: Secret Manager SMTP values and
  `NOTIFICATION_PREFERENCES_JSON`
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

- `docs/architecture/plans/SELF_SCHEDULING_PLAN.md`
- `docs/operations/history/architecture/CONTEXT_HISTORY.md`
- `docs/operations/history/architecture/CLEANUP_AUDIT.md`
- `docs/operations/history/architecture/REDUNDANCY_VERDICTS.md`
