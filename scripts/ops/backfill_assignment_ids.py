#!/usr/bin/env python3
"""
Backfill missing assignment IDs in schedule_output.json.

Usage:
  py -3 scripts/ops/backfill_assignment_ids.py
  py -3 scripts/ops/backfill_assignment_ids.py --apply
  py -3 scripts/ops/backfill_assignment_ids.py --schedule path/to/schedule_output.json --apply
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.paths import SCHEDULE_OUTPUT_JSON
from shared.schedule_store import ScheduleValidationError, load_schedule, save_schedule


def _canonical_assignment_id(assignment: dict) -> str:
    backpack = str(assignment.get("backpack", "")).upper().strip()
    route = str(assignment.get("route", "")).upper().strip()
    date_str = str(assignment.get("date", "")).strip()
    tod = str(assignment.get("tod", "")).upper().strip()
    return f"{backpack}_{route}_{date_str}_{tod}"


def run_backfill(schedule_path: Path, apply_changes: bool) -> int:
    try:
        schedule_data = load_schedule(schedule_path, strict=False)
    except ScheduleValidationError as exc:
        print(f"FAIL load schedule: {exc}")
        return 1

    assignments = schedule_data.get("assignments", [])
    seen_ids: set[str] = set()
    for assignment in assignments:
        explicit = str(assignment.get("id", "")).strip()
        if explicit:
            seen_ids.add(explicit)

    scanned = 0
    updated = 0
    already_had_id = 0
    collisions = 0
    skipped_missing_fields = 0

    for assignment in assignments:
        scanned += 1
        explicit = str(assignment.get("id", "")).strip()
        if explicit:
            already_had_id += 1
            continue

        backpack = str(assignment.get("backpack", "")).upper().strip()
        route = str(assignment.get("route", "")).upper().strip()
        date_str = str(assignment.get("date", "")).strip()
        tod = str(assignment.get("tod", "")).upper().strip()
        if not (backpack and route and date_str and tod):
            skipped_missing_fields += 1
            continue
        try:
            date.fromisoformat(date_str)
        except Exception:
            skipped_missing_fields += 1
            continue

        candidate = _canonical_assignment_id(assignment)
        if candidate in seen_ids:
            collisions += 1
            continue

        if apply_changes:
            assignment["id"] = candidate
        seen_ids.add(candidate)
        updated += 1

    mode = "apply" if apply_changes else "dry-run"
    print(f"Mode: {mode}")
    print(f"Schedule: {schedule_path}")
    print(f"Assignments scanned: {scanned}")
    print(f"Updated (missing id -> canonical id): {updated}")
    print(f"Already had id: {already_had_id}")
    print(f"Collisions skipped: {collisions}")
    print(f"Missing-field skips: {skipped_missing_fields}")

    if apply_changes and updated > 0:
        try:
            save_schedule(schedule_data, schedule_path, make_backup=True)
            print("Saved updated schedule (backup created).")
        except ScheduleValidationError as exc:
            print(f"FAIL save schedule: {exc}")
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing assignment IDs in schedule JSON")
    parser.add_argument(
        "--schedule",
        type=Path,
        default=SCHEDULE_OUTPUT_JSON,
        help="Path to schedule_output.json",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply and save changes (default is dry-run)",
    )
    args = parser.parse_args()
    return run_backfill(args.schedule, apply_changes=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
