#!/usr/bin/env python3
"""
build_weather.py — Parse all forecast PDFs and produce two weather JSON files.

  frozen_boolean_weather.json  — entries for dates > 2 weeks old (append-only)
  unfrozen_boolean_weather.json — entries for dates within 2 weeks (rebuilt each run)

On the first run (no frozen file exists), ALL PDFs are parsed to bootstrap
the frozen file. On every subsequent run only "active" PDFs (whose filename
end-date is within 2 weeks) are re-parsed. Entries in the unfrozen file that
age past 2 weeks are automatically shifted into the frozen file.

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
import os
import re
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR         = Path(__file__).parent
FORECAST_DIR     = BASE_DIR / "Forecast"
FROZEN_PATH      = BASE_DIR / "frozen_boolean_weather.json"
UNFROZEN_PATH    = BASE_DIR / "unfrozen_boolean_weather.json"
CLOUD_THRESHOLD  = 33          # ≤ this % cloud cover = good weather
FREEZE_WINDOW    = 14          # days — entries older than this are frozen
CURRENT_YEAR     = date.today().year
TODS             = ["AM", "MD", "PM"]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def parse_week_folder_name(name: str) -> Tuple[Optional[date], Optional[date]]:
    """Parse names like 'Mar 8 - Mar 14' → (start, end). Year = CURRENT_YEAR."""
    halves = re.split(r"\s+-\s+", name.strip(), maxsplit=1)
    if len(halves) < 2:
        return None, None

    def _parse_part(s: str) -> Optional[date]:
        m = re.match(r"([A-Za-z]+)\s+(\d+)", s.strip())
        if m and m.group(1) in MONTHS:
            return date(CURRENT_YEAR, MONTHS[m.group(1)], int(m.group(2)))
        return None

    return _parse_part(halves[0]), _parse_part(halves[1])


def safe_copy(src: Path, suffix: str) -> Path:
    """Copy a file to the system temp dir to avoid OneDrive locking."""
    tmp = Path(os.environ.get("TEMP", "/tmp")) / f"build_weather_{suffix}"
    shutil.copy2(src, tmp)
    return tmp


# ─────────────────────────────────────────────────────────────────────────────
# PDF PARSING
# ─────────────────────────────────────────────────────────────────────────────

def _extract_last_updated(pdf_path: Path) -> Optional[date]:
    """Extract the 'Last Updated: M/D/YYYY' date from a forecast PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            m = re.search(
                r"Last\s+Updated[:\s]*(\d+)/(\d+)/(\d{2,4})",
                text,
                re.IGNORECASE,
            )
            if m:
                mo = int(m.group(1))
                dy = int(m.group(2))
                yr = int(m.group(3))
                yr = (2000 + yr) if yr < 100 else yr
                try:
                    return date(yr, mo, dy)
                except ValueError:
                    pass
    return None


def _is_per_borough(pdf_path: Path) -> bool:
    """Check if a PDF uses per-borough format (has borough keywords in rows)."""
    BORO_KEYWORDS = {"bronx", "manhattan", "queens", "brooklyn"}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or len(row) < 14:
                        continue
                    # Check first cell for borough keyword
                    cell = row[0]
                    if cell and any(kw in str(cell).strip().lower() for kw in BORO_KEYWORDS):
                        return True
    return False


def _detect_avg_indices(pdf_path: Path) -> Tuple[int, int, int]:
    """
    Detect the column indices for AM, MD, PM averages based on the header row.

    Returns (am_avg_idx, md_avg_idx, pm_avg_idx).

    Two known formats:
      - 13-col (older): headers don't include 3:00 PM / 8:00 PM → avgs at 4, 8, 12
      - 15-col (newer): headers include 3:00 PM / 8:00 PM → avgs at 4, 9, 14
    """
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row:
                        continue
                    # Look for the header row with time labels
                    row_str = " ".join(str(c) for c in row if c)
                    if "Average" in row_str and ("7:00" in row_str or "AM" in row_str):
                        ncols = len(row)
                        if ncols >= 15:
                            return (4, 9, 14)  # 15-col format
                        else:
                            return (4, 8, 12)  # 13-col format
    # Default to 13-col format
    return (4, 8, 12)


