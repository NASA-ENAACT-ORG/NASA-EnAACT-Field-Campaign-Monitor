"""Notification preference loading for collector reminders."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from shared.paths import COLLECTOR_NOTIFICATION_PREFS
from shared.registry import STUDENT_COLLECTORS

DEFAULT_CHANNELS = ("email",)


def _normalize_channels(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return list(DEFAULT_CHANNELS)
    channels = []
    for item in raw:
        channel = str(item).strip().lower()
        if channel and channel not in channels:
            channels.append(channel)
    return channels or list(DEFAULT_CHANNELS)


def load_notification_preferences(path: Path = COLLECTOR_NOTIFICATION_PREFS) -> dict[str, dict[str, Any]]:
    """Load collector notification opt-ins.

    Missing files are treated as "no configured destinations" so preview/send
    endpoints keep working before real addresses are added.
    """
    prefs: dict[str, dict[str, Any]] = {}
    prefs_json = os.environ.get("NOTIFICATION_PREFERENCES_JSON", "").strip()
    if prefs_json:
        raw = json.loads(prefs_json)
    elif path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        raw = {}

    if not isinstance(raw, dict):
        raise ValueError("notification preferences must be a JSON object")

    if raw:
        for cid, config in raw.items():
            collector = str(cid).upper().strip()
            if not collector or not isinstance(config, dict):
                continue
            prefs[collector] = {
                "enabled": bool(config.get("enabled", True)),
                "email": str(config.get("email", "")).strip(),
                "slack_user_id": str(config.get("slack_user_id", "")).strip(),
                "preferred_channels": _normalize_channels(config.get("preferred_channels")),
            }

    for collector in STUDENT_COLLECTORS:
        prefs.setdefault(collector, {
            "enabled": False,
            "email": "",
            "slack_user_id": "",
            "preferred_channels": list(DEFAULT_CHANNELS),
        })
    return prefs


def destinations_for_collector(
    collector: str,
    requested_channels: list[str] | None = None,
    prefs: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Return configured destinations for one collector."""
    all_prefs = prefs if prefs is not None else load_notification_preferences()
    config = all_prefs.get(collector.upper(), {})
    if not config.get("enabled"):
        return []

    requested = [c.lower() for c in requested_channels] if requested_channels else None
    channels = requested or list(config.get("preferred_channels", DEFAULT_CHANNELS))
    destinations: list[dict[str, str]] = []
    if "email" in channels and config.get("email"):
        destinations.append({"channel": "email", "target": str(config["email"])})
    if "slack" in channels and config.get("slack_user_id"):
        destinations.append({"channel": "slack", "target": str(config["slack_user_id"])})
    return destinations
