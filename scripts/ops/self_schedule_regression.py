#!/usr/bin/env python3
"""
Focused regression checks for self-scheduling behavior.

Covers:
- claim/unclaim endpoints do not write Walks_Log.txt
- expired schedule assignments prune without touching completed-walk state
- past-date claims and edits are rejected
- dashboard past cells are non-clickable while completed walks still render
- claim success
- duplicate claim conflict
- collector double-booking conflict
- unclaim success
- patch success by fallback alias against explicit ID
- patch route update keeps explicit ID and refreshes boro/neigh/label
- patch date/tod update refreshes weather advisory state
- patch conflict returns validation error
- unclaim succeeds after route edits because slot identity excludes route
- delete success by explicit ID
- patch success by legacy pipe-delimited fallback ID
- delete success by legacy pipe-delimited fallback ID
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.paths import SCHEDULE_OUTPUT_JSON
from shared.registry import (
    BACKPACK_TO_SCHEDULE_COLLECTORS,
    ROUTE_CODES,
    ROUTE_LABELS,
    SCHEDULE_COLLECTOR_IDS,
    SLOT_TODS,
    VALID_BACKPACKS,
)
from shared.schedule_store import (
    ScheduleValidationError,
    build_default_schedule,
    load_schedule,
    load_schedule_pruning_expired,
    refresh_schedule_week_bounds,
    save_schedule,
    schedule_today,
    validate_schedule_date_not_past,
)


SERVER_SOURCE = REPO_ROOT / "app" / "server" / "serve.py"
DASHBOARD_SOURCE = REPO_ROOT / "pipelines" / "dashboard" / "build_dashboard.py"
ALLOWED_ROUTES = sorted(ROUTE_CODES)
ALLOWED_COLLECTORS = set(SCHEDULE_COLLECTOR_IDS)
BACKPACK_TO_COLLECTORS = BACKPACK_TO_SCHEDULE_COLLECTORS
TODS = SLOT_TODS


class ApiConflictError(ValueError):
    pass


@dataclass
class AssignmentRef:
    backpack: str
    route: str
    date_str: str
    tod: str
    collector: str
    assignment_id: str


def _compose_id(backpack: str, route: str, date_str: str, tod: str, sep: str = "_") -> str:
    return f"{backpack.upper()}{sep}{route.upper()}{sep}{date_str}{sep}{tod.upper()}"


def _assignment_aliases(assignment: dict) -> set[str]:
    backpack = str(assignment.get("backpack", "")).upper()
    route = str(assignment.get("route", "")).upper()
    date_str = str(assignment.get("date", ""))
    tod = str(assignment.get("tod", "")).upper()
    aliases = {
        _compose_id(backpack, route, date_str, tod, "_"),
        _compose_id(backpack, route, date_str, tod, "|"),
    }
    explicit = str(assignment.get("id", "")).strip()
    if explicit:
        aliases.add(explicit)
    return aliases


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
    validate_schedule_date_not_past(date_str)


def _refresh_schedule_week_bounds(schedule_data: dict) -> None:
    refresh_schedule_week_bounds(schedule_data)


def _find_open_slot(schedule_data: dict, *, start_date: str | None = None) -> tuple[str, str, str, str, str]:
    d0 = date.fromisoformat(start_date) if start_date else schedule_today()
    d0 = max(d0, schedule_today())
    existing = schedule_data.get("assignments", [])
    for offset in range(0, 21):
        date_str = str(d0 + timedelta(days=offset))
        for tod in TODS:
            for backpack in ("A", "B"):
                for route in ALLOWED_ROUTES:
                    slot_taken = any(
                        str(a.get("backpack", "")).upper() == backpack
                        and str(a.get("date", "")) == date_str
                        and str(a.get("tod", "")).upper() == tod
                        for a in existing
                    )
                    if slot_taken:
                        continue
                    for collector in sorted(BACKPACK_TO_COLLECTORS.get(backpack, set())):
                        collector_busy = any(
                            str(a.get("collector", "")).upper() == collector
                            and str(a.get("date", "")) == date_str
                            and str(a.get("tod", "")).upper() == tod
                            for a in existing
                        )
                        if not collector_busy:
                            return backpack, route, date_str, tod, collector
    raise RuntimeError("could not find an open slot in the next 21 days")


def _find_open_slot_for_collector(
    schedule_data: dict,
    *,
    backpack: str,
    collector: str,
    start_date: str | None = None,
) -> tuple[str, str, str]:
    d0 = date.fromisoformat(start_date) if start_date else schedule_today()
    d0 = max(d0, schedule_today())
    existing = schedule_data.get("assignments", [])
    for offset in range(0, 21):
        date_str = str(d0 + timedelta(days=offset))
        for tod in TODS:
            collector_busy = any(
                str(a.get("collector", "")).upper() == collector
                and str(a.get("date", "")) == date_str
                and str(a.get("tod", "")).upper() == tod
                for a in existing
            )
            if collector_busy:
                continue
            for route in ALLOWED_ROUTES:
                slot_taken = any(
                    str(a.get("backpack", "")).upper() == backpack
                    and str(a.get("date", "")) == date_str
                    and str(a.get("tod", "")).upper() == tod
                    for a in existing
                )
                if slot_taken:
                    break
                route_taken = any(
                    str(a.get("backpack", "")).upper() == backpack
                    and str(a.get("route", "")).upper() == route
                    and str(a.get("date", "")) == date_str
                    and str(a.get("tod", "")).upper() == tod
                    for a in existing
                )
                if route_taken:
                    continue
                return route, date_str, tod
    raise RuntimeError("could not find open slot for requested collector in next 21 days")


def _claim(schedule_data: dict, *, backpack: str, route: str, date_str: str, tod: str, collector: str) -> AssignmentRef:
    _validate_inputs(backpack, route, date_str, tod, collector)

    for assignment in schedule_data.get("assignments", []):
        if (
            str(assignment.get("backpack", "")).upper() == backpack
            and str(assignment.get("date", "")) == date_str
            and str(assignment.get("tod", "")).upper() == tod
        ):
            raise ApiConflictError("backpack already has a claimed walk in this date/tod slot")
        if (
            str(assignment.get("collector", "")).upper() == collector
            and str(assignment.get("date", "")) == date_str
            and str(assignment.get("tod", "")).upper() == tod
        ):
            raise ApiConflictError("collector already assigned in this date/tod slot")

    parts = route.split("_", 1)
    boro = parts[0] if parts else ""
    neigh = parts[1] if len(parts) > 1 else route
    label = ROUTE_LABELS.get(route, route)
    now_iso = datetime.now().isoformat()
    assignment_id = _compose_id(backpack, route, date_str, tod, "_")
    assignment = {
        "id": assignment_id,
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
    }
    schedule_data.setdefault("assignments", []).append(assignment)
    _refresh_schedule_week_bounds(schedule_data)
    return AssignmentRef(backpack, route, date_str, tod, collector, assignment_id)


def _unclaim(schedule_data: dict, ref: AssignmentRef) -> None:
    assignments = schedule_data.get("assignments", [])
    for idx, assignment in enumerate(assignments):
        if (
            str(assignment.get("backpack", "")).upper() == ref.backpack
            and str(assignment.get("date", "")) == ref.date_str
            and str(assignment.get("tod", "")).upper() == ref.tod
            and str(assignment.get("collector", "")).upper() == ref.collector
        ):
            assignments.pop(idx)
            _refresh_schedule_week_bounds(schedule_data)
            return
    raise ValueError("assignment not found")


def _patch(schedule_data: dict, assignment_id: str, updates: dict) -> dict:
    assignments = schedule_data.get("assignments", [])
    target = None
    for assignment in assignments:
        if assignment_id in _assignment_aliases(assignment):
            target = assignment
            break
    if target is None:
        raise ValueError("assignment not found")

    allowed = {"backpack", "route", "date", "tod", "collector", "label", "status"}
    normalized_updates = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not normalized_updates:
        raise ValueError("no updatable fields provided")

    for key, value in normalized_updates.items():
        if key in {"backpack", "route", "tod", "collector"}:
            target[key] = str(value).upper().strip()
        else:
            target[key] = str(value).strip()

    backpack = str(target.get("backpack", "")).upper().strip()
    route = str(target.get("route", "")).upper().strip()
    date_str = str(target.get("date", "")).strip()
    tod = str(target.get("tod", "")).upper().strip()
    collector = str(target.get("collector", "")).upper().strip()
    _validate_inputs(backpack, route, date_str, tod, collector)

    for assignment in assignments:
        if assignment is target:
            continue
        if (
            str(assignment.get("backpack", "")).upper() == backpack
            and str(assignment.get("date", "")) == date_str
            and str(assignment.get("tod", "")).upper() == tod
        ):
            raise ApiConflictError("backpack already has a claimed walk in this date/tod slot")
        if (
            str(assignment.get("collector", "")).upper() == collector
            and str(assignment.get("date", "")) == date_str
            and str(assignment.get("tod", "")).upper() == tod
        ):
            raise ApiConflictError("collector already assigned in this date/tod slot")

    if "route" in normalized_updates:
        parts = route.split("_", 1)
        target["boro"] = parts[0] if parts else ""
        target["neigh"] = parts[1] if len(parts) > 1 else route
        if "label" not in normalized_updates:
            target["label"] = ROUTE_LABELS.get(route, route)
    weather_key = f"{date_str}_{tod}"
    target["weather_advisory"] = schedule_data.get("weather", {}).get(weather_key) is False
    target["updated_at"] = datetime.now().isoformat()
    explicit_id = str(target.get("id", "")).strip()
    if explicit_id:
        target["id"] = explicit_id
    else:
        target["id"] = _compose_id(backpack, route, date_str, tod, "_")
    return target


def _delete(schedule_data: dict, assignment_id: str) -> dict:
    assignments = schedule_data.get("assignments", [])
    for idx, assignment in enumerate(assignments):
        if assignment_id in _assignment_aliases(assignment):
            removed = assignments.pop(idx)
            _refresh_schedule_week_bounds(schedule_data)
            return removed
    raise ValueError("assignment not found")


def _save_roundtrip(schedule_data: dict, path: Path) -> dict:
    save_schedule(schedule_data, path, make_backup=False)
    return load_schedule(path, strict=True)


def _sample_assignment(schedule_data: dict, *, assignment_id: str = "test-id") -> dict:
    backpack, route, date_str, tod, collector = _find_open_slot(schedule_data)
    parts = route.split("_", 1)
    return {
        "id": assignment_id,
        "route": route,
        "label": ROUTE_LABELS.get(route, route),
        "boro": parts[0] if parts else "",
        "neigh": parts[1] if len(parts) > 1 else route,
        "tod": tod,
        "backpack": backpack,
        "collector": collector,
        "date": date_str,
        "status": "claimed",
        "claimed_at": datetime.now().isoformat(),
        "claimed_by": collector,
        "updated_at": datetime.now().isoformat(),
    }


def _assert_validation_rejects(schedule_data: dict, path: Path, message: str, mutate) -> None:
    bad_schedule = copy.deepcopy(schedule_data)
    mutate(bad_schedule)
    try:
        save_schedule(bad_schedule, path, make_backup=False)
    except ScheduleValidationError as exc:
        if message not in str(exc):
            raise AssertionError(
                f"expected validation message containing {message!r}, got {exc!r}"
            )
        return
    raise AssertionError(f"expected validation failure containing {message!r}")


def _assert_storage_validation_guards(schedule_data: dict, path: Path) -> None:
    """Catch corrupt schedule_output.json cases before API edits inherit them."""

    def add_bad_route(data: dict) -> None:
        assignment = _sample_assignment(data, assignment_id="bad-route")
        assignment["route"] = "NOPE_ROUTE"
        data.setdefault("assignments", []).append(assignment)

    def add_bad_collector(data: dict) -> None:
        assignment = _sample_assignment(data, assignment_id="bad-collector")
        assignment["collector"] = "ZZZ"
        data.setdefault("assignments", []).append(assignment)

    def add_ineligible_collector(data: dict) -> None:
        assignment = _sample_assignment(data, assignment_id="ineligible-collector")
        assignment["backpack"] = "B"
        assignment["collector"] = "ANG"
        data.setdefault("assignments", []).append(assignment)

    def add_duplicate_lookup_id(data: dict) -> None:
        first = _sample_assignment(data, assignment_id="duplicate-id")
        data.setdefault("assignments", []).append(first)
        second = _sample_assignment(data, assignment_id="duplicate-id")
        data.setdefault("assignments", []).append(second)

    _assert_validation_rejects(schedule_data, path, "invalid route", add_bad_route)
    _assert_validation_rejects(schedule_data, path, "invalid collector", add_bad_collector)
    _assert_validation_rejects(schedule_data, path, "not eligible", add_ineligible_collector)
    _assert_validation_rejects(schedule_data, path, "Duplicate assignment lookup id", add_duplicate_lookup_id)


def _extract_endpoint_branch(source: str, endpoint: str) -> str:
    pattern = re.compile(
        rf'^(?P<indent>\s*)elif endpoint == "{re.escape(endpoint)}":\s*$',
        re.MULTILINE,
    )
    match = pattern.search(source)
    if not match:
        raise AssertionError(f"server endpoint branch not found: {endpoint}")

    indent = match.group("indent")
    branch_start = match.start()
    next_branch = re.search(
        rf'^{re.escape(indent)}(?:elif|else)\b|^    def ',
        source[match.end():],
        re.MULTILINE,
    )
    branch_end = match.end() + next_branch.start() if next_branch else len(source)
    return source[branch_start:branch_end]


def _assert_schedule_endpoints_do_not_write_walk_log() -> None:
    source = SERVER_SOURCE.read_text(encoding="utf-8")
    forbidden = (
        "WALKS_LOG",
        "Walks_Log.txt",
        "_rebuild_walk_log",
        "_run_drive_poll",
    )
    for endpoint in ("/api/schedule/claim", "/api/schedule/unclaim"):
        branch = _extract_endpoint_branch(source, endpoint)
        found = [token for token in forbidden if token in branch]
        if found:
            raise AssertionError(
                f"{endpoint} must not write or rebuild Walks_Log.txt; found {found}"
            )


def _assert_dashboard_past_slot_guards() -> None:
    source = DASHBOARD_SOURCE.read_text(encoding="utf-8")
    required = (
        "function isPastDateStr(dateStr)",
        "Past dates cannot be claimed. Completed walks reappear after upload.",
        "Date must be today or later.",
        "isPast?'cal-slot-past':'cal-slot-click'",
        "addWeekRange(schedData.weather_week_start,schedData.weather_week_end,'weather')",
        "for(let i=-1;i<=1;i++)",
        "w.source==='completed'?' completed':''",
    )
    missing = [token for token in required if token not in source]
    if missing:
        raise AssertionError(f"dashboard past-slot guard missing expected source tokens: {missing}")


def _assert_expired_pruning_preserves_state(path: Path) -> None:
    current = schedule_today()
    yesterday = current - timedelta(days=1)
    schedule = build_default_schedule(current)
    expired = _sample_assignment(schedule, assignment_id="expired-claim")
    expired["date"] = str(yesterday)
    expired["id"] = _compose_id(expired["backpack"], expired["route"], str(yesterday), expired["tod"])
    duplicate_expired = copy.deepcopy(expired)
    duplicate_expired["id"] = "expired-duplicate"
    duplicate_expired["route"] = next(r for r in ALLOWED_ROUTES if r != expired["route"])
    active = _sample_assignment(schedule, assignment_id="active-claim")
    active["date"] = str(current)
    active["id"] = _compose_id(active["backpack"], active["route"], str(current), active["tod"])
    schedule["assignments"] = [expired, duplicate_expired, active]
    schedule["weather"][f"{yesterday}_AM"] = False
    schedule["weather"][f"{current}_AM"] = True
    schedule["backpack_status"] = {
        "A": {
            "holder": "AYA",
            "location": "",
            "updated_at": "2026-05-04T00:00:00",
            "updated_by": "AYA",
            "source": "manual",
        }
    }

    path.write_text(json.dumps(schedule, indent=2), encoding="utf-8")
    schedule, removed = load_schedule_pruning_expired(path, strict=True, today=current)
    if removed != 2:
        raise AssertionError(f"expected two expired assignments to be pruned, got {removed}")
    remaining_ids = {a.get("id") for a in schedule.get("assignments", [])}
    if {expired["id"], duplicate_expired["id"]}.intersection(remaining_ids) or active["id"] not in remaining_ids:
        raise AssertionError("expired pruning removed the wrong assignment")
    if f"{yesterday}_AM" not in schedule.get("weather", {}):
        raise AssertionError("expired pruning should preserve weather history")
    if schedule.get("backpack_status", {}).get("A", {}).get("holder") != "AYA":
        raise AssertionError("expired pruning should preserve backpack_status")
    _save_roundtrip(schedule, path)


def run_regression(schedule_path: Path, start_date: str | None) -> int:
    tmp_dir = tempfile.TemporaryDirectory(prefix="self_schedule_regression_")
    work_path = Path(tmp_dir.name) / "schedule_output.json"
    try:
        # Calendar claims are reservations only. Completed walks enter Walks_Log.txt
        # through the Drive poll/upload flow, not through schedule claim APIs.
        _assert_schedule_endpoints_do_not_write_walk_log()
        _assert_dashboard_past_slot_guards()

        if schedule_path.exists():
            shutil.copy2(schedule_path, work_path)
        schedule = load_schedule(work_path, strict=False)
        _assert_storage_validation_guards(schedule, work_path)
        _assert_expired_pruning_preserves_state(work_path)

        yesterday = schedule_today() - timedelta(days=1)

        # 1) Claim succeeds on open slot
        slot1 = _find_open_slot(schedule, start_date=start_date)
        ref1 = _claim(
            schedule,
            backpack=slot1[0],
            route=slot1[1],
            date_str=slot1[2],
            tod=slot1[3],
            collector=slot1[4],
        )
        schedule = _save_roundtrip(schedule, work_path)

        # Past-date claims are rejected; today remains claimable.
        past_route = next(r for r in ALLOWED_ROUTES if r != ref1.route)
        try:
            _claim(
                copy.deepcopy(schedule),
                backpack=ref1.backpack,
                route=past_route,
                date_str=str(yesterday),
                tod=ref1.tod,
                collector=ref1.collector,
            )
            raise AssertionError("expected past-date claim rejection")
        except ScheduleValidationError as exc:
            if "date must be today or later" not in str(exc):
                raise
        if date.fromisoformat(ref1.date_str) < schedule_today():
            raise AssertionError("today/future claim regression picked a past date")

        # 2) Same-backpack same-slot conflict (even on different route)
        alt_route = next(r for r in ALLOWED_ROUTES if r != ref1.route)
        try:
            _claim(
                schedule,
                backpack=ref1.backpack,
                route=alt_route,
                date_str=ref1.date_str,
                tod=ref1.tod,
                collector=ref1.collector,
            )
            raise AssertionError("expected same-backpack same-slot conflict")
        except ApiConflictError:
            pass

        # 3) Collector double-booking conflict
        shared_collectors = sorted(
            BACKPACK_TO_COLLECTORS["A"].intersection(BACKPACK_TO_COLLECTORS["B"])
        )
        if not shared_collectors:
            raise RuntimeError("no collector is eligible for both backpacks")
        shared_collector = shared_collectors[0]

        if ref1.collector != shared_collector:
            _unclaim(schedule, ref1)
            schedule = _save_roundtrip(schedule, work_path)
            anchor_backpack = "A"
            route_anchor, date_anchor, tod_anchor = _find_open_slot_for_collector(
                schedule,
                backpack=anchor_backpack,
                collector=shared_collector,
                start_date=start_date,
            )
            ref1 = _claim(
                schedule,
                backpack=anchor_backpack,
                route=route_anchor,
                date_str=date_anchor,
                tod=tod_anchor,
                collector=shared_collector,
            )
            schedule = _save_roundtrip(schedule, work_path)

        other_backpack = "B" if ref1.backpack == "A" else "A"
        route_for_other = next(r for r in ALLOWED_ROUTES if r != ref1.route)
        try:
            _claim(
                schedule,
                backpack=other_backpack,
                route=route_for_other,
                date_str=ref1.date_str,
                tod=ref1.tod,
                collector=ref1.collector,
            )
            raise AssertionError("expected collector double-booking conflict")
        except ApiConflictError:
            pass

        # 4) Unclaim succeeds
        _unclaim(schedule, ref1)
        schedule = _save_roundtrip(schedule, work_path)

        # 5) Patch succeeds using fallback alias while preserving explicit ID
        slot2 = _find_open_slot(schedule, start_date=start_date)
        ref2 = _claim(
            schedule,
            backpack=slot2[0],
            route=slot2[1],
            date_str=slot2[2],
            tod=slot2[3],
            collector=slot2[4],
        )
        schedule = _save_roundtrip(schedule, work_path)
        custom_id = f"custom-{ref2.assignment_id}"
        for assignment in schedule.get("assignments", []):
            if str(assignment.get("id", "")).strip() == ref2.assignment_id:
                assignment["id"] = custom_id
                break
        schedule = _save_roundtrip(schedule, work_path)
        patched = _patch(schedule, ref2.assignment_id, {"status": "confirmed"})
        if str(patched.get("status", "")).lower() != "confirmed":
            raise AssertionError("fallback-alias patch failed to update status")
        if str(patched.get("id", "")).strip() != custom_id:
            raise AssertionError("patch should preserve explicit id")
        schedule = _save_roundtrip(schedule, work_path)

        try:
            _patch(copy.deepcopy(schedule), custom_id, {"date": str(yesterday)})
            raise AssertionError("expected past-date patch rejection")
        except ScheduleValidationError as exc:
            if "date must be today or later" not in str(exc):
                raise

        # 6) Route patch updates boro/neigh/label while preserving explicit ID
        route_after_patch = next(r for r in ALLOWED_ROUTES if r != ref2.route)
        patched_route = _patch(schedule, custom_id, {"route": route_after_patch})
        expected_label = ROUTE_LABELS.get(route_after_patch, route_after_patch)
        parts_after_patch = route_after_patch.split("_", 1)
        expected_boro = parts_after_patch[0] if parts_after_patch else ""
        expected_neigh = (
            parts_after_patch[1] if len(parts_after_patch) > 1 else route_after_patch
        )
        if str(patched_route.get("route", "")).upper() != route_after_patch:
            raise AssertionError("route patch failed")
        if str(patched_route.get("boro", "")) != expected_boro:
            raise AssertionError("route patch should refresh boro")
        if str(patched_route.get("neigh", "")) != expected_neigh:
            raise AssertionError("route patch should refresh neigh")
        if str(patched_route.get("label", "")) != expected_label:
            raise AssertionError("route patch should refresh default label")
        if str(patched_route.get("id", "")).strip() != custom_id:
            raise AssertionError("route patch should preserve explicit id")
        ref2 = AssignmentRef(
            backpack=str(patched_route.get("backpack", "")).upper(),
            route=str(patched_route.get("route", "")).upper(),
            date_str=str(patched_route.get("date", "")),
            tod=str(patched_route.get("tod", "")).upper(),
            collector=str(patched_route.get("collector", "")).upper(),
            assignment_id=custom_id,
        )
        schedule = _save_roundtrip(schedule, work_path)

        # 7) Date/tod patch refreshes weather advisory for the new slot.
        weather_route, weather_date, weather_tod = _find_open_slot_for_collector(
            schedule,
            backpack=ref2.backpack,
            collector=ref2.collector,
            start_date=start_date,
        )
        schedule.setdefault("weather", {})[f"{weather_date}_{weather_tod}"] = False
        patched_weather = _patch(
            schedule,
            custom_id,
            {"route": weather_route, "date": weather_date, "tod": weather_tod},
        )
        if patched_weather.get("weather_advisory") is not True:
            raise AssertionError("date/tod patch should refresh weather_advisory")
        ref2 = AssignmentRef(
            backpack=str(patched_weather.get("backpack", "")).upper(),
            route=str(patched_weather.get("route", "")).upper(),
            date_str=str(patched_weather.get("date", "")),
            tod=str(patched_weather.get("tod", "")).upper(),
            collector=str(patched_weather.get("collector", "")).upper(),
            assignment_id=custom_id,
        )
        schedule = _save_roundtrip(schedule, work_path)

        # 8) Patch conflict path (edit into occupied slot) -> API conflict
        slot3 = _find_open_slot(schedule, start_date=start_date)
        ref3 = _claim(
            schedule,
            backpack=slot3[0],
            route=slot3[1],
            date_str=slot3[2],
            tod=slot3[3],
            collector=slot3[4],
        )
        schedule = _save_roundtrip(schedule, work_path)
        try:
            _patch(
                schedule,
                ref3.assignment_id,
                {
                    "backpack": ref2.backpack,
                    "route": ref2.route,
                    "date": ref2.date_str,
                    "tod": ref2.tod,
                    "collector": ref2.collector,
                },
            )
            _save_roundtrip(schedule, work_path)
            raise AssertionError("expected patch conflict on duplicate slot")
        except ApiConflictError:
            schedule = load_schedule(work_path, strict=True)

        # 9) Unclaim uses the durable slot key, not stale route identity.
        stale_route_slot = _find_open_slot(schedule, start_date=start_date)
        stale_ref = _claim(
            schedule,
            backpack=stale_route_slot[0],
            route=stale_route_slot[1],
            date_str=stale_route_slot[2],
            tod=stale_route_slot[3],
            collector=stale_route_slot[4],
        )
        schedule = _save_roundtrip(schedule, work_path)
        stale_new_route = next(r for r in ALLOWED_ROUTES if r != stale_ref.route)
        _patch(schedule, stale_ref.assignment_id, {"route": stale_new_route})
        schedule = _save_roundtrip(schedule, work_path)
        _unclaim(schedule, stale_ref)
        schedule = _save_roundtrip(schedule, work_path)

        # 10) Delete succeeds for explicit ID
        _delete(schedule, ref3.assignment_id)
        schedule = _save_roundtrip(schedule, work_path)

        # 11) Legacy assignment: patch succeeds via pipe-delimited fallback ID
        legacy_backpack, legacy_route, legacy_date, legacy_tod, legacy_collector = _find_open_slot(
            schedule, start_date=start_date
        )
        legacy_parts = legacy_route.split("_", 1)
        schedule.setdefault("assignments", []).append(
            {
                "route": legacy_route,
                "label": ROUTE_LABELS.get(legacy_route, legacy_route),
                "boro": legacy_parts[0] if legacy_parts else "",
                "neigh": legacy_parts[1] if len(legacy_parts) > 1 else legacy_route,
                "tod": legacy_tod,
                "backpack": legacy_backpack,
                "collector": legacy_collector,
                "date": legacy_date,
                "status": "claimed",
                "claimed_at": datetime.now().isoformat(),
                "claimed_by": legacy_collector,
                "updated_at": datetime.now().isoformat(),
            }
        )
        schedule = _save_roundtrip(schedule, work_path)
        legacy_pipe_id = _compose_id(legacy_backpack, legacy_route, legacy_date, legacy_tod, "|")
        patched_legacy = _patch(schedule, legacy_pipe_id, {"status": "confirmed"})
        if str(patched_legacy.get("status", "")).lower() != "confirmed":
            raise AssertionError("legacy pipe-id patch failed")
        schedule = _save_roundtrip(schedule, work_path)

        # 12) Delete succeeds for legacy pipe-delimited fallback ID
        _delete(schedule, legacy_pipe_id)
        schedule = _save_roundtrip(schedule, work_path)

        print("PASS self-scheduling regression")
        print(f"  explicit patch id: {ref2.assignment_id}")
        print(f"  explicit delete id: {ref3.assignment_id}")
        print(f"  legacy patch id: {legacy_pipe_id}")
        print(f"  legacy delete id: {legacy_pipe_id}")
        print(f"  file: {work_path}")
        return 0
    except Exception as exc:
        print(f"FAIL self-scheduling regression: {exc}")
        print(f"  file: {work_path}")
        return 1
    finally:
        tmp_dir.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Focused self-scheduling regression checks")
    parser.add_argument(
        "--schedule",
        type=Path,
        default=SCHEDULE_OUTPUT_JSON,
        help="Path to schedule_output.json (default: shared.paths.SCHEDULE_OUTPUT_JSON)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Optional YYYY-MM-DD date to start open-slot search from",
    )
    args = parser.parse_args()
    return run_regression(args.schedule, args.start_date)


if __name__ == "__main__":
    raise SystemExit(main())
