"""upload_buffer.py — GCS holding bucket for staging walk uploads.

Stages submissions to a GCS bucket first; a background mover thread asynchronously
moves the data into Drive. Decouples the browser response from Drive flakiness —
a 200 means files are durable in GCS, not necessarily yet in Drive.

Backends:
  - GCS (production):  UPLOAD_HOLDING_BUCKET=upload_holding_bucket
  - Local (dev/test):  UPLOAD_HOLDING_BUCKET=local:./tmp/holding
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from shared.paths import PERSISTED_DIR

SCHEMA_VERSION = 1


class StagingError(Exception):
    """Raised when staging to the holding bucket fails."""


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class FileEntry:
    field: str
    index: int
    original_filename: str
    blob_path: str
    size: int
    md5: str

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "index": self.index,
            "original_filename": self.original_filename,
            "blob_path": self.blob_path,
            "size": self.size,
            "md5": self.md5,
        }


@dataclass
class StagedSubmission:
    submission_id: str
    walk_code: str
    manifest_blob_path: str
    files: list[FileEntry]


@dataclass
class ManifestRef:
    submission_id: str
    walk_code: str
    blob_path: str
    status: str
    generation: int | None
    time_created: datetime | None


@dataclass
class ClaimedSubmission:
    ref: ManifestRef
    manifest: dict


# ── Module state ─────────────────────────────────────────────────────────────

_backend: "_Backend | None" = None
_initialized = False
_inproc_locks_master = threading.Lock()
_inproc_locks: dict[str, threading.Lock] = {}


# ── Public API ───────────────────────────────────────────────────────────────

def init_holding_bucket() -> bool:
    """Idempotent. Reads UPLOAD_HOLDING_BUCKET. Returns True on success."""
    global _backend, _initialized
    if _initialized:
        return _backend is not None
    _initialized = True

    bucket_spec = os.environ.get("UPLOAD_HOLDING_BUCKET", "").strip()
    if not bucket_spec:
        print("[upload-buffer] Disabled (UPLOAD_HOLDING_BUCKET not set)")
        return False

    try:
        if bucket_spec.startswith("local:"):
            path = Path(bucket_spec[len("local:"):]).resolve()
            path.mkdir(parents=True, exist_ok=True)
            _backend = _LocalBackend(path)
            print(f"[upload-buffer] Initialized — local backend at {path}")
        else:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_spec)
            list(client.list_blobs(bucket, max_results=1))  # auth probe
            _backend = _GCSBackend(client, bucket)
            print(f"[upload-buffer] Initialized — GCS bucket {bucket_spec}")
        return True
    except Exception as exc:
        print(f"[upload-buffer] Init failed: {exc}")
        _backend = None
        return False


def holding_available() -> bool:
    if not _initialized:
        init_holding_bucket()
    return _backend is not None


def stage_submission(walk_code: str, fields: dict, files: dict, client_ip: str) -> StagedSubmission:
    """Synchronously upload all files + manifest to the holding bucket.

    Two-phase write so partial submissions are detectable: manifest is written
    first with status='staging', then files, then manifest is rewritten with
    status='ready'. list_pending() ignores 'staging' until the orphan reaper
    expires it.

    Raises StagingError on any failure; caller should fall back to direct-Drive.
    """
    if _backend is None:
        raise StagingError("holding bucket not initialized")

    submission_id = _generate_submission_id()
    base = f"pending/{walk_code}/{submission_id}"
    manifest_path = f"{base}/manifest.json"
    received_at = _utc_now_iso()

    initial_manifest = {
        "schema_version": SCHEMA_VERSION,
        "submission_id": submission_id,
        "walk_code": walk_code,
        "received_at": received_at,
        "client_ip": client_ip,
        "status": "staging",
        "fields": fields,
        "files": [],
        "drive_target": _build_drive_target(walk_code, fields),
        "attempts": [],
    }

    try:
        _backend.upload_string(manifest_path, _to_json_bytes(initial_manifest))
    except Exception as exc:
        raise StagingError(f"failed to write initial manifest: {exc}") from exc

    file_entries: list[FileEntry] = []
    try:
        for field_name, file_list in files.items():
            for idx, (filename, data) in enumerate(file_list):
                safe_name = _sanitize_filename(filename)
                blob_path = f"{base}/files/{field_name}/{idx:03d}_{safe_name}"
                _backend.upload_string(blob_path, data)
                file_entries.append(FileEntry(
                    field=field_name,
                    index=idx,
                    original_filename=filename,
                    blob_path=blob_path,
                    size=len(data),
                    md5=hashlib.md5(data).hexdigest(),
                ))
    except Exception as exc:
        _safe_delete_prefix(base)
        raise StagingError(f"failed to upload file blob: {exc}") from exc

    final_manifest = dict(initial_manifest)
    final_manifest["status"] = "ready"
    final_manifest["files"] = [e.to_dict() for e in file_entries]
    try:
        _backend.upload_string(manifest_path, _to_json_bytes(final_manifest))
    except Exception as exc:
        _safe_delete_prefix(base)
        raise StagingError(f"failed to finalize manifest: {exc}") from exc

    print(f"[upload-buffer] Staged {walk_code}/{submission_id}: {len(file_entries)} file(s)")
    return StagedSubmission(
        submission_id=submission_id,
        walk_code=walk_code,
        manifest_blob_path=manifest_path,
        files=file_entries,
    )


def list_pending() -> list[ManifestRef]:
    """Return manifests under pending/ with status in {ready, processing}.

    Reaps orphaned status='staging' manifests older than UPLOAD_STAGING_TTL_MIN
    by deleting their entire prefix.
    """
    if _backend is None:
        return []
    ttl_min = int(os.environ.get("UPLOAD_STAGING_TTL_MIN", "30"))
    cutoff = time.time() - ttl_min * 60
    refs: list[ManifestRef] = []

    for blob in _backend.list_blobs("pending/"):
        if not blob.name.endswith("/manifest.json"):
            continue
        try:
            data, generation = _backend.download_with_generation(blob.name)
            manifest = json.loads(data.decode("utf-8"))
        except Exception as exc:
            print(f"[upload-buffer] Failed to read {blob.name}: {exc}")
            continue

        status = manifest.get("status", "")
        sid = manifest.get("submission_id", "")
        walk_code = manifest.get("walk_code", "")
        time_created = blob.time_created

        if status == "staging":
            tc_ts = time_created.timestamp() if time_created else 0
            if tc_ts and tc_ts < cutoff:
                prefix = f"pending/{walk_code}/{sid}"
                print(f"[upload-buffer] Reaping orphan staging submission: {prefix}")
                _safe_delete_prefix(prefix)
            continue

        if status not in ("ready", "processing"):
            continue

        refs.append(ManifestRef(
            submission_id=sid,
            walk_code=walk_code,
            blob_path=blob.name,
            status=status,
            generation=generation,
            time_created=time_created,
        ))
    return refs


def try_claim(ref: ManifestRef) -> ClaimedSubmission | None:
    """Acquire in-process lock and CAS-flip manifest to status='processing'.

    Returns None if another worker holds the in-process lock or won the CAS.
    Caller must call release_claim() when done.
    """
    if _backend is None:
        return None

    with _inproc_locks_master:
        lock = _inproc_locks.setdefault(ref.submission_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return None

    success = False
    try:
        data, generation = _backend.download_with_generation(ref.blob_path)
        manifest = json.loads(data.decode("utf-8"))
        if manifest.get("status") not in ("ready", "processing"):
            return None
        manifest["status"] = "processing"
        try:
            _backend.upload_string(
                ref.blob_path, _to_json_bytes(manifest),
                if_generation_match=generation,
            )
        except _PreconditionFailed:
            return None
        ref.generation = (generation + 1) if generation is not None else None
        claimed = ClaimedSubmission(ref=ref, manifest=manifest)
        success = True
        return claimed
    except Exception as exc:
        print(f"[upload-buffer] try_claim error for {ref.submission_id}: {exc}")
        return None
    finally:
        if not success:
            try:
                lock.release()
            except RuntimeError:
                pass


def release_claim(claimed: ClaimedSubmission) -> None:
    with _inproc_locks_master:
        lock = _inproc_locks.get(claimed.ref.submission_id)
    if lock is None:
        return
    try:
        lock.release()
    except RuntimeError:
        pass


def write_manifest(claimed: ClaimedSubmission) -> None:
    """Persist claimed.manifest back to the holding bucket."""
    if _backend is None:
        return
    _backend.upload_string(claimed.ref.blob_path, _to_json_bytes(claimed.manifest))


def open_blob_stream(blob_path: str):
    """Return a binary file-like object for streaming reads. Caller must close."""
    if _backend is None:
        raise StagingError("holding bucket not initialized")
    return _backend.open_read(blob_path)


def archive_submission(claimed: ClaimedSubmission) -> None:
    """Copy manifest to done/, delete pending/ payload."""
    if _backend is None:
        return
    sid = claimed.ref.submission_id
    walk_code = claimed.ref.walk_code
    dst = f"done/{walk_code}/{sid}/manifest.json"
    claimed.manifest["status"] = "done"
    _backend.upload_string(dst, _to_json_bytes(claimed.manifest))
    _safe_delete_prefix(f"pending/{walk_code}/{sid}")
    print(f"[upload-buffer] Archived {walk_code}/{sid} -> {dst}")


def fail_submission(claimed: ClaimedSubmission, error: str) -> None:
    """Move payload to failed/ and write a failure marker for the dashboard."""
    if _backend is None:
        return
    sid = claimed.ref.submission_id
    walk_code = claimed.ref.walk_code
    claimed.manifest["status"] = "failed"
    src_prefix = f"pending/{walk_code}/{sid}"
    dst_prefix = f"failed/{walk_code}/{sid}"
    try:
        for blob in _backend.list_blobs(src_prefix + "/"):
            relative = blob.name[len(src_prefix) + 1:]
            _backend.copy(blob.name, f"{dst_prefix}/{relative}")
        _backend.upload_string(
            f"{dst_prefix}/manifest.json", _to_json_bytes(claimed.manifest))
        _safe_delete_prefix(src_prefix)
    except Exception as exc:
        print(f"[upload-buffer] fail_submission move error: {exc}")

    _write_failure_marker(sid, walk_code, error)
    print(f"[upload-mover] PERMANENT FAILURE walk_code={walk_code} "
          f"submission_id={sid} error={error}")


def cleanup_old(prefix: str, max_age_days: int) -> int:
    """Delete blobs under prefix whose time_created is older than max_age_days."""
    if _backend is None or max_age_days < 0:
        return 0
    cutoff = time.time() - max_age_days * 86400
    deleted = 0
    for blob in _backend.list_blobs(prefix):
        tc = blob.time_created
        if tc is not None and tc.timestamp() < cutoff:
            try:
                _backend.delete(blob.name)
                deleted += 1
            except Exception as exc:
                print(f"[upload-buffer] cleanup delete error ({blob.name}): {exc}")
    if deleted:
        print(f"[upload-buffer] cleanup_old({prefix}) deleted {deleted} blob(s)")
    return deleted


# ── Internals ────────────────────────────────────────────────────────────────

def _generate_submission_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{secrets.token_hex(3)}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name or "unnamed")
    return cleaned[:200] or "unnamed"


def _to_json_bytes(obj) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=False).encode("utf-8")


def _build_drive_target(walk_code: str, fields: dict) -> dict:
    return {
        "folder_path": [
            (fields.get("borough") or "").upper(),
            (fields.get("route") or "").upper(),
            walk_code,
        ],
        "subfolder_map": {
            "pom": "POM", "pop": "POP", "pam": "PAM",
            "start_time_img": "TIMES",
            "walk_time_img": "TIMES",
            "end_time_img": "TIMES",
        },
    }


def _safe_delete_prefix(prefix: str) -> None:
    if _backend is None:
        return
    try:
        list_prefix = prefix if prefix.endswith("/") else prefix + "/"
        for blob in list(_backend.list_blobs(list_prefix)):
            try:
                _backend.delete(blob.name)
            except Exception:
                pass
    except Exception as exc:
        print(f"[upload-buffer] delete_prefix error ({prefix}): {exc}")


def _write_failure_marker(submission_id: str, walk_code: str, error: str) -> None:
    """Append a record to upload_failures.json and push to the state GCS bucket."""
    PERSISTED_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = PERSISTED_DIR / "upload_failures.json"
    records: list = []
    if marker_path.exists():
        try:
            loaded = json.loads(marker_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                records = loaded
        except Exception:
            records = []
    records.append({
        "submission_id": submission_id,
        "walk_code": walk_code,
        "error": error,
        "failed_at": _utc_now_iso(),
    })
    records = records[-200:]  # bound the file
    marker_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    try:
        from shared import gcs as _shared_gcs
        _shared_gcs.push(marker_path, "upload_failures.json")
    except Exception as exc:
        print(f"[upload-buffer] failure marker push warning: {exc}")


# ── Backends ─────────────────────────────────────────────────────────────────

class _PreconditionFailed(Exception):
    """Raised when an if_generation_match precondition fails."""


@dataclass
class _BlobInfo:
    name: str
    generation: int | None
    time_created: datetime | None
    size: int | None


class _Backend:
    def upload_string(self, path: str, data: bytes,
                      if_generation_match: int | None = None) -> int | None:
        raise NotImplementedError

    def download_with_generation(self, path: str) -> tuple[bytes, int | None]:
        raise NotImplementedError

    def list_blobs(self, prefix: str) -> Iterator[_BlobInfo]:
        raise NotImplementedError

    def delete(self, path: str) -> None:
        raise NotImplementedError

    def copy(self, src: str, dst: str) -> None:
        raise NotImplementedError

    def open_read(self, path: str):
        raise NotImplementedError


class _GCSBackend(_Backend):
    def __init__(self, client, bucket):
        self._client = client
        self._bucket = bucket

    def upload_string(self, path, data, if_generation_match=None):
        from google.api_core.exceptions import PreconditionFailed
        blob = self._bucket.blob(path)
        try:
            if if_generation_match is not None:
                blob.upload_from_string(data, if_generation_match=if_generation_match)
            else:
                blob.upload_from_string(data)
        except PreconditionFailed as exc:
            raise _PreconditionFailed(str(exc)) from exc
        return blob.generation

    def download_with_generation(self, path):
        blob = self._bucket.blob(path)
        data = blob.download_as_bytes()
        return data, blob.generation

    def list_blobs(self, prefix):
        for b in self._client.list_blobs(self._bucket, prefix=prefix):
            yield _BlobInfo(
                name=b.name,
                generation=b.generation,
                time_created=b.time_created,
                size=b.size,
            )

    def delete(self, path):
        try:
            self._bucket.blob(path).delete()
        except Exception as exc:
            # Treat "not found" as success.
            if "404" not in str(exc) and "Not Found" not in str(exc):
                raise

    def copy(self, src, dst):
        src_blob = self._bucket.blob(src)
        self._bucket.copy_blob(src_blob, self._bucket, new_name=dst)

    def open_read(self, path):
        return self._bucket.blob(path).open("rb")


class _LocalBackend(_Backend):
    """Filesystem-backed dev/test backend. CAS only checks 'must not exist'."""

    def __init__(self, root: Path):
        self._root = root

    def _full(self, path: str) -> Path:
        return self._root / path

    def upload_string(self, path, data, if_generation_match=None):
        full = self._full(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        if if_generation_match == 0 and full.exists():
            raise _PreconditionFailed(f"{path} already exists")
        full.write_bytes(data)
        return int(full.stat().st_mtime_ns)

    def download_with_generation(self, path):
        full = self._full(path)
        return full.read_bytes(), int(full.stat().st_mtime_ns)

    def list_blobs(self, prefix):
        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self._root).as_posix()
            if not rel.startswith(prefix):
                continue
            stat = path.stat()
            yield _BlobInfo(
                name=rel,
                generation=int(stat.st_mtime_ns),
                time_created=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                size=stat.st_size,
            )

    def delete(self, path):
        full = self._full(path)
        if full.exists():
            full.unlink()
        # Best-effort empty-dir cleanup.
        parent = full.parent
        try:
            while parent != self._root and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except Exception:
            pass

    def copy(self, src, dst):
        src_full = self._full(src)
        dst_full = self._full(dst)
        dst_full.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_full, dst_full)

    def open_read(self, path):
        return self._full(path).open("rb")
