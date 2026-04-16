#!/usr/bin/env python3
"""
build_weather.py — Read forecast tabs from Google Sheets and produce a single weather JSON file.

  weather.json — all entries for dates >= HISTORY_START, rebuilt each run.

Each tab in the spreadsheet covers a 5- or 7-day rolling window (e.g. "Apr 7 - Apr 13").
Tab layout (3TOD only):
  - Column A: date (may include day-of-week prefix, e.g. "Sunday\n4/6/26")
  - Column B: AM average cloud %
  - Column C: MD (Midday) average cloud %
  - Column D: PM average cloud %
  - "Last Updated" cell: dynamically located — the LOWEST non-empty row in column B
    (NOT always B24)

Design rules (2026-04-08 rewrite):
  • Single output file. No frozen/unfrozen split, no age-out shift.
  • Hard history floor: HISTORY_START = 2026-03-16. Entries before this are ignored.
  • Tabs whose end-date < HISTORY_START are skipped entirely.
  • Conflict resolution: when two tabs cover the same (date, tod), the tab with the
    newest "Last Updated" date wins. Ties → later tab start-date → alphabetical tab title.
  • Year-crossing fix: tabs like "Dec 29 - Jan 4" correctly roll end-date into next year.
  • Raw cloud % retained in _meta for each entry.
  • All tabs are treated as 3TOD (columns B/C/D → AM/MD/PM). AM_only layout removed.

Usage:
    python build_weather.py
"""

from __future__ import annotations

import sys
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR             = Path(__file__).parent
SERVICE_ACCOUNT_JSON = BASE_DIR / "drive-service-account.json"
WEATHER_PATH         = BASE_DIR / "weather.json"

SPREADSHEET_ID  = "1-AQk9LXHlzeakHBvwdhFLeDrZojkZj3vG2h6cAOumm4"
CLOUD_THRESHOLD = 33                       # ≤ this % cloud cover = good weather
HISTORY_START   = date(2026, 3, 16)        # hard floor: no entries before this
TODS            = ["AM", "MD", "PM"]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION
# ─────────────────────────────────────────────────────────────────────────────

def authenticate_sheets():
    """Authenticate with Google Sheets API using service account."""
    if not SERVICE_ACCOUNT_JSON.exists():
        print(f"  [ERROR] Service account JSON not found: {SERVICE_ACCOUNT_JSON}")
        sys.exit(1)
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_JSON, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


# ─────────────────────────────────────────────────────────────────────────────
# PARSING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_week_folder_name(
    name: str,
    ref_year: Optional[int] = None,
) -> Tuple[Optional[date], Optional[date]]:
    """
    Parse names like 'Mar 8 - Mar 14' or 'Apr 7 - Apr 13' → (start, end).

    Year-crossing fix: if end < start, roll end into next year (e.g. "Dec 29 - Jan 4").
    If the resulting start-date is more than 180 days in the future relative to today,
    assume the tab refers to the prior year and roll both dates back.
    """
    if ref_year is None:
        ref_year = date.today().year

    halves = re.split(r"\s+-\s+", name.strip(), maxsplit=1)
    if len(halves) < 2:
        return None, None

    def _parse_part(s: str, year: int) -> Optional[date]:
        m = re.match(r"([A-Za-z]+)\s+(\d+)", s.strip())
        if m and m.group(1) in MONTHS:
            try:
                return date(year, MONTHS[m.group(1)], int(m.group(2)))
            except ValueError:
                return None
        return None

    start = _parse_part(halves[0], ref_year)
    end   = _parse_part(halves[1], ref_year)

    # Year-crossing: end before start → end is in the next year
    if start and end and end < start:
        try:
            end = date(end.year + 1, end.month, end.day)
        except ValueError:
            pass

    # Far-future: if start is > 180 days in the future, assume the tab refers
    # to the prior year (e.g. parsing "Feb 1 - Feb 7" in December should give 2026,
    # but parsing it in March of 2027 should give 2027 still → that's fine;
    # this branch only fires if the naive parse pushes us > 6 months out).
    today = date.today()
    if start and (start - today).days > 180:
        try:
            start = date(start.year - 1, start.month, start.day)
            if end:
                end = date(end.year - 1, end.month, end.day)
        except ValueError:
            pass

    return start, end


def pct(cell) -> Optional[int]:
    """Extract integer percentage from a cell value like '15%', '15', or None."""
    if cell is None or str(cell).strip() == "":
        return None
    m = re.search(r"(\d+)", str(cell))
    return int(m.group(1)) if m else None


