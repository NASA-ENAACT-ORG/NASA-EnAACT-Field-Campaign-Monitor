#!/usr/bin/env python3
"""Generate a deterministic, made-up demo dataset for the May 2026 dashboard.

Usage:  python gen_demo_data.py <repo_root>

Writes (inside <repo_root>):
  data/runtime/persisted/Walks_Log.txt
  data/runtime/persisted/Recal_Log.txt
  data/outputs/site/weather.json
  data/outputs/site/schedule_output.json
  data/inputs/collectors/notification_preferences.json

"Now" is frozen at Wed 2026-05-06 10:30 America/New_York. Real historical
values are kept where git history had them (8 walk-log lines, Mar 16 - Apr 15
cloud cover in fixtures/, Mar 1 / Apr 21 calibrations); everything else is invented.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SEED = 20260506
rng = random.Random(SEED)

NOW_DATE = date(2026, 5, 6)
TODS = ("AM", "MD", "PM")
CLOUD_THRESHOLD = 50

# ── Real data recovered from git history (commit 30f3762, Apr 22) ───────────
REAL_LOG = """X_TER_BK_BS_20260312_AM
X_ALX_QN_LA_20260309_MD
X_JEN_QN_LI_20260310_PM
X_JAM_QN_JH_20260228_MD
X_SOT_MN_HT_20260309_MD
X_JEN_MN_HT_20260302_PM
X_SOT_MN_MT_20260318_AM
X_JEN_BK_SP_20260314_PM""".splitlines()


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "weather_real_2026-03-16_to_04-15.json"
real_weather = json.loads(FIXTURE.read_text(encoding="utf-8"))

# ── Weather ─────────────────────────────────────────────────────────────────
weather: dict[str, bool] = {}
meta: dict[str, dict] = {}
for key, info in real_weather["_meta"].items():
    weather[key] = info["cloud_pct"] <= CLOUD_THRESHOLD
    meta[key] = info

# Hand-tuned cloud cover for the week the screenshots focus on.
HAND_CLOUD = {
    "2026-05-03": (18, 26, 41), "2026-05-04": (63, 72, 58), "2026-05-05": (12, 9, 30),
    "2026-05-06": (22, 35, 44), "2026-05-07": (15, 28, 33), "2026-05-08": (81, 74, 47),
    "2026-05-09": (8, 14, 20), "2026-05-10": (44, 57, 61), "2026-05-11": (31, 49, 38),
    "2026-05-12": (90, 86, 77),
}
# Forecast tabs (start, end, last_updated) for the invented range.
TABS = [
    (date(2026, 4, 16), date(2026, 4, 22), date(2026, 4, 15)),
    (date(2026, 4, 20), date(2026, 4, 26), date(2026, 4, 20)),
    (date(2026, 4, 23), date(2026, 4, 29), date(2026, 4, 23)),
    (date(2026, 4, 27), date(2026, 5, 3), date(2026, 4, 27)),
    (date(2026, 4, 30), date(2026, 5, 6), date(2026, 4, 30)),
    (date(2026, 5, 4), date(2026, 5, 10), date(2026, 5, 4)),
    (date(2026, 5, 6), date(2026, 5, 12), date(2026, 5, 6)),
]


def tab_for(d: date):
    covering = [t for t in TABS if t[0] <= d <= t[1]]
    return max(covering, key=lambda t: t[2])


def tab_title(t) -> str:
    return f"{t[0].strftime('%b')} {t[0].day} - {t[1].strftime('%b')} {t[1].day}"


level = 45.0
d = date(2026, 4, 16)
while d <= date(2026, 5, 12):
    # AR(1) day-level cloudiness so clear/overcast days cluster like real weather
    level = max(5.0, min(95.0, 0.55 * level + 0.45 * rng.uniform(0, 100)))
    ds = d.isoformat()
    for i, tod in enumerate(TODS):
        if ds in HAND_CLOUD:
            pct = HAND_CLOUD[ds][i]
        else:
            pct = int(max(0, min(100, level + rng.gauss(0, 14))))
        key = f"{ds}_{tod}"
        t = tab_for(d)
        weather[key] = pct <= CLOUD_THRESHOLD
        meta[key] = {"source": tab_title(t), "last_updated": t[2].isoformat(), "cloud_pct": pct}
    d += timedelta(days=1)

weather = dict(sorted(weather.items()))
meta = dict(sorted(meta.items()))
weather_doc = {
    "generated": "2026-05-06T08:12:41",
    "history_start": "2026-03-16",
    "current_week_start": "2026-05-06",
    "current_week_end": "2026-05-12",
    "weather": weather,
    "_meta": meta,
}


_unforecast_ok: dict[str, bool] = {}


def slot_ok(ds: str, tod: str) -> bool:
    """A walk can only happen on a GO slot. Slots older than the forecast sheet
    (early March, March MD/PM) get a seeded coin flip so March isn't all-clear."""
    key = f"{ds}_{tod}"
    if key in weather:
        return weather[key]
    if key not in _unforecast_ok:
        _unforecast_ok[key] = rng.random() < 0.5
    return _unforecast_ok[key]


