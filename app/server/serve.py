"""
serve.py " Server for the NYC field campaign dashboard.

Usage:
    python serve.py              # serves on 0.0.0.0:8765 (or $PORT)
    python serve.py --port 9000  # custom port

Endpoints:
  GET  /                        ' redirect to /dashboard.html
  GET  /<filename>              ' serve static file
  GET  /api/status              ' JSON with file mod times, Drive status
  GET  /api/schedule            ' JSON schedule document
  GET  /api/schedule/slots      ' slot-oriented schedule view
  POST /api/backpack-status     ' update backpack holder/location state
  POST /api/rebuild             ' rebuild dashboard, stream output
  POST /api/schedule/rebuild-site ' weather + site rebuild (no scheduler)
  POST /api/notifications/preview ' preview next-day confirmation/advisory payload
  POST /api/notifications/send  ' record/send next-day notifications
  POST /api/forecast-stability  ' run forecast stability analysis, stream output
  POST /api/drive/poll          ' manually trigger one Google Drive poll cycle
"""

from __future__ import annotations

import argparse
import email.parser
import email.policy
import io
import json
import mimetypes
import os
import re
import smtplib
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage, Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

# Add repo root to sys.path so shared package is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.paths import (
    REPO_ROOT,
    SITE_DIR,
    PERSISTED_DIR,
    BUILD_WEATHER,
    BUILD_DASHBOARD,
    WALKS_LOG,
    RECAL_LOG,
    DRIVE_SEEN_FILES,
    SCHEDULE_OUTPUT_JSON,
    DASHBOARD_HTML,
    WEATHER_JSON,
)
from shared import gcs
from shared.schedule_store import (
    ScheduleValidationError,
    load_schedule,
    save_schedule,
)
from shared.notification_preferences import (
    destinations_for_collector,
    load_notification_preferences,
)
from shared.registry import (
    BACKPACK_TO_SCHEDULE_COLLECTORS,
    ROUTE_CODES,
    ROUTE_LABELS,
    SCHEDULE_COLLECTOR_IDS,
    SLOT_TODS,
    VALID_BACKPACKS,
)

import upload_buffer
import drive_mover

BASE_DIR           = REPO_ROOT
FORECAST_STABILITY = REPO_ROOT / "scripts" / "ops" / "forecast_stability_analysis.py"
SEEN_FILES_PATH    = DRIVE_SEEN_FILES
SCHEDULE_OUTPUT    = SCHEDULE_OUTPUT_JSON
NOTIFICATION_LOG   = PERSISTED_DIR / "notification_dispatch_log.jsonl"
ALLOWED_ROUTES = ROUTE_CODES
BACKPACK_TO_COLLECTORS = {
    bp: set(collectors)
    for bp, collectors in BACKPACK_TO_SCHEDULE_COLLECTORS.items()
}
ALLOWED_COLLECTORS = SCHEDULE_COLLECTOR_IDS

# "" Drive config """""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""


# Files tracked by /api/status
STATUS_FILES = {
    "schedule_output": SCHEDULE_OUTPUT,
    "walk_log":        WALKS_LOG,
    "dashboard":       DASHBOARD_HTML,
}

# "" Drive polling state """"""""""""""""""""""""""""""""""""""""""""""""""""""""

