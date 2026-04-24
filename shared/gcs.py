"""GCS bucket I/O helpers.

All pipeline scripts and the server call these functions directly to push
outputs to the bucket and pull inputs back. When GCS_BUCKET is unset (local
development without cloud credentials) the helpers become no-ops so scripts
continue to read/write the local filesystem unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

_gcs_client = None
_gcs_bucket = None
_initialized = False


def init_gcs():
    """Initialize the GCS client from the GCS_BUCKET env var. Idempotent."""
    global _gcs_client, _gcs_bucket, _initialized
    if _initialized:
        return _gcs_bucket is not None
    _initialized = True

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
        _gcs_bucket = None
        return False


def bucket_available() -> bool:
    """True if a GCS bucket has been successfully initialized."""
    if not _initialized:
        init_gcs()
    return _gcs_bucket is not None


def download(gcs_path: str, local_path: Path) -> bool:
    """Download gcs_path to local_path. Returns True on success."""
    if not bucket_available():
        return False
    try:
        blob = _gcs_bucket.blob(gcs_path)
        if blob.exists():
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local_path))
            print(f"[gcs] Downloaded: {gcs_path} → {local_path}")
            return True
        print(f"[gcs] Blob not found in bucket: {gcs_path}")
    except Exception as e:
        print(f"[gcs] Download error ({gcs_path}): {e}")
    return False


def upload(local_path: Path, gcs_path: str) -> bool:
    """Upload local_path to gcs_path. Returns True on success."""
    if not bucket_available():
        return False
    try:
        blob = _gcs_bucket.blob(gcs_path)
        blob.upload_from_filename(str(local_path))
        print(f"[gcs] Uploaded: {local_path} → {gcs_path}")
        return True
    except Exception as e:
        print(f"[gcs] Upload error ({gcs_path}): {e}")
        return False


def pull_if_available(gcs_path: str, local_path: Path) -> Path:
    """Download gcs_path into local_path if GCS is set up. Returns local_path.

    Use at read sites so the local file reflects the authoritative bucket copy
    before a script reads it. Silently no-ops when GCS is unavailable, leaving
    whatever is already on local disk in place.
    """
    download(gcs_path, local_path)
    return local_path


def push(local_path: Path, gcs_path: str) -> None:
    """Upload local_path to gcs_path if GCS is set up. No-op otherwise.

    Use at write sites immediately after the script writes local_path so the
    bucket copy is updated in the same step, not by a downstream uploader.
    """
    upload(local_path, gcs_path)
