# Showcase tooling

Scripts that turn the retired dashboard into a standalone static app (a zip with an
`index.html` you can double-click) filled with made-up demo data, plus the tools used to
screenshot the May 2026 version. Background: `docs/showcase/STATIC_SITE_PLAN.md`.

| Script | What it does |
|---|---|
| `package_static_app.py --vendor DIR [--out dist]` | Builds `enaact-dashboard/` and `enaact-dashboard.zip`: demo data, the demo-mode dashboard as `index.html`, the extras pages and a README. Runs in a temporary copy of the repo. `--real-names` and `--keep-logos` switch off the pseudonyms and the logo swap. |
| `smoke_test_app.js <app-dir> [shots-dir]` | Opens the packaged app from disk (or `APP_URL=...` for a hosted copy) with the real clock and exercises every feature: claims, conflicts, backpack status, calibration, reminders, upload, rebuild, extras. Exits non-zero on any failure. |
| `gen_demo_data.py <checkout>` | Writes a deterministic demo dataset (walk log, weather, schedule claims, calibrations, reminder opt-ins) into a checkout. It keeps the real values git history had (`fixtures/`) and drops routes that checkout's `shared/registry.py` doesn't know. |
| `run_frozen.py <utc-time> <script.py> [args]` | Runs a pipeline script or `serve.py` with the clock moved to a fixed moment (uses `time-machine`; `freezegun` crashes Python 3.13 with openpyxl). |
| `shots.js <workdir> <outdir> [only-regex]` | Playwright tour of every page of the original server-backed build. |
| `build_subway_geojson.py <shapes.txt> <out.geojson>` | Stand-in basemap from the GTFS subway shapes, for networks that block CARTO tiles. |

Demo mode itself lives in `pipelines/dashboard/build_dashboard.py` (`DASHBOARD_DEMO=1`).
It turns on every feature, pins the clock to Wed May 6 2026, answers the dashboard's
server calls inside the browser, swaps names for pseudonyms and home locations for
walk-activity centres. Builds without the variable are byte-for-byte unchanged.

## Build the zip

```bash
pip install openpyxl pandas time-machine      # time-machine is optional
V=$(mktemp -d) && (cd "$V" \
  && npm pack leaflet@1.9.4 chart.js@4.4.0 d3@7.9.0 \
  && for f in *.tgz; do mkdir -p "${f%.tgz}" && tar -xzf "$f" -C "${f%.tgz}"; done \
  && mkdir fonts \
  && curl -sS -A "Mozilla/5.0 Chrome/140" -o fonts/space-grotesk.css \
     "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&display=swap" \
  && grep -oE "https://fonts.gstatic.com/[^)]+" fonts/space-grotesk.css | sort -u \
     | while read u; do curl -sS "$u" -o "fonts/$(basename "$u")"; done)
python scripts/showcase/package_static_app.py --vendor "$V" --out dist
NODE_PATH=$(npm root -g) node scripts/showcase/smoke_test_app.js dist/enaact-dashboard
```

The libraries and font are copied into `index.html`, so the app needs no outside
scripts; only the CARTO map tiles load from the internet.

## Reproduce the May 5 screenshots

```bash
git worktree add --detach /tmp/may5 34f4319          # May 5 2026 snapshot
python scripts/showcase/gen_demo_data.py /tmp/may5
cd /tmp/may5
T=2026-05-06T14:30:00                               # Wed May 6, 10:30 AM New York
python "$OLDPWD/scripts/showcase/run_frozen.py" $T pipelines/dashboard/build_dashboard.py
python "$OLDPWD/scripts/showcase/run_frozen.py" $T pipelines/dashboard/build_availability_heatmap.py
python "$OLDPWD/scripts/showcase/run_frozen.py" $T pipelines/students/student_scheduler.py
PORT=8765 DRIVE_POLL_INTERVAL=0 python "$OLDPWD/scripts/showcase/run_frozen.py" $T app/server/serve.py &
cd "$OLDPWD"
NODE_PATH=$(npm root -g) node scripts/showcase/shots.js /tmp/showcase-work /tmp/shots
```

Notes:

- `collector_map.html` does not build on the May 5 commit (bugs listed in the plan).
  Build it from commit `8c71a62` (May 1) with an `X`-tagged copy of the walk log, and
  copy it into the May 5 site folder.
- The April bonus pages come from `git show 30f3762:algorithm_diagram.html` and
  `git show 30f3762:architecture_map.html`; put them in `<workdir>/april_pages/`.
- Map tiles load from CARTO (`*.basemaps.cartocdn.com`). If the network blocks
  them, set `STANDIN_BASEMAP=1` and provide `<workdir>/vendor/subway.geojson` and
  `<workdir>/vendor/transparent.png`.
- If unpkg, jsdelivr, d3js.org or Google Fonts are blocked, put local copies in
  `<workdir>/vendor/` (layout described at the top of `shots.js`); missing copies
  are fetched from the network.
