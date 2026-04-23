#!/usr/bin/env python3
"""
forecast_monitor.py — Monitor a Google Sheets forecast spreadsheet and auto-trigger scheduler
========================================================================================
Periodically polls the Google Sheets spreadsheet for changes (via its Drive
modification time) and automatically triggers the walk scheduler when the
sheet is updated.

When a change is detected:
1. Runs build_weather.py  (reads fresh data from the spreadsheet)
2. Runs walk_scheduler.py
3. Runs build_dashboard.py
4. Logs all activity to forecast_monitor.log

Runs every 5 minutes and tracks the sheet's last-modified time in .forecast_state.json.

SETUP:
1. Ensure drive-service-account.json exists in the same folder as this script
2. Install dependencies: pip install google-auth google-auth-httplib2 google-api-python-client
3. Ensure build_weather.py, walk_scheduler.py and build_dashboard.py are in the same folder

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
from typing import Dict, Optional

# Google Drive API imports
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION & LOGGING
# ─────────────────────────────────────────────────────────────────────────────

import sys as _sys
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

from shared.paths import (
    SERVICE_ACCOUNT_KEY as SERVICE_ACCOUNT_JSON,
    FORECAST_MONITOR_LOG as LOG_FILE,
    FORECAST_STATE as STATE_FILE,
    BUILD_WEATHER, BUILD_DASHBOARD, WALK_SCHEDULER,
)

BASE_DIR = _REPO_ROOT

SPREADSHEET_ID = "1-AQk9LXHlzeakHBvwdhFLeDrZojkZj3vG2h6cAOumm4"

# BUILD_WEATHER, WALK_SCHEDULER, BUILD_DASHBOARD imported from shared.paths above

# Drive API scope (read file metadata only)
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


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
# SPREADSHEET CHANGE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def get_sheet_mtime(drive_service) -> Optional[int]:
    """
    Return the spreadsheet's last-modified time as a Unix integer timestamp,
    or None if the file cannot be reached.
    """
    try:
        result = drive_service.files().get(
            fileId=SPREADSHEET_ID,
            fields="modifiedTime",
            supportsAllDrives=True
        ).execute()
        mod_time = datetime.fromisoformat(result["modifiedTime"].replace("Z", "+00:00"))
        return int(mod_time.timestamp())
    except Exception as e:
        logger.warning(f"Could not read spreadsheet modifiedTime: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def load_forecast_state() -> Dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Error loading forecast state: {e}")
        return {}


def save_forecast_state(mtime_ts: int):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"spreadsheet_mtime": mtime_ts}, f, indent=2)
        logger.debug(f"Saved forecast state: spreadsheet_mtime={mtime_ts}")
    except Exception as e:
        logger.error(f"Error saving forecast state: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# SCRIPT EXECUTION
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
            pass
    return env


def _run_script(script_path: Path, label: str) -> bool:
    """Run a Python script and return True on success."""
    if not script_path.exists():
        logger.error(f"{label} not found at {script_path}")
        return False

    logger.info(f"Running {label}...")
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300,
            env=_get_env_with_api_key(),
        )
        if result.returncode == 0:
            logger.info(f"{label} completed successfully")
            if result.stdout:
                logger.debug(f"{label} output: {result.stdout[:500]}")
            return True
        else:
            logger.error(f"{label} failed with code {result.returncode}")
            if result.stderr:
                logger.error(f"Error: {result.stderr[:500]}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"{label} timed out (>5 minutes)")
        return False
    except Exception as e:
        logger.error(f"Error running {label}: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# MAIN SYNC LOOP
# ─────────────────────────────────────────────────────────────────────────────

def sync_once(drive_service) -> bool:
    """Run one sync cycle. Returns True if scripts were triggered."""
    try:
        prev_state = load_forecast_state()
        prev_mtime = prev_state.get("spreadsheet_mtime", -1)

        current_mtime = get_sheet_mtime(drive_service)
        if current_mtime is None:
            logger.info("Could not read spreadsheet; skipping cycle")
            return False

        if current_mtime <= prev_mtime:
            logger.info("No changes detected in spreadsheet")
            return False

        logger.info(f"Spreadsheet change detected (mtime: {current_mtime} > {prev_mtime})")

        weather_ok = _run_script(BUILD_WEATHER, "build_weather.py")

        if not weather_ok:
            logger.error("build_weather.py failed — skipping scheduler and dashboard")
            return False

        # Rebuild dashboard immediately after weather so the site always shows
        # the latest weather data, even if the scheduler fails below.
        _run_script(BUILD_DASHBOARD, "build_dashboard.py")

        scheduler_ok = _run_script(WALK_SCHEDULER, "walk_scheduler.py")

        if scheduler_ok:
            # Rebuild again to bake in the freshly generated schedule.
            _run_script(BUILD_DASHBOARD, "build_dashboard.py")
        else:
            logger.warning("walk_scheduler.py failed — dashboard reflects latest weather only")

        # Save state as long as weather succeeded; scheduler failure is non-fatal.
        save_forecast_state(current_mtime)
        logger.info("✓ Sync completed (weather updated; scheduler %s)",
                    "ok" if scheduler_ok else "FAILED")
        return True

    except Exception as e:
        logger.error(f"Sync cycle failed: {e}")
        return False


def main():
    logger.info("=" * 80)
    logger.info("Forecast Monitor Service Started")
    logger.info(f"Spreadsheet ID: {SPREADSHEET_ID}")
    logger.info("Polling Google Drive every 5 minutes")
    logger.info("=" * 80)

    drive_service = authenticate_drive()

    try:
        while True:
            logger.info("Starting forecast check...")
            sync_once(drive_service)
            logger.info("Forecast check complete. Waiting 5 minutes...\n")
            time.sleep(300)
    except KeyboardInterrupt:
        logger.info("Forecast Monitor Service stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
