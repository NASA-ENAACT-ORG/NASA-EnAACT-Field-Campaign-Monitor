"""drive_mover.py — background worker that drains the GCS holding bucket → Drive.

Runs as a daemon thread. Every UPLOAD_MOVER_POLL_INTERVAL seconds:
  1. lists ready/processing submissions from the holding bucket
  2. claims and uploads each one to Drive (with per-file idempotency)
  3. archives or fails the submission

Drive helpers are injected via bind() so we don't need to re-import serve.py
into a separate module namespace.
"""
from __future__ import annotations

import io
import mimetypes
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import upload_buffer

MOVER_POLL_INTERVAL = int(os.environ.get("UPLOAD_MOVER_POLL_INTERVAL", "15"))
MAX_ATTEMPTS = int(os.environ.get("UPLOAD_MAX_ATTEMPTS", "6"))
DONE_RETENTION_DAYS = int(os.environ.get("UPLOAD_DONE_RETENTION_DAYS", "7"))
FAILED_RETENTION_DAYS = int(os.environ.get("UPLOAD_FAILED_RETENTION_DAYS", "30"))

_cleanup_last: float = 0.0
_thread_started = False
_helpers: dict = {}


class TransientDriveError(Exception):
    """Drive call failed but might succeed on retry."""


class PermanentDriveError(Exception):
    """Drive call will not succeed without manual intervention."""


# ── Public API ───────────────────────────────────────────────────────────────

def bind(*, get_drive_service, get_folder_id, find_folder_by_prefix,
         create_or_get_folder) -> None:
    """Inject helpers from serve.py. Must be called before start_mover_thread()."""
    _helpers["get_drive_service"] = get_drive_service
    _helpers["get_folder_id"] = get_folder_id
    _helpers["find_folder_by_prefix"] = find_folder_by_prefix
    _helpers["create_or_get_folder"] = create_or_get_folder


def start_mover_thread() -> None:
    global _thread_started
    if _thread_started:
        return
    if not _helpers:
        print("[upload-mover] ERROR: bind() not called — refusing to start")
        return
    _thread_started = True
    t = threading.Thread(target=_mover_loop, daemon=True, name="drive-mover")
    t.start()
    print(f"[upload-mover] Started (interval={MOVER_POLL_INTERVAL}s, "
          f"max_attempts={MAX_ATTEMPTS})")


# ── Loop ─────────────────────────────────────────────────────────────────────

def _mover_loop() -> None:
    global _cleanup_last
    while True:
        try:
            refs = upload_buffer.list_pending()
            for ref in refs:
                try:
                    _process_one(ref)
                except Exception as exc:
                    print(f"[upload-mover] _process_one error for "
                          f"{ref.submission_id}: {exc}")
                    traceback.print_exc()

            now = time.time()
            if now - _cleanup_last > 3600:
                _cleanup_last = now
                upload_buffer.cleanup_old("done/", DONE_RETENTION_DAYS)
                upload_buffer.cleanup_old("failed/", FAILED_RETENTION_DAYS)
        except Exception as exc:
            print(f"[upload-mover] Loop error: {exc}")
            traceback.print_exc()
        time.sleep(MOVER_POLL_INTERVAL)


def _process_one(ref) -> None:
    claimed = upload_buffer.try_claim(ref)
    if claimed is None:
        return
    try:
        try:
            _move_to_drive(claimed)
        except TransientDriveError as exc:
            attempts = len(claimed.manifest.get("attempts", []))
            if attempts >= MAX_ATTEMPTS:
                upload_buffer.fail_submission(
                    claimed, f"transient errors exhausted ({attempts}): {exc}")
                return
            # Reset to ready so next loop iteration retries.
            claimed.manifest["status"] = "ready"
            upload_buffer.write_manifest(claimed)
            print(f"[upload-mover] Transient error on {claimed.ref.submission_id} "
                  f"(attempt {attempts}/{MAX_ATTEMPTS}): {exc}")
            return
        except PermanentDriveError as exc:
            upload_buffer.fail_submission(claimed, f"permanent error: {exc}")
            return

        upload_buffer.archive_submission(claimed)
    finally:
        upload_buffer.release_claim(claimed)


# ── Drive transfer ───────────────────────────────────────────────────────────

