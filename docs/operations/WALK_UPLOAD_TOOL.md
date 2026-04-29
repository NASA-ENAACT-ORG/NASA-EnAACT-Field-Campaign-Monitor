# Walk Data Upload Tool

## What It Does

The **Upload Data** button in the dashboard top bar opens a modal that lets field workers submit a completed walk directly from the browser — no Google Drive file naming conventions required. On submission the server:

1. Validates the walk code against the standard regex
2. **Stages all files into the GCS holding bucket** (`upload_holding_bucket`) and returns 200 to the browser as soon as that staging succeeds — so the user-visible response is decoupled from Drive flakiness
3. A background **mover thread** asynchronously moves the staged submission into Drive (creating the borough → route → walk folder hierarchy and uploading each file with idempotent retries)
4. On Drive success the holding-bucket payload is archived and `_run_drive_poll("upload-mover")` rebuilds `Walks_Log.txt` and triggers a dashboard rebuild
5. On terminal Drive failure the payload is moved to `failed/` and a banner is rendered on the dashboard

If `UPLOAD_HOLDING_BUCKET` is unset, or holding-bucket staging itself fails, the handler **falls back** to the legacy direct-Drive write path so local dev and degraded deployments stay functional.

---

## Form Fields

| Field | Required | Notes |
|---|---|---|
| Date | Yes | YYYY-MM-DD picker, sent as YYYYMMDD |
| Backpack | Yes | A or B |
| Collector | Yes | 3-letter code (SOT, AYA, ALX, TAH, JAM, JEN, SCT, TER, ANG, NRS, PRA, NAT) |
| Borough | Yes | MN / BX / BK / QN |
| Route | Yes | 2-letter code, filtered by borough selection |
| Time of Day | Yes | AM / MD / PM |
| Start / Walk / End Time | Yes | Each can be an image upload or typed HH:MM:SS UTC |
| POM / POP / PAM | Optional | Individually toggled checkboxes; any file type, multiple files |
| GPX Track | Yes | .gpx, .kml, .kmz |
| Walk Notes | Optional | Free text, saved as a .txt file |

---

## Drive Folder Structure

```
GOOGLE_DRIVE_WALKS_FOLDER_ID/
└── {BOROUGH}/               ← matched by prefix (e.g. "MN" matches "MN - Manhattan")
    └── {ROUTE}/             ← matched by prefix (e.g. "HT" matches "HT - Harlem")
        └── {WALK_CODE}/     ← e.g. A_SOT_MN_HT_20260314_AM
            ├── {WALK_CODE}_POM/
            ├── {WALK_CODE}_POP/
            ├── {WALK_CODE}_PAM/
            ├── {WALK_CODE}_TIMES/      ← start/walk/end time images
            ├── {WALK_CODE}_GPX.gpx     ← (or .kml / .kmz)
            └── {WALK_CODE}_Notes.txt
```

Borough and route folders are **matched by prefix** — the server lists all subfolders and picks the first whose name starts with the 2-letter code. If no match is found, a new folder with the short code is created as a fallback.

---

## Upload Buffer (GCS Holding Bucket)

Uploads are staged in a GCS holding bucket first, then asynchronously moved to Drive by a background worker. This decouples the browser response from Drive flakiness — a 200 means files are durably stored in GCS, not necessarily yet in Drive.

### Flow

```
Browser ──POST /api/upload-walk──▶ server ──stage──▶ gs://upload_holding_bucket/pending/...
                                       │
                                       └──200 returned (status: "staged")

[mover thread, every 15s]
  for each pending submission:
    claim → upload to Drive (resumable, idempotent) → archive to done/
    on permanent failure → move to failed/ + write upload_failures.json
```

### GCS key layout (`upload_holding_bucket`)

