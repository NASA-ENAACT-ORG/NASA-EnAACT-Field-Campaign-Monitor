# PR #8 Merge — Conflict Resolution Differences

**PR:** [#8 Migrating runtime from algo to self scheduling](https://github.com/NASA-ENAACT-ORG/NASA-EnAACT-Field-Campaign-Monitor/pull/8)
**Merged:** 2026-05-03 at merge commit `fca6e1f`
**Conflict resolution commit:** `f7282f9` (`Merge origin/main into feature/self-scheduling-v1`)
**Feature branch tip before resolution:** `061b602` (`Update handoff for reminder follow-up`)
**Main branch tip at time of merge:** `8c71a62` (`Pass supportsAllDrives=True on Drive API calls`)

This document records what changed between what the PR originally proposed and what was
actually merged after conflicts were resolved between `feature/self-scheduling-v1` and `main`.

---

## Summary of Resolutions

| File | PR's version | Resolution |
|---|---|---|
| `app/server/serve.py` | Did not include main's Drive API / GCS improvements | Took main's improvements |
| `integrations/gas/forecast_monitor.js` | Simple once-a-day check at 2:30 AM | Replaced entirely with main's debounced edit-driven + daily safety-net model |
| `docs/operations/guides/GCP_SETUP_GUIDE.md` | Included `GPS_AUTH_TOKEN` secret | `GPS_AUTH_TOKEN` removed; only `GAS_SECRET` kept |
| `pipelines/dashboard/build_dashboard.py` | Route groups as checkbox panel; had GPS CSS/JS | Took PR's template/registry approach; applied main's UI simplifications |
| `pipelines/_retired/walk_scheduler.py` | Stripped/retired version | Kept PR's retired version |

---

## File-by-file Details

### 1. `app/server/serve.py`

**Resolution:** Took main's improvements on top of PR's self-scheduling APIs.

Changes introduced by main that were merged in:

- **All Drive API calls now pass `supportsAllDrives=True` and `includeItemsFromAllDrives=True`**
  (`_drive_find_folder`, `_drive_find_folder_by_prefix`, `_drive_create_or_get_folder`,
  `_drive_upload_file`, and the Drive poll list/subfolder calls). These were absent from the PR.

- **GCS upload now checks the return value and logs success/failure explicitly:**
  ```python
  # PR had:
  _upload_to_gcs(WEATHER_JSON, "weather.json")
  print("[forecast] Uploaded weather.json -> GCS")

  # After resolution:
  ok = _upload_to_gcs(WEATHER_JSON, "weather.json")
  if ok:
      print("[forecast] Uploaded weather.json -> GCS")
  else:
      print("[forecast] WARNING: GCS upload of weather.json failed")
  ```
  Additional `elif` branches were added for missing GCS bucket and missing `weather.json`.

- **`_rebuild_walk_log` now validates each entry before writing:**
  The PR's version wrote all entries unconditionally. Main's version added a validation
  loop using `_WALK_LOG_RE`, skips malformed/RECAL entries, and logs skipped lines.

- **Walk log polling removed manual-entry preservation:**
  ```python
  # PR had:
  merged = sorted(drive_set | existing_entries)  # union of Drive + local manual entries
  log_changed = set(merged) != existing_entries

  # After resolution:
  merged = sorted(drive_set)  # Drive entries only
  log_changed = drive_set != prev_entries
  ```
  The comment rationale changed from "preserve manually-added entries" to "compare against
  what's currently on disk to detect real changes."

- **New admin endpoints added from main** (e.g., `POST /api/admin/clear-walks-log`),
  which were not part of the PR's feature work.

---

### 2. `integrations/gas/forecast_monitor.js`

**Resolution:** Took main's version entirely, discarding the PR's version.

The PR's `forecast_monitor.js` was a simple once-a-day check:
- Single `checkForecast()` function running at 2:30 AM
- Checked spreadsheet mtime via DriveApp; posted `/api/force-rebuild` if changed
- One `setupTrigger()` that registered a single daily trigger
- Header described: *"Triggers a weather + dashboard rebuild. Self-scheduled assignments are preserved."*

Main's version (what was merged) introduced a full debounced two-trigger model:
- `onForecastEdit(e)` — installable onEdit handler, schedules a one-shot debounced trigger
- `debouncedRerun()` — fires after `DEBOUNCE_MINUTES` (2), cleans up its own trigger, then calls shared logic
- `dailySafetyCheck()` — replaces `checkForecast()`, runs at 4 AM instead of 2:30 AM
- `triggerRerun_(reason)` — shared POST logic, logs with a `[reason]` prefix
- `setupTrigger()` now registers two triggers and removes the legacy `checkForecast` trigger
- Header incorrectly described the rebuild as *"weather → scheduler → dashboard rebuild"*
  (the scheduler is retired; this was corrected in the follow-up PR #9)

---

### 3. `docs/operations/guides/GCP_SETUP_GUIDE.md`

**Resolution:** `GPS_AUTH_TOKEN` was dropped; only `GAS_SECRET` was kept.

The PR's version of the guide included `GPS_AUTH_TOKEN` as secret #4:
```bash
# 4. GPS Auth Token (optional, but recommended)
echo -n "YOUR_GPS_BEARER_TOKEN" | gcloud secrets create GPS_AUTH_TOKEN \
  --data-file=-

# 5. GAS Secret (Google Apps Script trigger token)
echo -n "YOUR_GAS_SECRET_TOKEN" | gcloud secrets create GAS_SECRET \
```

After conflict resolution, `GPS_AUTH_TOKEN` was removed entirely:
```bash
# 4. GAS Secret (Google Apps Script trigger token)
echo -n "YOUR_GAS_SECRET_TOKEN" | gcloud secrets create GAS_SECRET \
```

Additional removals:
- `GPS_STALE_SECONDS=300` env var was removed from the `gcloud run deploy` command
- `GPS_AUTH_TOKEN=GPS_AUTH_TOKEN:latest` was removed from `--set-secrets`
- Secret count in the summary table changed from "Secrets (5x)" to "Secrets (4x)"
- `GAS_SECRET` description changed from referencing "existing deployment secrets or `.env` file"
  to "your Fly.io secrets or .env file"

> **Note:** The conflict resolution commit message described this as *"keep both GPS_AUTH_TOKEN + GAS_SECRET from PR"*, but the actual diff shows `GPS_AUTH_TOKEN` was removed. The commit message appears to be inaccurate for this file.

---

### 4. `pipelines/dashboard/build_dashboard.py`

**Resolution:** PR's template-placeholder/centralized-registry approach was kept, but main's
UI simplifications were applied on top.

Changes introduced by the conflict resolution (from main):

- **Route groups panel replaced with single toggle button:**
  ```html
  <!-- PR had a full checkbox panel: -->
  <div id="route-groups-panel">
    <div class="rgb-header">…</div>
    <div id="rgb-list"></div>
  </div>

  <!-- After resolution: -->
  <button id="route-groups-btn" title="Toggle route groups">■ Route Groups</button>
  ```
  The associated CSS (`#route-groups-panel`, `.rgb-header`, `.rgb-item`, `.rgb-dot`, `.rgb-lbl`,
  `#rgb-all-btn`) was replaced with simpler `.rgb-on` toggle-button styles.
  The JavaScript per-checkbox logic and `routeGroupVisible[]` array were removed;
  `routeGroupsVisible` became a single boolean toggle.

- **GPS CSS and JS removed:**
  The PR's version included live GPS marker CSS (`.gps-dot`, `.gps-dot-a`, `.gps-dot-b`,
  `.gps-dot-stale`, `@keyframes gps-pulse-a/b`) and related JS. These were removed as
  part of retiring GPS tracking from the active dashboard.
  Section header changed from `/* -- Drive / GPS header badges -- */` to `/* -- Drive header badges -- */`.

- **`#cal-body` / `#cal-grid` CSS fixes from main:**
  ```css
  /* PR had: */
  #cal-body { flex:1; overflow:auto; min-height:0 }
  #cal-grid  { … min-height:100% }

  /* After resolution: */
  #cal-body { flex:1; overflow-y:auto; overflow-x:hidden; min-height:0 }
  #cal-grid  { … min-height:100%; width:100% }
  ```

---

### 5. `pipelines/_retired/walk_scheduler.py`

**Resolution:** PR's stripped/retired version was kept. No notable differences from the PR's intent.

---

## Follow-up: PR #9 ("Followup/backpack status")

**PR:** [#9 Followup/backpack status](https://github.com/NASA-ENAACT-ORG/NASA-EnAACT-Field-Campaign-Monitor/pull/9)
**Merged:** 2026-05-03 at squash commit `15057c0`
**No conflict resolution was required.** PR #9 was opened from `origin/main` after PR #8 merged.

PR #9 addressed two items that were impacted by the conflict resolution above, plus added new functionality:

### Items Restored / Fixed After Conflict Resolution

1. **`integrations/gas/forecast_monitor.js` — scheduler reference removed from comment:**
   Main's version (taken in the PR #8 resolution) described the rebuild as
   *"weather → scheduler → dashboard rebuild"*, which was inaccurate since the scheduler
   is retired. PR #9 updated it to *"weather + dashboard/site rebuild without the scheduler."*

2. **`pipelines/dashboard/build_dashboard.py` — `Workbook.active` guard added:**
   The PR #8 branch called `_ws.iter_rows()` directly without a null check.
   PR #9 wrapped it in `if _ws is not None:` to resolve a Pylance `reportOptionalMemberAccess`
   diagnostic.

### New Functionality in PR #9

- `POST /api/backpack-status` endpoint for saving backpack holder/location state
- Backpack A/B status dropdowns in the calendar nav
- Persistence of backpack status in `schedule_output.json` under `backpack_status`
- Default holder inferred from most recent completed walk when no manual status exists
- Professor accounts and `ANG` included in Backpack A holder options
- `STAFF_COLLECTORS` and `LAST_RESORT_COLLECTORS`/`LAST_RESORT_BACKPACK` integrated into
  both `serve.py` and `build_dashboard.py`
