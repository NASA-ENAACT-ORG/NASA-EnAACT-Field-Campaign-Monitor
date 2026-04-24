"""Canonical path registry for the NYC EnAACT Walk Dashboard.

All scripts import from here instead of computing paths from __file__.
REPO_ROOT is the top-level repository directory (parent of this shared/ package).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Inputs ────────────────────────────────────────────────────────────────────
INPUTS_DIR          = REPO_ROOT / "data" / "inputs"
ROUTES_KML_DIR      = INPUTS_DIR / "routes" / "kml"
GTFS_DIR            = INPUTS_DIR / "transit" / "gtfs"
FORECASTS_DIR       = INPUTS_DIR / "forecasts"
AVAILABILITY_XLSX   = INPUTS_DIR / "availability" / "Availability.xlsx"
COORD_AVAIL_XLSX    = INPUTS_DIR / "availability" / "Coordinate Availability.xlsx"
PREFERRED_ROUTES    = INPUTS_DIR / "routes" / "Preferred_Routes.xlsx"
V2_PREFERRED_ROUTES = INPUTS_DIR / "routes" / "V2_Preferred_Routes.xlsx"
ROUTE_GROUPS        = INPUTS_DIR / "routes" / "Route_Groups.xlsx"
ROUTE_SUBWAY_STOPS  = INPUTS_DIR / "transit" / "Route_Subway_stops.xlsx"
EFD_FORM_CSV        = INPUTS_DIR / "students" / "EFD_Google_form.csv"
ROUTES_DATA_JSON    = INPUTS_DIR / "routes" / "routes_data.json"

# Collector_Schedule/ contains personal availability PDFs — git-ignored, kept locally
COLLECTOR_SCHEDULE_DIR = REPO_ROOT / "data" / "inputs" / "collectors"

# ── Outputs — site artifacts (generated HTML/JSON served by the app) ──────────
SITE_DIR                  = REPO_ROOT / "data" / "outputs" / "site"
DASHBOARD_HTML            = SITE_DIR / "dashboard.html"
COLLECTOR_MAP_HTML        = SITE_DIR / "collector_map.html"
AVAILABILITY_HEATMAP_HTML = SITE_DIR / "availability_heatmap.html"
STUDENT_SCHEDULE_HTML     = SITE_DIR / "student_schedule.html"
SCHEDULE_MAP_HTML         = SITE_DIR / "schedule_map.html"
WEATHER_JSON              = SITE_DIR / "weather.json"
SCHEDULE_OUTPUT_JSON      = SITE_DIR / "schedule_output.json"
STUDENT_SCHEDULE_JSON     = SITE_DIR / "student_schedule_output.json"
TRANSIT_MATRIX_JSON       = SITE_DIR / "transit_matrix.json"

# ── Outputs — logs ────────────────────────────────────────────────────────────
LOGS_DIR             = REPO_ROOT / "data" / "outputs" / "logs"
SCHEDULER_OUTPUT_TXT = LOGS_DIR / "scheduler_output.txt"
FORECAST_MONITOR_LOG = LOGS_DIR / "forecast_monitor.log"
DRIVE_SYNC_LOG       = LOGS_DIR / "drive_sync.log"

# ── Runtime — persisted (synced to/from GCS across container restarts) ────────
PERSISTED_DIR          = REPO_ROOT / "data" / "runtime" / "persisted"
WALKS_LOG              = PERSISTED_DIR / "Walks_Log.txt"
RECAL_LOG              = PERSISTED_DIR / "Recal_Log.txt"
DRIVE_SEEN_FILES       = PERSISTED_DIR / "drive_seen_files.json"

# ── Runtime — local/ephemeral (not persisted to GCS) ─────────────────────────
LOCAL_DIR      = REPO_ROOT / "data" / "runtime" / "local"
FORECAST_STATE = LOCAL_DIR / ".forecast_state.json"

# ── Pipeline script paths (for subprocess calls) ──────────────────────────────
BUILD_WEATHER         = REPO_ROOT / "pipelines" / "weather"    / "build_weather.py"
BUILD_DASHBOARD       = REPO_ROOT / "pipelines" / "dashboard"  / "build_dashboard.py"
BUILD_AVAILABILITY    = REPO_ROOT / "pipelines" / "dashboard"  / "build_availability_heatmap.py"
BUILD_COLLECTOR_MAP   = REPO_ROOT / "pipelines" / "maps"       / "build_collector_map.py"
WALK_SCHEDULER        = REPO_ROOT / "pipelines" / "scheduling" / "walk_scheduler.py"
STUDENT_SCHEDULER     = REPO_ROOT / "pipelines" / "students"   / "student_scheduler.py"
TRANSIT_MATRIX_SCRIPT = REPO_ROOT / "pipelines" / "scheduling" / "transit_matrix.py"
FORECAST_MONITOR      = REPO_ROOT / "pipelines" / "weather"    / "forecast_monitor.py"

# ── Credentials ───────────────────────────────────────────────────────────────
SERVICE_ACCOUNT_KEY = REPO_ROOT / "drive-service-account.json"