def _parse_mdy(s: str) -> Optional[date]:
    """Parse a date string in M/D/YY or M/D/YYYY format."""
    m = re.search(r"(\d+)/(\d+)/(\d{2,4})", s)
    if not m:
        return None
    try:
        mo = int(m.group(1))
        dy = int(m.group(2))
        yr = int(m.group(3))
        yr = (2000 + yr) if yr < 100 else yr
        return date(yr, mo, dy)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SHEETS API HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def list_forecast_tabs(sheets_service) -> List[str]:
    """
    Return tab titles from the spreadsheet that match the week-range name format.
    Non-matching tabs (e.g. summary sheets) are silently skipped.
    """
    result = sheets_service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        fields="sheets.properties.title",
    ).execute()

    titles: List[str] = []
    for sheet in result.get("sheets", []):
        title = sheet["properties"]["title"]
        start, end = parse_week_folder_name(title)
        if start is not None and end is not None:
            titles.append(title)
    return titles



def _find_last_updated(rows: List[list]) -> Optional[date]:
    """
    Locate the 'Last Updated:' cell. It's always the lowest labeled row
    with a date in column B.
    """
    for row_idx in range(len(rows) - 1, -1, -1):
        row = rows[row_idx]
        if not row or len(row) < 2:
            continue
        a = str(row[0]).strip().lower() if row[0] is not None else ""
        if "last updated" not in a:
            continue
        b_val = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        if not b_val:
            continue
        candidate = _parse_mdy(b_val)
        if candidate is not None:
            return candidate
    return None


