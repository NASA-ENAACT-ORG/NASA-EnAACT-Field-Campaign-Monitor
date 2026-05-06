# Current State

This document is the quickest way to re-establish the current project direction.

Last scanned: 2026-05-06 on `main` after the TEMPO favicon update.

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
- `data/outputs/site/favicon.png`

## Self-Scheduling Status

Self-scheduling is implemented in the dashboard and server:

- calendar slot -> modal -> claim/unclaim workflow exists
- calendar navigation includes one recent empty/history week, the current week,
  and one upcoming claimable empty week even when no claims exist
- past dates are visible as history but cannot be claimed or edited; yesterday
  and older are rejected by the API
- the claim modal now requires explicit bag, route, and collector selections
  before enabling the claim action, then resets those selections after opening
  or completing a claim so stale defaults are not accidentally submitted
- weather is advisory only
- uniqueness is enforced per `backpack + date + tod`
- collector double-booking is blocked within the same `date + tod`
- schedule storage validation rejects unknown route codes, unknown collectors,
  backpack-ineligible collectors, and duplicate assignment lookup IDs before
  saving `schedule_output.json`
- unclaim/removal semantics follow the durable slot identity
  `backpack + date + tod`, so a stale route value cannot strand a claim after a
  route edit
- assignment edits refresh `weather_advisory` when date/time-of-day changes
- claim/unclaim writes go through `shared/schedule_store.py` and persist only
  `schedule_output.json`; they must not write completed-walk entries to
  `Walks_Log.txt`
- `schedule_output.json` is current/future reservation state only; expired
  assignments are pruned, and completed walks reappear after upload/Drive poll
  rebuilds `Walks_Log.txt`
- local dashboard builds may have an empty `Walks_Log.txt` mirror when GCS is
  disabled; the calendar must still keep past/week-window navigation available
  from schedule and weather metadata, while completed walk cards only appear
  once the walk log mirror has entries
- backpack holder/location status is shown in the calendar nav and persists to
  `schedule_output.json` under `backpack_status`; when no manual status exists,
  the dashboard defaults to the collector from the most recent completed walk
  for each backpack
- the backpack status control group uses two compact, symmetrical status
  buttons; changing a backpack holder/location opens a confirmation modal before
  showing the dropdown and OK submit action; the warning text is red and the
  displayed person labels use names only, not `NAME (ID)`
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
- past schedule lifecycle guards are committed on `main`: schedule reads/builds
  prune expired reservations from `schedule_output.json`, reject past
  claim/edit targets, and preserve calendar navigation from schedule/weather
  window metadata when local walk-log mirrors are empty
- the calendar claim modal was tightened so bag/route/collector are explicit
  choices and the claim button stays disabled until all required values exist
- `scripts/ops/edge_case_regression.py` now covers broad offline edge cases for
  schedule validation, notification preferences/preview, multipart parsing,
  upload-buffer staging, weather parsing, and student-scheduler helpers
- local agent workspace files are ignored through `.gitignore`, keeping
  `.codex-local/` and related sandbox artifacts out of commits
- `EFD` is restored to the dashboard Collectors tab as a visible student-team
  collector tile in the lower auxiliary row next to Professors, without
  changing backpack claim eligibility
- `scripts/ops/edge_case_regression.py` now guards that `EFD` stays visible in
  the dashboard collector groups but remains excluded from backpack schedule
  claim eligibility
- the browser tab/bookmark favicon now uses the TEMPO mission logo: the source
  asset lives at `pipelines/dashboard/assets/tempo_logo.png`, dashboard builds
  copy it to `data/outputs/site/favicon.png`, and GCS restore/upload paths keep
  it with `dashboard.html`
- backpack status controls, confirmation-modal guardrail, red warning text, and
  name-only display labels are committed on `main`
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
- adversarial self-scheduling validation now guards against corrupt
  `schedule_output.json` records with bad routes, fake collectors,
  backpack-ineligible collectors, or duplicate lookup IDs
- adversarial self-scheduling route/edit checks now cover stale-route unclaim
  and weather-advisory refresh after assignment date/time edits

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

