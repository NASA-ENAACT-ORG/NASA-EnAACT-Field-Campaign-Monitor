#!/usr/bin/env python3
"""
drive_sync.py — Sync walks_log.txt from Google Drive Nasa_enaact folder structure
====================================================================================
Periodically polls the Google Drive folder structure (NASA_EnAACT_Research/Walks/[borough]/[route]/[combo_names])
and appends new walk entries to walks_log.txt.

Runs every 60 seconds and logs all activity to drive_sync.log.

SETUP:
1. Ensure drive-service-account.json exists in the same folder as this script
2. Install dependencies: pip install google-auth google-auth-httplib2 google-auth-oauthlib google-api-python-client

RUN:
python drive_sync.py
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple

# Google Drive API imports
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION & LOGGING
# ─────────────────────────────────────────────────────────────────────────────

import sys as _sys
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from shared.paths import WALKS_LOG, SERVICE_ACCOUNT_KEY as SERVICE_ACCOUNT_JSON, DRIVE_SYNC_LOG as LOG_FILE
from shared.gcs import pull_if_available as gcs_pull, push as gcs_push

BASE_DIR = _REPO_ROOT

# Google Drive API scopes
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Valid values for walk codes
VALID_BACKPACKS = {"A", "B", "X"}
VALID_BOROUGHS = {"MN", "BK", "QN", "BX"}
VALID_TODS = {"AM", "MD", "PM"}

# Valid route codes per borough (folder names start with these 2-char codes)
VALID_ROUTES: Dict[str, set] = {
    "MN": {"HT", "WH", "UE", "MT", "LE"},
    "BX": {"HP", "NW"},
    "BK": {"DT", "WB", "BS", "CH", "SP", "CI"},
    "QN": {"FU", "LI", "JH", "JA", "FH", "LA", "EE"},
}

# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE DRIVE API AUTHENTICATION
# ─────────────────────────────────────────────────────────────────────────────

def authenticate_drive():
    """Authenticate with Google Drive API using service account."""
    if not SERVICE_ACCOUNT_JSON.exists():
        logger.error(f"Service account JSON not found: {SERVICE_ACCOUNT_JSON}")
        sys.exit(1)

    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_JSON,
            scopes=SCOPES
        )
        service = build("drive", "v3", credentials=creds)
        logger.info("Successfully authenticated with Google Drive")
        return service
    except Exception as e:
        logger.error(f"Failed to authenticate: {e}")
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# FOLDER NAVIGATION & EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def find_folder_by_name(service, parent_id: str, folder_name: str) -> Optional[str]:
    """Find a folder by name within a parent folder. Returns folder ID or None."""
    try:
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1
        ).execute()

        files = results.get("files", [])
        if files:
            return files[0]["id"]
        return None
    except Exception as e:
        logger.warning(f"Error finding folder '{folder_name}': {e}")
        return None

def list_folders_in_parent(service, parent_id: str) -> List[Tuple[str, str]]:
    """List all folders in a parent folder. Returns list of (folder_name, folder_id) tuples."""
    try:
        query = f"mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1000
        ).execute()

        files = results.get("files", [])
        return [(f["name"], f["id"]) for f in files]
    except Exception as e:
        logger.warning(f"Error listing folders in parent {parent_id}: {e}")
        return []

def extract_walks_from_drive(service) -> Set[str]:
    """
    Navigate Google Drive folder structure and extract all combo names.

    Structure: NASA_EnAACT_Research/Walks/[borough]/[route]/[combo_names]
    Returns a set of extracted combo names like "X_TER_BK_BS_20260312_AM"
    """
    extracted = set()

    # Find NASA_EnAACT_Research folder (search from root)
    logger.info("Searching for NASA_EnAACT_Research folder...")
    nasa_id = find_folder_by_name(service, "root", "NASA_EnAACT_Research")
    if not nasa_id:
        logger.warning("NASA_EnAACT_Research folder not found in Google Drive")
        return extracted

    # Find Walks folder
    logger.info("Searching for Walks folder...")
    walks_id = find_folder_by_name(service, nasa_id, "Walks")
    if not walks_id:
        logger.warning("Walks folder not found under NASA_EnAACT_Research")
        return extracted

    # List all borough folders
    logger.info("Listing borough folders...")
    borough_folders = list_folders_in_parent(service, walks_id)

    for borough_name, borough_id in borough_folders:
        # Validate borough — folder names start with the 2-char code (e.g. "BK - Brooklyn")
        borough_code = borough_name[:2]
        if borough_code not in VALID_BOROUGHS:
            logger.debug(f"Skipping non-borough folder: {borough_name}")
            continue

        logger.debug(f"Processing borough: {borough_name}")

        # List route folders
        route_folders = list_folders_in_parent(service, borough_id)

        for route_name, route_id in route_folders:
            # Route folders start with the 2-char route code (e.g. "SP - Sunset Park")
            route_code = route_name[:2]
            if route_code not in VALID_ROUTES.get(borough_code, set()):
                logger.debug(f"Skipping non-route folder: {route_name}")
                continue

            logger.debug(f"Processing route: {borough_name}/{route_name}")

            # List combo names (deepest level)
            combo_folders = list_folders_in_parent(service, route_id)

            for combo_name, combo_id in combo_folders:
                # Validate combo name format
                if is_valid_walk_code(combo_name):
                    extracted.add(combo_name)
                    logger.debug(f"Extracted: {combo_name}")
                else:
                    logger.debug(f"Skipping invalid walk code: {combo_name}")

    logger.info(f"Extracted {len(extracted)} walks from Google Drive")
    return extracted

# ─────────────────────────────────────────────────────────────────────────────
# WALK CODE VALIDATION (using same logic as walk_scheduler.py)
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_walk_code(code: str) -> bool:
    """Validate if a string matches a walk code format."""
    parts = code.split("_")

    # Must be 6 parts (new format: BP_COL_BORO_NEIGH_YYYYMMDD_TOD)
    # or 8 parts (old format: BP_COL_BORO_NEIGH_MM_DD_YYYY_TOD)
    if len(parts) not in (6, 8):
        return False

    # Check backpack
    if parts[0] not in VALID_BACKPACKS:
        return False

    # Check borough (parts[2])
    if parts[2] not in VALID_BOROUGHS:
        return False

    # Check TOD (last part)
    if parts[-1] not in VALID_TODS:
        return False

    # Validate date
    if not _validate_walk_date(parts):
        return False

    return True

def _validate_walk_date(parts: List[str]) -> bool:
    """Validate date portion of walk code."""
    try:
        if len(parts) == 8:
            # Old format: MM_DD_YYYY
            mm, dd, yyyy = int(parts[4]), int(parts[5]), int(parts[6])
            date(yyyy, mm, dd)
            return True
        elif len(parts) == 6:
            # New format: YYYYMMDD
            raw = parts[4]
            if len(raw) != 8 or not raw.isdigit():
                return False
            yyyy, mm, dd = int(raw[:4]), int(raw[4:6]), int(raw[6:8])
            date(yyyy, mm, dd)
            return True
    except (ValueError, IndexError):
        pass

    return False

# ─────────────────────────────────────────────────────────────────────────────
# WALKS LOG MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def load_existing_walks() -> Set[str]:
    """Load all existing walk entries from walks_log.txt."""
    existing = set()

    # Pull the authoritative bucket copy before reading so appends are based on
    # the latest state, not a stale local snapshot.
    gcs_pull("Walks_Log.txt", WALKS_LOG)

    if not WALKS_LOG.exists():
        logger.warning(f"Walks log not found: {WALKS_LOG}")
        return existing

    try:
        with open(WALKS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    existing.add(line)
        logger.debug(f"Loaded {len(existing)} existing walks from log")
    except Exception as e:
        logger.error(f"Error reading walks log: {e}")

    return existing

def append_walks_to_log(new_walks: Set[str]) -> int:
    """Append new walks to the log file. Returns number of walks appended."""
    if not new_walks:
        return 0

    existing = load_existing_walks()
    walks_to_add = new_walks - existing

    if not walks_to_add:
        logger.info("No new walks to add")
        return 0

    try:
        with open(WALKS_LOG, "a", encoding="utf-8") as f:
            for walk in sorted(walks_to_add):
                f.write(walk + "\n")

        gcs_push(WALKS_LOG, "Walks_Log.txt")

        logger.info(f"Appended {len(walks_to_add)} new walks to {WALKS_LOG.name}")
        for walk in sorted(walks_to_add):
            logger.debug(f"  + {walk}")
        return len(walks_to_add)
    except Exception as e:
        logger.error(f"Error appending to walks log: {e}")
        return 0

# ─────────────────────────────────────────────────────────────────────────────
# MAIN SYNC LOOP
# ─────────────────────────────────────────────────────────────────────────────

def sync_once(service) -> int:
    """Run one sync cycle. Returns number of new walks added."""
    try:
        extracted = extract_walks_from_drive(service)
        count = append_walks_to_log(extracted)
        return count
    except Exception as e:
        logger.error(f"Sync cycle failed: {e}")
        return 0

def main():
    """Main loop: sync every 60 seconds."""
    logger.info("=" * 80)
    logger.info("Drive Sync Service Started")
    logger.info(f"Polling every 60 seconds")
    logger.info("=" * 80)

    service = authenticate_drive()

    try:
        while True:
            logger.info("Starting sync cycle...")
            count = sync_once(service)
            logger.info(f"Sync cycle complete. Added {count} walks.\n")

            # Wait 60 seconds before next sync
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Drive Sync Service stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
