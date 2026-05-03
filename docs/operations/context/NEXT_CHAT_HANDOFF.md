# Next Chat Handoff

## Agent Snapshot

- status: stabilization_validated_pending_manual_release_checks
- date: 2026-05-03
- branch: feature/self-scheduling-v1
- last_commit: 47c7574
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
  - codex_verifiable_checks: passed in user-local venv
  - manual_checks_remaining:
    - cloud_run_deploy_sanity
    - browser_ui_claim_conflict_unclaim_refresh
    - confirm_no_external_callers_of_/api/rerun*
    - production_notification_secrets_setup
    - decide_whether_to_remove_reminders_modal
- key_docs:
  - docs/operations/context/CURRENT_STATE.md
  - docs/operations/context/CLEANUP_PRIORITIES.md
  - docs/operations/context/CONTEXT_HISTORY.md
  - docs/operations/context/NEXT_CHAT_HANDOFF.md
- note: docs history reorg uses docs/operations/history/* (not docs/retired/history/*)

Date: 2026-05-03
Branch: `feature/self-scheduling-v1`

## What Just Landed

- Calendar navigation now includes the current week plus one upcoming empty
  claimable week, so collectors can claim next-week slots before any assignments
  exist for that week.
- Email-first notifications are implemented:
  - `POST /api/notifications/preview` enriches tomorrow reminders with opted-in
    email destinations, with addresses redacted in unauthenticated preview output.
  - `POST /api/notifications/send` sends via SMTP when configured, records
    per-channel results, and leaves Slack as a recognized-but-pending channel.
  - Dashboard has a `Reminders` button/modal for previewing and sending tomorrow
    emails from the browser.
  - Collector opt-ins load from local
    `data/inputs/collectors/notification_preferences.json` or production
    `NOTIFICATION_PREFERENCES_JSON`.
- Private/local files are ignored:
  - `data/inputs/collectors/notification_preferences.json`
  - `data/runtime/persisted/notification_dispatch_log.jsonl`
  - `.tmp/`
- Stabilization work aligns self-scheduling uniqueness across server, shared
  schedule validation, docs, and local ops scripts:
  `backpack + date + tod` is now the schedule slot uniqueness key.
- `PATCH /api/schedule/assignments/{id}` now applies claim-equivalent
  validation before saving, including collector double-booking rejection.
- Assignment IDs now use explicit IDs when present, with underscore and
  pipe-delimited composite fallbacks for legacy records.
- `integrations/gas/forecast_monitor.js` now references
  `/api/force-rebuild` and weather + dashboard rebuilds.
- `integrations/gas/drive_watcher.js` now describes drive polling and
  server-side dashboard rebuilds.
- Local ops scripts added:
  - `scripts/ops/self_schedule_regression.py`
  - `scripts/ops/backfill_assignment_ids.py`
- `app/server/serve.py` typing cleanup for Pylance:
  - multipart parsing now narrows payload types explicitly
  - `_stream_script` handles optional `proc.stdout`
  - `log_message` override signature aligned with `BaseHTTPRequestHandler`
  - status payload and Drive folder-id optional cases are type-safe

## Current Repo State

Latest feature changes were committed in `47c7574` and the branch is ahead of
`origin/feature/self-scheduling-v1`.

Agent shell still has isolated Python/path limits, but user-local execution
completed successfully in venv after schedule dedupe.

Recent commits from this chat:

- `47c7574` - "Add email reminders for self-scheduling"
- `21dfb61` - "Stabilize self-scheduling assignment handling"
- `8e950a5` - "Add assignment remove action to self-scheduling slot modal"
- `f5d5342` - "Finalize docs history reorg and add next-chat handoff snapshot"
- `ec4a87d` - "Polish self-scheduling runtime docs and legacy startup paths"

Most important current context docs:

- `docs/operations/context/CURRENT_STATE.md`
- `docs/operations/context/CLEANUP_PRIORITIES.md`
- `docs/operations/context/CONTEXT_HISTORY.md`
- `docs/operations/context/NEXT_CHAT_HANDOFF.md`

## Best Next Likely Task

Finish release validation and production notification setup for merge readiness.

User note for next session: the dashboard `Reminders` modal may be unnecessary
and should probably be removed. Do not remove it automatically; reassess UX and
likely keep notification sends as API/script/admin-only unless a real dashboard
workflow is wanted.

Use this minimal go/no-go set:

- Cloud Run sanity: deploy + one rebuild + no scheduler path regressions
- UI sanity: claim, conflict rejection, unclaim/delete, refresh persistence
- Automation sanity: confirm no active callers still depend on `/api/rerun*`
- Notification sanity:
  - revoke the Gmail app password pasted in chat and create a fresh app password
  - create/update Secret Manager secrets for SMTP and `NOTIFICATION_PREFERENCES_JSON`
  - only after those secrets exist, update the Cloud Run deploy workflow to pass
    them as `--set-secrets`
  - deploy, then preview/send one test reminder through the dashboard modal

## Validation Already Done

- current worktree consistency checks completed before commit
- docs and integrations updated to remove active scheduler-rerun wording
- schedule duplicate-slot data issue was confirmed and manually corrected
- local user-run checks now pass:
  - `python scripts/ops/self_schedule_regression.py` -> PASS
  - `python scripts/ops/self_schedule_smoke.py --schedule ".tmp/schedule_output.test.json" --in-place` -> PASS
- Pylance-reported `serve.py` diagnostics were addressed (multipart typing +
  override/type-narrowing fixes)
- Local email notification send was manually confirmed by the user after setting
  SMTP env vars in PowerShell.
- Agent shell still could not run Python: `python`, `py -3`, and repo `.venv`
  are unavailable/broken from this sandbox.

## Suggested Resume Strategy

1. Run quick release manual checks (Cloud Run + UI + rerun dependency check).
2. Re-open VS Code diagnostics once to confirm no new Pylance regressions.
3. If manual checks pass, push branch and proceed to merge readiness.
