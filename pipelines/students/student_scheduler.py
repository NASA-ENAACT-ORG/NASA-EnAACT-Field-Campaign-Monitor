#!/usr/bin/env python3
"""
student_scheduler.py
Generates a bag-passing schedule for EFD student data collectors.

Constraints:
  - 1 shared bag: only one team collects at a time
  - Each team needs >= MIN_SESSIONS consecutive TOD slots
  - GAP_SLOTS buffer between one team's last slot and the next team's first
  - Teams collect only during the availability they submitted on the Google Form

Output:
  - student_schedule_output.json  (merged by walk_scheduler.py as preserved/weather-exempt)
  - student_schedule.html         (visual timeline dashboard)
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date, timedelta
from itertools import permutations
from pathlib import Path

# Add repo root to sys.path so shared package is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.paths import EFD_FORM_CSV, STUDENT_SCHEDULE_JSON, STUDENT_SCHEDULE_HTML

# ── Configuration ─────────────────────────────────────────────────────────────

TODS         = ["AM", "MD", "PM"]
MIN_SESSIONS = 3   # consecutive TOD slots required per team
GAP_SLOTS    = 2   # TOD slots of buffer between successive teams

def _fmt_date(d: date) -> str:
    """'Apr 4' without zero-padding (cross-platform)."""
    return d.strftime("%b") + " " + str(d.day)


ROUTE_LABELS: dict[str, str] = {
    "MN_HT": "Manhattan – Harlem",
    "MN_WH": "Manhattan – Washington Hts",
    "MN_UE": "Manhattan – Upper East Side",
    "MN_MT": "Manhattan – Midtown",
    "MN_LE": "Manhattan – Union Sq/LES",
    "BX_HP": "Bronx – Hunts Point",
    "BX_NW": "Bronx – Norwood",
    "BK_DT": "Brooklyn – Downtown BK",
    "BK_WB": "Brooklyn – Williamsburg",
    "BK_BS": "Brooklyn – Bed Stuy",
    "BK_CH": "Brooklyn – Crown Heights",
    "BK_SP": "Brooklyn – Sunset Park",
    "BK_CI": "Brooklyn – Coney Island",
    "QN_FU": "Queens – Flushing",
    "QN_LI": "Queens – Astoria/LIC",
    "QN_JH": "Queens – Jackson Heights",
    "QN_JA": "Queens – Jamaica",
    "QN_FH": "Queens – Forest Hills",
    "QN_LA": "Queens – LaGuardia CC",
    "QN_EE": "Queens – East Elmhurst",
}

# Colours for up to 8 teams
TEAM_COLORS = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759",
    "#76b7b2", "#edc948", "#b07aa1", "#ff9da7",
]


# ── Parsing ───────────────────────────────────────────────────────────────────

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_date_header(header: str) -> date:
    """'30-Mar' → date(2026, 3, 30)"""
    day_str, mon_str = header.strip().split("-")
    return date(2026, _MONTH_MAP[mon_str], int(day_str))


def _parse_tod_cell(cell: str) -> list[str]:
    """'AM (7 - 10 AM);MD (12 - 3 PM)' → ['AM', 'MD']"""
    result = []
    for part in cell.split(";"):
        p = part.strip()
        if p.startswith("AM"):
            result.append("AM")
        elif p.startswith("MD"):
            result.append("MD")
        elif p.startswith("PM"):
            result.append("PM")
    return result


def parse_google_form(csv_path: Path) -> dict[str, dict]:
    """
    Returns::

        {
            "Robert": {
                "route": "QN_FU",
                "available": [(date(2026,3,30), "PM"), ...]
            },
            ...
        }
    """
    teams: dict[str, dict] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)

    # Index the date columns (everything after 'Who are you' and 'Route ID')
    date_cols: list[tuple[int, date]] = []
    for i, h in enumerate(headers):
        if i < 2:
            continue
        h = h.strip()
        if not h:
            continue
        try:
            date_cols.append((i, _parse_date_header(h)))
        except (ValueError, KeyError):
            pass

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip header row
        for row in reader:
            if not row or not row[0].strip():
                continue
            name  = row[0].strip()
            route = row[1].strip() if len(row) > 1 else ""
            available: list[tuple[date, str]] = []
            for col_idx, d in date_cols:
                cell = row[col_idx] if col_idx < len(row) else ""
                for tod in _parse_tod_cell(cell):
                    available.append((d, tod))
            teams[name] = {"route": route, "available": available}

    return teams


# ── TOD Sequence ──────────────────────────────────────────────────────────────

def build_tod_sequence(start: date, end: date) -> list[tuple[date, str]]:
    seq: list[tuple[date, str]] = []
    d = start
    while d <= end:
        for tod in TODS:
            seq.append((d, tod))
        d += timedelta(days=1)
    return seq


# ── Consecutive Window Finder ─────────────────────────────────────────────────

def find_consecutive_windows(
    available_set: set[tuple[date, str]],
    tod_sequence: list[tuple[date, str]],
    min_len: int = MIN_SESSIONS,
) -> list[list[tuple[date, str]]]:
    """All windows of exactly *min_len* consecutive available slots."""
    windows: list[list[tuple[date, str]]] = []
    run: list[tuple[date, str]] = []

    for slot in tod_sequence:
        if slot in available_set:
            run.append(slot)
        else:
            if len(run) >= min_len:
                for start_i in range(len(run) - min_len + 1):
                    windows.append(run[start_i: start_i + min_len])
            run = []

    if len(run) >= min_len:
        for start_i in range(len(run) - min_len + 1):
            windows.append(run[start_i: start_i + min_len])

    return windows


# ── Scheduler ─────────────────────────────────────────────────────────────────

def schedule_teams(
    teams: dict[str, dict],
    tod_sequence: list[tuple[date, str]],
    min_sessions: int = MIN_SESSIONS,
    gap: int = GAP_SLOTS,
) -> tuple[dict[str, list[tuple[date, str]]], list[str]]:
    """
    Try all N! orderings of teams (N ≤ 8 is fast), greedy-assigning the
    earliest valid window for each team in turn.  Returns the ordering that
    maximises the number of teams scheduled and minimises the finish date.
    """
    slot_index = {slot: i for i, slot in enumerate(tod_sequence)}

    team_windows: dict[str, list[list[tuple[date, str]]]] = {}
    for name, info in teams.items():
        avail_set = set(info["available"])
        wins = find_consecutive_windows(avail_set, tod_sequence, min_sessions)
        wins.sort(key=lambda w: slot_index[w[0]])
        team_windows[name] = wins

    names = list(teams.keys())

    def try_order(order: tuple[str, ...]) -> tuple[dict, list[str]]:
        assignments: dict[str, list[tuple[date, str]]] = {}
        unassigned: list[str] = []
        blocked_until = -1
        for name in order:
            placed = False
            for window in team_windows[name]:
                if slot_index[window[0]] > blocked_until:
                    assignments[name] = window
                    blocked_until = slot_index[window[-1]] + gap
                    placed = True
                    break
            if not placed:
                unassigned.append(name)
        return assignments, unassigned

    best_assignments: dict[str, list] = {}
    best_unassigned:  list[str]       = names[:]
    best_finish = float("inf")

    for perm in permutations(names):
        a, u = try_order(perm)
        finish = max((slot_index[w[-1]] for w in a.values()), default=float("inf"))
        if len(u) < len(best_unassigned) or (
            len(u) == len(best_unassigned) and finish < best_finish
        ):
            best_assignments = a
            best_unassigned  = u
            best_finish      = finish

    return best_assignments, best_unassigned


# ── JSON Output ───────────────────────────────────────────────────────────────

def build_json(
    teams: dict[str, dict],
    assignments: dict[str, list[tuple[date, str]]],
    unassigned: list[str],
    tod_sequence: list[tuple[date, str]],
) -> dict:
    entries = []
    for name, slots in assignments.items():
        route = teams[name]["route"]
        boro, neigh = (route.split("_") + [""])[:2]
        for d, tod in slots:
            entries.append({
                "route":          route,
                "label":          ROUTE_LABELS.get(route, route),
                "boro":           boro,
                "neigh":          neigh,
                "tod":            tod,
                "backpack":       "STUDENT_BAG",
                "collector":      name,
                "date":           str(d),
                "preserved":      True,
                "weather_exempt": True,
            })

    unassigned_entries = [
        {"team": n, "route": teams[n]["route"], "reason": "no_consecutive_window"}
        for n in unassigned
    ]

    return {
        "generated":        str(date.today()),
        "type":             "student_collection",
        "date_range_start": str(tod_sequence[0][0]),
        "date_range_end":   str(tod_sequence[-1][0]),
        "assignments":      entries,
        "unassigned":       unassigned_entries,
    }


# ── HTML Output ───────────────────────────────────────────────────────────────

def build_html(
    teams: dict[str, dict],
    assignments: dict[str, list[tuple[date, str]]],
    unassigned: list[str],
    tod_sequence: list[tuple[date, str]],
) -> str:
    dates      = sorted({d for d, _ in tod_sequence})
    team_names = list(teams.keys())
    color_map  = {n: TEAM_COLORS[i % len(TEAM_COLORS)] for i, n in enumerate(team_names)}

    slot_index = {slot: i for i, slot in enumerate(tod_sequence)}

    # Mark gap slots
    gap_slots: set[tuple[date, str]] = set()
    for slots in assignments.values():
        last_idx = slot_index[slots[-1]]
        for g in range(1, GAP_SLOTS + 1):
            gi = last_idx + g
            if gi < len(tod_sequence):
                gap_slots.add(tod_sequence[gi])

    # ── Table header ──────────────────────────────────────────────────────────
    date_headers = "\n".join(
        f'<th colspan="3" class="date-header">'
        f'{d.strftime("%a")}<br>{d.strftime("%b %d")}</th>'
        for d in dates
    )
    tod_sub = "\n".join(
        f'<th class="tod-header">{tod}</th>'
        for _ in dates for tod in TODS
    )

    # ── Table rows ────────────────────────────────────────────────────────────
    rows_html = ""
    for name in team_names:
        route      = teams[name]["route"]
        label      = ROUTE_LABELS.get(route, route)
        avail_set  = set(teams[name]["available"])
        assigned   = set(assignments.get(name, []))

        row = (
            f'<tr>'
            f'<td class="team-cell">'
            f'<strong>{name}</strong><br>'
            f'<span class="route-tag">{route}</span><br>'
            f'<small>{label}</small>'
            f'</td>'
        )
        for d in dates:
            for tod in TODS:
                slot = (d, tod)
                if slot in assigned:
                    c    = color_map[name]
                    row += (
                        f'<td class="slot assigned" '
                        f'style="background:{c};color:white">{tod}</td>'
                    )
                elif slot in gap_slots:
                    row += '<td class="slot gap">–</td>'
                elif slot in avail_set:
                    row += '<td class="slot available">✓</td>'
                else:
                    row += '<td class="slot unavail"></td>'
        row += "</tr>\n"
        rows_html += row

    # ── Summary table ─────────────────────────────────────────────────────────
    summary_rows = ""
    for name, slots in sorted(assignments.items(), key=lambda kv: kv[1][0]):
        route      = teams[name]["route"]
        color      = color_map[name]
        slot_strs  = ", ".join(f"{d.strftime('%b %d')} {t}" for d, t in slots)
        summary_rows += (
            f'<tr>'
            f'<td><span class="dot" style="background:{color}"></span>'
            f'<strong>{name}</strong></td>'
            f'<td>{route} — {ROUTE_LABELS.get(route, route)}</td>'
            f'<td>{slot_strs}</td>'
            f'<td style="text-align:center">{len(slots)}</td>'
            f'</tr>\n'
        )
    for name in unassigned:
        summary_rows += (
            f'<tr class="unassigned-row">'
            f'<td><strong>{name}</strong></td>'
            f'<td>{teams[name]["route"]}</td>'
            f'<td colspan="2"><em>Could not be scheduled — '
            f'no valid 3-consecutive window found</em></td>'
            f'</tr>\n'
        )

    legend_items = "".join(
        f'<div class="legend-item">'
        f'<div class="legend-box" style="background:{color_map[n]}"></div>{n}'
        f'</div>'
        for n in team_names if n in assignments
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>EFD Student Collection Schedule</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #f0f2f5;
    color: #222;
    margin: 0;
    padding: 24px 20px;
  }}
  h1 {{ font-size: 1.35rem; margin: 0 0 4px; }}
  .subtitle {{ color: #666; font-size: .88rem; margin-bottom: 18px; }}
  .legend {{
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 14px;
    align-items: center;
  }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: .82rem;
  }}
  .legend-box {{
    width: 13px; height: 13px;
    border-radius: 3px;
    flex-shrink: 0;
  }}
  .table-wrap {{ overflow-x: auto; margin-bottom: 36px; }}
  table {{
    border-collapse: collapse;
    white-space: nowrap;
    background: white;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 5px rgba(0,0,0,.12);
  }}
  th, td {{
    border: 1px solid #ddd;
    padding: 5px 7px;
    font-size: .78rem;
    text-align: center;
  }}
  .date-header  {{ background: #2c3e50; color: white; font-size: .76rem; }}
  .tod-header   {{ background: #34495e; color: #ccc;  font-size: .7rem; padding: 3px 5px; }}
  .team-cell    {{
    text-align: left;
    background: #f9f9f9;
    min-width: 170px;
    padding: 6px 10px;
    font-size: .8rem;
  }}
  .route-tag {{
    display: inline-block;
    background: #e6e6e6;
    border-radius: 3px;
    padding: 1px 5px;
    font-size: .7rem;
    font-family: monospace;
  }}
  .slot {{ min-width: 32px; }}
  .slot.assigned  {{ font-weight: 700; font-size: .76rem; }}
  .slot.gap       {{ background: #ebebeb; color: #aaa; }}
  .slot.available {{ background: #edfaf3; color: #27ae60; }}
  .slot.unavail   {{ background: #fafafa; }}
  h2 {{ font-size: 1.05rem; margin: 28px 0 8px; }}
  .summary-table {{
    border-collapse: collapse;
    background: white;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 5px rgba(0,0,0,.12);
  }}
  .summary-table th {{
    background: #2c3e50;
    color: white;
    padding: 8px 14px;
    text-align: left;
    font-size: .82rem;
  }}
  .summary-table td {{
    padding: 8px 14px;
    border-bottom: 1px solid #eee;
    font-size: .83rem;
  }}
  .unassigned-row td {{ color: #c0392b; background: #fdf2f2; }}
  .dot {{
    display: inline-block;
    width: 9px; height: 9px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
  }}
  .note {{ font-size: .76rem; color: #999; margin-top: 10px; }}
</style>
</head>
<body>
<h1>EFD Student Collection Schedule</h1>
<p class="subtitle">
  {dates[0].strftime('%b %d')} – {dates[-1].strftime('%b %d, %Y')}
  &nbsp;·&nbsp; 1 shared bag
  &nbsp;·&nbsp; {MIN_SESSIONS}-slot minimum per team
  &nbsp;·&nbsp; {GAP_SLOTS}-slot buffer between teams
</p>

<div class="legend">
  {legend_items}
  <div class="legend-item">
    <div class="legend-box" style="background:#edfaf3;border:1px solid #27ae60"></div>
    Available (unassigned)
  </div>
  <div class="legend-item">
    <div class="legend-box" style="background:#ebebeb;border:1px solid #ccc"></div>
    Buffer gap
  </div>
</div>

<div class="table-wrap">
<table>
<thead>
  <tr>
    <th rowspan="2" style="background:#1a252f;color:white;min-width:170px">Team</th>
    {date_headers}
  </tr>
  <tr>
    {tod_sub}
  </tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>

<h2>Schedule Summary</h2>
<table class="summary-table">
<thead>
  <tr>
    <th>Team</th>
    <th>Route</th>
    <th>Assigned Slots</th>
    <th># Sessions</th>
  </tr>
</thead>
<tbody>
{summary_rows}
</tbody>
</table>

<p class="note">
  Generated {date.today().strftime('%Y-%m-%d')} by student_scheduler.py
  &nbsp;·&nbsp;
  Slots are marked <code>weather_exempt: true</code> and will be
  preserved by the main walk scheduler regardless of forecast.
</p>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("cp"):
        sys.stdout.reconfigure(encoding="utf-8")

    csv_path = EFD_FORM_CSV

    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found")
        raise SystemExit(1)

    print("Parsing Google Form responses ...")
    teams = parse_google_form(csv_path)
    for name, info in teams.items():
        print(f"  {name:<30s}  route={info['route']:<8s}  "
              f"{len(info['available'])} available slots")

    all_dates  = sorted({d for info in teams.values() for d, _ in info["available"]})
    start_date = all_dates[0]
    end_date   = all_dates[-1]
    print(f"\nDate range: {start_date} -> {end_date}")

    tod_sequence = build_tod_sequence(start_date, end_date)
    print(f"  {len(tod_sequence)} total TOD slots in window")

    print(f"\nScheduling (trying all {len(teams)}! team orderings) ...")
    assignments, unassigned = schedule_teams(teams, tod_sequence)

    print()
    for name, slots in sorted(assignments.items(), key=lambda kv: kv[1][0]):
        slot_strs = " -> ".join(f"{d.strftime('%b %d')} {t}" for d, t in slots)
        print(f"  OK  {name:<30s}: {slot_strs}")
    for name in unassigned:
        print(f"  !!  {name:<30s}: could not be scheduled (no valid window)")

    # ── Write JSON ────────────────────────────────────────────────────────────
    STUDENT_SCHEDULE_JSON.parent.mkdir(parents=True, exist_ok=True)
    data = build_json(teams, assignments, unassigned, tod_sequence)
    with open(STUDENT_SCHEDULE_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"\nJSON  -> {STUDENT_SCHEDULE_JSON}")

    # ── Write HTML ────────────────────────────────────────────────────────────
    STUDENT_SCHEDULE_HTML.parent.mkdir(parents=True, exist_ok=True)
    html = build_html(teams, assignments, unassigned, tod_sequence)
    with open(STUDENT_SCHEDULE_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML  -> {STUDENT_SCHEDULE_HTML}")

    if unassigned:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
