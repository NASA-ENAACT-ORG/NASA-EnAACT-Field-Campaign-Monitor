#!/usr/bin/env python3
"""
forecast_monitor.py — Monitor Google Drive forecasts and auto-trigger scheduler
========================================================================================
Periodically polls the Google Drive folder (Nasa_enaact/Forecasts/) for new forecast
PDFs and automatically triggers the walk scheduler when new forecasts are detected.
Forecast PDFs are stored flat in the Forecasts/ folder, named by date range
(e.g., "Mar 23 - Mar 27.pdf").

When new forecasts are found:
1. Downloads the new forecast PDF
2. Copies to local Forecast/ folder
3. Runs walk_scheduler.py
4. Runs build_dashboard.py
5. Logs all activity to forecast_monitor.log

Runs every 5 minutes and tracks processed forecasts in .forecast_state.json.

SETUP:
1. Ensure drive-service-account.json exists in the same folder as this script
2. Install dependencies: pip install google-auth google-auth-httplib2 google-api-python-client
3. Ensure walk_scheduler.py and build_dashboard.py are in the same folder

RUN:
python forecast_monitor.py
"""

import os
import sys
import time
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple

# Google Drive API imports
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION & LOGGING
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
SERVICE_ACCOUNT_JSON = BASE_DIR / "drive-service-account.json"
LOG_FILE = BASE_DIR / "forecast_monitor.log"
FORECAST_DIR = BASE_DIR / "Forecast"
STATE_FILE = BASE_DIR / ".forecast_state.json"

# Walk scheduler scripts
WALK_SCHEDULER = BASE_DIR / "walk_scheduler.py"
BUILD_DASHBOARD = BASE_DIR / "build_dashboard.py"

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
# FOLDER NAVIGATION & FORECAST DETECTION
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

def find_folder_by_name_global(service, folder_name: str) -> Optional[str]:
    """Find a folder by name anywhere visible to the service account (including shared)."""
    try:
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1
        ).execute()

        files = results.get("files", [])
        if files:
            logger.info(f"Found '{folder_name}' via global search (id: {files[0]['id']})")
            return files[0]["id"]
        return None
    except Exception as e:
        logger.warning(f"Error in global search for '{folder_name}': {e}")
        return None

def list_files_in_folder(service, parent_id: str, mime_type: str = "application/pdf") -> List[Tuple[str, str, int]]:
    """List all files of a given MIME type in a folder. Returns list of (filename, file_id, mtime) tuples."""
    try:
        query = f"mimeType='{mime_type}' and '{parent_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name, modifiedTime)",
            pageSize=1000,
            orderBy="modifiedTime desc"
        ).execute()

        files = results.get("files", [])
        file_list = []
        for f in files:
            try:
                mod_time = datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00"))
                mtime_ts = int(mod_time.timestamp())
                file_list.append((f["name"], f["id"], mtime_ts))
            except (ValueError, KeyError):
                logger.debug(f"Skipping file with invalid timestamp: {f.get('name')}")
                continue
        return file_list
    except Exception as e:
        logger.warning(f"Error listing files in parent {parent_id}: {e}")
        return []

def find_forecasts(service) -> Dict[str, Tuple[str, str, int]]:
    """
    Find all forecast PDFs in Nasa_enaact/Forecasts/ (flat folder, no subfolders).

    PDFs are named by date range, e.g. "Mar 23 - Mar 27.pdf".

    Returns: {filename: (filename, file_id, mtime_ts), ...}
    """
    forecasts = {}

    # Find NASA_EnAACT_Research folder (may be shared, not under "root")
    logger.info("Searching for NASA_EnAACT_Research folder...")
    nasa_id = find_folder_by_name(service, "root", "NASA_EnAACT_Research")
    if not nasa_id:
        # Shared folders don't appear under root — search globally by name
        nasa_id = find_folder_by_name_global(service, "NASA_EnAACT_Research")
    if not nasa_id:
        logger.warning("NASA_EnAACT_Research folder not found in Google Drive")
        return forecasts

    # Find Forecast folder
    logger.info("Searching for Forecast folder...")
    forecasts_id = find_folder_by_name(service, nasa_id, "Forecast")
    if not forecasts_id:
        logger.warning("Forecast folder not found under NASA_EnAACT_Research")
        return forecasts

    # List PDFs directly in Forecasts/
    logger.info("Listing forecast PDFs...")
    pdfs = list_files_in_folder(service, forecasts_id, mime_type="application/pdf")

    for filename, file_id, mtime_ts in pdfs:
        forecasts[filename] = (filename, file_id, mtime_ts)
        logger.debug(f"Found forecast: {filename} (mtime: {mtime_ts})")

    logger.info(f"Found {len(forecasts)} forecast PDF(s)")
    return forecasts

# ─────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def load_forecast_state() -> Dict[str, int]:
    """Load the state of processed forecasts. Returns {borough: mtime_ts}."""
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Error loading forecast state: {e}")
        return {}