def parse_forecast_pdf(pdf_path: Path) -> Dict[Tuple[date, str], bool]:
    """
    Extract city-wide cloud-coverage data from a forecast PDF.

    Returns {(date, tod): True if avg ≤ CLOUD_THRESHOLD}.
    Only handles city-wide (unified) format — per-borough PDFs should be
    filtered out before calling this.
    """
    tmp = safe_copy(pdf_path, pdf_path.name)
    am_idx, md_idx, pm_idx = _detect_avg_indices(tmp)
    weather: Dict[Tuple[date, str], bool] = {}

    def pct(cell) -> Optional[int]:
        if cell is None:
            return None
        m = re.search(r"(\d+)", str(cell))
        return int(m.group(1)) if m else None

    with pdfplumber.open(tmp) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row:
                        continue

                    # Find which cell holds the date
                    d = None
                    date_col = None
                    for ci, cell in enumerate(row):
                        if cell is None:
                            continue
                        dm = re.search(r"(\d+)/(\d+)/(\d{2,4})", str(cell))
                        if dm:
                            try:
                                mo_ = int(dm.group(1))
                                dy_ = int(dm.group(2))
                                yr_ = int(dm.group(3))
                                yr_ = (2000 + yr_) if yr_ < 100 else yr_
                                d = date(yr_, mo_, dy_)
                                date_col = ci
                            except ValueError:
                                pass
                            break

                    if d is None or date_col is None:
                        continue

                    # For city-wide, date should be at column 0
                    if date_col != 0:
                        continue  # per-borough row (date at col 1), skip

                    # Check row has enough columns
                    need = max(am_idx, md_idx, pm_idx) + 1
                    if len(row) < need:
                        continue

                    am_avg = pct(row[am_idx])
                    md_avg = pct(row[md_idx])
                    pm_avg = pct(row[pm_idx])

                    if am_avg is not None:
                        weather[(d, "AM")] = am_avg <= CLOUD_THRESHOLD
                    if md_avg is not None:
                        weather[(d, "MD")] = md_avg <= CLOUD_THRESHOLD
                    if pm_avg is not None:
                        weather[(d, "PM")] = pm_avg <= CLOUD_THRESHOLD

    if not weather:
        # Fallback: regex-based text extraction
        weather = _parse_forecast_text_fallback(tmp)

    return weather


def _parse_forecast_text_fallback(pdf_path: Path) -> Dict[Tuple[date, str], bool]:
    """
    Regex-based fallback: match lines like
    'Sunday\\n3/8/26  98% 99% 99% 99%  85% 80% 86% 84%  40% 16% 11% 22%'
    """
    weather: Dict[Tuple[date, str], bool] = {}
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)

    flat = re.sub(r"\s+", " ", text)

    # 13-col pattern: 3 hourly + 1 avg per TOD (12 percentage values total)
    pattern_13 = re.compile(
        r"(?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s+"
        r"(\d+)/(\d+)/(\d+)\s+"
        r"\d+%\s+\d+%\s+\d+%\s+(\d+)%\s+"    # 7am 8am 9am AM_avg
        r"\d+%\s+\d+%\s+\d+%\s+(\d+)%\s+"     # 12pm 1pm 2pm MD_avg
        r"\d+%\s+\d+%\s+\d+%\s+(\d+)%"        # 5pm 6pm 7pm PM_avg
    )

    # 15-col pattern: 3 hourly + avg + 4 hourly + avg + 4 hourly + avg
    pattern_15 = re.compile(
        r"(?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s+"
        r"(\d+)/(\d+)/(\d+)\s+"
        r"\d+%\s+\d+%\s+\d+%\s+(\d+)%\s+"         # 7am 8am 9am AM_avg
        r"\d+%\s+\d+%\s+\d+%\s+\d+%\s+(\d+)%\s+"  # 12pm 1pm 2pm 3pm MD_avg
        r"\d+%\s+\d+%\s+\d+%\s+\d+%\s+(\d+)%"     # 5pm 6pm 7pm 8pm PM_avg
    )

    # Try 15-col first, fall back to 13-col
    for pattern in [pattern_15, pattern_13]:
        for m in pattern.finditer(flat):
            mo, dy, yr_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
            yr = (2000 + yr_) if yr_ < 100 else yr_
            try:
                d = date(yr, mo, dy)
            except ValueError:
                continue
            weather[(d, "AM")] = int(m.group(4)) <= CLOUD_THRESHOLD
            weather[(d, "MD")] = int(m.group(5)) <= CLOUD_THRESHOLD
            weather[(d, "PM")] = int(m.group(6)) <= CLOUD_THRESHOLD
        if weather:
            break

    return weather


# ─────────────────────────────────────────────────────────────────────────────
# MAIN: BUILD boolean_weather.json
# ─────────────────────────────────────────────────────────────────────────────

