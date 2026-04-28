"""
serve.py " Server for the NYC Walk Scheduler dashboard.

Usage:
    python serve.py              # serves on 0.0.0.0:8765 (or $PORT)
    python serve.py --port 9000  # custom port

Endpoints:
  GET  /                        ' redirect to /dashboard.html
  GET  /<filename>              ' serve static file
  GET  /api/status              ' JSON with file mod times, Drive status
  POST /api/rerun               ' run scheduler (both backpacks) + rebuild dashboards, stream output
  POST /api/rerun/a             ' run scheduler for Backpack A (CCNY) only + rebuild dashboards
  POST /api/rerun/b             ' run scheduler for Backpack B (LaGCC) only + rebuild dashboards
  POST /api/rebuild             ' rebuild dashboards only, stream output
  POST /api/drive/poll          ' manually trigger one Google Drive poll cycle
"""

import argparse
import json
import mimetypes
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

# Add repo root to sys.path so shared package is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.paths import (
    REPO_ROOT,
    SITE_DIR,
    WALK_SCHEDULER,
    BUILD_WEATHER,
    BUILD_DASHBOARD,
    BUILD_COLLECTOR_MAP,
    WALKS_LOG,
    RECAL_LOG,
    DRIVE_SEEN_FILES,
    SCHEDULE_OUTPUT_JSON,
    DASHBOARD_HTML,
    COLLECTOR_MAP_HTML,
    AVAILABILITY_HEATMAP_HTML,
    SCHEDULE_MAP_HTML,
    WEATHER_JSON,
)
from shared import gcs

BASE_DIR           = REPO_ROOT
SCHEDULER          = WALK_SCHEDULER
BUILD_MAP          = BUILD_COLLECTOR_MAP
FORECAST_STABILITY = REPO_ROOT / "scripts" / "ops" / "forecast_stability_analysis.py"
SEEN_FILES_PATH    = DRIVE_SEEN_FILES
SCHEDULE_OUTPUT    = SCHEDULE_OUTPUT_JSON

# "" Drive config """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""


# Files tracked by /api/status
STATUS_FILES = {
    "schedule_output": SCHEDULE_OUTPUT,
    "walk_log":        WALKS_LOG,
    "dashboard":       DASHBOARD_HTML,
    "collector_map":   COLLECTOR_MAP_HTML,
}

# "" Drive polling state """"""""""""""""""""""""""""""""""""""""""""""""""""""""

DRIVE_FOLDER_ID       = os.environ.get("GOOGLE_DRIVE_WALKS_FOLDER_ID", "")
DRIVE_POLL_INTERVAL   = int(os.environ.get("DRIVE_POLL_INTERVAL", "300"))
_DRIVE_LOCK           = threading.Lock()
_drive_last_poll      = None
_drive_new_today      = 0
_scheduler_running    = threading.Lock()  # prevents concurrent scheduler runs


# "" GCS helpers """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

_gcs_client = None
_gcs_bucket = None


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
        print(f"[gcs] Initialized-- bucket: {bucket_name}")
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
            print(f"[gcs] Downloaded: {gcs_path} ' {local_path}")
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


# "" Shared Google Drive helpers """"""""""""""""""""""""""""""""""""""""""""""""

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
    """Return the ID of a named subfolder (exact match), or None."""
    try:
        q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
             f" and '{parent_id}' in parents and trashed=false")
        r = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
        files = r.get("files", [])
        return files[0]["id"] if files else None
    except Exception as e:
        print(f"[drive] Find folder '{name}' error: {e}")
        return None


