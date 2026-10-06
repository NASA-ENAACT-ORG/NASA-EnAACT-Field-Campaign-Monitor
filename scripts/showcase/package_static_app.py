#!/usr/bin/env python3
"""Package the dashboard as a standalone static app (a folder plus a .zip).

Usage:
  python scripts/showcase/package_static_app.py --vendor DIR [--out dist] [--real-names] [--keep-logos]

Output (<out>/enaact-dashboard/ and <out>/enaact-dashboard.zip):
  index.html                        the dashboard: double-click to open; works offline except map tiles
  extras/algorithm-flowchart.html   April 2026 scheduler flowchart
  extras/architecture-map.html      April 2026 system map
  extras/student-schedule.html      EFD student team schedule
  README.txt

The build runs in a temporary copy of the repository, so the working tree is never
touched. DIR holds npm copies of leaflet 1.9.4, chart.js 4.4.0 and d3 7.9.0 plus the
Space Grotesk font (layout in scripts/showcase/README.md).
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
APP_NAME = "enaact-dashboard"
APRIL_COMMIT = "30f3762"           # last commit that still had the April design pages
PINNED_UTC = "2026-05-06T14:30:00"  # Wed May 6 2026, 10:30 AM New York
SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist"}

README = """NASA EnAACT Field Campaign Data Desk: archived demo
====================================================

Open index.html in any web browser (double-click it). That's it.

- Everything runs inside your browser. There is no server and nothing is sent anywhere.
- Internet is only needed for the map background (CARTO map tiles). Offline, the
  routes still draw on a dark background.
- The data is made up: walks, claims, calibrations and most weather values.
  {names_line}
- The clock is pinned to Wed May 6, 2026, 10:30 AM, so the calendar opens mid-campaign.
  Changes you make (claims, calibration logs, uploads) reset when you reload.
- Admin Login accepts any PIN.

Extras (also linked from "About" at the bottom of the dashboard):
  extras/algorithm-flowchart.html   the original scheduling algorithm (April 2026)
  extras/architecture-map.html      how the system's parts connected (April 2026)
  extras/student-schedule.html      the student team schedule{team_note}

To put it online, upload this folder to any static host (GitHub Pages, Netlify Drop,
Cloudflare Pages). No build step is needed.

Student project archive. Not an official NASA website.
"""

BACK_LINK = (
    '<a href="../index.html" style="position:fixed;top:12px;right:12px;z-index:99999;'
    "padding:6px 12px;border-radius:999px;background:rgba(13,17,23,.88);color:#e6edf3;"
    "border:1px solid #30363d;font:600 12px system-ui,sans-serif;text-decoration:none\">"
    "&larr; Dashboard</a>\n"
)


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")


def copy_repo(dst: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {n for n in names if n in SKIP}
        if Path(directory).resolve() == (REPO / "data").resolve():
            skipped.add("outputs")
        return skipped
    shutil.copytree(REPO, dst, ignore=ignore)


def pseudonymize_teams(csv_path: Path) -> None:
    """Replace student team names (first column) with Team 1, Team 2, ..."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for i, row in enumerate(rows[1:], start=1):
        if row:
            row[0] = f"Team {i}"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def add_back_link(html: str) -> str:
    end = html.rfind("</body>")
    return html[:end] + BACK_LINK + html[end:] if end >= 0 else html + BACK_LINK


def april_page(name: str) -> str | None:
    result = subprocess.run(["git", "-C", str(REPO), "show", f"{APRIL_COMMIT}:{name}"],
                            capture_output=True, text=True)
    if result.returncode:
        print(f"warning: {name} not found in git history (shallow clone?); skipping it")
        return None
    return result.stdout


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendor", required=True, type=Path, help="local copies of the CDN libraries and font")
    ap.add_argument("--out", default="dist", type=Path, help="output folder (default: dist)")
    ap.add_argument("--real-names", action="store_true", help="keep real collector and team names")
    ap.add_argument("--keep-logos", action="store_true", help="keep the NASA and TEMPO logos")
    args = ap.parse_args()
    vendor = args.vendor.resolve()
    out = args.out.resolve()

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        copy_repo(work)
        run([sys.executable, str(HERE / "gen_demo_data.py"), str(work)], cwd=work)
        if not args.real_names:
            pseudonymize_teams(work / "data" / "inputs" / "students" / "EFD_Google_form.csv")

        env = {k: v for k, v in os.environ.items() if k != "GCS_BUCKET"}
        env.update(DASHBOARD_DEMO="1", DASHBOARD_DEMO_VENDOR=str(vendor))
        if args.real_names:
            env["DASHBOARD_DEMO_REAL_NAMES"] = "1"
        if args.keep_logos:
            env["DASHBOARD_DEMO_KEEP_LOGOS"] = "1"
        run([sys.executable, "pipelines/dashboard/build_dashboard.py"], cwd=work, env=env)

        # The schedule page stamps "Generated <today>"; pin it when time-machine is installed.
        scheduler = ["pipelines/students/student_scheduler.py"]
        if importlib.util.find_spec("time_machine"):
            scheduler = [str(HERE / "run_frozen.py"), PINNED_UTC] + scheduler
        run([sys.executable] + scheduler, cwd=work, env=env)

        site = work / "data" / "outputs" / "site"
        app = out / APP_NAME
        if app.exists():
            shutil.rmtree(app)
        (app / "extras").mkdir(parents=True)
        shutil.copy(site / "dashboard.html", app / "index.html")
        (app / "extras" / "student-schedule.html").write_text(
            add_back_link((site / "student_schedule.html").read_text(encoding="utf-8")), encoding="utf-8")

    flowchart = april_page("algorithm_diagram.html")
    if flowchart:
        (app / "extras" / "algorithm-flowchart.html").write_text(add_back_link(flowchart), encoding="utf-8")
    arch = april_page("architecture_map.html")
    if arch:
        d3 = (vendor / "d3-7.9.0" / "package" / "dist" / "d3.min.js").read_text(encoding="utf-8")
        arch = arch.replace('<script src="https://d3js.org/d3.v7.min.js"></script>',
                            "<script>" + d3.replace("</script", "<\\/script") + "</script>", 1)
        (app / "extras" / "architecture-map.html").write_text(add_back_link(arch), encoding="utf-8")

    names_line = ("Map pins show walk-activity centres, not homes." if args.real_names else
                  "Collector names are pseudonyms, and map pins show walk-activity centres, not homes.")
    team_note = "" if args.real_names else " (team names replaced)"
    (app / "README.txt").write_text(README.format(names_line=names_line, team_note=team_note), encoding="utf-8")

    archive = out / f"{APP_NAME}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(app.rglob("*")):
            zf.write(path, path.relative_to(out))
    print(f"app:     {app}")
    print(f"zip:     {archive} ({archive.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
