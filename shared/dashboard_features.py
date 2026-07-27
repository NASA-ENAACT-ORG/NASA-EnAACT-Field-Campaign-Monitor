"""Visibility switches for optional public dashboard features.

Set a feature to ``True`` and rebuild the dashboard to restore it. These
switches affect only what is shown in the generated site: the implementation,
persisted data, and server APIs remain available for a future reactivation.
"""

from typing import Final


DASHBOARD_FEATURES: Final[dict[str, bool]] = {
    # Active public monitor views.
    "map": True,
    "filters": True,
    "collectors": True,
    "collector_areas": True,

    # Dormant features: retained in code, hidden from the public dashboard.
    "calendar": False,
    "backpack_status": False,
    "calibration_logs": False,
    "route_groups": False,
    "availability": False,
    "reminders": False,
    "data_upload": False,
}


def dashboard_feature_enabled(name: str) -> bool:
    """Return whether a named dashboard feature should be visible.

    A misspelled feature is treated as disabled so a new optional UI control
    cannot become public merely because its flag was omitted.
    """
    return DASHBOARD_FEATURES.get(name, False)