def _load_json_weather(path: Path) -> Tuple[Dict[str, bool], Dict[str, dict]]:
    """Load weather + _meta from a boolean weather JSON file. Returns empty dicts on failure."""
    if not path.exists():
        return {}, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data.get("weather", {})), dict(data.get("_meta", {}))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [WARN] Could not load {path.name}: {e}")
        return {}, {}


def build_weather() -> Path:
    """
    Main entry point. Produces:
      frozen_boolean_weather.json  — append-only, entries for dates > 2 weeks old
      unfrozen_boolean_weather.json — rebuilt each run, entries within 2 weeks
      boolean_weather.json          — combined merge of both (for scheduler compat)
    """
    today = date.today()
    freeze_cutoff = today - timedelta(days=FREEZE_WINDOW)
    bootstrap = not FROZEN_PATH.exists()

    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   Build Weather                                      ║")
    print(f"║   Run date    : {today.strftime('%B %d, %Y'):<38}║")
    print(f"║   Freeze cutoff: {freeze_cutoff}  ({'BOOTSTRAP — parsing all PDFs' if bootstrap else 'incremental'}) ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # ── Load existing frozen + unfrozen data ─────────────────────────────────
    frozen_weather, frozen_meta = _load_json_weather(FROZEN_PATH)
    unfrozen_weather, unfrozen_meta = _load_json_weather(UNFROZEN_PATH)
    if frozen_weather:
        print(f"  Loaded {FROZEN_PATH.name}: {len(frozen_weather)} entries")
    if unfrozen_weather:
        print(f"  Loaded {UNFROZEN_PATH.name}: {len(unfrozen_weather)} entries")

    # ── Age-out shift: move stale unfrozen entries → frozen ──────────────────
    aged_count = 0
    for key in list(unfrozen_weather.keys()):
        d_str = key.rsplit("_", 1)[0]
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if d < freeze_cutoff:
            if key not in frozen_weather:   # never overwrite existing frozen entries
                frozen_weather[key] = unfrozen_weather[key]
                if key in unfrozen_meta:
                    frozen_meta[key] = unfrozen_meta[key]
                aged_count += 1
            del unfrozen_weather[key]
            unfrozen_meta.pop(key, None)
    if aged_count:
        print(f"  Aged-out {aged_count} entries from unfrozen → frozen")

    # ── Scan PDFs ─────────────────────────────────────────────────────────────
    if not FORECAST_DIR.is_dir():
        print(f"  [ERROR] Forecast directory not found: {FORECAST_DIR}")
        _write_frozen_json(frozen_weather, frozen_meta, freeze_cutoff)
        _write_unfrozen_json({}, {}, freeze_cutoff, None, None)
        _write_combined_json(frozen_weather, frozen_meta, {}, {}, freeze_cutoff, None, None)
        return OUTPUT_PATH

    pdf_files = sorted(FORECAST_DIR.glob("*.pdf"))
    print(f"  Found {len(pdf_files)} PDF(s) in Forecast/")

    # Candidates for unfrozen entries (active PDFs, d >= freeze_cutoff)
    # Candidates for frozen bootstrap entries (old PDFs, d < freeze_cutoff)
    CandidateEntry = Tuple[bool, date, str]  # (is_good, last_updated, source)
    active_candidates:    Dict[str, List[CandidateEntry]] = defaultdict(list)
    bootstrap_candidates: Dict[str, List[CandidateEntry]] = defaultdict(list)
    active_processed_files: List[Tuple[date, date, date, str]] = []

    for pdf_path in pdf_files:
        start, end = parse_week_folder_name(pdf_path.stem)
        if start is None or end is None:
            print(f"    [SKIP] Can't parse date range: {pdf_path.name}")
            continue

        is_active = (end >= freeze_cutoff)

        # Non-active PDFs are only parsed during bootstrap
        if not is_active and not bootstrap:
            print(f"    [FROZEN FILE] Skipping {pdf_path.name} (end={end} < cutoff)")
            continue

        tmp = safe_copy(pdf_path, pdf_path.name)

        last_updated = _extract_last_updated(tmp)
        if last_updated is None:
            last_updated = date.fromtimestamp(pdf_path.stat().st_mtime)
            print(f"    [INFO] No 'Last Updated' in {pdf_path.name}, using mtime: {last_updated}")

        if _is_per_borough(tmp):
            print(f"    [SKIP] Per-borough format: {pdf_path.name}")
            continue

        weather_data = parse_forecast_pdf(pdf_path)
        if not weather_data:
            print(f"    [WARN] No weather data extracted: {pdf_path.name}")
            continue

        n_active = n_boot = 0
        for (d, tod), is_good in weather_data.items():
            key = f"{d}_{tod}"
            if d >= freeze_cutoff:
                if is_active:
                    active_candidates[key].append((is_good, last_updated, pdf_path.name))
                    n_active += 1
            else:
                if bootstrap:
                    bootstrap_candidates[key].append((is_good, last_updated, pdf_path.name))
                    n_boot += 1

        label = "[ACTIVE]" if is_active else "[BOOTSTRAP-OLD]"
        print(f"    ✓ {pdf_path.name} {label}: {n_active} active + {n_boot} frozen entries, "
              f"Last Updated: {last_updated}")
        if is_active:
            active_processed_files.append((start, end, last_updated, pdf_path.name))

    # ── Conflict resolution: active candidates → unfrozen ────────────────────
    fresh_weather: Dict[str, bool] = {}
    fresh_meta: Dict[str, dict] = {}
    for key, entries in active_candidates.items():
        entries.sort(key=lambda e: e[1], reverse=True)
        is_good, lu, source = entries[0]
        fresh_weather[key] = is_good
        fresh_meta[key] = {"source": source, "last_updated": str(lu)}

    # ── Bootstrap: apply old-PDF entries → frozen (never overwrite existing) ──
    if bootstrap:
        boot_added = 0
        for key, entries in bootstrap_candidates.items():
            if key in frozen_weather:
                continue
            entries.sort(key=lambda e: e[1], reverse=True)
            is_good, lu, source = entries[0]
            frozen_weather[key] = is_good
            frozen_meta[key] = {"source": source, "last_updated": str(lu)}
            boot_added += 1
        print(f"  Bootstrap: added {boot_added} historical entries to frozen file")

    # ── Current week detection: latest filename end-date wins ────────────────
    # "Last Updated" is only for per-slot conflict resolution, NOT week range.
    # The furthest-out active PDF defines the current week.
    best_week_start: Optional[date] = None
    best_week_end: Optional[date] = None
    if active_processed_files:
        active_processed_files.sort(key=lambda x: x[1], reverse=True)  # end_date DESC
        best_week_start = active_processed_files[0][0]
        best_week_end   = active_processed_files[0][1]

    print(f"\n  Unfrozen (active) entries : {len(fresh_weather)}")
    print(f"  Frozen entries total       : {len(frozen_weather)}")

    # ── Write all three output files ──────────────────────────────────────────
    _write_frozen_json(frozen_weather, frozen_meta, freeze_cutoff)
    _write_unfrozen_json(fresh_weather, fresh_meta, freeze_cutoff, best_week_start, best_week_end)
    return UNFROZEN_PATH


def _write_frozen_json(
    weather: Dict[str, bool],
    meta: Dict[str, dict],
    freeze_cutoff: date,
) -> None:
    """Write frozen_boolean_weather.json (append-only historical store)."""
    output = {
        "generated": datetime.now().isoformat(),
        "frozen_before": str(freeze_cutoff),
        "weather": dict(sorted(weather.items())),
        "_meta": dict(sorted(meta.items())),
    }
    with open(FROZEN_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    good = sum(1 for v in weather.values() if v)
    bad  = sum(1 for v in weather.values() if not v)
    print(f"  ✓ Wrote {FROZEN_PATH.name}: {len(weather)} entries ({good} good, {bad} bad)")


def _write_unfrozen_json(
    weather: Dict[str, bool],
    meta: Dict[str, dict],
    freeze_cutoff: date,
    week_start: Optional[date],
    week_end: Optional[date],
) -> None:
    """Write unfrozen_boolean_weather.json (active/recent, rebuilt each run)."""
    output = {
        "generated": datetime.now().isoformat(),
        "frozen_before": str(freeze_cutoff),
        "current_week_start": str(week_start) if week_start else None,
        "current_week_end":   str(week_end)   if week_end   else None,
        "weather": dict(sorted(weather.items())),
        "_meta": dict(sorted(meta.items())),
    }
    with open(UNFROZEN_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    good = sum(1 for v in weather.values() if v)
    bad  = sum(1 for v in weather.values() if not v)
    print(f"  ✓ Wrote {UNFROZEN_PATH.name}: {len(weather)} entries ({good} good, {bad} bad)")
    if week_start and week_end:
        print(f"    Current week: {week_start} → {week_end}")



# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    build_weather()