- `.codex-local\python39\python.exe -m compileall -q app shared scripts pipelines` -> PASS
- `.codex-local\python39\python.exe scripts\ops\edge_case_regression.py` -> PASS
- `.codex-local\python39\python.exe -m py_compile app/server/serve.py pipelines/dashboard/build_dashboard.py shared/schedule_store.py` -> PASS
- `.codex-local\python39\python.exe pipelines/dashboard/build_dashboard.py` -> PASS
- `.codex-local\python39\python.exe pipelines/dashboard/build_availability_heatmap.py` -> PASS
- `.codex-local\python39\python.exe scripts/ops/self_schedule_regression.py` -> PASS
- `.codex-local\python39\python.exe scripts/ops/self_schedule_smoke.py --schedule ".tmp/schedule_output.test.json" --in-place` -> PASS
- `.codex-local\python39\python.exe scripts\ops\backfill_assignment_ids.py` -> PASS dry-run; no missing assignment IDs
- `git diff --check` -> PASS

Most recent focused checks after the backpack status modal/name polish:

- `.codex-local\python39\python.exe -m py_compile pipelines/dashboard/build_dashboard.py scripts/ops/self_schedule_regression.py` -> PASS
- `.codex-local\python39\python.exe pipelines/dashboard/build_dashboard.py` -> PASS
- `.codex-local\python39\python.exe scripts/ops/self_schedule_regression.py` -> PASS
- `git diff --check` -> PASS

Most recent EFD collector dashboard checks after `78353c2`:

- `.codex-local\python39\python.exe -m compileall -q app shared scripts pipelines` -> PASS
- `.codex-local\python39\python.exe scripts\ops\edge_case_regression.py` -> PASS
- `.codex-local\python39\python.exe scripts\ops\self_schedule_regression.py` -> PASS
- `.codex-local\python39\python.exe scripts\ops\self_schedule_smoke.py --schedule ".tmp\schedule_output.test.json" --in-place` -> PASS
- `.codex-local\python39\python.exe pipelines\dashboard\build_dashboard.py` -> PASS
- `.codex-local\python39\python.exe pipelines\dashboard\build_availability_heatmap.py` -> PASS
- `git diff --check` -> PASS

Most recent TEMPO favicon checks:

- `.codex-local\python39\python.exe -m py_compile app\server\serve.py pipelines\dashboard\build_dashboard.py shared\paths.py` -> PASS
- `.codex-local\python39\python.exe pipelines\dashboard\build_dashboard.py` -> PASS
- source `pipelines/dashboard/assets/tempo_logo.png` and generated
  `data/outputs/site/favicon.png` SHA-256 hashes match
- `git diff --check` -> PASS

Most recent adversarial self-scheduling hardening checks:

- `.codex-local\python39\python.exe -m py_compile app/server/serve.py pipelines/dashboard/build_dashboard.py shared/registry.py shared/schedule_store.py scripts/ops/self_schedule_regression.py scripts/ops/self_schedule_smoke.py` -> PASS
- `.codex-local\python39\python.exe scripts/ops/self_schedule_regression.py` -> PASS
- `.codex-local\python39\python.exe scripts/ops/self_schedule_smoke.py --schedule ".tmp/schedule_output.test.json" --in-place` -> PASS
- `.codex-local\python39\python.exe pipelines/dashboard/build_dashboard.py` -> PASS
- `git diff --check` -> PASS

GCP-owner/manual checks still worth doing before production release:

- Cloud Run deploy sanity: deploy, trigger one rebuild, and confirm no scheduler
  path regression; this requires access to the production Google Cloud project
- browser UI sanity: claim, conflict rejection, unclaim/delete, and refresh
  persistence; also verify Backpack A/B status-button/modal persistence after
  refresh/reopen in production
- production notification setup: Secret Manager SMTP values and
  `NOTIFICATION_PREFERENCES_JSON`; this requires Secret Manager access and is
  handed off to the GCP owner
- automation sanity: confirm no coworker-owned/external caller still depends on
  `/api/rerun*`
- dashboard Reminders modal decision is intentionally deferred; do not remove
  or redesign it until production access and notification ownership are clearer
- Soteri has been asked for help with GCP/Cloud Run/Secret Manager validation;
  use `docs/operations/guides/SOTERI_PRODUCTION_VALIDATION_CHECKLIST.md` as the
  handoff checklist

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
