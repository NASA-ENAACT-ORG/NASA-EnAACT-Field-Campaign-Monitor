#!/usr/bin/env python3
"""
Offline smoke test for self-scheduling claim/unclaim logic.

This script does not start the HTTP server. It exercises the core constraints
using a temporary copy of schedule_output.json by default.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

# Add repo root to sys.path so shared package is importable
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.paths import SCHEDULE_OUTPUT_JSON
from shared.registry import (
    BACKPACK_TO_STUDENT_COLLECTORS,
    ROUTE_CODES,
    ROUTE_LABELS,
    SLOT_TODS,
    STUDENT_COLLECTOR_IDS,
    VALID_BACKPACKS,
)
from shared.schedule_store import (
    ScheduleValidationError,
    load_schedule,
    save_schedule,
)

ALLOWED_ROUTES = ROUTE_CODES
ALLOWED_COLLECTORS = STUDENT_COLLECTOR_IDS
BACKPACK_TO_COLLECTORS = BACKPACK_TO_STUDENT_COLLECTORS
TODS = SLOT_TODS


def _refresh_schedule_week_bounds(schedule_data: dict) -> None:
    assignments = schedule_data.get("assignments", []) or []
    parsed_dates: list[date] = []
    for assignment in assignments:
        try:
            parsed_dates.append(date.fromisoformat(str(assignment.get("date", ""))))
        except Exception:
            continue
    if not parsed_dates:
        return
    schedule_data["week_start"] = str(min(parsed_dates))
    schedule_data["week_end"] = str(max(parsed_dates))


def _validate_inputs(backpack: str, route: str, date_str: str, tod: str, collector: str) -> None:
    if backpack not in VALID_BACKPACKS:
        raise ValueError("backpack must be 'A' or 'B'")
    if route not in ALLOWED_ROUTES:
        raise ValueError("route is not a known route code")
    if tod not in TODS:
        raise ValueError("tod must be one of AM, MD, PM")
    if collector not in ALLOWED_COLLECTORS:
        raise ValueError("collector is not a known student collector")
    if collector not in BACKPACK_TO_COLLECTORS.get(backpack, set()):
        raise ValueError(f"collector {collector} is not eligible for Backpack {backpack}")
    try:
        date.fromisoformat(date_str)
    except Exception as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc


def _claim_assignment(schedule_data: dict, *, backpack: str, route: str, date_str: str, tod: str, collector: str) -> dict:
    _validate_inputs(backpack, route, date_str, tod, collector)

    for assignment in schedule_data.get("assignments", []):
        if (
            str(assignment.get("backpack", "")).upper() == backpack
            and str(assignment.get("route", "")).upper() == route
            and str(assignment.get("date", "")) == date_str
            and str(assignment.get("tod", "")).upper() == tod
        ):
            raise ValueError("slot already claimed for this backpack")
        if (
            str(assignment.get("collector", "")).upper() == collector
            and str(assignment.get("date", "")) == date_str
            and str(assignment.get("tod", "")).upper() == tod
        ):
            raise ValueError("collector already assigned in this date/tod slot")

    parts = route.split("_", 1)
    boro = parts[0] if parts else ""
    neigh = parts[1] if len(parts) > 1 else route
    label = ROUTE_LABELS.get(route, route)
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
    return assignment


def _unclaim_assignment(schedule_data: dict, *, backpack: str, route: str, date_str: str, tod: str, collector: str) -> dict:
    match_idx = None
    for idx, assignment in enumerate(schedule_data.get("assignments", [])):
        if (
            str(assignment.get("backpack", "")).upper() == backpack
            and str(assignment.get("route", "")).upper() == route
            and str(assignment.get("date", "")) == date_str
            and str(assignment.get("tod", "")).upper() == tod
        ):
            if collector and str(assignment.get("collector", "")).upper() != collector:
                continue
            match_idx = idx
            break
    if match_idx is None:
        raise ValueError("assignment not found")
    removed = schedule_data["assignments"].pop(match_idx)
    _refresh_schedule_week_bounds(schedule_data)
    return removed


def _pick_open_slot(schedule_data: dict, start_date: str | None) -> tuple[str, str, str, str, str]:
    d0 = date.fromisoformat(start_date) if start_date else date.today()
    existing = schedule_data.get("assignments", [])
    routes = sorted(ALLOWED_ROUTES)
    backpacks = ("A", "B")
    for offset in range(0, 21):
        d_str = str(d0 + timedelta(days=offset))
        for tod in TODS:
            for backpack in backpacks:
                for route in routes:
                    slot_taken = any(
                        str(a.get("backpack", "")).upper() == backpack
                        and str(a.get("route", "")).upper() == route
                        and str(a.get("date", "")) == d_str
                        and str(a.get("tod", "")).upper() == tod
                        for a in existing
                    )
                    if slot_taken:
                        continue
                    for collector in sorted(BACKPACK_TO_COLLECTORS.get(backpack, set())):
                        collector_busy = any(
                            str(a.get("collector", "")).upper() == collector
                            and str(a.get("date", "")) == d_str
                            and str(a.get("tod", "")).upper() == tod
                            for a in existing
                        )
                        if not collector_busy:
                            return backpack, route, d_str, tod, collector
    raise RuntimeError("could not find an open slot in the next 21 days")


def run_smoke(schedule_path: Path, in_place: bool, start_date: str | None) -> int:
    if in_place:
        work_path = schedule_path
        _tmp_dir = None
    else:
        _tmp_dir = tempfile.TemporaryDirectory(prefix="self_schedule_smoke_")
        work_path = Path(_tmp_dir.name) / "schedule_output.json"
        if schedule_path.exists():
            shutil.copy2(schedule_path, work_path)

    try:
        schedule_data = load_schedule(work_path, strict=False)
        before_count = len(schedule_data.get("assignments", []))

        backpack, route, date_str, tod, collector = _pick_open_slot(schedule_data, start_date)
        claimed = _claim_assignment(
            schedule_data,
            backpack=backpack,
            route=route,
            date_str=date_str,
            tod=tod,
            collector=collector,
        )
        save_schedule(schedule_data, work_path, make_backup=False)

        reloaded = load_schedule(work_path, strict=True)
        after_claim_count = len(reloaded.get("assignments", []))
        if after_claim_count != before_count + 1:
            raise AssertionError("claim count check failed")

        removed = _unclaim_assignment(
            reloaded,
            backpack=backpack,
            route=route,
            date_str=date_str,
            tod=tod,
            collector=collector,
        )
        save_schedule(reloaded, work_path, make_backup=False)

        final_data = load_schedule(work_path, strict=True)
        final_count = len(final_data.get("assignments", []))
        if final_count != before_count:
            raise AssertionError("unclaim count check failed")

        print("PASS self-schedule smoke test")
        print(f"  claimed: {claimed['id']} ({collector})")
        print(f"  removed: {removed.get('id') or claimed['id']}")
        print(f"  file: {work_path}")
        if not in_place:
            print("  mode: temp-copy (original schedule unchanged)")
        return 0

    except (ScheduleValidationError, ValueError, AssertionError, RuntimeError) as exc:
        print(f"FAIL self-schedule smoke test: {exc}")
        print(f"  file: {work_path}")
        return 1
    finally:
        if _tmp_dir is not None:
            _tmp_dir.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline smoke test for self-schedule claim/unclaim")
    parser.add_argument(
        "--schedule",
        type=Path,
        default=SCHEDULE_OUTPUT_JSON,
        help="Path to schedule_output.json (default: shared.paths.SCHEDULE_OUTPUT_JSON)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Run directly against the target schedule file (default uses temp copy)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Optional YYYY-MM-DD date to start open-slot search from",
    )
    args = parser.parse_args()
    return run_smoke(args.schedule, args.in_place, args.start_date)


if __name__ == "__main__":
    raise SystemExit(main())