def _rebuild_dashboard_and_upload():
    """Run build_dashboard.py and upload all HTML + weather artefacts to GCS."""
    r = subprocess.run(
        [sys.executable, str(BUILD_DASHBOARD)],
        cwd=str(REPO_ROOT),
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=120,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    print(f"[forecast] Dashboard rebuild exit={r.returncode}")
    if r.stdout:
        print(r.stdout[-500:])
    if _gcs_bucket:
        for html_path, blob_name in (
            (DASHBOARD_HTML,            "dashboard.html"),
            (AVAILABILITY_HEATMAP_HTML, "availability_heatmap.html"),
            (SCHEDULE_MAP_HTML,         "schedule_map.html"),
            (COLLECTOR_MAP_HTML,        "collector_map.html"),
        ):
            if html_path.exists():
                _upload_to_gcs(html_path, blob_name)
        print("[forecast] Uploaded rebuilt HTML files -> GCS")


def _run_scheduler_and_rebuild():
    """Run build_weather.py -> walk_scheduler.py -> build_dashboard.py, upload to GCS.

    The dashboard is rebuilt immediately after build_weather.py so that the site
    always reflects the latest weather data, even when the scheduler fails.
    Protected by _scheduler_running lock to prevent concurrent runs.
    """
    if not _scheduler_running.acquire(blocking=False):
        print("[forecast] Scheduler already running -- skipping this trigger")
        return

    try:
        print("[forecast] Running build_weather.py ...")
        r_weather = subprocess.run(
            [sys.executable, str(BUILD_WEATHER)],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        out_w = (r_weather.stdout or "") + (r_weather.stderr or "")
        if out_w:
            print(out_w[-2000:])
        print(f"[forecast] build_weather.py exit={r_weather.returncode}")

        if r_weather.returncode != 0:
            print("[forecast] build_weather.py failed -- aborting pipeline")
            return

        # Upload fresh weather.json to GCS right away.
        if WEATHER_JSON.exists() and _gcs_bucket:
            _upload_to_gcs(WEATHER_JSON, "weather.json")
        print("[forecast] Uploaded weather.json -> GCS")

        # Rebuild the dashboard immediately so the site shows up-to-date weather
        # regardless of whether the scheduler succeeds below.
        print("[forecast] Rebuilding dashboard (post-weather) ...")
        _rebuild_dashboard_and_upload()

        print("[forecast] Running walk_scheduler.py ...")
        r = subprocess.run(
            [sys.executable, str(SCHEDULER)],
            cwd=str(REPO_ROOT),
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

        if r.returncode == 0:
            if SCHEDULE_OUTPUT.exists() and _gcs_bucket:
                _upload_to_gcs(SCHEDULE_OUTPUT, "schedule_output.json")
                print("[forecast] Uploaded schedule_output.json -> GCS")
            # Rebuild once more to bake in the freshly generated schedule.
            print("[forecast] Rebuilding dashboard (post-scheduler) ...")
            _rebuild_dashboard_and_upload()
        else:
            print("[forecast] walk_scheduler.py failed -- dashboard reflects latest weather only")

    except subprocess.TimeoutExpired:
        print("[forecast] Scheduler timed out (6 min limit)")
    except Exception as e:
        print(f"[forecast] Pipeline error: {e}")
    finally:
        _scheduler_running.release()


# "" Forecast monitor " polls Drive for new forecast PDFs """"""""""""""""""""""

def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stream_script(wfile, script: Path, label: str, extra_args: list = None) -> int:
    header = f"\n"" {label} ""\n"
    _write_chunk(wfile, header.encode())

    proc = subprocess.Popen(
        [sys.executable, str(script)] + (extra_args or []),
        cwd=str(REPO_ROOT),
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


# "" Drive polling """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

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
    Never writes RECAL lines " those live exclusively in Recal_Log.txt."""
    content = "\n".join(entries) + "\n" if entries else ""
    WALKS_LOG.write_text(content, encoding="utf-8")
    print(f"[drive] Rebuilt Walks_Log.txt: {len(entries)} walk entries")
    if _gcs_bucket:
        _upload_to_gcs(WALKS_LOG, "Walks_Log.txt")


def _trigger_rebuild():
    subprocess.Popen(
        [sys.executable, str(BUILD_DASHBOARD)],
        cwd=str(REPO_ROOT),
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
        print(f"[drive] Walk log changed -- triggering dashboard rebuild")
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


# "" Request handler """""""""""""""""""""""""""""""""""""""""""""""""""""""""""

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # suppress per-request console noise
        pass

    # "" GET """"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
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

        # Static files -- served from the generated site output directory
        file_path = SITE_DIR / path
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

    # "" POST """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
    def do_POST(self):
        endpoint = self.path.split("?")[0]

        # Authenticate /api/drive/poll and /api/rerun with GAS_SECRET bearer token.
        # If GAS_SECRET is not set, these endpoints remain open (local dev compatible).
        # /api/rebuild is not gated " it is only triggered by the browser UI.
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
            # Accepts either a valid GAS_SECRET bearer token (automated triggers)
            # or a valid SCHEDULER_PIN in the request body (browser UI).
            _sched_pin  = os.environ.get("SCHEDULER_PIN", "")
            _gas_secret = os.environ.get("GAS_SECRET", "")
            auth_header = self.headers.get("Authorization", "")
            gas_authed  = bool(_gas_secret and auth_header == f"Bearer {_gas_secret}")
            if not gas_authed:
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
                       json.dumps({"status": "ok", "message": "Rebuild started -- check back in 30 seconds"}).encode())
        elif endpoint == "/api/record-calibration":
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
            date_str = payload.get("date", "")
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                self._send(400, "application/json",
                           json.dumps({"error": "invalid date"}).encode())
                return
            try:
                # Pull the latest Recal_Log from GCS so the append is based on
                # the authoritative bucket copy, not whatever the ephemeral
                # container happens to have on disk.
                _download_from_gcs("Recal_Log.txt", RECAL_LOG)
                RECAL_LOG.parent.mkdir(parents=True, exist_ok=True)
                y, m, d = date_str.split("-")
                entry = f"RECAL_{m}_{d}_{y}\n"
                with open(RECAL_LOG, "a") as fh:
                    fh.write(entry)
                if _gcs_bucket:
                    _upload_to_gcs(RECAL_LOG, "Recal_Log.txt")
            except Exception as exc:
                self._send(500, "application/json",
                           json.dumps({"error": str(exc)}).encode())
                return
            self._send(200, "application/json", json.dumps({"ok": True}).encode())

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

            done = "\n[All done -- reload the page to see updated dashboards]\n"
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


# "" Entry point """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

def _restore_gcs_state():
    """Download persistent state from GCS. Called both at container start
    (before build_dashboard.py via --restore-only) and again when serve starts."""
    _init_gcs()
    if not _gcs_bucket:
        return
    # Ensure output dirs exist before writing into them
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    WALKS_LOG.parent.mkdir(parents=True, exist_ok=True)

    # Walks_Log.txt -- always refresh from GCS (may be newer than baked copy)
    _download_from_gcs("Walks_Log.txt", WALKS_LOG)
    if not WALKS_LOG.exists():
        print("[gcs-restore] Walks_Log.txt not available from GCS -- starting empty")
        WALKS_LOG.write_text("", encoding="utf-8")

    # Weather and schedule JSON
    _download_from_gcs("weather.json", WEATHER_JSON)
    _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)

    # Recal_Log.txt -- calibration entries survive across redeploys only via GCS
    _download_from_gcs("Recal_Log.txt", RECAL_LOG)

    # Pre-built HTML -- restore so server can serve immediately even if rebuild fails
    for _blob, _local in (
        ("dashboard.html",            DASHBOARD_HTML),
        ("collector_map.html",        COLLECTOR_MAP_HTML),
        ("availability_heatmap.html", AVAILABILITY_HEATMAP_HTML),
        ("schedule_map.html",         SCHEDULE_MAP_HTML),
    ):
        _download_from_gcs(_blob, _local)

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

    # "" Restore persistent state from GCS on startup """""""""""""""""""""""""""
    if _gcs_bucket:
        # Walks_Log.txt " always pull from GCS (never rely on image-baked copy)
        _download_from_gcs("Walks_Log.txt", WALKS_LOG)
        # Recal_Log.txt " calibration history survives redeploys only via GCS
        _download_from_gcs("Recal_Log.txt", RECAL_LOG)

        # schedule_output.json is already

        print("[startup] GCS state restored")

    # Ensure Walks_Log.txt always exists (empty fallback if GCS unavailable)
    if not WALKS_LOG.exists():
        print("[startup] Walks_Log.txt not available -- starting with empty log")
        WALKS_LOG.write_text("", encoding="utf-8")

    # "" Start Drive walk-log polling thread """"""""""""""""""""""""""""""""""""
    if DRIVE_POLL_INTERVAL > 0:
        t = threading.Thread(target=_drive_poll_thread, daemon=True)
        t.start()
        print(f"  Walk-log poll : active (every {DRIVE_POLL_INTERVAL}s)")
    else:
        print(f"  Walk-log poll : DISABLED -- relying on GAS push triggers")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"")
    print(f"  NYC Walk Scheduler -- server")
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