# ── Walk log ────────────────────────────────────────────────────────────────
# Final per-route totals we want the map to show (target = 6 walks).
FINAL_TOTALS = {
    "MN_HT": 12, "MN_WH": 9, "MN_UE": 8, "MN_MT": 11, "MN_LE": 10,
    "BX_HP": 5, "BX_NW": 3,
    "BK_DT": 10, "BK_WB": 10, "BK_BS": 8, "BK_CH": 6, "BK_SP": 5, "BK_CI": 3,
    "QN_FU": 8, "QN_LI": 11, "QN_JH": 9, "QN_JA": 5, "QN_FH": 9, "QN_LA": 13, "QN_EE": 4,
}

# EFD student teams (from student_scheduler.py output); bag "X" = shared student bag.
EFD_WALKS = [
    ("QN_FH", "2026-03-30", "AM"), ("QN_FH", "2026-03-30", "MD"), ("QN_FH", "2026-03-30", "PM"),
    ("QN_FU", "2026-04-01", "AM"), ("QN_FU", "2026-04-01", "MD"), ("QN_FU", "2026-04-01", "PM"),
    ("BK_CI", "2026-04-02", "PM"), ("BK_CI", "2026-04-03", "AM"),
    ("BK_DT", "2026-04-04", "MD"), ("BK_DT", "2026-04-04", "PM"), ("BK_DT", "2026-04-05", "AM"),
]
HAND_WALKS = [("A", "TAH", "QN_LI", "2026-05-06", "AM")]  # this morning's upload

AFFINITY = {
    "SOT": None, "AYA": {"MT", "LE", "DT", "WB", "BS", "CH", "SP", "CI"},
    "ALX": {"LE", "WB", "BS", "JA", "FH", "LA"},
    "TAH": {"HT", "MT", "LE", "FU", "LI", "JH", "JA", "FH", "LA", "EE"},
    "JAM": {"JH", "FH"},
    "JEN": {"HP", "HT", "WH", "UE", "MT", "LE", "DT", "WB", "BS", "FU", "LI", "JH", "FH", "LA", "EE"},
    "SCT": {"HT", "WH", "FU", "LI", "JH", "FH", "LA", "EE"},
    "TER": {"HT", "MT", "LE", "DT", "WB", "BS", "CH", "LI", "LA"},
    "ANG": None, "PRA": None, "NRS": None, "NAT": None,
}
# Target walk counts per (backpack, collector) for the generated walks.
TEAM_TARGETS = {
    "A": {"SOT": 23, "AYA": 18, "TAH": 19, "JEN": 9, "PRA": 2, "NRS": 1, "ANG": 1},
    "B": {"TER": 22, "ALX": 16, "SCT": 14, "JAM": 8, "JEN": 6, "NAT": 1},
}

