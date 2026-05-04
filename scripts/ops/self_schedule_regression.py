#!/usr/bin/env python3
"""
Focused regression checks for self-scheduling behavior.

Covers:
- claim/unclaim endpoints do not write Walks_Log.txt
- claim success
- duplicate claim conflict
- collector double-booking conflict
- unclaim success
- patch success by fallback alias against explicit ID
- patch route update keeps explicit ID and refreshes boro/neigh/label
- patch conflict returns validation error
- delete success by explicit ID
- patch success by legacy pipe-delimited fallback ID
- delete success by legacy pipe-delimited fallback ID
"""

from __future__ import annotations

import argparse
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
    BACKPACK_TO_STUDENT_COLLECTORS,
    ROUTE_CODES,
    ROUTE_LABELS,
    SLOT_TODS,
    STUDENT_COLLECTOR_IDS,
    VALID_BACKPACKS,
)
from shared.schedule_store import ScheduleValidationError, load_schedule, save_schedule


SERVER_SOURCE = REPO_ROOT / "app" / "server" / "serve.py"
ALLOWED_ROUTES = sorted(ROUTE_CODES)
ALLOWED_COLLECTORS = set(STUDENT_COLLECTOR_IDS)
BACKPACK_TO_COLLECTORS = BACKPACK_TO_STUDENT_COLLECTORS
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


def _find_open_slot(schedule_data: dict, *, start_date: str | None = None) -> tuple[str, str, str, str, str]:
    d0 = date.fromisoformat(start_date) if start_date else date.today()
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
    d0 = date.fromisoformat(start_date) if start_date else date.today()
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
            and str(assignment.get("route", "")).upper() == ref.route
            and str(assignment.get("date", "")) == ref.date_str
            and str(assignment.get("tod", "")).upper() == ref.tod
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


def run_regression(schedule_path: Path, start_date: str | None) -> int:
    tmp_dir = tempfile.TemporaryDirectory(prefix="self_schedule_regression_")
    work_path = Path(tmp_dir.name) / "schedule_output.json"
    try:
        # Calendar claims are reservations only. Completed walks enter Walks_Log.txt
        # through the Drive poll/upload flow, not through schedule claim APIs.
        _assert_schedule_endpoints_do_not_write_walk_log()

        if schedule_path.exists():
            shutil.copy2(schedule_path, work_path)
        schedule = load_schedule(work_path, strict=False)

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

        # 7) Patch conflict path (edit into occupied slot) -> API conflict
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

        # 8) Delete succeeds for explicit ID
        _delete(schedule, ref3.assignment_id)
        schedule = _save_roundtrip(schedule, work_path)

        # 9) Legacy assignment: patch succeeds via pipe-delimited fallback ID
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

        # 10) Delete succeeds for legacy pipe-delimited fallback ID
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
