"""Schedule storage utilities for schedule_output.json.

This module centralizes schedule file I/O and validation so API handlers can
avoid duplicating conflict checks and write logic.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from shared.paths import SCHEDULE_OUTPUT_JSON
from shared.registry import (
    BACKPACK_TO_SCHEDULE_COLLECTORS,
    ROUTE_CODES,
    SCHEDULE_COLLECTOR_IDS,
)

TODS = {"AM", "MD", "PM"}
_REQUIRED_TOP_LEVEL = {
    "generated",
    "generated_at",
    "week_start",
    "week_end",
    "weather",
    "bad_weather_slots",
    "assignments",
    "unassigned",
}
_REQUIRED_ASSIGNMENT_FIELDS = {
    "route",
    "label",
    "boro",
    "neigh",
    "tod",
    "backpack",
    "collector",
    "date",
}


class ScheduleValidationError(ValueError):
    """Raised when schedule data fails schema or conflict checks."""


def _iso_now() -> str:
    return datetime.now().isoformat()


def _validate_date(value: str, field_name: str) -> None:
    try:
        date.fromisoformat(value)
    except Exception as exc:  # pragma: no cover - defensive
        raise ScheduleValidationError(
            f"Invalid {field_name}: {value!r} (expected YYYY-MM-DD)"
        ) from exc


def _assignment_slot_key(assignment: dict) -> tuple[str, str, str]:
    return (
        str(assignment.get("backpack", "")).upper(),
        str(assignment.get("date", "")),
        str(assignment.get("tod", "")).upper(),
    )


def _collector_slot_key(assignment: dict) -> tuple[str, str, str]:
    return (
        str(assignment.get("collector", "")).upper(),
        str(assignment.get("date", "")),
        str(assignment.get("tod", "")).upper(),
    )


def _assignment_lookup_aliases(assignment: dict) -> set[str]:
    backpack, date_str, tod = _assignment_slot_key(assignment)
    route = str(assignment.get("route", "")).upper()
    aliases = {
        f"{backpack}_{route}_{date_str}_{tod}",
        f"{backpack}|{route}|{date_str}|{tod}",
    }
    explicit = str(assignment.get("id", "")).strip()
    if explicit:
        aliases.add(explicit)
    return aliases


def build_default_schedule(today: date | None = None) -> dict:
    """Return an empty schedule document with compatibility fields present."""
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    return {
        "generated": str(today),
        "generated_at": _iso_now(),
        "week_start": str(week_start),
        "week_end": str(week_end),
        "weather_history_start": None,
        "weather_week_start": str(week_start),
        "weather_week_end": str(week_end),
        "weather": {},
        "bad_weather_slots": [],
        "assignments": [],
        "unassigned": [],
    }


def validate_schedule(data: dict) -> None:
    """Validate schema and conflict constraints for schedule data."""
    if not isinstance(data, dict):
        raise ScheduleValidationError("Schedule must be a JSON object")

    missing_top = sorted(_REQUIRED_TOP_LEVEL - set(data.keys()))
    if missing_top:
        raise ScheduleValidationError(
            "Missing required top-level fields: " + ", ".join(missing_top)
        )

    if not isinstance(data.get("assignments"), list):
        raise ScheduleValidationError("'assignments' must be an array")
    if not isinstance(data.get("unassigned"), list):
        raise ScheduleValidationError("'unassigned' must be an array")
    if not isinstance(data.get("weather"), dict):
        raise ScheduleValidationError("'weather' must be an object")
    if not isinstance(data.get("bad_weather_slots"), list):
        raise ScheduleValidationError("'bad_weather_slots' must be an array")

    if data.get("week_start"):
        _validate_date(str(data["week_start"]), "week_start")
    if data.get("week_end"):
        _validate_date(str(data["week_end"]), "week_end")

    seen_assignment_slots: set[tuple[str, str, str]] = set()
    seen_collector_slots: set[tuple[str, str, str]] = set()
    seen_lookup_aliases: set[str] = set()

    for idx, assignment in enumerate(data["assignments"]):
        if not isinstance(assignment, dict):
            raise ScheduleValidationError(f"Assignment #{idx} must be an object")

        missing_fields = sorted(_REQUIRED_ASSIGNMENT_FIELDS - set(assignment.keys()))
        if missing_fields:
            raise ScheduleValidationError(
                f"Assignment #{idx} missing fields: {', '.join(missing_fields)}"
            )

        backpack = str(assignment["backpack"]).strip().upper()
        tod = str(assignment["tod"]).strip().upper()
        collector = str(assignment["collector"]).strip().upper()
        route = str(assignment["route"]).strip().upper()
        date_str = str(assignment["date"]).strip()

        if backpack not in {"A", "B"}:
            raise ScheduleValidationError(
                f"Assignment #{idx} has invalid backpack: {assignment['backpack']!r}"
            )
        if tod not in TODS:
            raise ScheduleValidationError(
                f"Assignment #{idx} has invalid tod: {assignment['tod']!r}"
            )
        if not collector:
            raise ScheduleValidationError(f"Assignment #{idx} collector cannot be empty")
        if not route:
            raise ScheduleValidationError(f"Assignment #{idx} route cannot be empty")
        if route not in ROUTE_CODES:
            raise ScheduleValidationError(
                f"Assignment #{idx} has invalid route: {assignment['route']!r}"
            )
        if collector not in SCHEDULE_COLLECTOR_IDS:
            raise ScheduleValidationError(
                f"Assignment #{idx} has invalid collector: {assignment['collector']!r}"
            )
        allowed_collectors = BACKPACK_TO_SCHEDULE_COLLECTORS.get(backpack, frozenset())
        if collector not in allowed_collectors:
            raise ScheduleValidationError(
                f"Assignment #{idx} collector {collector} is not eligible "
                f"for Backpack {backpack}"
            )

        _validate_date(date_str, f"assignments[{idx}].date")

        assignment["backpack"] = backpack
        assignment["tod"] = tod
        assignment["collector"] = collector
        assignment["route"] = route
        assignment["date"] = date_str
        if "id" in assignment:
            assignment["id"] = str(assignment.get("id", "")).strip()

        slot_key = _assignment_slot_key(assignment)
        if slot_key in seen_assignment_slots:
            raise ScheduleValidationError(
                "Duplicate slot key (backpack+date+tod): "
                f"{slot_key[0]} {slot_key[1]} {slot_key[2]}"
            )
        seen_assignment_slots.add(slot_key)

        collector_key = _collector_slot_key(assignment)
        if collector_key in seen_collector_slots:
            raise ScheduleValidationError(
                "Collector double-booked for date/tod: "
                f"{collector_key[0]} {collector_key[1]} {collector_key[2]}"
            )
        seen_collector_slots.add(collector_key)

        lookup_aliases = _assignment_lookup_aliases(assignment)
        duplicate_aliases = lookup_aliases.intersection(seen_lookup_aliases)
        if duplicate_aliases:
            duplicate = sorted(duplicate_aliases)[0]
            raise ScheduleValidationError(
                f"Duplicate assignment lookup id: {duplicate}"
            )
        seen_lookup_aliases.update(lookup_aliases)


def load_schedule(path: Path = SCHEDULE_OUTPUT_JSON, *, strict: bool = False) -> dict:
    """Load schedule JSON from disk.

    When strict=False and the file is missing, returns a default schedule.
    When strict=True and file is missing or invalid, raises ScheduleValidationError.
    """
    if not path.exists():
        if strict:
            raise ScheduleValidationError(f"Schedule file does not exist: {path}")
        return build_default_schedule()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScheduleValidationError(f"Invalid JSON in {path}: {exc}") from exc

    validate_schedule(data)
    return data


def _atomic_write_json(path: Path, payload: str) -> None:
    """Atomically replace a JSON file by writing to a temp file first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_schedule(data: dict, path: Path = SCHEDULE_OUTPUT_JSON, *, make_backup: bool = True) -> None:
    """Validate then persist schedule JSON using atomic write semantics."""
    validate_schedule(data)
    data["generated"] = str(date.today())
    data["generated_at"] = _iso_now()

    if make_backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        backup_path.write_bytes(path.read_bytes())

    payload = json.dumps(data, indent=2, ensure_ascii=False)
    _atomic_write_json(path, payload)