# Drop routes the target checkout no longer knows (e.g. QN_EE was removed on May 18).
sys.path.insert(0, str(ROOT))
from shared.registry import ROUTE_LABELS as REGISTRY_ROUTES  # noqa: E402
FINAL_TOTALS = {r: n for r, n in FINAL_TOTALS.items() if r in REGISTRY_ROUTES}
need = dict(FINAL_TOTALS)
for line in REAL_LOG:
    p = line.split("_")
    need[f"{p[2]}_{p[3]}"] -= 1
for route, _, _ in EFD_WALKS:
    need[route] -= 1
for _, _, route, _, _ in HAND_WALKS:
    need[route] -= 1
assert all(v >= 0 for v in need.values()), need

walks: list[tuple[str, str, str, str, str]] = list(HAND_WALKS)  # (bp, col, route, date, tod)
busy_bp = {(bp, ds, tod) for bp, _, _, ds, tod in walks}
busy_col = {(col, ds, tod) for _, col, _, ds, tod in walks}
day_count: dict[tuple[str, str], int] = {}
assigned = {bp: {c: 0 for c in cols} for bp, cols in TEAM_TARGETS.items()}
assigned["A"]["TAH"] += 1
route_tod: dict[tuple[str, str], int] = {}


def month_weight(day: date) -> float:
    if day < date(2026, 3, 16):
        return 0.25
    if day < date(2026, 4, 1):
        return 0.45
    if day < date(2026, 4, 20):
        return 0.85
    return 1.0


# Candidate slots: every GO slot per backpack, ordered by a random priority that
# favours later months (the campaign ramped up through April).
candidates: list[tuple[float, str, str, str]] = []
day = date(2026, 3, 2)
while day < NOW_DATE:
    ds = day.isoformat()
    weekend_factor = 0.5 if day.weekday() >= 5 else 1.0
    for bp in ("A", "B"):
        for tod in TODS:
            if slot_ok(ds, tod):
                prio = rng.random() / (month_weight(day) * weekend_factor)
                if ds >= "2026-05-03":
                    prio *= 0.25  # keep the screenshot week busy
                candidates.append((prio, bp, ds, tod))
    day += timedelta(days=1)
candidates.sort()

# How many collectors could walk each route (scarce routes get priority from
# flexible collectors such as SOT and staff).
route_supply = {
    r: sum(1 for c, aff in AFFINITY.items() if aff is None or r.split("_")[1] in aff)
    for r in FINAL_TOTALS
}


