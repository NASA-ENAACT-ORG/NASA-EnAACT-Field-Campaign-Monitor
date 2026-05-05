#!/usr/bin/env python3
"""Offline edge-case regression checks across active repo helpers."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import warnings
from datetime import date, timedelta
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\..*")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
server_path = REPO_ROOT / "app" / "server"
if str(server_path) not in sys.path:
    sys.path.insert(0, str(server_path))

from shared.notification_preferences import destinations_for_collector, load_notification_preferences
from shared.registry import BACKPACK_TO_SCHEDULE_COLLECTORS, ROUTE_CODES, STUDENT_COLLECTORS
from shared.schedule_store import (
    ScheduleValidationError,
    build_default_schedule,
    load_schedule,
    save_schedule,
    schedule_today,
    validate_schedule,
)

import serve
import upload_buffer
from pipelines.students import student_scheduler
from pipelines.weather import build_weather


ROUTES = sorted(ROUTE_CODES)
A_COLLECTOR = sorted(BACKPACK_TO_SCHEDULE_COLLECTORS["A"])[0]
SHARED_COLLECTORS = sorted(
    BACKPACK_TO_SCHEDULE_COLLECTORS["A"].intersection(BACKPACK_TO_SCHEDULE_COLLECTORS["B"])
)


def _expect_raises(name: str, exc_type: type[Exception], substring: str, func) -> None:
    try:
        func()
    except exc_type as exc:
        if substring and substring not in str(exc):
            raise AssertionError(f"{name}: expected {substring!r}, got {exc!r}") from exc
        return
    raise AssertionError(f"{name}: expected {exc_type.__name__}")


def _valid_assignment(**overrides) -> dict:
    route = ROUTES[0]
    assignment = {
        "id": "A_TEST_2026-05-05_AM",
        "route": route,
        "label": route,
        "boro": route.split("_", 1)[0],
        "neigh": route.split("_", 1)[1] if "_" in route else route,
        "tod": "AM",
        "backpack": "A",
        "collector": A_COLLECTOR,
        "date": "2026-05-05",
        "status": "claimed",
    }
    assignment.update(overrides)
    return assignment


def _schedule_with(assignments: list) -> dict:
    schedule = build_default_schedule(date(2026, 5, 4))
    schedule["assignments"] = assignments
    return schedule


def _student_for_schedule() -> tuple[str, str]:
    students = set(STUDENT_COLLECTORS)
    for backpack in ("A", "B"):
        eligible = sorted(students.intersection(BACKPACK_TO_SCHEDULE_COLLECTORS[backpack]))
        if eligible:
            return backpack, eligible[0]
    raise AssertionError("no student collector is eligible for schedule notifications")


def _check_schedule_store_edges() -> None:
    _expect_raises("non-object schedule", ScheduleValidationError, "JSON object", lambda: validate_schedule([]))
    _expect_raises(
        "missing top-level fields",
        ScheduleValidationError,
        "Missing required top-level fields",
        lambda: validate_schedule({}),
    )

    bad = build_default_schedule(date(2026, 5, 4))
    bad["assignments"] = {}
    _expect_raises(
        "assignments not array",
        ScheduleValidationError,
        "'assignments' must be an array",
        lambda: validate_schedule(bad),
    )

    bad = build_default_schedule(date(2026, 5, 4))
    bad["week_start"] = "2026-99-99"
    _expect_raises("bad week_start", ScheduleValidationError, "Invalid week_start", lambda: validate_schedule(bad))

    normalized = _schedule_with([_valid_assignment(route=ROUTES[0].lower(), tod="am")])
    validate_schedule(normalized)
    assert normalized["assignments"][0]["route"] == ROUTES[0]
    assert normalized["assignments"][0]["tod"] == "AM"

    cases = [
        ("assignment not object", "must be an object", lambda: _schedule_with(["bad"])),
        (
            "missing assignment field",
            "missing fields",
            lambda: _schedule_with([{k: v for k, v in _valid_assignment().items() if k != "route"}]),
        ),
        ("bad backpack", "invalid backpack", lambda: _schedule_with([_valid_assignment(backpack="C")])),
        ("bad tod", "invalid tod", lambda: _schedule_with([_valid_assignment(tod="NIGHT")])),
        ("empty collector", "collector cannot be empty", lambda: _schedule_with([_valid_assignment(collector="")])),
        ("empty route", "route cannot be empty", lambda: _schedule_with([_valid_assignment(route="")])),
        ("bad route", "invalid route", lambda: _schedule_with([_valid_assignment(route="NOPE_ROUTE")])),
        ("bad collector", "invalid collector", lambda: _schedule_with([_valid_assignment(collector="ZZZ")])),
        ("bad date", "Invalid assignments[0].date", lambda: _schedule_with([_valid_assignment(date="2026-02-31")])),
        (
            "duplicate slot",
            "Duplicate slot key",
            lambda: _schedule_with([_valid_assignment(id="one"), _valid_assignment(id="two", route=ROUTES[1])]),
        ),
        (
            "duplicate explicit id",
            "Duplicate assignment lookup id",
            lambda: _schedule_with([_valid_assignment(id="same"), _valid_assignment(id="same", date="2026-05-06")]),
        ),
    ]
    for case_name, substring, maker in cases:
        _expect_raises(case_name, ScheduleValidationError, substring, lambda maker=maker: validate_schedule(maker()))

    if SHARED_COLLECTORS:
        collector = SHARED_COLLECTORS[0]
        _expect_raises(
            "duplicate collector slot",
            ScheduleValidationError,
            "Collector double-booked",
            lambda: validate_schedule(
                _schedule_with(
                    [
                        _valid_assignment(id="one", backpack="A", collector=collector),
                        _valid_assignment(id="two", backpack="B", collector=collector, route=ROUTES[1]),
                    ]
                )
            ),
        )

    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "missing.json"
        assert load_schedule(missing, strict=False)["assignments"] == []
        _expect_raises(
            "strict missing schedule",
            ScheduleValidationError,
            "does not exist",
            lambda: load_schedule(missing, strict=True),
        )
        out = Path(td) / "schedule.json"
        save_schedule(_schedule_with([_valid_assignment()]), out, make_backup=False)
        assert len(load_schedule(out, strict=True)["assignments"]) == 1


def _check_notification_edges() -> None:
    old_env = os.environ.pop("NOTIFICATION_PREFERENCES_JSON", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            prefs = load_notification_preferences(Path(td) / "missing.json")
            assert all(not cfg["enabled"] for cfg in prefs.values())

        collector = sorted(STUDENT_COLLECTORS)[0]
        os.environ["NOTIFICATION_PREFERENCES_JSON"] = json.dumps(
            {
                collector.lower(): {
                    "enabled": True,
                    "email": "collector@example.com",
                    "slack_user_id": "U123",
                    "preferred_channels": ["EMAIL", "email", "slack", "pager", ""],
                },
                "bad-entry": "ignored",
            }
        )
        prefs = load_notification_preferences()
        assert prefs[collector]["enabled"] is True
        assert prefs[collector]["preferred_channels"] == ["email", "slack", "pager"]
        assert destinations_for_collector(collector, ["email"], prefs) == [
            {"channel": "email", "target": "collector@example.com"}
        ]
        assert destinations_for_collector(collector, ["slack"], prefs) == [
            {"channel": "slack", "target": "U123"}
        ]
        prefs[collector]["enabled"] = False
        assert destinations_for_collector(collector, ["email", "slack"], prefs) == []

        os.environ["NOTIFICATION_PREFERENCES_JSON"] = "[]"
        _expect_raises(
            "non-object notification prefs",
            ValueError,
            "JSON object",
            load_notification_preferences,
        )
    finally:
        if old_env is None:
            os.environ.pop("NOTIFICATION_PREFERENCES_JSON", None)
        else:
            os.environ["NOTIFICATION_PREFERENCES_JSON"] = old_env


def _check_server_helper_edges() -> None:
    assert serve._normalize_notification_channels(None) == ["email"]
    assert serve._normalize_notification_channels("slack") == ["slack"]
    assert serve._normalize_notification_channels(["EMAIL", "bad", "slack", "email"]) == ["email", "slack"]
    assert serve._normalize_notification_channels({"bad": True}) == ["email"]
    assert serve._redact_notification_target("abc@example.com") == "ab***@example.com"
    assert serve._redact_notification_target("ab@example.com") == "a***@example.com"
    assert serve._redact_notification_target("U123") == "***"

    boundary = "----edgecase"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"collector\"\r\n\r\nTerra\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"notes\"\r\n\r\nline one\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"pom\"; filename=\"bad name?.jpg\"\r\n"
        "Content-Type: image/jpeg\r\n\r\nabc\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"pom\"; filename=\"empty.txt\"\r\n"
        "Content-Type: text/plain\r\n\r\n\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    fields, files = serve._parse_multipart({"Content-Type": f"multipart/form-data; boundary={boundary}"}, body)
    assert fields["collector"] == "Terra"
    assert fields["notes"] == "line one"
    assert files["pom"][0] == ("bad name?.jpg", b"abc")
    assert files["pom"][1] == ("empty.txt", b"")

    with tempfile.TemporaryDirectory() as td:
        tmp_schedule = Path(td) / "schedule.json"
        backpack, collector = _student_for_schedule()
        target_date = str(schedule_today() + timedelta(days=1))
        schedule = build_default_schedule(schedule_today())
        schedule["weather"][f"{target_date}_AM"] = False
        schedule["assignments"] = [
            _valid_assignment(
                id="notify-one",
                backpack=backpack,
                collector=collector,
                route=ROUTES[0],
                date=target_date,
            )
        ]
        save_schedule(schedule, tmp_schedule, make_backup=False)

        old_schedule = serve.SCHEDULE_OUTPUT
        old_download = serve._download_from_gcs
        old_env = os.environ.get("NOTIFICATION_PREFERENCES_JSON")
        old_smtp = os.environ.pop("SMTP_HOST", None)
        old_from = os.environ.pop("NOTIFICATION_FROM_EMAIL", None)
        try:
            serve.SCHEDULE_OUTPUT = tmp_schedule
            serve._download_from_gcs = lambda gcs_path, local_path: False
            os.environ["NOTIFICATION_PREFERENCES_JSON"] = json.dumps(
                {
                    collector: {
                        "enabled": True,
                        "email": "preview@example.com",
                        "slack_user_id": "U999",
                        "preferred_channels": ["email", "slack"],
                    }
                }
            )
            preview = serve._build_notifications_preview(target_date, ["email", "slack"])
            assert preview["assignment_count"] == 1
            msg = preview["messages"][0]
            assert msg["weather_advisory"] is True
            assert msg["sendable"] is True
            assert {"channel": "email", "target": "preview@example.com"} in msg["destinations"]
            redacted = serve._redact_notification_preview(preview)
            assert redacted["messages"][0]["destinations"][0]["target"] != "preview@example.com"
        finally:
            serve.SCHEDULE_OUTPUT = old_schedule
            serve._download_from_gcs = old_download
            if old_env is None:
                os.environ.pop("NOTIFICATION_PREFERENCES_JSON", None)
            else:
                os.environ["NOTIFICATION_PREFERENCES_JSON"] = old_env
            if old_smtp is not None:
                os.environ["SMTP_HOST"] = old_smtp
            if old_from is not None:
                os.environ["NOTIFICATION_FROM_EMAIL"] = old_from


def _check_upload_buffer_edges() -> None:
    old_bucket = os.environ.get("UPLOAD_HOLDING_BUCKET")
    try:
        upload_buffer._backend = None
        upload_buffer._initialized = False
        _expect_raises(
            "stage without backend",
            upload_buffer.StagingError,
            "not initialized",
            lambda: upload_buffer.stage_submission("WALK_A", {}, {}, "127.0.0.1"),
        )
        with tempfile.TemporaryDirectory() as td:
            os.environ["UPLOAD_HOLDING_BUCKET"] = "local:" + td
            upload_buffer._backend = None
            upload_buffer._initialized = False
            upload_buffer._inproc_locks.clear()
            assert upload_buffer.init_holding_bucket() is True
            staged = upload_buffer.stage_submission(
                "WALK_A",
                {"borough": "MN", "route": "MN_TEST"},
                {"pom": [("bad name?.jpg", b"abc"), ("empty.txt", b"")]},
                "127.0.0.1",
            )
            assert len(staged.files) == 2
            assert staged.files[0].blob_path.endswith("bad_name_.jpg")
            refs = upload_buffer.list_pending()
            assert len(refs) == 1 and refs[0].status == "ready"
            claimed = upload_buffer.try_claim(refs[0])
            assert claimed is not None
            assert upload_buffer.try_claim(refs[0]) is None
            with upload_buffer.open_blob_stream(staged.files[0].blob_path) as fh:
                assert fh.read() == b"abc"
            upload_buffer.archive_submission(claimed)
            upload_buffer.release_claim(claimed)
            assert upload_buffer.list_pending() == []
    finally:
        upload_buffer._backend = None
        upload_buffer._initialized = False
        upload_buffer._inproc_locks.clear()
        if old_bucket is None:
            os.environ.pop("UPLOAD_HOLDING_BUCKET", None)
        else:
            os.environ["UPLOAD_HOLDING_BUCKET"] = old_bucket


def _check_weather_edges() -> None:
    assert build_weather.parse_week_folder_name("May 4 - May 10", ref_year=2026) == (
        date(2026, 5, 4),
        date(2026, 5, 10),
    )
    assert build_weather.parse_week_folder_name("not a week", ref_year=2026) == (None, None)
    assert build_weather.pct("45% clouds") == 45
    assert build_weather.pct("") is None
    assert build_weather._parse_mdy("updated 5/6/26") == date(2026, 5, 6)
    assert build_weather._parse_mdy("13/99/26") is None

    rows = [
        ["Monday 5/5/26", "40%", "60%", ""],
        ["Tuesday", "10", "70", "50"],
        ["5/6/26"],
        ["Last Updated:", "3/25/25"],
    ]

    class _FakeGet:
        def execute(self):
            return {"values": rows}

    class _FakeValues:
        def get(self, **kwargs):
            return _FakeGet()

    class _FakeSpreadsheets:
        def values(self):
            return _FakeValues()

    class _FakeService:
        def spreadsheets(self):
            return _FakeSpreadsheets()

    weather, last_updated = build_weather.parse_forecast_tab(_FakeService(), "May 4 - May 10", date(2026, 5, 4))
    assert weather[(date(2026, 5, 5), "AM")] == (True, 40)
    assert weather[(date(2026, 5, 5), "MD")] == (False, 60)
    assert weather[(date(2026, 5, 6), "PM")] == (True, 50)
    assert last_updated == date(2026, 3, 25)


def _check_student_scheduler_edges() -> None:
    assert student_scheduler._parse_tod_cell("AM (7 - 10 AM);MD (12 - 3 PM);noise;PM (4 - 7 PM)") == [
        "AM",
        "MD",
        "PM",
    ]
    seq = student_scheduler.build_tod_sequence(date(2026, 5, 4), date(2026, 5, 5))
    windows = student_scheduler.find_consecutive_windows(set(seq[:4]), seq, min_len=3)
    assert windows == [seq[:3], seq[1:4]]
    teams = {
        "A": {"route": ROUTES[0], "available": seq[:3]},
        "B": {"route": ROUTES[1], "available": seq[3:]},
    }
    assignments, unassigned = student_scheduler.schedule_teams(teams, seq, min_sessions=3, gap=0)
    assert set(assignments) == {"A", "B"}
    assert unassigned == []
    assignments, unassigned = student_scheduler.schedule_teams(teams, seq, min_sessions=3, gap=2)
    assert len(assignments) == 1
    assert len(unassigned) == 1


def main() -> int:
    checks = [
        ("schedule_store schema/conflict edges", _check_schedule_store_edges),
        ("notification preference edges", _check_notification_edges),
        ("server helper/preview/multipart edges", _check_server_helper_edges),
        ("upload buffer local backend edges", _check_upload_buffer_edges),
        ("weather parser edges", _check_weather_edges),
        ("student scheduler helper edges", _check_student_scheduler_edges),
    ]
    for name, func in checks:
        func()
        print(f"PASS {name}")
    print("PASS edge-case regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