DRIVE_FOLDER_ID       = os.environ.get("GOOGLE_DRIVE_WALKS_FOLDER_ID", "")
DRIVE_POLL_INTERVAL   = int(os.environ.get("DRIVE_POLL_INTERVAL", "300"))
_DRIVE_LOCK           = threading.Lock()
_drive_last_poll      = None
_drive_new_today      = 0
_rebuild_running      = threading.Lock()  # prevents concurrent weather/site rebuild runs
_schedule_write_lock  = threading.Lock()  # prevents concurrent schedule writes
_bootstrap_build_lock = threading.Lock()  # prevents concurrent startup/missing-file builds
_bootstrap_errors     = []


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
        r = service.files().list(
            q=q, fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = r.get("files", [])
        return files[0]["id"] if files else None
    except Exception as e:
        print(f"[drive] Find folder '{name}' error: {e}")
        return None


def _drive_find_folder_by_prefix(service, parent_id: str, prefix: str) -> str | None:
    """Return the ID of the first subfolder whose name starts with `prefix` (case-insensitive).
    Falls back to exact match if no prefix match found."""
    try:
        q = (f"mimeType='application/vnd.google-apps.folder'"
             f" and '{parent_id}' in parents and trashed=false")
        r = service.files().list(
            q=q, fields="files(id, name)", pageSize=200,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        prefix_up = prefix.strip().upper()
        for f in r.get("files", []):
            if f["name"].strip().upper().startswith(prefix_up):
                print(f"[drive] Prefix match: '{prefix}' -> '{f['name']}'")
                return f["id"]
        return None
    except Exception as e:
        print(f"[drive] Prefix folder search '{prefix}' error: {e}")
        return None


def _get_drive_write_service():
    """Return an authenticated Drive v3 service with full read/write access, or None."""
    svc_json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not svc_json_str:
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build as gdrive_build
        svc_info = json.loads(svc_json_str)
        creds = service_account.Credentials.from_service_account_info(
            svc_info, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return gdrive_build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"[drive] Write auth error: {e}")
        return None


def _drive_create_or_get_folder(service, parent_id: str, name: str) -> str | None:
    """Return the ID of a named subfolder, creating it if it doesn't exist."""
    fid = _drive_find_folder(service, parent_id, name)
    if fid:
        return fid
    try:
        meta = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        f = service.files().create(
            body=meta, fields="id", supportsAllDrives=True,
        ).execute()
        return f.get("id")
    except Exception as e:
        print(f"[drive] Create folder '{name}' error: {e}")
        return None


def _drive_upload_file(service, folder_id: str, filename: str, data) -> bool:
    """Upload bytes (or a binary file-like) as a file into a Drive folder.
    Uses resumable=True with internal HttpError 5xx/429 retries to ride out
    transient flakiness. Raises on terminal failure."""
    from googleapiclient.http import MediaIoBaseUpload
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    fp = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    size = len(data) if isinstance(data, (bytes, bytearray)) else "stream"
    meta = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(fp, mimetype=mime, resumable=True,
                              chunksize=5 * 1024 * 1024)
    req = service.files().create(
        body=meta, media_body=media, fields="id", supportsAllDrives=True,
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    print(f"[drive] Uploaded '{filename}' ({size} bytes)")
    return True


def _parse_multipart(headers, body: bytes) -> tuple[dict[str, str], dict[str, list[tuple[str, bytes]]]]:
    """Parse multipart/form-data. Returns (fields, files).
    fields: {name: str}
    files:  {name: [(filename, bytes), ...]}
    Uses email.parser — pure stdlib, works on Python 3.13+.
    """
    content_type = headers.get("Content-Type", "")
    # Build a minimal MIME message so email.parser can parse it
    raw = b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
    msg = email.parser.BytesParser(policy=email.policy.compat32).parsebytes(raw)

    fields: dict[str, str] = {}
    files: dict[str, list[tuple[str, bytes]]] = {}
    parts = msg.get_payload()
    if not isinstance(parts, list):
        return fields, files
    message_parts = [p for p in parts if isinstance(p, Message)]
    for part in message_parts:
        disposition = part.get("Content-Disposition", "")
        # Extract name= and optional filename= from the disposition header
        name = None
        filename = None
        for segment in disposition.split(";"):
            segment = segment.strip()
            if segment.lower().startswith("name="):
                name = segment[5:].strip().strip('"')
            elif segment.lower().startswith("filename="):
                filename = segment[9:].strip().strip('"')
        if name is None:
            continue
        payload_raw = part.get_payload(decode=True)
        payload: bytes
        if payload_raw is None:
            payload = b""
        elif isinstance(payload_raw, bytes):
            payload = payload_raw
        elif isinstance(payload_raw, bytearray):
            payload = bytes(payload_raw)
        elif isinstance(payload_raw, memoryview):
            payload = payload_raw.tobytes()
        elif isinstance(payload_raw, str):
            payload = payload_raw.encode("utf-8", errors="replace")
        else:
            payload = cast(Message, payload_raw).as_bytes()
        if filename:
            print(f"[upload] file '{filename}' field='{name}' size={len(payload)}")
            files.setdefault(name, []).append((filename, payload))
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    return fields, files


def _rebuild_dashboard_and_upload():
    """Run build_dashboard.py and upload dashboard artefacts to GCS."""
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
            (DASHBOARD_HTML, "dashboard.html"),
        ):
            if html_path.exists():
                _upload_to_gcs(html_path, blob_name)
        print("[forecast] Uploaded rebuilt dashboard -> GCS")


def _run_weather_and_rebuild_site():
    """Run build_weather.py then rebuild site artifacts (no scheduler)."""
    if not _rebuild_running.acquire(blocking=False):
        print("[forecast] Rebuild already running -- skipping this trigger")
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
            print("[forecast] build_weather.py failed -- aborting rebuild")
            return

        if WEATHER_JSON.exists() and _gcs_bucket:
            ok = _upload_to_gcs(WEATHER_JSON, "weather.json")
            if ok:
                print("[forecast] Uploaded weather.json -> GCS")
            else:
                print("[forecast] WARNING: GCS upload of weather.json failed")
        elif not _gcs_bucket:
            print("[forecast] WARNING: GCS not configured -- weather.json not uploaded")
        elif not WEATHER_JSON.exists():
            print("[forecast] WARNING: weather.json missing -- nothing to upload")

        print("[forecast] Rebuilding dashboard (scheduler-free) ...")
        _rebuild_dashboard_and_upload()
    except subprocess.TimeoutExpired:
        print("[forecast] Weather/site rebuild timed out")
    except Exception as e:
        print(f"[forecast] Weather/site rebuild error: {e}")
    finally:
        _rebuild_running.release()


def _resolve_notification_date(date_value: str | None) -> str:
    """Resolve notification date, defaulting to tomorrow in local server time."""
    if date_value:
        return date_value
    return str(datetime.now().date() + timedelta(days=1))


def _normalize_notification_channels(raw: Any) -> list[str]:
    if raw is None:
        return ["email"]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ["email"]
    channels: list[str] = []
    for item in raw:
        channel = str(item).strip().lower()
        if channel in {"email", "slack"} and channel not in channels:
            channels.append(channel)
    return channels or ["email"]


def _email_transport_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("NOTIFICATION_FROM_EMAIL"))


def _redact_notification_target(target: str) -> str:
    if "@" not in target:
        return "***"
    name, domain = target.split("@", 1)
    visible = name[:2] if len(name) > 2 else name[:1]
    return f"{visible}***@{domain}"


def _redact_notification_preview(preview: dict) -> dict:
    redacted = json.loads(json.dumps(preview))
    for msg in redacted.get("messages", []):
        for destination in msg.get("destinations", []):
            if destination.get("target"):
                destination["target"] = _redact_notification_target(str(destination["target"]))
    for messages in redacted.get("collectors", {}).values():
        for msg in messages:
            for destination in msg.get("destinations", []):
                if destination.get("target"):
                    destination["target"] = _redact_notification_target(str(destination["target"]))
    return redacted


def _send_email_notification(*, to_email: str, subject: str, body: str) -> dict:
    host = os.environ.get("SMTP_HOST", "").strip()
    from_email = os.environ.get("NOTIFICATION_FROM_EMAIL", "").strip()
    if not host or not from_email:
        return {"ok": False, "status": "skipped", "error": "SMTP_HOST and NOTIFICATION_FROM_EMAIL are required"}

    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        return {"ok": False, "status": "failed", "error": "SMTP_PORT must be an integer"}
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    use_tls = os.environ.get("SMTP_USE_TLS", "1").strip().lower() not in {"0", "false", "no"}

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)
        return {"ok": True, "status": "sent"}
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc)}


def _refresh_schedule_week_bounds(schedule_data: dict) -> None:
    """Keep week_start/week_end aligned to the current assignment date span."""
    assignments = schedule_data.get("assignments", []) or []
    parsed_dates: list[date] = []
    for assignment in assignments:
        try:
            parsed_dates.append(date.fromisoformat(str(assignment.get("date", ""))))
        except Exception:
            continue

    if not parsed_dates:
        return

    start = min(parsed_dates)
    end = max(parsed_dates)
    schedule_data["week_start"] = str(start)
    schedule_data["week_end"] = str(end)


def _build_notifications_preview(target_date: str, requested_channels: list[str] | None = None) -> dict:
    """Build a preview payload for assignments on one date."""
    _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
    schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
    weather = schedule_data.get("weather", {})
    preferences = load_notification_preferences()
    assignments = [
        a for a in schedule_data.get("assignments", [])
        if str(a.get("date", "")) == target_date
    ]
    assignments.sort(key=lambda a: (
        str(a.get("backpack", "")),
        str(a.get("tod", "")),
        str(a.get("route", "")),
    ))

    messages = []
    collectors = {}
    for assignment in assignments:
        date_str = str(assignment.get("date", ""))
        tod = str(assignment.get("tod", "")).upper()
        weather_key = f"{date_str}_{tod}"
        advisory = weather.get(weather_key) is False
        advisory_text = (
            "Weather advisory: forecast is unfavorable, verify before departure."
            if advisory else
            "Weather check: no advisory for this slot."
        )
        collector = str(assignment.get("collector", "")).upper()
        route_label = assignment.get("label") or assignment.get("route")
        backpack = str(assignment.get("backpack", "")).upper()
        destinations = destinations_for_collector(collector, requested_channels, preferences)
        msg = {
            "collector": collector,
            "date": date_str,
            "tod": tod,
            "backpack": backpack,
            "route": assignment.get("route"),
            "route_label": route_label,
            "weather_advisory": advisory,
            "advisory_text": advisory_text,
            "destinations": destinations,
            "sendable": any(d.get("channel") == "email" for d in destinations),
            "message_text": (
                f"Reminder for {collector}: {date_str} {tod}, Backpack {backpack}, "
                f"{route_label}. {advisory_text}"
            ),
        }
        messages.append(msg)
        collectors.setdefault(collector, []).append(msg)

    return {
        "date": target_date,
        "assignment_count": len(assignments),
        "collector_count": len(collectors),
        "email_transport_configured": _email_transport_configured(),
        "messages": messages,
        "collectors": collectors,
    }