def save_forecast_state(state: Dict[str, int]):
    """Save the current state of processed forecasts."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        logger.debug(f"Saved forecast state: {state}")
    except Exception as e:
        logger.error(f"Error saving forecast state: {e}")

def has_new_forecasts(current: Dict[str, Tuple[str, str, int]], previous: Dict[str, int]) -> bool:
    """Check if any forecast PDF is new or updated since last sync."""
    for filename, (_, file_id, mtime_ts) in current.items():
        prev_mtime = previous.get(filename, -1)
        if mtime_ts > prev_mtime:
            logger.info(f"New forecast detected: {filename} (mtime: {mtime_ts} > {prev_mtime})")
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
# FORECAST DOWNLOAD & MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def download_file(service, file_id: str, file_name: str, destination: Path) -> bool:
    """Download a file from Google Drive and save it locally."""
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        fh.seek(0)
        with open(destination, "wb") as f:
            f.write(fh.read())

        logger.info(f"Downloaded {file_name} to {destination}")
        return True
    except Exception as e:
        logger.error(f"Error downloading {file_name}: {e}")
        return False

def sync_forecasts(service, forecasts: Dict[str, Tuple[str, str, int]]) -> bool:
    """Download forecast PDFs to the local Forecast/ folder."""
    if not forecasts:
        logger.info("No forecasts to sync")
        return False

    # Ensure Forecast directory exists
    FORECAST_DIR.mkdir(exist_ok=True)

    success = True
    for filename, (_, file_id, mtime_ts) in forecasts.items():
        # Ensure local filename ends with .pdf
        local_name = filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"
        dest = FORECAST_DIR / local_name
        if dest.exists():
            logger.debug(f"Skipping already-present forecast: {local_name}")
            continue
        logger.info(f"Downloading forecast: {filename}")
        if not download_file(service, file_id, filename, dest):
            success = False

    return success

# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

def _get_env_with_api_key() -> dict:
    """Return os.environ extended with ANTHROPIC_API_KEY from Windows User env if missing."""
    env = os.environ.copy()
    if not env.get("ANTHROPIC_API_KEY"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "ANTHROPIC_API_KEY")
                env["ANTHROPIC_API_KEY"] = value
                logger.debug("Loaded ANTHROPIC_API_KEY from Windows User environment")
        except Exception:
            pass  # Not on Windows or key not there — subprocess will fail with its own error
    return env

def run_scheduler():
    """Run walk_scheduler.py and return success status."""
    if not WALK_SCHEDULER.exists():
        logger.error(f"walk_scheduler.py not found at {WALK_SCHEDULER}")
        return False

    logger.info("Running walk_scheduler.py...")
    try:
        result = subprocess.run(
            [sys.executable, str(WALK_SCHEDULER)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            env=_get_env_with_api_key(),
        )

        if result.returncode == 0:
            logger.info("walk_scheduler.py completed successfully")
            # Log the output
            if result.stdout:
                logger.debug(f"Scheduler output: {result.stdout[:500]}")
            return True
        else:
            logger.error(f"walk_scheduler.py failed with code {result.returncode}")
            if result.stderr:
                logger.error(f"Error: {result.stderr[:500]}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("walk_scheduler.py timed out (>5 minutes)")
        return False
    except Exception as e:
        logger.error(f"Error running walk_scheduler.py: {e}")
        return False

def run_build_dashboard():
    """Run build_dashboard.py and return success status."""
    if not BUILD_DASHBOARD.exists():
        logger.error(f"build_dashboard.py not found at {BUILD_DASHBOARD}")
        return False

    logger.info("Running build_dashboard.py...")
    try:
        result = subprocess.run(
            [sys.executable, str(BUILD_DASHBOARD)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode == 0:
            logger.info("build_dashboard.py completed successfully")
            if result.stdout:
                logger.debug(f"Dashboard output: {result.stdout[:500]}")
            return True
        else:
            logger.error(f"build_dashboard.py failed with code {result.returncode}")
            if result.stderr:
                logger.error(f"Error: {result.stderr[:500]}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("build_dashboard.py timed out (>5 minutes)")
        return False
    except Exception as e:
        logger.error(f"Error running build_dashboard.py: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# MAIN SYNC LOOP
# ─────────────────────────────────────────────────────────────────────────────

def sync_once(service) -> bool:
    """Run one sync cycle. Returns True if scheduler was triggered."""
    try:
        # Load previous state
        prev_state = load_forecast_state()

        # Find current forecasts on Google Drive
        current_forecasts = find_forecasts(service)

        if not current_forecasts:
            logger.info("No forecasts found on Google Drive")
            return False

        # Check for new forecasts
        if not has_new_forecasts(current_forecasts, prev_state):
            logger.info("No new forecasts detected")
            return False

        logger.info("New forecasts detected! Starting sync...")

        # Download forecasts to local folder
        if not sync_forecasts(service, current_forecasts):
            logger.warning("Failed to download some forecasts, but continuing...")

        # Run scheduler and dashboard
        scheduler_ok = run_scheduler()
        dashboard_ok = run_build_dashboard()

        if scheduler_ok and dashboard_ok:
            # Update state with latest mtimes
            new_state = {filename: mtime_ts for filename, (_, _, mtime_ts) in current_forecasts.items()}
            save_forecast_state(new_state)
            logger.info("✓ Sync completed successfully")
            return True
        else:
            logger.error("Scheduler or dashboard build failed")
            return False

    except Exception as e:
        logger.error(f"Sync cycle failed: {e}")
        return False

def main():
    """Main loop: check for new forecasts every 5 minutes."""
    logger.info("=" * 80)
    logger.info("Forecast Monitor Service Started")
    logger.info(f"Polling Google Drive every 5 minutes")
    logger.info("=" * 80)

    service = authenticate_drive()

    try:
        while True:
            logger.info("Starting forecast check...")
            sync_once(service)
            logger.info("Forecast check complete. Waiting 5 minutes...\n")

            # Wait 5 minutes before next check
            time.sleep(300)
    except KeyboardInterrupt:
        logger.info("Forecast Monitor Service stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