def _move_to_drive(claimed) -> None:
    svc = _helpers["get_drive_service"]()
    if svc is None:
        raise TransientDriveError("Drive auth unavailable")

    drive_folder_id = _helpers["get_folder_id"]()
    if not drive_folder_id:
        raise PermanentDriveError("GOOGLE_DRIVE_WALKS_FOLDER_ID not set")

    manifest = claimed.manifest
    walk_code = manifest["walk_code"]
    fields = manifest.get("fields", {})
    target = manifest.get("drive_target", {})
    folder_path = target.get("folder_path", [])
    subfolder_map = target.get("subfolder_map", {})

    attempt: dict = {
        "started_at": _utc_now_iso(),
        "ended_at": None,
        "result": None,
        "error": None,
        "uploaded_drive_ids": _accumulated_uploaded_ids(manifest),
    }

    def _persist_attempt(result: str, error: str | None):
        attempt["ended_at"] = _utc_now_iso()
        attempt["result"] = result
        attempt["error"] = error
        manifest.setdefault("attempts", []).append(attempt)
        upload_buffer.write_manifest(claimed)

    try:
        borough_code = (
            folder_path[0] if len(folder_path) > 0 else fields.get("borough", "")
        ).upper()
        route_code = (
            folder_path[1] if len(folder_path) > 1 else fields.get("route", "")
        ).upper()

        borough_id = (_helpers["find_folder_by_prefix"](svc, drive_folder_id, borough_code)
                      or _helpers["create_or_get_folder"](svc, drive_folder_id, borough_code))
        if not borough_id:
            raise TransientDriveError(f"could not get borough folder '{borough_code}'")

        route_id = (_helpers["find_folder_by_prefix"](svc, borough_id, route_code)
                    or _helpers["create_or_get_folder"](svc, borough_id, route_code))
        if not route_id:
            raise TransientDriveError(f"could not get route folder '{route_code}'")

        walk_folder_id = _helpers["create_or_get_folder"](svc, route_id, walk_code)
        if not walk_folder_id:
            raise TransientDriveError(f"could not get walk folder '{walk_code}'")

        subfolder_ids: dict[str, str] = {}
        for fentry in manifest.get("files", []):
            blob_path = fentry["blob_path"]
            if blob_path in attempt["uploaded_drive_ids"]:
                continue

            field_name = fentry["field"]
            sub_label = subfolder_map.get(field_name)

            if field_name == "gpx_file":
                target_folder_id = walk_folder_id
                ext = Path(fentry["original_filename"]).suffix
                target_filename = f"{walk_code}_{ext.lstrip('.').upper()}{ext.lower()}"
            elif sub_label:
                folder_name = f"{walk_code}_{sub_label}"
                if folder_name not in subfolder_ids:
                    fid = _helpers["create_or_get_folder"](svc, walk_folder_id, folder_name)
                    if not fid:
                        raise TransientDriveError(f"could not create '{folder_name}'")
                    subfolder_ids[folder_name] = fid
                target_folder_id = subfolder_ids[folder_name]
                target_filename = fentry["original_filename"]
            else:
                continue  # unknown field — skip

            existing_id = _drive_find_file(svc, target_folder_id, target_filename)
            if existing_id:
                attempt["uploaded_drive_ids"][blob_path] = existing_id
                upload_buffer.write_manifest(claimed)
                continue

            try:
                fp = upload_buffer.open_blob_stream(blob_path)
            except Exception as exc:
                raise TransientDriveError(
                    f"could not open holding blob {blob_path}: {exc}")
            try:
                drive_id = _upload_stream_to_drive(
                    svc, target_folder_id, target_filename, fp)
            finally:
                try:
                    fp.close()
                except Exception:
                    pass

            attempt["uploaded_drive_ids"][blob_path] = drive_id
            upload_buffer.write_manifest(claimed)

        notes_text = (fields.get("notes") or "").strip()
        if notes_text:
            notes_filename = f"{walk_code}_Notes.txt"
            existing_id = _drive_find_file(svc, walk_folder_id, notes_filename)
            if not existing_id:
                _upload_stream_to_drive(
                    svc, walk_folder_id, notes_filename,
                    io.BytesIO(notes_text.encode("utf-8")),
                )

        _persist_attempt("success", None)

    except TransientDriveError as exc:
        _persist_attempt("transient_error", str(exc))
        raise
    except PermanentDriveError as exc:
        _persist_attempt("permanent_error", str(exc))
        raise
    except Exception as exc:
        _persist_attempt("transient_error", f"{type(exc).__name__}: {exc}")
        raise TransientDriveError(str(exc)) from exc


def _accumulated_uploaded_ids(manifest: dict) -> dict:
    merged: dict = {}
    for a in manifest.get("attempts", []):
        merged.update(a.get("uploaded_drive_ids", {}))
    return merged


def _drive_find_file(svc, folder_id: str, name: str) -> str | None:
    """Return file id if a non-trashed file with `name` exists in folder_id."""
    try:
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name='{safe}' and '{folder_id}' in parents and trashed=false"
        r = svc.files().list(q=q, fields="files(id)", pageSize=1).execute()
        files = r.get("files", [])
        return files[0]["id"] if files else None
    except Exception as exc:
        print(f"[upload-mover] Drive name probe error for '{name}': {exc}")
        return None


def _upload_stream_to_drive(svc, folder_id: str, filename: str, fp) -> str:
    """Resumable upload of a file-like object to Drive. Returns the new file id."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload

    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    media = MediaIoBaseUpload(fp, mimetype=mime, resumable=True,
                              chunksize=5 * 1024 * 1024)
    meta = {"name": filename, "parents": [folder_id]}
    try:
        req = svc.files().create(body=meta, media_body=media, fields="id")
        resp = None
        while resp is None:
            try:
                _, resp = req.next_chunk(num_retries=5)
            except HttpError as e:
                code = getattr(e, "status_code", None) or getattr(e.resp, "status", None)
                if code in (429, 500, 502, 503, 504, 401, 403):
                    raise TransientDriveError(f"Drive HTTP {code}: {e}") from e
                raise PermanentDriveError(f"Drive HTTP {code}: {e}") from e
        drive_id = resp.get("id", "")
        print(f"[upload-mover] Uploaded '{filename}' -> Drive id={drive_id}")
        return drive_id
    except (TransientDriveError, PermanentDriveError):
        raise
    except Exception as exc:
        raise TransientDriveError(f"upload error: {exc}") from exc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