# "" Forecast monitor " polls Drive for new forecast PDFs """"""""""""""""""""""

def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stream_script(wfile, script: Path, label: str, extra_args: list[str] | None = None) -> int:
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
    if proc.stdout is not None:
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


def _run_script_once(script: Path, label: str, timeout: int = 180) -> tuple[bool, str | None]:
    """Run a pipeline script and return (success, error_message)."""
    print(f"[startup] Running {label} ...")
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired:
        msg = f"{label} timed out after {timeout}s"
        print(f"[startup] {msg}")
        return False, msg
    except Exception as e:
        msg = f"{label} crashed before launch: {e}"
        print(f"[startup] {msg}")
        return False, msg

    out = (r.stdout or "") + (r.stderr or "")
    if out:
        print(out[-3000:])
    print(f"[startup] {label} exit={r.returncode}")
    if r.returncode == 0:
        return True, None

    tail = [line.strip() for line in out.splitlines() if line.strip()]
    detail = tail[-1] if tail else f"exit code {r.returncode}"
    return False, f"{label} failed: {detail}"


def _ensure_site_artifacts():
    """Build site artifacts when key HTML outputs are missing."""
    global _bootstrap_errors
    with _bootstrap_build_lock:
        _bootstrap_errors = []
        required = [DASHBOARD_HTML]
        missing = [p for p in required if not p.exists()]
        if not missing:
            return

        print("[startup] Missing generated site files:")
        for p in missing:
            print(f"[startup]   - {p}")

        # build_dashboard.py writes dashboard.html.
        ok_dash, err_dash = _run_script_once(BUILD_DASHBOARD, "build_dashboard.py", timeout=240)
        if not ok_dash and err_dash:
            _bootstrap_errors.append(err_dash)


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
    Never writes RECAL lines — those live exclusively in Recal_Log.txt.
    Validates each entry and skips malformed ones."""
    validated = []
    skipped = []
    for e in entries:
        line = e.strip() if isinstance(e, str) else str(e)
        if not line or line.startswith("RECAL_"):
            continue
        if _WALK_LOG_RE.match(line.upper()):
            validated.append(line.upper())
        else:
            skipped.append(line)

    if skipped:
        print(f"[drive] WARNING: Skipped {len(skipped)} invalid walk entries: {skipped[:5]}")

    content = "\n".join(validated) + "\n" if validated else ""
    WALKS_LOG.write_text(content, encoding="utf-8")
    print(f"[drive] Rebuilt Walks_Log.txt: {len(validated)} valid walk entries")
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
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
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
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
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

    # Compare against what's currently on disk to detect real changes
    prev_entries: set = set()
    if WALKS_LOG.exists():
        prev_entries = {
            l.strip().upper() for l in WALKS_LOG.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().upper().startswith("RECAL_")
        }

    # Rebuild log from Drive entries only (no manual-entry preservation)
    merged = sorted(drive_set)
    log_changed = drive_set != prev_entries
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

    def log_message(self, format, *args):  # suppress per-request console noise
        pass

    def _assignment_fallback_id(self, assignment: dict, *, sep: str = "_") -> str:
        """Build composite ID from slot identity fields.

        This is used as a compatibility fallback for legacy assignments that
        predate explicit `id` fields.
        """
        backpack = str(assignment.get("backpack", "")).upper()
        route = str(assignment.get("route", "")).upper()
        date_str = str(assignment.get("date", ""))
        tod = str(assignment.get("tod", "")).upper()
        return f"{backpack}{sep}{route}{sep}{date_str}{sep}{tod}"

    def _assignment_id_aliases(self, assignment: dict) -> set[str]:
        """Return all accepted ID forms for one assignment."""
        aliases = {
            self._assignment_fallback_id(assignment, sep="_"),
            self._assignment_fallback_id(assignment, sep="|"),
        }
        explicit = str(assignment.get("id", "")).strip()
        if explicit:
            aliases.add(explicit)
        return aliases

    def _find_assignment_by_id(self, assignments: list[dict], assignment_id: str) -> tuple[int | None, dict | None]:
        for idx, assignment in enumerate(assignments):
            if assignment_id in self._assignment_id_aliases(assignment):
                return idx, assignment
        return None, None

    def _read_json_body(self) -> tuple[dict, str | None]:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body_bytes)
            if not isinstance(payload, dict):
                return {}, "bad request"
            return payload, None
        except Exception:
            return {}, "bad request"

    def _pin_ok(self, payload: dict) -> bool:
        sched_pin = os.environ.get("SCHEDULER_PIN", "")
        if not sched_pin:
            return True
        provided = (
            str(payload.get("pin", "")).strip()
            or str(self.headers.get("X-Scheduler-Pin", "")).strip()
        )
        return provided == sched_pin

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
            payload: dict[str, Any] = {name: _mtime_iso(p) for name, p in STATUS_FILES.items()}
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

        # /api/schedule
        if path == "api/schedule":
            try:
                # Keep local disk synced with bucket-authoritative schedule when available.
                _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
                schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
                self._send(
                    200,
                    "application/json",
                    json.dumps(schedule_data, indent=2).encode("utf-8"),
                )
            except ScheduleValidationError as exc:
                self._send(
                    500,
                    "application/json",
                    json.dumps({"error": f"invalid schedule data: {exc}"}).encode("utf-8"),
                )
            return

        # /api/schedule/slots?week_start=YYYY-MM-DD
        if path == "api/schedule/slots":
            try:
                _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
                schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
            except ScheduleValidationError as exc:
                self._send(
                    500,
                    "application/json",
                    json.dumps({"error": f"invalid schedule data: {exc}"}).encode("utf-8"),
                )
                return

            week_start = params.get("week_start", [None])[0]
            week_start_date = None
            week_end_date = None
            if week_start:
                try:
                    week_start_date = date.fromisoformat(week_start)
                    week_end_date = week_start_date + timedelta(days=6)
                except ValueError:
                    self._send(
                        400,
                        "application/json",
                        json.dumps({"error": "week_start must be YYYY-MM-DD"}).encode("utf-8"),
                    )
                    return

            slots = []
            for assignment in schedule_data.get("assignments", []):
                try:
                    assignment_date = date.fromisoformat(str(assignment.get("date", "")))
                except Exception:
                    continue
                if week_start_date and week_end_date:
                    if not (week_start_date <= assignment_date <= week_end_date):
                        continue

                tod = str(assignment.get("tod", "")).upper()
                date_str = str(assignment.get("date", ""))
                weather_key = f"{date_str}_{tod}"
                is_advisory = schedule_data.get("weather", {}).get(weather_key) is False
                slot_id = assignment.get("id") or (
                    f"{assignment.get('backpack','')}_{assignment.get('route','')}_{date_str}_{tod}"
                )

                slots.append({
                    "id": slot_id,
                    "backpack": assignment.get("backpack"),
                    "route": assignment.get("route"),
                    "label": assignment.get("label") or ROUTE_LABELS.get(str(assignment.get("route", "")), assignment.get("route")),
                    "date": date_str,
                    "tod": tod,
                    "collector": assignment.get("collector"),
                    "status": assignment.get("status", "claimed"),
                    "weather_advisory": is_advisory,
                })

            payload = {
                "week_start": week_start or schedule_data.get("week_start"),
                "week_end": (str(week_end_date) if week_end_date else schedule_data.get("week_end")),
                "slots": slots,
            }
            self._send(
                200,
                "application/json",
                json.dumps(payload, indent=2).encode("utf-8"),
            )
            return

        # Static files -- served from the generated site output directory.
        # Runtime logs are persisted separately and served from PERSISTED_DIR.
        if path in ("Walks_Log.txt", "Recal_Log.txt"):
            file_path = PERSISTED_DIR / path
        else:
            file_path = SITE_DIR / path
            if path == "dashboard.html" and not file_path.exists():
                _ensure_site_artifacts()
        if not file_path.exists() or not file_path.is_file():
            if path == "dashboard.html" and _bootstrap_errors:
                msg = (
                    "Dashboard files are missing because auto-build failed.\n\n"
                    + "\n".join(f"- {e}" for e in _bootstrap_errors)
                    + "\n\nInstall dependencies and retry:\n"
                    "  pip install -r requirements.txt\n"
                )
                self._send(503, "text/plain; charset=utf-8", msg.encode("utf-8"))
                return
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
        parsed = urllib.parse.urlparse(self.path)
        endpoint = parsed.path
        query_params = urllib.parse.parse_qs(parsed.query)

        # Authenticate /api/drive/poll with GAS_SECRET bearer token.
        # If GAS_SECRET is not set, these endpoints remain open (local dev compatible).
        # /api/rebuild is not gated " it is only triggered by the browser UI.
        _gas_secret = os.environ.get("GAS_SECRET", "")
        if _gas_secret and endpoint == "/api/drive/poll":
            auth_header = self.headers.get("Authorization", "")
            if auth_header != f"Bearer {_gas_secret}":
                self._send(401, "application/json",
                           json.dumps({"error": "unauthorized"}).encode())
                return

        if endpoint == "/api/rerun":
            self._send(
                410,
                "application/json",
                json.dumps({
                    "error": "endpoint retired",
                    "message": "Use /api/rebuild or /api/schedule/rebuild-site",
                }).encode(),
            )
        elif endpoint == "/api/rerun/a":
            self._send(
                410,
                "application/json",
                json.dumps({
                    "error": "endpoint retired",
                    "message": "Backpack-scoped scheduler runs are deprecated.",
                }).encode(),
            )
        elif endpoint == "/api/rerun/b":
            self._send(
                410,
                "application/json",
                json.dumps({
                    "error": "endpoint retired",
                    "message": "Backpack-scoped scheduler runs are deprecated.",
                }).encode(),
            )
        elif endpoint == "/api/rebuild":
            self._stream_response()
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

        elif endpoint == "/api/backpack-status":
            payload, err = self._read_json_body()
            if err:
                self._send(400, "application/json",
                           json.dumps({"error": err}).encode())
                return

            backpack = str(payload.get("backpack", "")).upper().strip()
            holder = str(payload.get("holder", "")).upper().strip()
            location = str(payload.get("location", "")).strip()
            updated_by = str(payload.get("updated_by", "")).upper().strip()

            allowed_locations = {
                "A": {"CCNY"},
                "B": {"CCNY", "LaGuardia"},
            }
            if backpack not in VALID_BACKPACKS:
                self._send(400, "application/json",
                           json.dumps({"error": "backpack must be 'A' or 'B'"}).encode())
                return
            allowed_holders = BACKPACK_TO_COLLECTORS.get(backpack, set())
            if holder and holder not in allowed_holders:
                self._send(400, "application/json",
                           json.dumps({"error": f"holder must be on Backpack {backpack} team"}).encode())
                return
            if location and location not in allowed_locations[backpack]:
                self._send(400, "application/json",
                           json.dumps({"error": f"location must be one of: {', '.join(sorted(allowed_locations[backpack]))}"}).encode())
                return
            if bool(holder) == bool(location):
                self._send(400, "application/json",
                           json.dumps({"error": "provide exactly one holder or location"}).encode())
                return

            with _schedule_write_lock:
                _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
                try:
                    schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
                except ScheduleValidationError as exc:
                    self._send(500, "application/json",
                               json.dumps({"error": f"invalid schedule data: {exc}"}).encode())
                    return

                status = schedule_data.setdefault("backpack_status", {})
                status[backpack] = {
                    "holder": holder,
                    "location": location,
                    "updated_at": datetime.now().isoformat(),
                    "updated_by": updated_by,
                    "source": "manual",
                }

                try:
                    save_schedule(schedule_data, SCHEDULE_OUTPUT, make_backup=True)
                except ScheduleValidationError as exc:
                    self._send(409, "application/json",
                               json.dumps({"error": f"validation failed: {exc}"}).encode())
                    return

                if _gcs_bucket:
                    _upload_to_gcs(SCHEDULE_OUTPUT, "schedule_output.json")

            self._send(200, "application/json",
                       json.dumps({"ok": True, "schedule": schedule_data}).encode())

        elif endpoint == "/api/schedule/claim":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length) if length else b"{}"
                payload = json.loads(body_bytes)
            except Exception:
                self._send(400, "application/json",
                           json.dumps({"error": "bad request"}).encode())
                return

            required = ["backpack", "route", "date", "tod", "collector"]
            missing = [k for k in required if not payload.get(k)]
            if missing:
                self._send(400, "application/json",
                           json.dumps({"error": f"missing fields: {', '.join(missing)}"}).encode())
                return

            backpack = str(payload.get("backpack", "")).upper().strip()
            route = str(payload.get("route", "")).upper().strip()
            date_str = str(payload.get("date", "")).strip()
            tod = str(payload.get("tod", "")).upper().strip()
            collector = str(payload.get("collector", "")).upper().strip()

            if backpack not in VALID_BACKPACKS:
                self._send(400, "application/json",
                           json.dumps({"error": "backpack must be 'A' or 'B'"}).encode())
                return
            if tod not in SLOT_TODS:
                self._send(400, "application/json",
                           json.dumps({"error": "tod must be one of AM, MD, PM"}).encode())
                return
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                self._send(400, "application/json",
                           json.dumps({"error": "date must be YYYY-MM-DD"}).encode())
                return
            if route not in ALLOWED_ROUTES:
                self._send(400, "application/json",
                           json.dumps({"error": f"route must be one of: {', '.join(sorted(ALLOWED_ROUTES))}"}).encode())
                return
            if collector not in ALLOWED_COLLECTORS:
                self._send(400, "application/json",
                           json.dumps({"error": f"collector must be one of: {', '.join(sorted(ALLOWED_COLLECTORS))}"}).encode())
                return
            allowed_for_backpack = BACKPACK_TO_COLLECTORS.get(backpack, set())
            if collector not in allowed_for_backpack:
                self._send(400, "application/json",
                           json.dumps({"error": f"collector {collector} is not eligible for Backpack {backpack}"}).encode())
                return

            parts = route.split("_", 1)
            boro = parts[0] if parts else ""
            neigh = parts[1] if len(parts) > 1 else route
            label = str(payload.get("label") or ROUTE_LABELS.get(route, route))

            with _schedule_write_lock:
                _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
                try:
                    schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
                except ScheduleValidationError as exc:
                    self._send(500, "application/json",
                               json.dumps({"error": f"invalid schedule data: {exc}"}).encode())
                    return

                for assignment in schedule_data.get("assignments", []):
                    if (
                        str(assignment.get("backpack", "")).upper() == backpack
                        and str(assignment.get("date", "")) == date_str
                        and str(assignment.get("tod", "")).upper() == tod
                    ):
                        self._send(409, "application/json",
                                   json.dumps({"error": "backpack already has a claimed walk in this date/tod slot"}).encode())
                        return

                    if (
                        str(assignment.get("collector", "")).upper() == collector
                        and str(assignment.get("date", "")) == date_str
                        and str(assignment.get("tod", "")).upper() == tod
                    ):
                        self._send(409, "application/json",
                                   json.dumps({"error": "collector already assigned in this date/tod slot"}).encode())
                        return

                weather_key = f"{date_str}_{tod}"
                weather_advisory = schedule_data.get("weather", {}).get(weather_key) is False
                now_iso = datetime.now().isoformat()
                assignment = {
                    "id": f"{backpack}_{route}_{date_str}_{tod}",
                    "route": route,
                    "label": label,
                    "boro": boro,
                    "neigh": neigh,
                    "tod": tod,
                    "backpack": backpack,
                    "collector": collector,
                    "date": date_str,
                    "status": "claimed",
                    "claimed_at": now_iso,
                    "claimed_by": collector,
                    "updated_at": now_iso,
                    "weather_advisory": weather_advisory,
                }
                schedule_data.setdefault("assignments", []).append(assignment)
                _refresh_schedule_week_bounds(schedule_data)

                try:
                    save_schedule(schedule_data, SCHEDULE_OUTPUT, make_backup=True)
                except ScheduleValidationError as exc:
                    self._send(400, "application/json",
                               json.dumps({"error": f"validation failed: {exc}"}).encode())
                    return

                if _gcs_bucket:
                    _upload_to_gcs(SCHEDULE_OUTPUT, "schedule_output.json")

            self._send(200, "application/json",
                       json.dumps({"ok": True, "assignment": assignment}).encode())

        elif endpoint == "/api/schedule/unclaim":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length) if length else b"{}"
                payload = json.loads(body_bytes)
            except Exception:
                self._send(400, "application/json",
                           json.dumps({"error": "bad request"}).encode())
                return

            required = ["backpack", "date", "tod"]
            missing = [k for k in required if not payload.get(k)]
            if missing:
                self._send(400, "application/json",
                           json.dumps({"error": f"missing fields: {', '.join(missing)}"}).encode())
                return

            backpack = str(payload.get("backpack", "")).upper().strip()
            date_str = str(payload.get("date", "")).strip()
            tod = str(payload.get("tod", "")).upper().strip()
            collector = str(payload.get("collector", "")).upper().strip()

            with _schedule_write_lock:
                _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
                try:
                    schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
                except ScheduleValidationError as exc:
                    self._send(500, "application/json",
                               json.dumps({"error": f"invalid schedule data: {exc}"}).encode())
                    return

                match_idx = None
                for idx, assignment in enumerate(schedule_data.get("assignments", [])):
                    if (
                        str(assignment.get("backpack", "")).upper() == backpack
                        and str(assignment.get("date", "")) == date_str
                        and str(assignment.get("tod", "")).upper() == tod
                    ):
                        if collector and str(assignment.get("collector", "")).upper() != collector:
                            continue
                        match_idx = idx
                        break

                if match_idx is None:
                    self._send(404, "application/json",
                               json.dumps({"error": "assignment not found"}).encode())
                    return

                removed = schedule_data["assignments"].pop(match_idx)
                _refresh_schedule_week_bounds(schedule_data)
                try:
                    save_schedule(schedule_data, SCHEDULE_OUTPUT, make_backup=True)
                except ScheduleValidationError as exc:
                    self._send(400, "application/json",
                               json.dumps({"error": f"validation failed: {exc}"}).encode())
                    return

                if _gcs_bucket:
                    _upload_to_gcs(SCHEDULE_OUTPUT, "schedule_output.json")

            self._send(200, "application/json",
                       json.dumps({"ok": True, "removed": removed}).encode())

        elif endpoint == "/api/drive/poll":
            count, err = _run_drive_poll(source="gas")
            if err:
                body = json.dumps({"status": "error", "message": err}).encode()
                self._send(500, "application/json", body)
            else:
                body = json.dumps({"status": "ok", "new_files": count}).encode()
                self._send(200, "application/json", body)
        elif endpoint == "/api/force-rebuild":
            # Force immediate rebuild: build weather + rebuild site (no scheduler)
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
            t = threading.Thread(target=_run_weather_and_rebuild_site, daemon=True)
            t.start()
            self._send(200, "application/json",
                       json.dumps({"status": "ok", "message": "Rebuild started -- check back in 30 seconds"}).encode())
        elif endpoint == "/api/schedule/rebuild-site":
            _sched_pin  = os.environ.get("SCHEDULER_PIN", "")
            _gas_secret = os.environ.get("GAS_SECRET", "")
            auth_header = self.headers.get("Authorization", "")
            gas_authed  = bool(_gas_secret and auth_header == f"Bearer {_gas_secret}")
            if not gas_authed:
                payload, err = self._read_json_body()
                if err:
                    self._send(400, "application/json",
                               json.dumps({"error": err}).encode())
                    return
                if _sched_pin and payload.get("pin", "") != _sched_pin:
                    self._send(403, "application/json",
                               json.dumps({"error": "wrong pin"}).encode())
                    return
            t = threading.Thread(target=_run_weather_and_rebuild_site, daemon=True)
            t.start()
            self._send(200, "application/json",
                       json.dumps({"status": "ok", "message": "Scheduler-free rebuild started"}).encode())
        elif endpoint == "/api/notifications/preview":
            payload, err = self._read_json_body()
            if err:
                self._send(400, "application/json",
                           json.dumps({"error": err}).encode())
                return
            query_date = query_params.get("date", [None])[0]
            target_date = _resolve_notification_date(payload.get("date") or query_date)
            requested_channels = _normalize_notification_channels(payload.get("channels"))
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", target_date):
                self._send(400, "application/json",
                           json.dumps({"error": "date must be YYYY-MM-DD"}).encode())
                return
            try:
                preview = _build_notifications_preview(target_date, requested_channels)
            except (ScheduleValidationError, ValueError) as exc:
                self._send(500, "application/json",
                           json.dumps({"error": f"invalid notification preview data: {exc}"}).encode())
                return
            self._send(200, "application/json",
                       json.dumps({"ok": True, "preview": _redact_notification_preview(preview)}).encode())
        elif endpoint == "/api/notifications/send":
            payload, err = self._read_json_body()
            if err:
                self._send(400, "application/json",
                           json.dumps({"error": err}).encode())
                return
            if not self._pin_ok(payload):
                self._send(403, "application/json",
                           json.dumps({"error": "wrong pin"}).encode())
                return
            query_date = query_params.get("date", [None])[0]
            target_date = _resolve_notification_date(payload.get("date") or query_date)
            requested_channels = _normalize_notification_channels(payload.get("channels"))
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", target_date):
                self._send(400, "application/json",
                           json.dumps({"error": "date must be YYYY-MM-DD"}).encode())
                return
            try:
                preview = _build_notifications_preview(target_date, requested_channels)
            except (ScheduleValidationError, ValueError) as exc:
                self._send(500, "application/json",
                           json.dumps({"error": f"invalid notification send data: {exc}"}).encode())
                return

            sender = str(payload.get("sender", "")).strip() or "manual"
            send_results = []
            dry_run = bool(payload.get("dry_run", False))
            for msg in preview.get("messages", []):
                subject = f"EnAACT walk reminder: {msg.get('date')} {msg.get('tod')}"
                body = str(msg.get("message_text", ""))
                destinations = msg.get("destinations", [])
                if not destinations:
                    send_results.append({
                        "collector": msg.get("collector"),
                        "channel": None,
                        "target": None,
                        "status": "skipped",
                        "error": "no opted-in destination configured",
                    })
                    continue
                for destination in destinations:
                    channel = destination.get("channel")
                    target = destination.get("target")
                    if channel == "email":
                        if dry_run:
                            result = {"ok": True, "status": "dry_run"}
                        else:
                            result = _send_email_notification(to_email=str(target), subject=subject, body=body)
                        send_results.append({
                            "collector": msg.get("collector"),
                            "channel": "email",
                            "target": target,
                            **result,
                        })
                    elif channel == "slack":
                        send_results.append({
                            "collector": msg.get("collector"),
                            "channel": "slack",
                            "target": target,
                            "ok": False,
                            "status": "skipped",
                            "error": "Slack transport integration pending",
                        })
                    else:
                        send_results.append({
                            "collector": msg.get("collector"),
                            "channel": channel,
                            "target": target,
                            "ok": False,
                            "status": "skipped",
                            "error": "unsupported notification channel",
                        })
            dispatch_record = {
                "dispatched_at": datetime.now().isoformat(),
                "dispatched_by": sender,
                "date": target_date,
                "assignment_count": preview.get("assignment_count", 0),
                "collector_count": preview.get("collector_count", 0),
                "channels": requested_channels,
                "dry_run": dry_run,
                "email_transport_configured": _email_transport_configured(),
                "send_results": send_results,
                "messages": preview.get("messages", []),
            }
            try:
                NOTIFICATION_LOG.parent.mkdir(parents=True, exist_ok=True)
                with open(NOTIFICATION_LOG, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(dispatch_record) + "\n")
                if _gcs_bucket:
                    _upload_to_gcs(NOTIFICATION_LOG, "notification_dispatch_log.jsonl")
            except Exception as exc:
                self._send(500, "application/json",
                           json.dumps({"error": f"failed to persist dispatch log: {exc}"}).encode())
                return

            self._send(200, "application/json", json.dumps({
                "ok": True,
                "mode": "dry_run" if dry_run else "sent_or_recorded",
                "message": "Notification dispatch processed. Slack transport integration pending.",
                "dispatch": dispatch_record,
            }).encode())
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
            bp = (payload.get("backpack", "") or "").upper()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                self._send(400, "application/json",
                           json.dumps({"error": "invalid date"}).encode())
                return
            if bp not in ("A", "B"):
                self._send(400, "application/json",
                           json.dumps({"error": "backpack must be 'A' or 'B'"}).encode())
                return
            try:
                # Pull the latest Recal_Log from GCS so the append is based on
                # the authoritative bucket copy, not whatever the ephemeral
                # container happens to have on disk.
                _download_from_gcs("Recal_Log.txt", RECAL_LOG)
                RECAL_LOG.parent.mkdir(parents=True, exist_ok=True)
                y, m, d = date_str.split("-")
                entry = f"RECAL_{bp}_{y}{m}{d}\n"
                with open(RECAL_LOG, "a") as fh:
                    fh.write(entry)
                if _gcs_bucket:
                    _upload_to_gcs(RECAL_LOG, "Recal_Log.txt")
            except Exception as exc:
                self._send(500, "application/json",
                           json.dumps({"error": str(exc)}).encode())
                return
            self._send(200, "application/json", json.dumps({"ok": True}).encode())

        elif endpoint == "/api/upload-walk":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                fields, files = _parse_multipart(self.headers, body)
            except Exception as exc:
                self._send(400, "application/json",
                           json.dumps({"error": f"bad request: {exc}"}).encode())
                return

            required = ["backpack", "collector", "borough", "route", "date", "tod"]
            missing = [r for r in required if not fields.get(r)]
            for slot in ("start", "walk", "end"):
                if not (files.get(f"{slot}_time_img") or fields.get(f"{slot}_time_manual")):
                    missing.append(f"{slot}_time")
            if not files.get("gpx_file"):
                missing.append("gpx_file")
            if missing:
                self._send(400, "application/json",
                           json.dumps({"error": f"missing fields: {', '.join(missing)}"}).encode())
                return

            date_clean = re.sub(r"[^0-9]", "", fields["date"])
            walk_code = (
                f"{fields['backpack']}_{fields['collector']}_{fields['borough']}"
                f"_{fields['route']}_{date_clean}_{fields['tod']}"
            ).upper()

            if not _WALK_LOG_RE.match(walk_code):
                self._send(400, "application/json",
                           json.dumps({"error": f"invalid walk code: {walk_code}"}).encode())
                return

            # Stage to GCS holding bucket first (decouples browser from Drive flakiness).
            # On staging success → 200 immediately; mover thread drains to Drive async.
            # On staging failure → fall through to legacy direct-Drive write.
            if upload_buffer.holding_available():
                try:
                    staged = upload_buffer.stage_submission(
                        walk_code=walk_code,
                        fields=fields,
                        files=files,
                        client_ip=self.client_address[0],
                    )
                    self._send(200, "application/json", json.dumps({
                        "ok": True,
                        "walk": walk_code,
                        "submission_id": staged.submission_id,
                        "status": "staged",
                    }).encode())
                    return
                except upload_buffer.StagingError as exc:
                    print(f"[upload] Holding bucket staging failed: {exc} "
                          f"— falling back to direct Drive")

            # Create the walk folder in Drive. Walks_Log.txt is updated only by
            # the regular Drive poll path, not immediately by this upload handler.
            if DRIVE_FOLDER_ID:
                try:
                    svc = _get_drive_write_service()
                    if svc:
                        # Walks/{BOROUGH}/{ROUTE}/{walk_code}/
                        # Match borough and route by prefix so "MN - Manhattan" matches "MN" etc.
                        borough_folder_id = _drive_find_folder_by_prefix(
                            svc, DRIVE_FOLDER_ID, fields["borough"].upper())
                        if not borough_folder_id:
                            borough_folder_id = _drive_create_or_get_folder(
                                svc, DRIVE_FOLDER_ID, fields["borough"].upper())
                        route_folder_id = (_drive_find_folder_by_prefix(
                            svc, borough_folder_id, fields["route"].upper())
                            if borough_folder_id else None)
                        if borough_folder_id and not route_folder_id:
                            route_folder_id = _drive_create_or_get_folder(
                                svc, borough_folder_id, fields["route"].upper())
                        walk_folder_id = _drive_create_or_get_folder(
                            svc, route_folder_id, walk_code) if route_folder_id else None
                        if walk_folder_id and files:
                            # Subfolders named {walk_code}_{LABEL}.
                            # GPX/LOG go to the walk folder root, renamed to include the code.
                            subfolder_map = {
                                "pom":            "POM",
                                "pop":            "POP",
                                "pam":            "PAM",
                                "start_time_img": "TIMES",
                                "walk_time_img":  "TIMES",
                                "end_time_img":   "TIMES",
                            }
                            sub_ids: dict = {}
                            for field_name, file_list in files.items():
                                sub_label = subfolder_map.get(field_name)
                                if sub_label:
                                    folder_name = f"{walk_code}_{sub_label}"
                                    if folder_name not in sub_ids:
                                        sub_ids[folder_name] = _drive_create_or_get_folder(
                                            svc, walk_folder_id, folder_name)
                                    fid = sub_ids[folder_name]
                                    if fid:
                                        for filename, data in file_list:
                                            _drive_upload_file(svc, fid, filename, data)
                                elif field_name == "gpx_file":
                                    for filename, data in file_list:
                                        ext = Path(filename).suffix.lstrip(".").upper()
                                        new_name = f"{walk_code}_{ext}{Path(filename).suffix.lower()}"
                                        _drive_upload_file(svc, walk_folder_id, new_name, data)
                        notes_text = fields.get("notes", "").strip()
                        if notes_text and walk_folder_id:
                            notes_bytes = notes_text.encode("utf-8")
                            _drive_upload_file(svc, walk_folder_id,
                                               f"{walk_code}_Notes.txt", notes_bytes)
                        print(f"[upload] Drive folder ready: {walk_code}")
                except Exception as exc:
                    print(f"[upload] Drive error (non-fatal): {exc}")
            else:
                print(f"[upload] No Drive folder configured; accepted {walk_code} "
                      f"without updating Walks_Log.txt")

            self._send(200, "application/json",
                       json.dumps({"ok": True, "walk": walk_code}).encode())

        elif endpoint == "/api/admin/clear-walks-log":
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
            try:
                WALKS_LOG.write_text("", encoding="utf-8")
                if _gcs_bucket:
                    _upload_to_gcs(WALKS_LOG, "Walks_Log.txt")
                print("[API] Walks_Log.txt cleared")
                self._send(200, "application/json",
                           json.dumps({"ok": True, "message": "Walks_Log.txt cleared"}).encode())
            except Exception as e:
                self._send(500, "application/json",
                           json.dumps({"error": str(e)}).encode())

        elif endpoint == "/api/admin/rebuild-walks-log-now":
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
            try:
                count, err = _run_drive_poll(source="manual-admin")
                if err:
                    self._send(500, "application/json",
                               json.dumps({"error": err}).encode())
                else:
                    body = json.dumps({
                        "ok": True,
                        "message": "Drive poll triggered and log rebuilt",
                        "new_files": count
                    }).encode()
                    self._send(200, "application/json", body)
            except Exception as e:
                self._send(500, "application/json",
                           json.dumps({"error": str(e)}).encode())

        elif endpoint == "/api/admin/get-walks-log":
            try:
                if not WALKS_LOG.exists():
                    entries = []
                else:
                    entries = [
                        l.strip() for l in WALKS_LOG.read_text(encoding="utf-8").splitlines()
                        if l.strip()
                    ]
                body = json.dumps({
                    "ok": True,
                    "count": len(entries),
                    "entries": entries
                }).encode()
                self._send(200, "application/json", body)
            except Exception as e:
                self._send(500, "application/json",
                           json.dumps({"error": str(e)}).encode())

        else:
            self._send(404, "text/plain", b"Unknown endpoint")

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        prefix = "/api/schedule/assignments/"
        if not path.startswith(prefix):
            self._send(404, "text/plain", b"Unknown endpoint")
            return

        assignment_id = urllib.parse.unquote(path[len(prefix):]).strip()
        if not assignment_id:
            self._send(400, "application/json",
                       json.dumps({"error": "missing assignment id"}).encode())
            return

        payload, err = self._read_json_body()
        if err:
            self._send(400, "application/json",
                       json.dumps({"error": err}).encode())
            return

        allowed = {"backpack", "route", "date", "tod", "collector", "label", "status"}
        updates = {k: v for k, v in payload.items() if k in allowed and v is not None}
        if not updates:
            self._send(400, "application/json",
                       json.dumps({"error": "no updatable fields provided"}).encode())
            return

        with _schedule_write_lock:
            _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
            try:
                schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
            except ScheduleValidationError as exc:
                self._send(500, "application/json",
                           json.dumps({"error": f"invalid schedule data: {exc}"}).encode())
                return

            assignments = schedule_data.get("assignments", [])
            _, target = self._find_assignment_by_id(assignments, assignment_id)
            if target is None:
                self._send(404, "application/json",
                           json.dumps({"error": "assignment not found"}).encode())
                return

            for key, value in updates.items():
                if key in {"backpack", "route", "tod", "collector"}:
                    target[key] = str(value).upper().strip()
                else:
                    target[key] = str(value).strip()

            # Keep edit semantics aligned with claim validations.
            backpack = str(target.get("backpack", "")).upper().strip()
            route = str(target.get("route", "")).upper().strip()
            date_str = str(target.get("date", "")).strip()
            tod = str(target.get("tod", "")).upper().strip()
            collector = str(target.get("collector", "")).upper().strip()

            if backpack not in VALID_BACKPACKS:
                self._send(400, "application/json",
                           json.dumps({"error": "backpack must be 'A' or 'B'"}).encode())
                return
            if tod not in SLOT_TODS:
                self._send(400, "application/json",
                           json.dumps({"error": "tod must be one of AM, MD, PM"}).encode())
                return
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                self._send(400, "application/json",
                           json.dumps({"error": "date must be YYYY-MM-DD"}).encode())
                return
            if route not in ALLOWED_ROUTES:
                self._send(400, "application/json",
                           json.dumps({"error": f"route must be one of: {', '.join(sorted(ALLOWED_ROUTES))}"}).encode())
                return
            if collector not in ALLOWED_COLLECTORS:
                self._send(400, "application/json",
                           json.dumps({"error": f"collector must be one of: {', '.join(sorted(ALLOWED_COLLECTORS))}"}).encode())
                return
            allowed_for_backpack = BACKPACK_TO_COLLECTORS.get(backpack, set())
            if collector not in allowed_for_backpack:
                self._send(400, "application/json",
                           json.dumps({"error": f"collector {collector} is not eligible for Backpack {backpack}"}).encode())
                return

            target["backpack"] = backpack
            target["route"] = route
            target["date"] = date_str
            target["tod"] = tod
            target["collector"] = collector

            for assignment in assignments:
                if assignment is target:
                    continue
                if (
                    str(assignment.get("backpack", "")).upper() == backpack
                    and str(assignment.get("date", "")) == date_str
                    and str(assignment.get("tod", "")).upper() == tod
                ):
                    self._send(409, "application/json",
                               json.dumps({"error": "backpack already has a claimed walk in this date/tod slot"}).encode())
                    return
                if (
                    str(assignment.get("collector", "")).upper() == collector
                    and str(assignment.get("date", "")) == date_str
                    and str(assignment.get("tod", "")).upper() == tod
                ):
                    self._send(409, "application/json",
                               json.dumps({"error": "collector already assigned in this date/tod slot"}).encode())
                    return

            if "route" in updates:
                parts = str(target.get("route", "")).split("_", 1)
                target["boro"] = parts[0] if parts else ""
                target["neigh"] = parts[1] if len(parts) > 1 else str(target.get("route", ""))
                if "label" not in updates:
                    target["label"] = ROUTE_LABELS.get(route, route)
            weather_key = f"{date_str}_{tod}"
            target["weather_advisory"] = schedule_data.get("weather", {}).get(weather_key) is False
            target["updated_at"] = datetime.now().isoformat()
            explicit_id = str(target.get("id", "")).strip()
            if explicit_id:
                target["id"] = explicit_id
            else:
                target["id"] = self._assignment_fallback_id(target, sep="_")

            try:
                save_schedule(schedule_data, SCHEDULE_OUTPUT, make_backup=True)
            except ScheduleValidationError as exc:
                self._send(409, "application/json",
                           json.dumps({"error": f"validation failed: {exc}"}).encode())
                return

            if _gcs_bucket:
                _upload_to_gcs(SCHEDULE_OUTPUT, "schedule_output.json")

        self._send(200, "application/json",
                   json.dumps({"ok": True, "assignment": target}).encode())

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        prefix = "/api/schedule/assignments/"
        if not path.startswith(prefix):
            self._send(404, "text/plain", b"Unknown endpoint")
            return

        assignment_id = urllib.parse.unquote(path[len(prefix):]).strip()
        if not assignment_id:
            self._send(400, "application/json",
                       json.dumps({"error": "missing assignment id"}).encode())
            return

        payload, err = self._read_json_body()
        if err:
            self._send(400, "application/json",
                       json.dumps({"error": err}).encode())
            return

        with _schedule_write_lock:
            _download_from_gcs("schedule_output.json", SCHEDULE_OUTPUT)
            try:
                schedule_data = load_schedule(SCHEDULE_OUTPUT, strict=False)
            except ScheduleValidationError as exc:
                self._send(500, "application/json",
                           json.dumps({"error": f"invalid schedule data: {exc}"}).encode())
                return

            removed = None
            assignments = schedule_data.get("assignments", [])
            match_idx, _ = self._find_assignment_by_id(assignments, assignment_id)
            if match_idx is not None:
                removed = assignments.pop(match_idx)
            if removed is None:
                self._send(404, "application/json",
                           json.dumps({"error": "assignment not found"}).encode())
                return

            try:
                save_schedule(schedule_data, SCHEDULE_OUTPUT, make_backup=True)
            except ScheduleValidationError as exc:
                self._send(409, "application/json",
                           json.dumps({"error": f"validation failed: {exc}"}).encode())
                return

            if _gcs_bucket:
                _upload_to_gcs(SCHEDULE_OUTPUT, "schedule_output.json")

        self._send(200, "application/json",
                   json.dumps({"ok": True, "removed": removed}).encode())

    def _stream_response(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            _stream_script(self.wfile, BUILD_DASHBOARD, "build_dashboard.py")

            done = "\n[All done -- reload the page to see updated dashboard]\n"
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

    # Pre-built dashboard HTML -- restore so server can serve immediately even if rebuild fails
    for _blob, _local in (
        ("dashboard.html",            DASHBOARD_HTML),
    ):
        _download_from_gcs(_blob, _local)

    print("[gcs-restore] State restored from GCS")


def main():
    parser = argparse.ArgumentParser(description="Field campaign dashboard server")
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

    # Ensure generated site files exist so the dashboard link works on fresh runs.
    _ensure_site_artifacts()

    # "" Start Drive walk-log polling thread """"""""""""""""""""""""""""""""""""
    if DRIVE_POLL_INTERVAL > 0:
        t = threading.Thread(target=_drive_poll_thread, daemon=True)
        t.start()
        print(f"  Walk-log poll : active (every {DRIVE_POLL_INTERVAL}s)")
    else:
        print(f"  Walk-log poll : DISABLED -- relying on GAS push triggers")

    # "" Start GCS upload-buffer mover """"""""""""""""""""""""""""""""""""""""""
    if upload_buffer.init_holding_bucket():
        drive_mover.bind(
            get_drive_service=_get_drive_write_service,
            get_folder_id=lambda: DRIVE_FOLDER_ID,
            find_folder_by_prefix=_drive_find_folder_by_prefix,
            create_or_get_folder=_drive_create_or_get_folder,
        )
        drive_mover.start_mover_thread()
        print(f"  Upload buffer : active (bucket: {os.environ.get('UPLOAD_HOLDING_BUCKET')})")
    else:
        print(f"  Upload buffer : DISABLED -- uploads will write directly to Drive")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"")
    print(f"  NYC Field Campaign Dashboard -- server")
    print(f"  Dashboard  : {url}")
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