```
pending/{walk_code}/{submission_id}/
    manifest.json                # status: staging → ready → processing
    files/{field}/{nnn}_{safe_filename}
done/{walk_code}/{submission_id}/manifest.json   # success archive (manifest only)
failed/{walk_code}/{submission_id}/              # full payload kept for retry
```

`submission_id` is `YYYYMMDDTHHMMSSZ_<6-char-rand>`. Filenames are sanitized to `[A-Za-z0-9._-]`; the original is preserved in the manifest.

### Idempotency

Each per-file Drive upload is recorded in `manifest.attempts[*].uploaded_drive_ids` so retries skip files already in Drive. The mover also probes Drive by name before uploading, so re-submissions and pre-existing folders never produce duplicates.

### Required IAM

The existing service account (set via `GOOGLE_SERVICE_ACCOUNT_JSON`) needs **Storage Object Admin** on the holding bucket:

```
gcloud storage buckets add-iam-policy-binding gs://upload_holding_bucket \
  --member=serviceAccount:<client_email> \
  --role=roles/storage.objectAdmin
```

### Retention

- `done/` retained for `UPLOAD_DONE_RETENTION_DAYS` (default 7).
- `failed/` retained for `UPLOAD_FAILED_RETENTION_DAYS` (default 30).
- A GCS Object Lifecycle rule auto-deleting anything > 90 days is recommended as a belt-and-suspenders safety net.

### Recovery

- **Re-stage a failed submission**: `gsutil mv gs://upload_holding_bucket/failed/{walk_code}/{sid} gs://upload_holding_bucket/pending/{walk_code}/{sid}` — the mover picks it up on the next cycle.
- **Inspect a stuck submission**: `gsutil cat gs://upload_holding_bucket/pending/.../manifest.json | jq .attempts`
- **Force a Drive poll**: `POST /api/drive/poll`

---

## Walk Code Format

`{BACKPACK}_{COLLECTOR}_{BOROUGH}_{ROUTE}_{YYYYMMDD}_{TOD}`

Example: `A_SOT_MN_HT_20260314_AM`

Validated against: `^[ABX]_[A-Z]{2,4}_[A-Z]{2}_[A-Z]{2,3}_\d{8}_(AM|MD|PM)$`

---

## Key Files

| File | Purpose |
|---|---|
| `pipelines/dashboard/build_dashboard.py` | Generates the dashboard HTML including the upload modal UI |
| `app/server/serve.py` | HTTP server — `POST /api/upload-walk` endpoint |
| `app/server/serve_upload_test.py` | Minimal local test server (no Drive/GCS needed) |

### Relevant functions in `serve.py`

| Function | What it does |
|---|---|
| `_parse_multipart(headers, body)` | Parses multipart/form-data using `cgi.FieldStorage`; returns `(fields, files)` |
| `_get_drive_write_service()` | Authenticates Drive with full read/write scope |
| `_drive_find_folder_by_prefix(svc, parent_id, prefix)` | Finds a subfolder whose name starts with `prefix` |
| `_drive_create_or_get_folder(svc, parent_id, name)` | Gets or creates a subfolder by exact name |
| `_drive_upload_file(svc, folder_id, filename, data)` | Uploads bytes to a Drive folder; raises on failure |
| `_run_drive_poll("upload")` | Scans Drive, rebuilds `Walks_Log.txt`, triggers dashboard rebuild |

---

## Environment Variables Required

| Variable | Purpose |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account credentials (JSON string) |
| `GOOGLE_DRIVE_WALKS_FOLDER_ID` | ID of the top-level Walks folder in Drive |
| `GCS_BUCKET` | GCS bucket name for persistent state (optional locally) |
| `UPLOAD_HOLDING_BUCKET` | GCS bucket name used as the upload buffer. Unset → holding bucket disabled, direct-Drive fallback. Use `local:./tmp/holding` to test the new flow against a local-filesystem backend. |
| `UPLOAD_MOVER_POLL_INTERVAL` | Seconds between mover passes (default 15) |
| `UPLOAD_MAX_ATTEMPTS` | Max attempts before a submission is moved to `failed/` (default 6) |
| `UPLOAD_STAGING_TTL_MIN` | Minutes after which orphaned `status="staging"` submissions are reaped (default 30) |
| `UPLOAD_DONE_RETENTION_DAYS` | Retention for `done/` (default 7) |
| `UPLOAD_FAILED_RETENTION_DAYS` | Retention for `failed/` (default 30) |
| `SCHEDULER_PIN` | PIN for gated endpoints (optional locally) |

