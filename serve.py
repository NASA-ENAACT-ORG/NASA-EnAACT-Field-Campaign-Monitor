"""
serve.py — Server for the NYC Walk Scheduler dashboard.

Usage:
    python serve.py              # serves on 0.0.0.0:8765 (or $PORT)
    python serve.py --port 9000  # custom port

Endpoints:
  GET  /                        → redirect to /dashboard.html
  GET  /<filename>              → serve static file
  GET  /api/status              → JSON with file mod times, GPS positions, Drive status
  POST /api/rerun               → run scheduler + rebuild dashboards, stream output
  POST /api/rebuild             → rebuild dashboards only, stream output
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

BASE_DIR        = Path(__file__).parent.resolve()
SCHEDULER       = BASE_DIR / "walk_scheduler.py"
BUILD_DASHBOARD = BASE_DIR / "build_dashboard.py"
BUILD_MAP       = BASE_DIR / "build_collector_map.py"
WALKS_LOG       = BASE_DIR / "Walks_Log.txt"
SEEN_FILES_PATH = BASE_DIR / "drive_seen_files.json"

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

DRIVE_FOLDER_ID    = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
DRIVE_POLL_INTERVAL = int(os.environ.get("DRIVE_POLL_INTERVAL", "60"))
_DRIVE_LOCK        = threading.Lock()
_drive_last_poll   = None   # datetime or None
_drive_new_today   = 0      # count of new files detected today


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stream_script(wfile, script: Path, label: str) -> int:
    header = f"\n── {label} ──\n"
    _write_chunk(wfile, header.encode())

    proc = subprocess.Popen(
        [sys.executable, str(script)],
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
    try:
        return set(json.loads(SEEN_FILES_PATH.read_text()))
    except Exception:
        return set()


def _save_seen_ids(ids: set):
    try:
        SEEN_FILES_PATH.write_text(json.dumps(sorted(ids), indent=2))
    except Exception as e:
        print(f"[drive] Warning: could not save seen IDs: {e}")


def _append_to_walk_log(entry: str):
    with open(WALKS_LOG, "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    print(f"[drive] Appended to Walks_Log.txt: {entry}")


def _trigger_rebuild():
    subprocess.Popen(
        [sys.executable, str(BUILD_DASHBOARD)],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _run_drive_poll(source: str = "background"):
    """Run one Drive poll cycle. Returns (new_count, error_msg).
    source: 'background' (polling thread) or 'gas' (GAS push trigger)
    """
    global _drive_last_poll, _drive_new_today
    print(f"[drive] Poll triggered by: {source}")

    if not DRIVE_FOLDER_ID:
        return 0, "GOOGLE_DRIVE_FOLDER_ID not set"

    svc_json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not svc_json_str:
        return 0, "GOOGLE_SERVICE_ACCOUNT_JSON not set"

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build as gdrive_build
    except ImportError:
        return 0, "google-api-python-client not installed"

    try:
        svc_info = json.loads(svc_json_str)
        creds = service_account.Credentials.from_service_account_info(
            svc_info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = gdrive_build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        return 0, f"Drive auth failed: {e}"

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
        elif endpoint == "/api/rebuild":
            self._stream_response(run_scheduler=False)
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

    def _stream_response(self, run_scheduler: bool):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            if run_scheduler:
                _stream_script(self.wfile, SCHEDULER, "walk_scheduler.py")

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
        self.end_headers()
        self.wfile.write(body)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Walk Scheduler server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()

    # Start Drive polling background thread.
    # Set DRIVE_POLL_INTERVAL=0 to disable (use when GAS push triggers are active).
    if DRIVE_POLL_INTERVAL > 0:
        t = threading.Thread(target=_drive_poll_thread, daemon=True)
        t.start()
        print(f"  Drive poll : active (every {DRIVE_POLL_INTERVAL}s)")
    else:
        print(f"  Drive poll : DISABLED — relying on GAS push triggers")

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