def parse_forecast_tab(
    sheets_service,
    tab_title: str,
    tab_start: date,
) -> Tuple[Dict[Tuple[date, str], Tuple[bool, int]], Optional[date]]:
    """
    Read weather data from a single tab (3TOD layout only: B/C/D = AM/MD/PM).

      Date layout variants supported:
        • "combined" — column A of the data row contains "Dayname\\nM/D/YY"
        • "split"    — data row has only "Dayname" in A; next row has "M/D/YY" in A

    Returns:
      weather       — {(date, tod): (is_good, cloud_pct)}
      last_updated  — date or None
    """
    range_data = f"'{tab_title}'!A1:D50"
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_data,
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    rows = response.get("values", [])

    weather: Dict[Tuple[date, str], Tuple[bool, int]] = {}

    def _emit(d: date, pcts: List[Optional[int]]) -> None:
        """Map [b, c, d] percentages to AM/MD/PM (3TOD layout)."""
        for name, p in zip(("AM", "MD", "PM"), pcts):
            if p is not None:
                weather[(d, name)] = (p <= CLOUD_THRESHOLD, p)

    # ── Walk rows: detect (date, percentages) pairs ──────────────────────────
    i = 0
    while i < len(rows):
        row = rows[i]
        if not row:
            i += 1
            continue

        cell_a = str(row[0]) if len(row) > 0 and row[0] is not None else ""
        percents = [
            pct(row[1]) if len(row) > 1 else None,
            pct(row[2]) if len(row) > 2 else None,
            pct(row[3]) if len(row) > 3 else None,
        ]
        has_percents = any(p is not None for p in percents)

        # Ignore the "Last Updated:" row even if someone typo'd a % in it
        if "last updated" in cell_a.strip().lower():
            i += 1
            continue

        date_in_a = _parse_mdy(cell_a)

        if date_in_a and has_percents:
            # Combined layout: day+date in A, percentages in B/C/D
            _emit(date_in_a, percents)
            i += 1
            continue

        if has_percents and date_in_a is None:
            # Split layout: look for the date on the next non-empty row's column A
            j = i + 1
            while j < len(rows) and (not rows[j] or len(rows[j]) == 0):
                j += 1
            if j < len(rows):
                next_a = str(rows[j][0]) if len(rows[j]) > 0 and rows[j][0] is not None else ""
                date_next = _parse_mdy(next_a)
                if date_next:
                    _emit(date_next, percents)
                    i = j + 1
                    continue
            i += 1
            continue

        i += 1

    # ── Locate "Last Updated" cell ───────────────────────────────────────────
    last_updated = _find_last_updated(rows)

    # Sanity check: if parsed year is < 2026, log a warning and fall back to
    # the tab's start-year (data-entry error in the sheet — user's case: a
    # cell containing "3/25/25" instead of "3/25/26").
    if last_updated is not None and last_updated.year < 2026:
        print(
            f"    [WARN] {tab_title!r}: last_updated year {last_updated.year} "
            f"< 2026 — treating as {tab_start.year} (likely data-entry typo)"
        )
        try:
            last_updated = date(tab_start.year, last_updated.month, last_updated.day)
        except ValueError:
            last_updated = tab_start

    if last_updated is None:
        # No Last Updated cell found — fall back to tab start-date so conflict
        # resolution still has a value to compare.
        last_updated = tab_start

    return weather, last_updated


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def build_weather() -> Path:
    """Build weather.json from Google Sheets forecast tabs."""
    today = date.today()

    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   Build Weather (Google Sheets → weather.json)       ║")
    print(f"║   Run date      : {today.strftime('%B %d, %Y'):<36}║")
    print(f"║   History start : {str(HISTORY_START):<36}║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    sheets_service = authenticate_sheets()

    # ── List forecast tabs ────────────────────────────────────────────────────
    tab_titles = list_forecast_tabs(sheets_service)
    print(f"  Found {len(tab_titles)} forecast tab(s) in spreadsheet")

    # Candidate entry:
    #   (is_good, cloud_pct, last_updated, tab_start, tab_title)
    CandidateEntry = Tuple[bool, int, date, date, str]
    candidates: Dict[str, List[CandidateEntry]] = defaultdict(list)
    processed_tabs: List[Tuple[date, date, date, str]] = []  # (start, end, last_updated, title)

    for tab_title in tab_titles:
        start, end = parse_week_folder_name(tab_title)
        if start is None or end is None:
            print(f"    [SKIP] Can't parse date range: {tab_title!r}")
            continue

        # Hard history floor — skip tabs that end before HISTORY_START
        if end < HISTORY_START:
            print(f"    [SKIP < HISTORY_START] {tab_title!r} (end={end})")
            continue

        weather_data, last_updated = parse_forecast_tab(
            sheets_service, tab_title, tab_start=start,
        )
        if not weather_data:
            print(f"    [WARN] No weather data extracted from tab: {tab_title!r}")
            continue

        n_kept = n_dropped = 0
        for (d, tod), (is_good, cloud_pct) in weather_data.items():
            if d < HISTORY_START:
                n_dropped += 1
                continue
            key = f"{d}_{tod}"
            candidates[key].append((is_good, cloud_pct, last_updated, start, tab_title))
            n_kept += 1

        # Summarise which TODs are present (quick signal for AM-only vs 3TOD)
        tods_present = sorted({tod for (_d, tod) in weather_data.keys()})
        dropped_note = f" ({n_dropped} dropped < HISTORY_START)" if n_dropped else ""
        print(
            f"    ✓ {tab_title!r}: {n_kept} entries [{','.join(tods_present)}]{dropped_note}, "
            f"Last Updated: {last_updated}"
        )
        processed_tabs.append((start, end, last_updated, tab_title))

    # ── Conflict resolution ──────────────────────────────────────────────────
    # Newest last_updated wins. Ties broken by tab start-date DESC, then tab
    # title alphabetical DESC (deterministic).
    weather_out: Dict[str, bool] = {}
    meta_out: Dict[str, dict] = {}
    for key, entries in candidates.items():
        entries.sort(
            key=lambda e: (e[2], e[3], e[4]),  # (last_updated, tab_start, tab_title)
            reverse=True,
        )
        is_good, cloud_pct, lu, _tab_start, source = entries[0]
        weather_out[key] = is_good
        meta_out[key] = {
            "source":       source,
            "last_updated": str(lu),
            "cloud_pct":    cloud_pct,
        }

    # ── Current week detection: latest tab end-date wins ─────────────────────
    current_week_start: Optional[date] = None
    current_week_end: Optional[date] = None
    active_tabs = [t for t in processed_tabs if t[1] >= today]
    if active_tabs:
        active_tabs.sort(key=lambda x: x[1], reverse=True)  # end DESC
        current_week_start = active_tabs[0][0]
        current_week_end   = active_tabs[0][1]
    else:
        print(
            "  [WARN] No active tab covers today — "
            "current_week_start/current_week_end will be null. "
            "Add a new tab covering the current week to the spreadsheet."
        )

    good = sum(1 for v in weather_out.values() if v)
    bad  = sum(1 for v in weather_out.values() if not v)
    print()
    print(f"  Total entries: {len(weather_out)} ({good} good, {bad} bad)")
    if current_week_start and current_week_end:
        print(f"  Current week : {current_week_start} → {current_week_end}")

    # ── Write weather.json ───────────────────────────────────────────────────
    output = {
        "generated":          datetime.now().isoformat(),
        "history_start":      str(HISTORY_START),
        "current_week_start": str(current_week_start) if current_week_start else None,
        "current_week_end":   str(current_week_end)   if current_week_end   else None,
        "weather":            dict(sorted(weather_out.items())),
        "_meta":              dict(sorted(meta_out.items())),
    }
    with open(WEATHER_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"  ✓ Wrote {WEATHER_PATH.name}")
    return WEATHER_PATH


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Sanity assertion: year-crossing tab parses correctly
    _s, _e = parse_week_folder_name("Dec 29 - Jan 4")
    assert _s is not None and _e is not None and _e > _s, \
        f"Year-crossing bug: Dec 29 - Jan 4 → {_s} .. {_e}"

    build_weather()