**Drive permission:** The service account must have **Editor** access on the Walks folder. Set this once in Drive's sharing UI using the `client_email` from the service account JSON.

---

## Local Testing (No Credentials)

```bash
# Rebuild the dashboard HTML first
python pipelines/dashboard/build_dashboard.py

# Start the test server
python app/server/serve_upload_test.py

# Open in browser
http://localhost:8765/dashboard.html
```

The test server stubs `/api/upload-walk` — it logs the walk code and all file names/sizes to the terminal without touching Drive or GCS.

---

## Troubleshooting

### `Error: bad request: ...`
Multipart parsing failed. Check that:
- The browser is sending `Content-Type: multipart/form-data` (it always should for FormData)
- `Content-Length` header is present and correct

### `Error: missing fields: backpack, ...`
One or more required dropdowns were not filled in before submitting.

### `Error: invalid walk code: ...`
The assembled code doesn't match the expected regex. Check that all dropdown values are correct codes (no full names, no spaces).

### Folder created in wrong location / new folder created instead of matching existing one
The prefix match failed. Check what the actual borough/route folder names are in Drive — the server matches on the first characters, so `"MN"` should match any folder starting with `"MN"`. If the folder is named something unexpected (e.g. `"Manhattan"` with no code prefix), it won't match and a fallback folder is created.

### Files not appearing in Drive (folders created but empty)
Check Cloud Run logs for `[upload] file '...' field='...' size=N` lines:
- If `size=0`: the browser sent an empty file — the user likely selected a file then deselected it, or the file was 0 bytes
- If the line doesn't appear at all: the multipart parse didn't detect the file — check that the checkbox was ticked and a file was actually dropped/selected
- If size is correct but no `[drive] Uploaded '...'` line follows: `_drive_upload_file` raised an exception — look for the error message immediately after

### Walk not appearing in `Walks_Log.txt` / dashboard not updating
The `_run_drive_poll("upload-mover")` call runs in a background thread after the mover archives a successful submission. It can take 15–60 seconds end-to-end (up to one mover-loop interval plus Drive write time). If it still doesn't appear:
- Check `gsutil ls gs://upload_holding_bucket/pending/` to see if the submission is stuck staged
- Look for `[upload-mover]` lines in server logs
- Confirm the walk folder was created in Drive (if not, the mover is failing — check its attempt logs in the manifest)
- Check that `GOOGLE_DRIVE_WALKS_FOLDER_ID` is set correctly
- Manually trigger a poll via `POST /api/drive/poll`

### 200 returned but a red banner appears on the dashboard
The mover exhausted retries and moved a submission to `failed/`. Inspect the manifest:
```
gsutil cat gs://upload_holding_bucket/failed/<walk_code>/<sid>/manifest.json | jq .attempts
```
After fixing the root cause, replay with:
```
gsutil mv gs://upload_holding_bucket/failed/<walk_code>/<sid> gs://upload_holding_bucket/pending/<walk_code>/<sid>
```

### Server logs `[upload] Holding bucket staging failed: ... — falling back to direct Drive`
The holding bucket is configured but a staging operation failed. Common causes:
- Service account missing `roles/storage.objectAdmin` on the holding bucket
- Bucket name typo in `UPLOAD_HOLDING_BUCKET`
- Network / IAM transient issue
The handler falls back to direct-Drive in this case so the upload still succeeds (degraded mode).
