"""
serve.py — Server for the NYC Walk Scheduler dashboard.

Usage:
    python serve.py              # serves on 0.0.0.0:8765 (or $PORT)
    python serve.py --port 9000  # custom port

Endpoints:
  GET  /                        → redirect to /dashboard.html
  GET  /<filename>              → serve static file
  GET  /api/status              → JSON with file mod times, GPS positions, Drive status
  POST /api/rerun               → run scheduler (both backpacks) + rebuild dashboards, stream output
  POST /api/rerun/a             → run scheduler for Backpack A (CCNY) only + rebuild dashboards
  POST /api/rerun/b             → run scheduler for Backpack B (LaGCC) only + rebuild dashboards
  POST /api/rebuild             → rebuild dashboards only, stream output
  POST /api/forecast-stability  → run forecast stability analysis, stream output
  GET  /api/gps                 → Traccar Client GPS push (id, lat, lon, speed, batt, token)
  GET  /api/gps/status          → current positions for both backpacks as JSON
  GET  /api/gps/trail           → recent position history for one backpack (?id=BP_A)
  POST /api/drive/poll          → manually trigger one Google Drive poll cycle
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
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# GCS support (optional — only initialized if GCS_BUCKET is set)
_gcs_client = None
_gcs_bucket = None

BASE_DIR              = Path(__file__).parent.resolve()
SCHEDULER             = BASE_DIR / "walk_scheduler.py"
BUILD_DASHBOARD       = BASE_DIR / "build_dashboard.py"
BUILD_MAP             = BASE_DIR / "build_collector_map.py"
FORECAST_STABILITY    = BASE_DIR / "forecast_stability_analysis.py"
WALKS_LOG             = BASE_DIR / "Walks_Log.txt"
SEEN_FILES_PATH       = BASE_DIR / "drive_seen_files.json"
CONFIRMATIONS_FILE    = BASE_DIR / "schedule_confirmations.json"
SCHEDULE_OUTPUT       = BASE_DIR / "schedule_output.json"
FORECAST_DIR          = BASE_DIR / "Forecast"

# ── Drive config ───────────────────────────────────────────────────────────────
# DRIVE_FORECASTS_FOLDER_ID — Google Drive folder where forecast PDFs are uploaded
DRIVE_FORECASTS_FOLDER_ID   = os.environ.get("DRIVE_FORECASTS_FOLDER_ID", "")


# ── Confirmation helpers ───────────────────────────────────────────────────────
import threading
_CONFIRM_LOCK = threading.Lock()

def _load_confirmations() -> dict:
    with _CONFIRM_LOCK:
        if not CONFIRMATIONS_FILE.exists():
            return {}
        try:
            return json.loads(CONFIRMATIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

def _save_confirmations(data: dict) -> None:
    with _CONFIRM_LOCK:
        CONFIRMATIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Persist to GCS so confirmations survive container restarts
    if _gcs_bucket:
        _upload_to_gcs(CONFIRMATIONS_FILE, "schedule_confirmations.json")

# Files tracked by /api/status
STATUS_FILES = {
    "schedule_output": BASE_DIR / "schedule_output.json",
    "walk_log":        WALKS_LOG,
    "dashboard":       BASE_DIR / "dashboard.html",
    "collector_map":   BASE_DIR / "collector_map.html",
}

# ── GPS state ─────────────────────────────────────────────────────────────────

GPS_BACKPACK_IDS = ["BP_A", "BP_B"]
GPS_STALE_SECONDS = int(os.environ.get("GPS_STALE_SECONDS", "300"))
GPS_AUTH_TOKEN = os.environ.get("GPS_AUTH_TOKEN", "")   # empty = no auth required

_GPS_LOCK = threading.Lock()
_GPS_POSITIONS = {
    bp: {"lat": None, "lon": None, "ts": None, "speed": None, "batt": None, "stale": True}
    for bp in GPS_BACKPACK_IDS
}
_GPS_TRAILS = {bp: deque(maxlen=200) for bp in GPS_BACKPACK_IDS}

# ── Drive polling state ────────────────────────────────────────────────────────

DRIVE_FOLDER_ID       = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
DRIVE_POLL_INTERVAL   = int(os.environ.get("DRIVE_POLL_INTERVAL", "60"))
FORECAST_POLL_INTERVAL = int(os.environ.get("FORECAST_POLL_INTERVAL", "300"))  # 5 min default
_DRIVE_LOCK           = threading.Lock()
_drive_last_poll      = None
_drive_new_today      = 0
_forecast_last_poll   = None
_scheduler_running    = threading.Lock()  # prevents concurrent scheduler runs


# ── GCS helpers ───────────────────────────────────────────────────────────────

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
        print(f"[gcs] Initialized — bucket: {bucket_name}")
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
            print(f"[gcs] Downloaded: {gcs_path} → {local_path}")
            return True
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


# ── Shared Google Drive helpers ────────────────────────────────────────────────

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


def _drive_list_files(service, folder_id: str, mime: str = "application/pdf") -> list:
    """List files in a Drive folder. Returns list of file dicts."""
    try:
        q = f"mimeType='{mime}' and '{folder_id}' in parents and trashed=false"
        r = service.files().list(
            q=q, fields="files(id,name,modifiedTime)", pageSize=200,
            orderBy="modifiedTime desc"
        ).execute()
        return r.get("files", [])
    except Exception as e:
        print(f"[drive] List files error: {e}")
        return []


def _drive_download_file(service, file_id: str, dest: Path) -> bool:
    """Download a Drive file to a local path. Returns True on success."""
    try:
        from googleapiclient.http import MediaIoBaseDownload
        import io
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
        fh.seek(0)
        dest.write_bytes(fh.read())
        return True
    except Exception as e:
        print(f"[drive] Download {file_id} → {dest.name} error: {e}")
        return False


# ── Forecast state (persisted in GCS so it survives container restarts) ────────

_FORECAST_STATE_GCS = "forecast_state.json"
_FORECAST_STATE_LOCAL = BASE_DIR / ".forecast_state.json"


def _load_forecast_state() -> dict:
    """Load dict of {file_id: modifiedTime} for already-processed forecast PDFs."""
    if _gcs_bucket:
        _download_from_gcs(_FORECAST_STATE_GCS, _FORECAST_STATE_LOCAL)
    try:
        return json.loads(_FORECAST_STATE_LOCAL.read_text())
    except Exception:
        return {}


def _save_forecast_state(state: dict):
    _FORECAST_STATE_LOCAL.write_text(json.dumps(state, indent=2))
    if _gcs_bucket:
        _upload_to_gcs(_FORECAST_STATE_LOCAL, _FORECAST_STATE_GCS)


# ── Scheduler + rebuild pipeline ───────────────────────────────────────────────

def _run_scheduler_and_rebuild():
    """Run walk_scheduler.py → build_dashboard.py, upload results to GCS.
    Protected by _scheduler_running lock to prevent concurrent runs."""
    if not _scheduler_running.acquire(blocking=False):
        print("[forecast] Scheduler already running — skipping this trigger")
        return

    try:
        print("[forecast] ▶ Running walk_scheduler.py …")
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
            print("[forecast] Uploaded schedule_output.json → GCS")

        print("[forecast] ▶ Rebuilding dashboard …")
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
    except subprocess.TimeoutExpired:
        print("[forecast] Scheduler timed out (6 min limit)")
    except Exception as e:
        print(f"[forecast] Pipeline error: {e}")
    finally:
        _scheduler_running.release()


# ── Forecast monitor — polls Drive for new forecast PDFs ──────────────────────

def _poll_forecast_pdfs() -> tuple[int, str | None]:
    """Check DRIVE_FORECASTS_FOLDER_ID for new/updated forecast PDFs.
    Downloads any new ones and triggers the scheduler+rebuild pipeline.
    Returns (new_count, error_msg)."""
    global _forecast_last_poll

    if not DRIVE_FORECASTS_FOLDER_ID:
        return 0, "DRIVE_FORECASTS_FOLDER_ID not set"

    service = _get_drive_service()
    if service is None:
        return 0, "Drive auth failed (GOOGLE_SERVICE_ACCOUNT_JSON missing or invalid)"

    state = _load_forecast_state()  # {file_id: modifiedTime}

    # Collect PDFs: top-level + one level of month subfolders (e.g. "March 2026/")
    def _list_all_forecast_pdfs() -> list:
        results: list = []
        try:
            resp = service.files().list(
                q=f"'{DRIVE_FORECASTS_FOLDER_ID}' in parents and trashed=false",
                fields="files(id,name,mimeType,modifiedTime)",
                pageSize=200,
            ).execute()
            items = resp.get("files", [])
        except Exception as e:
            print(f"[forecast] Error listing root folder: {e}")
            return results
        for item in items:
            if item.get("mimeType") == "application/pdf":
                results.append(item)
            elif item.get("mimeType") == "application/vnd.google-apps.folder":
                # Recurse one level into month subfolders
                sub = _drive_list_files(service, item["id"], mime="application/pdf")
                results.extend(sub)
        return results

    pdfs = _list_all_forecast_pdfs()

    new_pdfs = [f for f in pdfs if state.get(f["id"]) != f.get("modifiedTime")]
    if not new_pdfs:
        with _DRIVE_LOCK:
            _forecast_last_poll = _now_iso()
        return 0, None

    FORECAST_DIR.mkdir(exist_ok=True)
    downloaded = 0
    for f in new_pdfs:
        dest = FORECAST_DIR / f["name"]
        print(f"[forecast] New PDF: {f['name']} — downloading …")
        if _drive_download_file(service, f["id"], dest):
            state[f["id"]] = f["modifiedTime"]
            downloaded += 1
            print(f"[forecast] ✓ {f['name']} saved to Forecast/")

    if downloaded:
        _save_forecast_state(state)
        # Run scheduler pipeline in a background thread so poll returns quickly
        t = threading.Thread(target=_run_scheduler_and_rebuild, daemon=True)
        t.start()

    with _DRIVE_LOCK:
        _forecast_last_poll = _now_iso()

    return downloaded, None


def _forecast_monitor_thread():
    """Daemon thread: polls Drive for new forecast PDFs on a fixed interval."""
    print(f"[forecast] Monitor started (interval: {FORECAST_POLL_INTERVAL}s,"
          f" folder: {DRIVE_FORECASTS_FOLDER_ID or 'NOT SET'})")
    while True:
        try:
            count, err = _poll_forecast_pdfs()
            if err:
                print(f"[forecast] Poll skipped: {err}")
            elif count:
                print(f"[forecast] {count} new forecast PDF(s) detected — scheduler triggered")
        except Exception as e:
            print(f"[forecast] Unexpected error: {e}")
        time.sleep(FORECAST_POLL_INTERVAL)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stream_script(wfile, script: Path, label: str, extra_args: list = None) -> int:
    header = f"\n── {label} ──\n"
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


# ── GPS helpers ───────────────────────────────────────────────────────────────

def _update_gps(bp_id: str, lat: float, lon: float, speed=None, batt=None):
    ts = _now_iso()
    with _GPS_LOCK:
        _GPS_POSITIONS[bp_id] = {
            "lat": lat, "lon": lon, "ts": ts,
            "speed": speed, "batt": batt, "stale": False,
        }
        _GPS_TRAILS[bp_id].append({"lat": lat, "lon": lon, "ts": ts})


def _refresh_stale():
    """Mark GPS positions as stale if older than GPS_STALE_SECONDS."""
    now = datetime.now()
    with _GPS_LOCK:
        for bp, pos in _GPS_POSITIONS.items():
            if pos["ts"]:
                age = (now - datetime.fromisoformat(pos["ts"])).total_seconds()
                pos["stale"] = age > GPS_STALE_SECONDS
            else:
                pos["stale"] = True


# ── Drive polling ─────────────────────────────────────────────────────────────

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


def _append_to_walk_log(entry: str):
    with open(WALKS_LOG, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    print(f"[drive] Appended to Walks_Log.txt: {entry}")

    # Also upload to GCS if configured
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
    """Poll walk-log Drive folder for new completed-walk files. Returns (new_count, error_msg)."""
    global _drive_last_poll, _drive_new_today
    print(f"[drive] Walk-log poll triggered by: {source}")

    if not DRIVE_FOLDER_ID:
        return 0, "GOOGLE_DRIVE_FOLDER_ID not set"

    service = _get_drive_service()
    if service is None:
        return 0, "Drive auth failed (GOOGLE_SERVICE_ACCOUNT_JSON missing or invalid)"

    seen_ids = _load_seen_ids()
    new_count = 0

    try:
        # List all files in the folder tree (recursive via query)
        page_token = None
        while True:
            query = f"'{DRIVE_FOLDER_ID}' in parents or parents in (select id from driveFiles where '{DRIVE_FOLDER_ID}' in parents)"
            # Simpler: list all files under the root folder (1 level) + subfolder files
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

                # Recurse into subfolders
                if mime == "application/vnd.google-apps.folder":
                    sub_resp = service.files().list(
                        q=f"'{fid}' in parents and trashed=false",
                        fields="files(id, name, mimeType)",
                        pageSize=200,
                    ).execute()
                    files.extend(sub_resp.get("files", []))
                    continue

                if fid in seen_ids:
                    continue

                entry = _parse_filename_to_log_entry(fname)
                if entry:
                    _append_to_walk_log(entry)
                    new_count += 1

                seen_ids.add(fid)

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    except Exception as e:
        return new_count, f"Drive list error: {e}"

    _save_seen_ids(seen_ids)

    with _DRIVE_LOCK:
        _drive_last_poll = _now_iso()
        today = datetime.now().date()
        if new_count > 0:
            _drive_new_today += new_count

    if new_count > 0:
        _trigger_rebuild()

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


# ── Request handler ───────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # suppress per-request console noise
        pass

    # ── GET ──────────────────────────────────────────────────────────────────
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
            _refresh_stale()
            with _GPS_LOCK:
                gps_snap = {bp: dict(pos) for bp, pos in _GPS_POSITIONS.items()}
            with _DRIVE_LOCK:
                drive_last = _drive_last_poll
                drive_today = _drive_new_today
            payload = {name: _mtime_iso(p) for name, p in STATUS_FILES.items()}
            payload["gps_bp_a"] = gps_snap.get("BP_A")
            payload["gps_bp_b"] = gps_snap.get("BP_B")
            payload["drive_last_poll"] = drive_last
            payload["drive_new_files_today"] = drive_today
            payload["forecast_last_poll"] = _forecast_last_poll
            payload["forecast_folder_configured"] = bool(DRIVE_FORECASTS_FOLDER_ID)
            body = json.dumps(payload, indent=2).encode()
            self._send(200, "application/json", body)
            return

        # /api/gps — Traccar Client GPS push
        # Format: GET /api/gps?id=BP_A&lat=40.71&lon=-73.96&speed=1.3&batt=80&token=secret
        if path == "api/gps":
            bp_id = params.get("id", [None])[0]
            lat   = params.get("lat", [None])[0]
            lon   = params.get("lon", [None])[0]
            speed = params.get("speed", [None])[0]
            batt  = params.get("batt", [None])[0]
            token = params.get("token", [""])[0]

            if GPS_AUTH_TOKEN and token != GPS_AUTH_TOKEN:
                self._send(403, "text/plain", b"Forbidden")
                return

            if bp_id not in GPS_BACKPACK_IDS or lat is None or lon is None:
                self._send(400, "application/json",
                           json.dumps({"error": "missing or invalid id/lat/lon"}).encode())
                return

            try:
                _update_gps(
                    bp_id,
                    float(lat), float(lon),
                    speed=float(speed) if speed else None,
                    batt=float(batt) if batt else None,
                )
            except ValueError:
                self._send(400, "application/json",
                           json.dumps({"error": "non-numeric lat/lon/speed/batt"}).encode())
                return

            self._send(200, "application/json", json.dumps({"status": "ok"}).encode())
            return

        # /api/gps/status — current positions
        if path == "api/gps/status":
            _refresh_stale()
            with _GPS_LOCK:
                data = {bp: dict(pos) for bp, pos in _GPS_POSITIONS.items()}
            self._send(200, "application/json", json.dumps(data).encode())
            return

        # /api/gps/trail — position history for one backpack
        if path == "api/gps/trail":
            bp_id = params.get("id", [None])[0]
            if bp_id not in GPS_BACKPACK_IDS:
                self._send(400, "application/json",
                           json.dumps({"error": "invalid id"}).encode())
                return
            with _GPS_LOCK:
                trail = list(_GPS_TRAILS[bp_id])
            self._send(200, "application/json", json.dumps(trail).encode())
            return

        # /api/confirmations — current confirm/deny state for schedule assignments
        if path == "api/confirmations":
            body = json.dumps(_load_confirmations()).encode()
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

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self):
        endpoint = self.path.split("?")[0]

        # Authenticate /api/drive/poll and /api/rerun with GAS_SECRET bearer token.
        # If GAS_SECRET is not set, these endpoints remain open (local dev compatible).
        # /api/rebuild is not gated — it is only triggered by the browser UI.
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
            # Body: {"id": "<route>_<tod>_<date>", "status": "confirmed"|"denied"|"pending",
            #        "scheduler": "CCNY"|"LaGCC", "pin": "xxxx"}
            _sched_pin = os.environ.get("SCHEDULER_PIN", "")
            try:
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length) if length else b"{}"
                payload = json.loads(body_bytes)
            except Exception:
                self._send(400, "application/json",
                           json.dumps({"error": "bad request"}).encode())
                return
            # Verify PIN (if SCHEDULER_PIN is set)
            if _sched_pin and payload.get("pin", "") != _sched_pin:
                self._send(403, "application/json",
                           json.dumps({"error": "wrong pin"}).encode())
                return
            assign_id = payload.get("id", "").strip()
            status    = payload.get("status", "")
            scheduler = payload.get("scheduler", "unknown")
            if not assign_id or status not in ("confirmed", "denied", "pending"):
                self._send(400, "application/json",
                           json.dumps({"error": "missing id or invalid status"}).encode())
                return
            data = _load_confirmations()
            if status == "pending":
                data.pop(assign_id, None)   # reset → remove entry
            else:
                data[assign_id] = {
                    "status":    status,
                    "scheduler": scheduler,
                    "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                }
            _save_confirmations(data)
            self._send(200, "application/json",
                       json.dumps({"ok": True, "confirmations": data}).encode())
        elif endpoint == "/api/drive/poll":
            count, err = _run_drive_poll(source="gas")
            if err:
                body = json.dumps({"status": "error", "message": err}).encode()
                self._send(500, "application/json", body)
            else:
                body = json.dumps({"status": "ok", "new_files": count}).encode()
                self._send(200, "application/json", body)
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

            done = "\n[All done — reload the page to see updated dashboards]\n"
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


# ── Entry point ───────────────────────────────────────────────────────────────

def _restore_gcs_state():
    """Download persistent state from GCS. Called both at container start
    (before build_dashboard.py via --restore-only) and again when serve starts."""
    _init_gcs()
    if not _gcs_bucket:
        return
    # Walks_Log.txt — always refresh from GCS (may be newer than baked copy)
    _download_from_gcs("Walks_Log.txt", WALKS_LOG)
    # schedule_output.json — last scheduler run results
    _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
    # schedule_confirmations.json — confirm/deny state
    _download_from_gcs("schedule_confirmations.json", CONFIRMATIONS_FILE)
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

    # ── Restore persistent state from GCS on startup ───────────────────────────
    if _gcs_bucket:
        # Walks_Log.txt — source of truth for walk history
        if not WALKS_LOG.exists():
            _download_from_gcs("Walks_Log.txt", WALKS_LOG)
        else:
            # Always refresh — GCS copy may be newer than the baked image copy
            _download_from_gcs("Walks_Log.txt", WALKS_LOG)

        # schedule_output.json — last scheduler run results
        _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)

        # schedule_confirmations.json — confirm/deny state
        _download_from_gcs("schedule_confirmations.json", CONFIRMATIONS_FILE)

        print("[startup] GCS state restored")

    # ── Start Drive walk-log polling thread ────────────────────────────────────
    if DRIVE_POLL_INTERVAL > 0:
        t = threading.Thread(target=_drive_poll_thread, daemon=True)
        t.start()
        print(f"  Walk-log poll : active (every {DRIVE_POLL_INTERVAL}s)")
    else:
        print(f"  Walk-log poll : DISABLED — relying on GAS push triggers")

    # ── Start forecast monitor thread ──────────────────────────────────────────
    if DRIVE_FORECASTS_FOLDER_ID and FORECAST_POLL_INTERVAL > 0:
        ft = threading.Thread(target=_forecast_monitor_thread, daemon=True)
        ft.start()
        print(f"  Forecast monitor : active (every {FORECAST_POLL_INTERVAL}s)")
    else:
        print(f"  Forecast monitor : DISABLED"
              f" (set DRIVE_FORECASTS_FOLDER_ID + FORECAST_POLL_INTERVAL in GCP env)")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"")
    print(f"  NYC Walk Scheduler — server")
    print(f"  Dashboard  : {url}")
    print(f"  Rerun API  : POST {url}/api/rerun")
    print(f"  Rebuild API: POST {url}/api/rebuild")
    print(f"  Status API : GET  {url}/api/status")
    print(f"  GPS API    : GET  {url}/api/gps?id=BP_A&lat=...&lon=...")
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