def pick_collector(bp: str, ds: str, tod: str, exclude: set[str]) -> str | None:
    pool = [
        c for c, t in TEAM_TARGETS[bp].items()
        if assigned[bp][c] < t and c not in exclude
        and (c, ds, tod) not in busy_col and col_day.get((c, ds), 0) < 2
    ]
    if not pool:
        return None
    weights = [TEAM_TARGETS[bp][c] - assigned[bp][c] for c in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def pick_route(col: str, tod: str, relaxed: bool = False) -> str | None:
    aff = AFFINITY[col]
    routes = [
        r for r, n in need.items()
        if n > 0 and (relaxed or aff is None or r.split("_")[1] in aff)
    ]
    if not routes:
        return None

    def score(r: str) -> float:
        s = need[r] + rng.random() * 1.5 - 0.6 * route_tod.get((r, tod), 0)
        if aff is None:
            s += 6.0 / route_supply[r]  # flexible walkers cover hard-to-staff routes
        return s

    return max(routes, key=score)


col_day: dict[tuple[str, str], int] = {}


def place(bp: str, col: str, route: str, ds: str, tod: str) -> None:
    walks.append((bp, col, route, ds, tod))
    need[route] -= 1
    assigned[bp][col] += 1
    busy_bp.add((bp, ds, tod))
    busy_col.add((col, ds, tod))
    day_count[(bp, ds)] = day_count.get((bp, ds), 0) + 1
    col_day[(col, ds)] = col_day.get((col, ds), 0) + 1
    route_tod[(route, tod)] = route_tod.get((route, tod), 0) + 1


for relaxed in (False, True):
    for _, bp, ds, tod in candidates:
        if not any(n > 0 for n in need.values()):
            break
        if (bp, ds, tod) in busy_bp or day_count.get((bp, ds), 0) >= 2:
            continue
        tried: set[str] = set()
        while True:
            col = pick_collector(bp, ds, tod, tried)
            if col is None:
                break
            route = pick_route(col, tod, relaxed)
            if route:
                place(bp, col, route, ds, tod)
                break
            tried.add(col)

leftover = {r: n for r, n in need.items() if n > 0}
if leftover:
    print("[demo] WARNING: unfilled route needs:", leftover)

log_lines = list(REAL_LOG)
for route, ds, tod in EFD_WALKS:
    boro, neigh = route.split("_")
    log_lines.append(f"X_EFD_{boro}_{neigh}_{ds.replace('-', '')}_{tod}")
for bp, col, route, ds, tod in sorted(walks, key=lambda w: (w[3], TODS.index(w[4]), w[0])):
    boro, neigh = route.split("_")
    log_lines.append(f"{bp}_{col}_{boro}_{neigh}_{ds.replace('-', '')}_{tod}")

# ── Schedule (claims for today and the coming week) ─────────────────────────
LABELS = {
    "MN_UE": "Manhattan - Upper East Side", "QN_JA": "Queens - Jamaica", "QN_FU": "Queens - Flushing",
    "BX_HP": "Bronx - Hunts Point", "BK_SP": "Brooklyn - Sunset Park", "BK_CI": "Brooklyn - Coney Island",
    "QN_EE": "Queens - East Elmhurst", "BX_NW": "Bronx - Norwood", "MN_WH": "Manhattan - Washington Hts",
    "BK_CH": "Brooklyn - Crown Heights", "QN_JH": "Queens - Jackson Heights",
}
CLAIMS = [
    ("A", "BX_HP", "SOT", "2026-05-06", "MD", "2026-05-03T19:12:00-04:00"),
    ("B", "QN_JA", "ALX", "2026-05-06", "PM", "2026-05-04T08:41:00-04:00"),
    ("B", "QN_FU", "SCT", "2026-05-07", "AM", "2026-05-04T21:05:00-04:00"),
    ("A", "MN_UE", "JEN", "2026-05-07", "MD", "2026-05-05T10:22:00-04:00"),
    ("B", "BK_SP", "TER", "2026-05-07", "PM", "2026-05-05T12:47:00-04:00"),
    ("A", "BK_CI", "AYA", "2026-05-08", "AM", "2026-05-02T16:30:00-04:00"),
    ("B", "QN_EE", "SCT", "2026-05-08", "PM", "2026-05-05T22:15:00-04:00"),
    ("A", "BX_NW", "SOT", "2026-05-09", "MD", "2026-05-05T18:03:00-04:00"),
    ("B", "QN_JA", "ALX", "2026-05-11", "AM", "2026-05-06T07:58:00-04:00"),
    ("A", "MN_WH", "JEN", "2026-05-11", "PM", "2026-05-06T09:14:00-04:00"),
    ("A", "BK_CH", "AYA", "2026-05-13", "MD", "2026-05-06T09:40:00-04:00"),
    ("B", "QN_JH", "JAM", "2026-05-14", "AM", "2026-05-06T10:05:00-04:00"),
]
assignments = []
CLAIMS = [c for c in CLAIMS if c[1] in REGISTRY_ROUTES]
for bp, route, col, ds, tod, claimed_at in CLAIMS:
    boro, neigh = route.split("_")
    assignments.append({
        "id": f"{bp}_{route}_{ds}_{tod}",
        "route": route, "label": LABELS[route], "boro": boro, "neigh": neigh,
        "tod": tod, "backpack": bp, "collector": col, "date": ds,
        "status": "claimed", "claimed_at": claimed_at, "claimed_by": col,
        "updated_at": claimed_at,
        "weather_advisory": weather.get(f"{ds}_{tod}") is False,
    })

window = {k: v for k, v in weather.items() if "2026-05-06" <= k[:10] <= "2026-05-12"}
schedule_doc = {
    "generated": "2026-05-06",
    "generated_at": "2026-05-06T10:05:12-04:00",
    "week_start": min(a["date"] for a in assignments),
    "week_end": max(a["date"] for a in assignments),
    "weather_history_start": "2026-04-26",
    "weather_week_start": "2026-05-06",
    "weather_week_end": "2026-05-12",
    "weather": window,
    "bad_weather_slots": sorted(k for k, v in window.items() if not v),
    "assignments": assignments,
    "unassigned": [],
    "backpack_status": {
        "B": {"holder": "", "location": "LaGuardia", "updated_at": "2026-05-05T18:40:00-04:00",
              "updated_by": "TER", "source": "manual"},
    },
}

# ── Calibration log ─────────────────────────────────────────────────────────
recal = ["RECAL_A_20260301", "RECAL_B_20260301", "RECAL_A_20260322", "RECAL_B_20260324",
         "RECAL_A_20260421", "RECAL_B_20260428"]

# ── Reminder opt-ins (fake addresses; JAM intentionally missing) ────────────
prefs = {}
for cid, name in (("SOT", "soteri"), ("AYA", "aya"), ("ALX", "alex"), ("TAH", "taha"),
                  ("JEN", "jennifer"), ("SCT", "scott"), ("TER", "terra")):
    prefs[cid] = {"enabled": True, "email": f"{name}@example.com",
                  "preferred_channels": ["email"], "slack_user_id": ""}
prefs["JAM"] = {"enabled": False, "email": "", "preferred_channels": ["email"], "slack_user_id": ""}

# ── Write everything ────────────────────────────────────────────────────────
site = ROOT / "data" / "outputs" / "site"
persisted = ROOT / "data" / "runtime" / "persisted"
site.mkdir(parents=True, exist_ok=True)
persisted.mkdir(parents=True, exist_ok=True)
(persisted / "Walks_Log.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
(persisted / "Recal_Log.txt").write_text("\n".join(recal), encoding="utf-8")
(site / "weather.json").write_text(json.dumps(weather_doc, indent=2), encoding="utf-8")
(site / "schedule_output.json").write_text(json.dumps(schedule_doc, indent=2), encoding="utf-8")
prefs_path = ROOT / "data" / "inputs" / "collectors" / "notification_preferences.json"
prefs_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")

# ── Summary ─────────────────────────────────────────────────────────────────
totals: dict[str, int] = {}
by_col: dict[str, int] = {}
for line in log_lines:
    p = line.split("_")
    totals[f"{p[2]}_{p[3]}"] = totals.get(f"{p[2]}_{p[3]}", 0) + 1
    by_col[p[1]] = by_col.get(p[1], 0) + 1
print(f"[demo] walks: {len(log_lines)}  (real {len(REAL_LOG)}, EFD {len(EFD_WALKS)}, generated {len(walks)})")
print("[demo] at target (6+):", sum(1 for v in totals.values() if v >= 6), "/", len(FINAL_TOTALS))
print("[demo] per route:", dict(sorted(totals.items())))
print("[demo] per collector:", dict(sorted(by_col.items(), key=lambda kv: -kv[1])))
print(f"[demo] claims: {len(assignments)}  advisory: {sum(a['weather_advisory'] for a in assignments)}")
print(f"[demo] weather keys: {len(weather)}  ({min(weather)[:10]} -> {max(weather)[:10]})")
