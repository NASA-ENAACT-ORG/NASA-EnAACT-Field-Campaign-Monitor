"""
serve.py â€” Server for the NYC Walk Scheduler dashboard.

Usage:
    python serve.py              # serves on 0.0.0.0:8765 (or $PORT)
    python serve.py --port 9000  # custom port

Endpoints:
  GET  /                        â†’ redirect to /dashboard.html
  GET  /<filename>              â†’ serve static file
  GET  /api/status              â†’ JSON with file mod times, Drive status
  POST /api/rerun               â†’ run scheduler (both backpacks) + rebuild dashboards, stream output
  POST /api/rerun/a             â†’ run scheduler for Backpack A (CCNY) only + rebuild dashboards
  POST /api/rerun/b             â†’ run scheduler for Backpack B (LaGCC) only + rebuild dashboards
  POST /api/rebuild             â†’ rebuild dashboards only, stream output
  POST /api/forecast-stability  â†’ run forecast stability analysis, stream output
  POST /api/drive/poll          â†’ manually trigger one Google Drive poll cycle
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# GCS support (optional â€” only initialized if GCS_BUCKET is set)
_gcs_client = None
_gcs_bucket = None

BASE_DIR              = Path(__file__).parent.resolve()
SCHEDULER             = BASE_DIR / "walk_scheduler.py"
BUILD_WEATHER         = BASE_DIR / "build_weather.py"
BUILD_DASHBOARD       = BASE_DIR / "build_dashboard.py"
BUILD_MAP             = BASE_DIR / "build_collector_map.py"
FORECAST_STABILITY    = BASE_DIR / "forecast_stability_analysis.py"
WALKS_LOG             = BASE_DIR / "Walks_Log.txt"
RECAL_LOG             = BASE_DIR / "Recal_Log.txt"
SEEN_FILES_PATH       = BASE_DIR / "drive_seen_files.json"
SCHEDULE_OUTPUT       = BASE_DIR / "schedule_output.json"

# â”€â”€ Drive config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


# Files tracked by /api/status
STATUS_FILES = {
    "schedule_output": BASE_DIR / "schedule_output.json",
    "walk_log":        WALKS_LOG,
    "dashboard":       BASE_DIR / "dashboard.html",
    "collector_map":   BASE_DIR / "collector_map.html",
}

# â”€â”€ Drive polling state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

DRIVE_FOLDER_ID       = os.environ.get("GOOGLE_DRIVE_WALKS_FOLDER_ID", "")
DRIVE_POLL_INTERVAL   = int(os.environ.get("DRIVE_POLL_INTERVAL", "300"))
_DRIVE_LOCK           = threading.Lock()
_drive_last_poll      = None
_drive_new_today      = 0
_scheduler_running    = threading.Lock()  # prevents concurrent scheduler runs


# â”€â”€ GCS helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _init_gcs():
    """Initialize GCS client if GCS_BUCKET is configured."""
    global _gcs_client, _gcs_bucket
    bucket_name = os.environ.get("GCS_BUCKET", "")
    if not bucket_name:
        print("[gcs] Disabled (GCS_BUCKET not set)")
        return False

    try:
        from google.cloud import storage
        _gcs_client = storage.Client()
        _gcs_bucket = _gcs_client.bucket(bucket_name)
        print(f"[gcs] Initialized â€” bucket: {bucket_name}")
        return True
    except Exception as e:
        print(f"[gcs] Warning: Failed to initialize GCS: {e}")
        return False


def _download_from_gcs(gcs_path: str, local_path: Path) -> bool:
    """Download a file from GCS to local path. Returns True if successful."""
    if not _gcs_bucket:
        return False
    try:
        blob = _gcs_bucket.blob(gcs_path)
        if blob.exists():
            blob.download_to_filename(str(local_path))
            print(f"[gcs] Downloaded: {gcs_path} â†’ {local_path}")
            return True
        else:
            print(f"[gcs] Blob not found in bucket: {gcs_path}")
    except Exception as e:
        print(f"[gcs] Download error ({gcs_path}): {e}")
    return False


def _upload_to_gcs(local_path: Path, gcs_path: str) -> bool:
    """Upload a file to GCS. Returns True if successful."""
    if not _gcs_bucket:
        return False
    try:
        blob = _gcs_bucket.blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        return True
    except Exception as e:
        print(f"[gcs] Upload error ({gcs_path}): {e}")
        return False


# â”€â”€ Shared Google Drive helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _get_drive_service():
    """Return an authenticated Drive v3 service, or None if not configured."""
    svc_json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not svc_json_str:
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build as gdrive_build
        svc_info = json.loads(svc_json_str)
        creds = service_account.Credentials.from_service_account_info(
            svc_info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        return gdrive_build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"[drive] Auth error: {e}")
        return None


def _drive_find_folder(service, parent_id: str, name: str) -> str | None:
    """Return the ID of a named subfolder, or None."""
    try:
        q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
             f" and '{parent_id}' in parents and trashed=false")
        r = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
        files = r.get("files", [])
        return files[0]["id"] if files else None
    except Exception as e:
        print(f"[drive] Find folder '{name}' error: {e}")
        return None


def _run_scheduler_and_rebuild():
    """Run walk_scheduler.py â†’ build_dashboard.py, upload results to GCS.
    Protected by _scheduler_running lock to prevent concurrent runs."""
    if not _scheduler_running.acquire(blocking=False):
        print("[forecast] Scheduler already running â€” skipping this trigger")
        return

    try:
        print("[forecast] â–¶ Running build_weather.py â€¦")
        r_weather = subprocess.run(
            [sys.executable, str(BUILD_WEATHER)],
            cwd=str(BASE_DIR),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        out_w = (r_weather.stdout or "") + (r_weather.stderr or "")
        if out_w:
            print(out_w[-2000:])
        print(f"[forecast] build_weather.py exit={r_weather.returncode}")

        # Upload weather JSON file to GCS
        for _wfname in ("weather.json",):
            _wfpath = BASE_DIR / _wfname
            if _wfpath.exists() and _gcs_bucket:
                _upload_to_gcs(_wfpath, _wfname)
        print("[forecast] Uploaded weather.json â†’ GCS")

        print("[forecast] â–¶ Running walk_scheduler.py â€¦")
        r = subprocess.run(
            [sys.executable, str(SCHEDULER)],
            cwd=str(BASE_DIR),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=360,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        # Print last 3000 chars so Cloud Run logs show what happened
        out = (r.stdout or "") + (r.stderr or "")
        if out:
            print(out[-3000:])
        print(f"[forecast] Scheduler exit={r.returncode}")

        if SCHEDULE_OUTPUT.exists() and _gcs_bucket:
            _upload_to_gcs(SCHEDULE_OUTPUT, "schedule_output.json")
            print("[forecast] Uploaded schedule_output.json â†’ GCS")

        print("[forecast] â–¶ Rebuilding dashboard â€¦")
        r2 = subprocess.run(
            [sys.executable, str(BUILD_DASHBOARD)],
            cwd=str(BASE_DIR),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        print(f"[forecast] Dashboard rebuild exit={r2.returncode}")
        if r2.stdout:
            print(r2.stdout[-500:])

        # Upload rebuilt HTML files to GCS so they persist across container restarts
        if _gcs_bucket:
            for html_name in ("dashboard.html", "availability_heatmap.html",
                              "schedule_map.html", "collector_map.html"):
                html_path = BASE_DIR / html_name
                if html_path.exists():
                    _upload_to_gcs(html_path, html_name)
            print("[forecast] Uploaded rebuilt HTML files â†’ GCS")
    except subprocess.TimeoutExpired:
        print("[forecast] Scheduler timed out (6 min limit)")
    except Exception as e:
        print(f"[forecast] Pipeline error: {e}")
    finally:
        _scheduler_running.release()


# â”€â”€ Forecast monitor â€” polls Drive for new forecast PDFs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stream_script(wfile, script: Path, label: str, extra_args: list = None) -> int:
    header = f"\nâ”€â”€ {label} â”€â”€\n"
    _write_chunk(wfile, header.encode())

    proc = subprocess.Popen(
        [sys.executable, str(script)] + (extra_args or []),
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"},
    )
    for line in proc.stdout:
        try:
            _write_chunk(wfile, line.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            proc.terminate()
            return -1
    proc.wait()
    footer = f"[{label} exited with code {proc.returncode}]\n"
    _write_chunk(wfile, footer.encode("utf-8"))
    return proc.returncode


def _write_chunk(wfile, data: bytes):
    wfile.write(f"{len(data):X}\r\n".encode())
    wfile.write(data)
    wfile.write(b"\r\n")
    wfile.flush()


# â”€â”€ Drive polling â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_WALK_LOG_RE = re.compile(
    r'^([ABX])_([A-Z]{2,4})_([A-Z]{2})_([A-Z]{2,3})_(\d{8})_(AM|MD|PM)$',
    re.IGNORECASE,
)


def _parse_filename_to_log_entry(name: str) -> str | None:
    """Return a Walks_Log.txt line if `name` (without extension) matches the walk format."""
    stem = Path(name).stem.upper()
    m = _WALK_LOG_RE.match(stem)
    if m:
        return stem
    return None


def _load_seen_ids() -> set:
    # Try GCS first so seen-IDs survive container restarts
    if _gcs_bucket:
        _download_from_gcs("drive_seen_files.json", SEEN_FILES_PATH)
    try:
        return set(json.loads(SEEN_FILES_PATH.read_text()))
    except Exception:
        return set()


def _save_seen_ids(ids: set):
    try:
        SEEN_FILES_PATH.write_text(json.dumps(sorted(ids), indent=2))
        if _gcs_bucket:
            _upload_to_gcs(SEEN_FILES_PATH, "drive_seen_files.json")
    except Exception as e:
        print(f"[drive] Warning: could not save seen IDs: {e}")


def _rebuild_walk_log(entries: list):
    """Rewrite Walks_Log.txt from scratch with all discovered walk entries.
    Never writes RECAL lines â€” those live exclusively in Recal_Log.txt."""
    content = "\n".join(entries) + "\n" if entries else ""
    WALKS_LOG.write_text(content, encoding="utf-8")
    print(f"[drive] Rebuilt Walks_Log.txt: {len(entries)} walk entries")
    if _gcs_bucket:
        _upload_to_gcs(WALKS_LOG, "Walks_Log.txt")


def _trigger_rebuild():
    subprocess.Popen(
        [sys.executable, str(BUILD_DASHBOARD)],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _run_drive_poll(source: str = "background"):
    """Poll walk-log Drive folder, rebuild Walks_Log.txt from ALL files every cycle.
    Returns (new_file_count, error_msg). new_file_count = IDs not seen in previous poll."""
    global _drive_last_poll, _drive_new_today
    print(f"[drive] Walk-log poll triggered by: {source}")

    if not DRIVE_FOLDER_ID:
        return 0, "GOOGLE_DRIVE_WALKS_FOLDER_ID not set"

    service = _get_drive_service()
    if service is None:
        return 0, "Drive auth failed (GOOGLE_SERVICE_ACCOUNT_JSON missing or invalid)"

    seen_ids = _load_seen_ids()
    all_entries: list = []    # every valid walk entry found in Drive this cycle
    current_ids: set = set()

    try:
        page_token = None
        while True:
            response = service.files().list(
                q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                pageSize=100,
            ).execute()
            files = response.get("files", [])

            for f in files:
                fid = f["id"]
                fname = f["name"]
                mime = f.get("mimeType", "")

                # Recurse one level into subfolders
                if mime == "application/vnd.google-apps.folder":
                    sub_resp = service.files().list(
                        q=f"'{fid}' in parents and trashed=false",
                        fields="files(id, name, mimeType)",
                        pageSize=200,
                    ).execute()
                    files.extend(sub_resp.get("files", []))

                current_ids.add(fid)
                entry = _parse_filename_to_log_entry(fname)
                if entry:
                    all_entries.append(entry.upper())

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    except Exception as e:
        return 0, f"Drive list error: {e}"

    # Deduplicate Drive entries
    drive_set: set = set()
    for e in all_entries:
        drive_set.add(e.upper())

    # Load existing log entries (manually added entries not in Drive must be preserved)
    existing_entries: set = set()
    if WALKS_LOG.exists():
        existing_entries = {
            l.strip().upper() for l in WALKS_LOG.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().upper().startswith("RECAL_")
        }

    # Union: Drive entries + any manually-added entries not found in Drive
    merged = sorted(drive_set | existing_entries)
    log_changed = set(merged) != existing_entries

    # Rebuild log from merged set (Drive state + preserved manual entries)
    _rebuild_walk_log(merged)

    new_count = len(current_ids - seen_ids)
    _save_seen_ids(current_ids)

    with _DRIVE_LOCK:
        _drive_last_poll = _now_iso()
        if new_count > 0:
            _drive_new_today += new_count

    if log_changed:
        print(f"[drive] Walk log changed â€” triggering dashboard rebuild")
        _trigger_rebuild()
    else:
        print(f"[drive] Walk log unchanged ({len(merged)} entries)")

    return new_count, None


def _drive_poll_thread():
    """Background daemon: polls Google Drive every DRIVE_POLL_INTERVAL seconds."""
    print(f"[drive] Polling thread started (interval: {DRIVE_POLL_INTERVAL}s, folder: {DRIVE_FOLDER_ID or 'NOT SET'})")
    while True:
        try:
            count, err = _run_drive_poll(source="background")
            if err:
                print(f"[drive] Poll skipped: {err}")
            elif count:
                print(f"[drive] Poll complete: {count} new file(s) detected")
        except Exception as e:
            print(f"[drive] Unexpected poll error: {e}")
        time.sleep(DRIVE_POLL_INTERVAL)


# â”€â”€ Request handler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # suppress per-request console noise
        pass

    # â”€â”€ GET â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")
        params = urllib.parse.parse_qs(parsed.query)

        # Redirect root to dashboard
        if not path or path == "index.html":
            self.send_response(302)
            self.send_header("Location", "/dashboard.html")
            self.end_headers()
            return

        # /api/status
        if path == "api/status":
            with _DRIVE_LOCK:
                drive_last = _drive_last_poll
                drive_today = _drive_new_today
            payload = {name: _mtime_iso(p) for name, p in STATUS_FILES.items()}
            payload["drive_last_poll"] = drive_last
            payload["drive_new_files_today"] = drive_today
            # GCS health
            payload["gcs_bucket"] = os.environ.get("GCS_BUCKET", "") or None
            payload["gcs_connected"] = _gcs_bucket is not None
            if _gcs_bucket:
                try:
                    blob = _gcs_bucket.blob("Walks_Log.txt")
                    payload["gcs_walks_log_exists"] = blob.exists()
                except Exception as e:
                    payload["gcs_walks_log_exists"] = f"error: {e}"
            else:
                payload["gcs_walks_log_exists"] = None
            body = json.dumps(payload, indent=2).encode()
            self._send(200, "application/json", body)
            return

        # Static files
        file_path = BASE_DIR / path
        if not file_path.exists() or not file_path.is_file():
            self._send(404, "text/plain", b"Not found")
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".json": "application/json",
            ".txt":  "text/plain; charset=utf-8",
            ".js":   "application/javascript",
            ".css":  "text/css",
            ".kml":  "application/vnd.google-earth.kml+xml",
            ".pdf":  "application/pdf",
        }
        ct = content_types.get(file_path.suffix.lower(), "application/octet-stream")
        self._send(200, ct, file_path.read_bytes())

    # â”€â”€ POST â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def do_POST(self):
        endpoint = self.path.split("?")[0]

        # Authenticate /api/drive/poll and /api/rerun with GAS_SECRET bearer token.
        # If GAS_SECRET is not set, these endpoints remain open (local dev compatible).
        # /api/rebuild is not gated â€” it is only triggered by the browser UI.
        _gas_secret = os.environ.get("GAS_SECRET", "")
        if _gas_secret and endpoint in ("/api/drive/poll", "/api/rerun"):
            auth_header = self.headers.get("Authorization", "")
            if auth_header != f"Bearer {_gas_secret}":
                self._send(401, "application/json",
                           json.dumps({"error": "unauthorized"}).encode())
                return

        if endpoint == "/api/rerun":
            self._stream_response(run_scheduler=True)
        elif endpoint == "/api/rerun/a":
            self._stream_response(run_scheduler=True, backpack="A")
        elif endpoint == "/api/rerun/b":
            self._stream_response(run_scheduler=True, backpack="B")
        elif endpoint == "/api/rebuild":
            self._stream_response(run_scheduler=False)
        elif endpoint == "/api/forecast-stability":
            self._stream_forecast_stability()
        elif endpoint == "/api/confirm":
            # PIN-verify endpoint used by the admin auth modal
            _sched_pin = os.environ.get("SCHEDULER_PIN", "")
            try:
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length) if length else b"{}"
                payload = json.loads(body_bytes)
            except Exception:
                self._send(400, "application/json",
                           json.dumps({"error": "bad request"}).encode())
                return
            if _sched_pin and payload.get("pin", "") != _sched_pin:
                self._send(403, "application/json",
                           json.dumps({"error": "wrong pin"}).encode())
                return
            self._send(200, "application/json", json.dumps({"ok": True}).encode())

        elif endpoint == "/api/drive/poll":
            count, err = _run_drive_poll(source="gas")
            if err:
                body = json.dumps({"status": "error", "message": err}).encode()
                self._send(500, "application/json", body)
            else:
                body = json.dumps({"status": "ok", "new_files": count}).encode()
                self._send(200, "application/json", body)
        elif endpoint == "/api/force-rebuild":
            # Force immediate rebuild: build weather, run scheduler, rebuild dashboard
            # Protected by SCHEDULER_PIN
            _sched_pin = os.environ.get("SCHEDULER_PIN", "")
            try:
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length) if length else b"{}"
                payload = json.loads(body_bytes)
            except Exception:
                self._send(400, "application/json",
                           json.dumps({"error": "bad request"}).encode())
                return
            if _sched_pin and payload.get("pin", "") != _sched_pin:
                self._send(403, "application/json",
                           json.dumps({"error": "wrong pin"}).encode())
                return
            print("[API] Force rebuild triggered")
            t = threading.Thread(target=_run_scheduler_and_rebuild, daemon=True)
            t.start()
            self._send(200, "application/json",
                       json.dumps({"status": "ok", "message": "Rebuild started â€” check back in 30 seconds"}).encode())
        else:
            self._send(404, "text/plain", b"Unknown endpoint")

    def _stream_response(self, run_scheduler: bool, backpack: str = None):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            if run_scheduler:
                extra = ["--backpack", backpack] if backpack else []
                _stream_script(self.wfile, SCHEDULER, "walk_scheduler.py", extra)

            _stream_script(self.wfile, BUILD_DASHBOARD, "build_dashboard.py")
            _stream_script(self.wfile, BUILD_MAP,       "build_collector_map.py")

            done = "\n[All done â€” reload the page to see updated dashboards]\n"
            _write_chunk(self.wfile, done.encode("utf-8"))
        except Exception as e:
            err = f"\n[Server error: {e}]\n".encode("utf-8")
            _write_chunk(self.wfile, err)
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass

    def _stream_forecast_stability(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            _stream_script(self.wfile, FORECAST_STABILITY, "forecast_stability_analysis.py")
            _write_chunk(self.wfile, b"\n[Analysis complete]\n")
        except Exception as e:
            _write_chunk(self.wfile, f"\n[Server error: {e}]\n".encode("utf-8"))
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _send(self, code, ct, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)


# â”€â”€ Entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _restore_gcs_state():
    """Download persistent state from GCS. Called both at container start
    (before build_dashboard.py via --restore-only) and again when serve starts."""
    _init_gcs()
    if not _gcs_bucket:
        return
    # Walks_Log.txt â€” always refresh from GCS (may be newer than baked copy)
    _download_from_gcs("Walks_Log.txt", WALKS_LOG)
    if not WALKS_LOG.exists():
        print("[gcs-restore] Walks_Log.txt not available from GCS â€” starting empty")
        WALKS_LOG.write_text("", encoding="utf-8")
    # schedule_output.json â€” use the baked copy from the Docker image.
    # The forecast monitor will regenerate and upload to GCS when new forecasts arrive.
    print("[gcs-restore] Using baked schedule_output.json from Docker image")
    # Weather JSON file
    for _wfname in ("weather.json",):
        _download_from_gcs(_wfname, BASE_DIR / _wfname)
    print("[gcs-restore] State restored from GCS")


def main():
    parser = argparse.ArgumentParser(description="Walk Scheduler server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--restore-only", action="store_true",
                        help="Download GCS state then exit (run before build_dashboard.py)")
    args = parser.parse_args()

    if args.restore_only:
        _restore_gcs_state()
        return

    # Initialize GCS (optional)
    _init_gcs()

    # â”€â”€ Restore persistent state from GCS on startup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if _gcs_bucket:
        # Walks_Log.txt â€” always pull from GCS (never rely on image-baked copy)
        _download_from_gcs("Walks_Log.txt", WALKS_LOG)

        # schedule_output.json is already

        print("[startup] GCS state restored")

    # Ensure Walks_Log.txt always exists (empty fallback if GCS unavailable)
    if not WALKS_LOG.exists():
        print("[startup] Walks_Log.txt not available â€” starting with empty log")
        WALKS_LOG.write_text("", encoding="utf-8")

    # â”€â”€ Start Drive walk-log polling thread â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if DRIVE_POLL_INTERVAL > 0:
        t = threading.Thread(target=_drive_poll_thread, daemon=True)
        t.start()
        print(f"  Walk-log poll : active (every {DRIVE_POLL_INTERVAL}s)")
    else:
        print(f"  Walk-log poll : DISABLED â€” relying on GAS push triggers")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"")
    print(f"  NYC Walk Scheduler â€” server")
    print(f"  Dashboard  : {url}")
    print(f"  Rerun API  : POST {url}/api/rerun")
    print(f"  Rebuild API: POST {url}/api/rebuild")
    print(f"  Status API : GET  {url}/api/status")
    print(f"  Drive Poll : POST {url}/api/drive/poll")
    print(f"  Drive folder: {DRIVE_FOLDER_ID or '(not configured)'}")
    print(f"")
    print(f"  Press Ctrl+C to stop.")
    print(f"")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()


