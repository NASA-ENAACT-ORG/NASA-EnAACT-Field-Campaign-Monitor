# Showcase tooling

Prototype scripts used to rebuild the May 2026 dashboard with made-up demo data and
screenshot every page. See `docs/showcase/STATIC_SITE_PLAN.md` for how they feed the
static showcase site.

| Script | What it does |
|---|---|
| `gen_demo_data.py <checkout>` | Writes a deterministic demo dataset (walk log, weather, schedule claims, calibrations, reminder opt-ins) into a checkout. It keeps the real values git history has and drops routes that checkout's `shared/registry.py` doesn't know. |
| `run_frozen.py <utc-time> <script.py> [args]` | Runs a pipeline script or `serve.py` with the clock moved to a fixed moment (uses `time-machine`; `freezegun` crashes Python 3.13 with openpyxl). |
| `shots.js <workdir> <outdir> [only-regex]` | Playwright tour of every dashboard view, modal and standalone page. |
| `build_subway_geojson.py <shapes.txt> <out.geojson>` | Stand-in basemap from the GTFS subway shapes, for networks that block CARTO tiles. |

## Reproduce the screenshots

```bash
pip install openpyxl pandas time-machine
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
