"""Schedule storage utilities for schedule_output.json.

This module centralizes schedule file I/O and validation so API handlers can
avoid duplicating conflict checks and write logic.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python/runtime compatibility fallback
    ZoneInfo = None

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


SCHEDULE_TIMEZONE = "America/New_York"


def _new_york_dst_bounds_utc(year: int) -> tuple[datetime, datetime]:
    """Return UTC instants for US Eastern DST start/end in a given year."""

    def nth_sunday(month: int, n: int) -> date:
        first = date(year, month, 1)
        offset = (6 - first.weekday()) % 7
        return first + timedelta(days=offset + (7 * (n - 1)))

    dst_start = nth_sunday(3, 2)
    dst_end = nth_sunday(11, 1)
    return (
        datetime(year, 3, dst_start.day, 7, tzinfo=timezone.utc),
        datetime(year, 11, dst_end.day, 6, tzinfo=timezone.utc),
    )


def _fallback_new_york_now() -> datetime:
    """Best-effort New York time when zoneinfo/tzdata is unavailable."""
    utc_now = datetime.now(timezone.utc)
    dst_start, dst_end = _new_york_dst_bounds_utc(utc_now.year)
    offset = timezone(timedelta(hours=-4 if dst_start <= utc_now < dst_end else -5))
    return utc_now.astimezone(offset)


def schedule_now(now: date | datetime | None = None) -> datetime:
    """Return the current schedule clock time in America/New_York."""
    if isinstance(now, datetime):
        if now.tzinfo is not None:
            if ZoneInfo is not None:
                try:
                    return now.astimezone(ZoneInfo(SCHEDULE_TIMEZONE))
                except Exception:
                    pass
            return now
        return now
    if isinstance(now, date):
        return datetime.combine(now, datetime.min.time())

    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(SCHEDULE_TIMEZONE))
        except Exception:
            pass
    return _fallback_new_york_now()


def schedule_today(now: date | datetime | None = None) -> date:
    """Return the current schedule date in America/New_York."""
    return schedule_now(now).date()


def _iso_now() -> str:
    return schedule_now().isoformat()


def parse_schedule_date(value: str, field_name: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except Exception as exc:  # pragma: no cover - defensive
        raise ScheduleValidationError(
            f"Invalid {field_name}: {value!r} (expected YYYY-MM-DD)"
        ) from exc


def _validate_date(value: str, field_name: str) -> None:
    parse_schedule_date(value, field_name)


def is_past_schedule_date(value: str, today: date | datetime | None = None) -> bool:
    """Return True when a schedule date is before the active schedule day."""
    return parse_schedule_date(value) < schedule_today(today)


def validate_schedule_date_not_past(value: str, today: date | datetime | None = None) -> None:
    """Reject dates that are no longer claimable/editable."""
    if is_past_schedule_date(value, today):
        raise ScheduleValidationError("date must be today or later")


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
    today = today or schedule_today()
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


def refresh_schedule_week_bounds(schedule_data: dict, today: date | datetime | None = None) -> None:
    """Keep week_start/week_end aligned to current/future assignment dates."""
    parsed_dates: list[date] = []
    for assignment in schedule_data.get("assignments", []) or []:
        try:
            parsed_dates.append(parse_schedule_date(str(assignment.get("date", ""))))
        except ScheduleValidationError:
            continue

    if parsed_dates:
        start = min(parsed_dates)
        end = max(parsed_dates)
    else:
        current = schedule_today(today)
        start = current - timedelta(days=current.weekday())
        end = start + timedelta(days=6)

    schedule_data["week_start"] = str(start)
    schedule_data["week_end"] = str(end)


def prune_expired_assignments(schedule_data: dict, today: date | datetime | None = None) -> int:
    """Remove assignments dated before the current schedule day.

    Completed walks are restored through Walks_Log.txt after upload/Drive polling;
    schedule_output.json keeps only active reservations.
    """
    assignments = schedule_data.get("assignments")
    if not isinstance(assignments, list):
        return 0

    current = schedule_today(today)
    kept: list = []
    removed = 0
    for assignment in assignments:
        if not isinstance(assignment, dict):
            kept.append(assignment)
            continue
        try:
            assignment_date = parse_schedule_date(str(assignment.get("date", "")))
        except ScheduleValidationError:
            kept.append(assignment)
            continue
        if assignment_date < current:
            removed += 1
        else:
            kept.append(assignment)

    if removed:
        schedule_data["assignments"] = kept
        refresh_schedule_week_bounds(schedule_data, today=current)
    return removed


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


def load_schedule_pruning_expired(
    path: Path = SCHEDULE_OUTPUT_JSON,
    *,
    strict: bool = False,
    today: date | datetime | None = None,
) -> tuple[dict, int]:
    """Load a schedule, allowing expired rows to be pruned before validation.

    This prevents old duplicate/corrupt reservations from blocking the cleanup
    path when their dates are already outside the active scheduling window.
    """
    try:
        data = load_schedule(path, strict=strict)
    except ScheduleValidationError as original_exc:
        if not path.exists():
            raise
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise original_exc
        removed = prune_expired_assignments(data, today=today)
        if not removed:
            raise original_exc
        validate_schedule(data)
        return data, removed

    removed = prune_expired_assignments(data, today=today)
    if removed:
        validate_schedule(data)
    return data, removed


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
    data["generated"] = str(schedule_today())
    data["generated_at"] = _iso_now()

    if make_backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        backup_path.write_bytes(path.read_bytes())

    payload = json.dumps(data, indent=2, ensure_ascii=False)
    _atomic_write_json(path, payload)
