# Static Showcase Site: Plan

Status (2026-10-06): steps 2 to 4 are built. Demo mode is in `build_dashboard.py`
(`DASHBOARD_DEMO=1`), and `scripts/showcase/package_static_app.py` produces
`enaact-dashboard.zip`, a folder whose `index.html` works by double-click or on any static
host. It uses today's code with every feature on, pseudonyms, activity-centre pins and no
logos. Nothing is deployed yet; hosting (steps 5 and 6) is still open.

Goal: keep a free, always-available copy of the dashboard at its most complete,
filled with clearly labeled made-up data, so the project can be shown as a
portfolio piece after the campaign ended.

---

## TL;DR

- **What:** a static copy of the dashboard (every tab, overlay and modal working),
  the standalone pages, and a short "how I approached this" landing page.
- **How:** reuse the existing Python builders. Add a **demo mode** to
  `pipelines/dashboard/build_dashboard.py` that (1) pins the clock to
  Wed May 6 2026, (2) answers the dashboard's server calls inside the browser,
  and (3) strips private information. Publish the output folder to a static host.
- **Map:** CARTO's dark basemap, exactly as in the original. Tiles load straight
  from CARTO in each visitor's browser.
- **Cost:** $0. There is no server, so nothing runs (or bills) while nobody is looking.
- **Decisions needed:** see [section 12](#12-decisions-needed).

---

## 1. What we are recreating

The screenshots taken from commit `34f4319` (May 5 2026, the last commit before the
mid-May changes) show these pages and views. The screenshots were shared directly and
are not committed: this repo is public, and they pair real names with invented
numbers and show collectors' home-area pins.

| URL | What it is | Built by | Live in production on May 5? |
|---|---|---|---|
| `/` | Redirect to `/dashboard.html` | `app/server/serve.py` | yes |
| `/dashboard.html` | Single-page dashboard: **Map** (route completion, route detail panel, filters, Route Groups overlay, Collector Areas overlay), **Collectors** (tiles, per-collector charts, side-by-side comparison), **Calendar** (weekly walk calendar, weather NO GO overlay, claim modal, backpack holder status, calibration bars), **Availability** (heatmaps), plus header tools: Admin Login, Rebuild, Reminders, Upload Data | `pipelines/dashboard/build_dashboard.py` | yes |
| `/collector_map.html` | Collector pins with a detail sidebar | `pipelines/_retired/maps/build_collector_map.py` | **no**, the build crashed (see below) |
| `/availability_heatmap.html` | Standalone availability grid | `pipelines/dashboard/build_availability_heatmap.py` | only if run by hand |
| `/student_schedule.html` | EFD student bag-passing schedule | `pipelines/students/student_scheduler.py` | only if run by hand |
| `/schedule_map.html` | Old algorithm's route map | `pipelines/_retired/scheduling/walk_scheduler.py` | no (needed the Claude API and private PDFs) |

Two April-only pages that were deleted in the Apr 23 reorganization are worth
bringing back for the portfolio, because they show the thinking behind the system:
`algorithm_diagram.html` (scheduler flowchart) and `architecture_map.html`
(interactive system map). Both are recoverable from commit `30f3762`.

Things found while rebuilding:

- `collector_map.html` was broken on May 5 for two reasons. The script moved one
  folder deeper on May 2 and stopped finding `shared/`. EFD was added on May 5 with
  no pin color (`KeyError: 'EFD'`). It also only counts walk-log lines whose bag is
  `X` (the pre-April log format). The screenshots used the May 1 version of the
  script fed an `X`-tagged copy of the log.
- The route panel heading says "TOD vs Target (8)" but the code's target is 6.
- EFD student walks use the shared student bag, which the calendar can only label
  "Legacy X".
- At widths below about 2100 px the calendar's calibration-bar strip scrolls sideways.

## 2. Why static fits "quiet until someone opens it"

Industry terms in **bold**.

- The production site was a **server** on Google **Cloud Run**: a program that
  waits for visitors, rebuilds pages and saves changes. A server costs money while
  it runs, and this one was configured to always keep one copy running
  (`--min-instances=1`).
- Streamlit Community Cloud handles idle time by putting the app to sleep and waking
  it on the next visit. That is called **scale-to-zero**, and the wake-up delay is a
  **cold start**.
- A **static site** goes one step further: it is just files (HTML, JSON, images)
  copied to a **CDN** (content delivery network), a worldwide network of file
  servers run by the host. Nothing of yours runs, so there is nothing to sleep or
  wake. Pages load instantly and hosting is free at this size.

This dashboard is already almost static: `build_dashboard.py` bakes the routes, walk
log, schedule, weather and availability into one self-contained `dashboard.html`.
The only live parts are calls to the server's `/api/...` endpoints, handled in
section 3.

## 3. What breaks without the server, and the fix

On a static host every `/api/...` call returns "404 Not Found". The dashboard
already degrades gracefully in a few places; the login code even contains a
"server unreachable, allow PIN-less mode for static viewing" branch. Plan per feature:

| Feature | Server call | Without a fix | Demo-mode fix |
|---|---|---|---|
| Map, filters, route panel, Collectors, Availability | none (baked data) plus static files `Walks_Log.txt`, `weather.json`, `schedule_output.json`, `Recal_Log.txt` | works if those files are published next to the HTML | publish the files |
| "Drive: Xm ago" badge | `GET /api/status` every 30 s | badge stays blank | fake "last poll 4 m ago" |
| Admin Login | `POST /api/confirm` | unlocks anyway (built-in static fallback) | accept any PIN; banner says "demo: any PIN works" |
| Claim / unclaim / edit / remove a slot | `POST /api/schedule/claim`, `/unclaim`, `PATCH`/`DELETE /api/schedule/assignments/{id}` | error message | update the schedule in browser memory, reusing the server's rules (one walk per bag per slot, no double-booked collector, no past dates) |
| Backpack holder/location | `POST /api/backpack-status` | error | update in memory |
| Log calibration | `POST /api/record-calibration` | error | add the date in memory; the "days since cal" bar updates |
| Reminders preview / send | `POST /api/notifications/preview`, `/send` | error | compute tomorrow's reminders from the in-memory schedule; "send" shows "Demo: no emails sent" |
| Rebuild | `POST /api/force-rebuild` | error | simulated success toast |
| Drive sync | `POST /api/drive/poll` | error | "no new files" |
| Upload walk data | `POST /api/upload-walk` | error | **never upload files**; optionally add the walk to the in-memory log so the map updates |

The in-browser stand-in for the server is called a **mock API** or **shim**: a small
script that intercepts `fetch('/api/...')` and answers it locally. Each visitor gets
their own sandbox. Changes vanish on reload, so the demo always starts from the same
state.

## 4. Which code to build from

`main` today (`9527fac`) still contains every feature. The July 27 commit only hid
seven features with CSS switches in `shared/dashboard_features.py`
(calendar, backpack status, calibration logs, route groups, availability, reminders,
data upload).

| | **A. Today's `main` with every flag on** (recommended) | **B. Frozen May 5 snapshot** (`34f4319`) |
|---|---|---|
| Features | Everything from May 5, **plus** a Route Details card (length and walking time), a rolling 7-day calendar starting yesterday, auto-refreshed backpack holder, bigger tab buttons | Exactly the early-May look (what the screenshots show) |
| Routes | 19 (East Elmhurst was dropped from the campaign on May 18) | 20 |
| Default map filter | current season (Spring under the pinned date) | all walks |
| Where edits go | `main`'s `build_dashboard.py`, next to the existing flag file | a separate branch cut from `34f4319` |
| Upkeep | one code line | two diverging lines |

Screenshots `C1` and `C2` show option A rendered with the same demo data.
Option A is the most expansive version that ever existed. Choose B only if the exact
May 5 appearance matters more than the extra features.

## 5. Demo data

`scripts/showcase/gen_demo_data.py` writes a complete, deterministic dataset.
Deterministic means the same seed always produces the same data.

| File | Contents | Real or invented |
|---|---|---|
| `Walks_Log.txt` | 159 walks, Feb 28 to May 6, ramping up through April; 14 of 20 routes at target | 8 real lines from git history; EFD walks follow the real student schedule; the rest invented |
| `weather.json` | cloud cover % per day and time of day, Mar 16 to May 12 | Mar 16 to Apr 15 real (from git history), the rest invented |
| `schedule_output.json` | 12 upcoming claims (1 on a bad-weather slot) and backpack B's manual location | invented |
| `Recal_Log.txt` | calibrations; bag A at 15 days ("soon"), bag B at 8 ("good") | Mar 1 and Apr 21 real, the rest invented |
| `notification_preferences.json` | reminder opt-ins with `@example.com` addresses | invented |

Walks only fall on good-weather slots, nobody walks two places at once, and
collectors mostly walk their preferred routes. The script also drops routes the
target checkout no longer knows, so it serves both options in section 4.

For the automated build, commit the real Mar 16 to Apr 15 weather as a small fixture
file. The script currently reads it with `git show 30f3762:weather.json`, which
needs full git history.

## 6. Demo mode in `build_dashboard.py`

Per the project rule, every front-end change goes into
`pipelines/dashboard/build_dashboard.py` (the generator), never into the generated
`dashboard.html`. Turn it on with an environment variable (`DASHBOARD_DEMO=1`) or a
`demo_mode` entry in `shared/dashboard_features.py`. Normal builds stay unchanged.

Pieces, all injected only in demo builds:

1. **Pinned clock.** The archive always opens on Wed May 6 2026, 10:30 AM in the
   visitor's own time zone, and time then ticks normally. Without this, a 2027
   visitor would land on an empty calendar week. Using the visitor's local wall clock
   keeps "today" on May 6 in every time zone. This snippet goes first in `<head>`:

   ```js
   (function () {
     const RealDate = Date;
     const offset = new RealDate(2026, 4, 6, 10, 30).getTime() - RealDate.now();
     function DemoDate(...args) {
       return args.length ? new RealDate(...args) : new RealDate(RealDate.now() + offset);
     }
     DemoDate.prototype = RealDate.prototype;
     DemoDate.now = () => RealDate.now() + offset;
     DemoDate.parse = RealDate.parse;
     DemoDate.UTC = RealDate.UTC;
     window.Date = DemoDate;
   })();
   ```

   Time must keep moving: the chart library animates from `Date.now()`, and a fully
   frozen clock leaves every chart empty. The screenshot session hit exactly this.
2. **Mock API**, per the table in section 3.
3. **Demo banner:** "Archived demo · synthetic data · snapshot of Wed May 6 2026 ·
   not an official NASA site".
4. **Privacy transforms** (section 10): pseudonyms instead of real names, campus pins
   instead of home locations.
5. **Optional:** bundle Leaflet, Chart.js and D3 with the site instead of loading
   them from unpkg/jsdelivr, so the archive keeps working if those CDNs change.

## 7. What gets published

```
site/
├── index.html                 landing page: the story + links (new)
├── dashboard.html             demo build
├── favicon.png
├── Walks_Log.txt  Recal_Log.txt  weather.json  schedule_output.json
├── collector_map.html         fixed or May 1 version, campus pins only
├── availability_heatmap.html
├── student_schedule.html      team names pseudonymized
├── algorithm_diagram.html     restored from 30f3762
├── architecture_map.html      restored from 30f3762
└── 404.html
```

The dashboard reads its data files with relative paths, so the site works under a
sub-path such as `https://<you>.github.io/enaact-dashboard/`. The absolute `/api/...`
calls are caught by the mock API before they reach the network.

Landing page outline (your story; the git history supplies the timeline):

1. The problem: two sensor backpacks, about 10 student collectors, 20 NYC routes,
   cloud-cover limits, collector schedules.
2. First approach (Mar to Apr): an algorithmic scheduler (weather, availability,
   subway travel times, calibration rules). Link the algorithm flowchart.
3. System design: link the architecture map.
4. The pivot (May 1 to 5): replacing the algorithm with self-scheduling (claims,
   conflict rules, reminders), simpler and closer to how people actually worked.
5. Graceful wind-down (Jul 27): features retired behind switches, not deleted.
6. Link: open the live demo.

## 8. Hosting options

| Host | Cost | Notes |
|---|---|---|
| **GitHub Pages** from a repo under your own account (recommended) | free for public repos | Credible `github.io` portfolio URL, deploys from GitHub Actions. A free account can only publish public repos. |
| GitHub Pages from `NASA-ENAACT-ORG/...` | free (the repo is public) | URL sits under the org name, needs an org admin to enable, and reads as an official project site. |
| Cloudflare Pages | free | Works with private repos, unlimited bandwidth on the free plan, preview URL per branch. Best alternative. |
| Netlify / Vercel | free tiers | Similar to Cloudflare Pages. |
| Firebase Hosting | free tier | Same Google account as before; no real advantage for static files. |
| Cloud Run with min-instances=0 / Streamlit | near-free but not zero | Only worth it if a real backend were needed. It is not. |

## 9. Automation

A GitHub Actions workflow builds and publishes on every push to a `showcase` branch,
or on a button press. Automating build and publish this way is **CI/CD**
(continuous integration / continuous deployment).

```yaml
name: Showcase site
on:
  workflow_dispatch:
  push:
    branches: [showcase]
permissions: { contents: read, pages: write, id-token: write }
concurrency: { group: pages, cancel-in-progress: true }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install openpyxl pandas time-machine
      - run: python scripts/showcase/build_static_site.py --out site   # new script
      - uses: actions/upload-pages-artifact@v3
        with: { path: site }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: github-pages, url: '${{ steps.deploy.outputs.page_url }}' }
    steps:
      - id: deploy
        uses: actions/deploy-pages@v4
```

`build_static_site.py` would generate the demo data, run the builders with the clock
pinned, restore the April pages, write `index.html` and `404.html`, and copy
everything into `site/`. Optionally add a **smoke test**, an automatic quick check:
run `scripts/showcase/shots.js` against the built folder and fail the build on
browser errors.

## 10. Before going public: safety and privacy checklist

1. **Turn off the old auto-deploy.** `.github/workflows/gcp-deploy.yml` deploys to
   Cloud Run on every push to `main`. Change its trigger to `workflow_dispatch`
   (manual only) before anything lands on `main`. Otherwise a merge redeploys or
   fails against the retired Google Cloud project.
2. **Check for leftover cloud bills.** If the `enact-walk-dashboard` Cloud Run
   service still exists, `--min-instances=1` (2 CPU / 2 GiB) keeps it billing around
   the clock with zero visitors. Delete it, or set min-instances to 0, in the Google
   Cloud console.
3. **No home locations.** `build_dashboard.py` bakes collector home coordinates
   (`COLLECTOR_HOMES`) into the page even when the Collector Areas layer is hidden.
   The admin login only hides the button; anyone can read the page source. Demo
   builds should ship campus pins (CCNY, LaGCC) instead. The same applies to
   `collector_map.html`.
4. **Pseudonyms.** The demo's walk counts are invented. Pairing invented numbers with
   real students' and professors' names on a public page is misleading. Swap display
   names (`shared/registry.py`) and EFD team names for pseudonyms unless people agree
   to be named.
5. **Label the data** as synthetic (banner and landing page).
6. **Branding.** NASA's insignia and logotype ("worm") have usage restrictions, and
   the page must not look like an official NASA site. Check NASA's media-usage
   guidelines; the simplest safe route is to drop the NASA and TEMPO logos in demo
   mode and keep a plain-text credit ("built for the NASA EnAACT campaign at CCNY
   and LaGCC").
7. **Already public:** the repo has published `data/inputs/routes/kml/Collector_Locs.kml`
   (exact coordinates for 11 people) since Apr 23. That is a separate cleanup and
   needs a decision from the repo owners.

## 11. Milestones

| # | Step | Size |
|---|---|---|
| 0 | Decisions in section 12 | you |
| 1 | Safety: disable `gcp-deploy.yml` auto-deploy; create the `showcase` branch (or a personal repo) | small |
| 2 | Move the demo data generator into the build; add the weather fixture | small |
| 3 | Demo mode in `build_dashboard.py`: clock pin, mock API, banner, privacy transforms | main work, about one session |
| 4 | `build_static_site.py`: April pages, collector map fix, landing page, 404 | about half a session |
| 5 | Pages workflow and smoke test | small |
| 6 | Publish, check on phone and laptop, share the link | small |

## 12. Decisions needed

1. **Build base:** today's `main` with all flags on (recommended) or the frozen May 5
   snapshot?
2. **Host and owner:** your personal GitHub Pages (recommended), the
   `NASA-ENAACT-ORG` org's Pages, or Cloudflare Pages?
3. **Privacy level:** pseudonyms for everyone (recommended), or real names with the
   people's consent?
4. **Logos:** remove the NASA and TEMPO logos in the demo (recommended) or get
   permission to keep them?

## 13. Cost and upkeep

- Hosting: $0. Domain: optional (about $10 to $15 a year).
- Map tiles load from CARTO in each visitor's browser. Their basemaps are free for
  light non-commercial use with attribution (already shown in the corner); confirm
  the current terms before launch.
- Upkeep: none. No server, secrets or database. The only things that could break are
  third-party CDNs (bundling the libraries removes that) and the tile provider.

## Appendix: how the screenshots were made

Run from a scratch copy of commit `34f4319` (`git worktree add`); the scripts are in
`scripts/showcase/`:

1. `gen_demo_data.py <checkout>` writes the demo dataset into that checkout.
2. `run_frozen.py 2026-05-06T14:30:00 <script.py>` runs each builder and the
   original `serve.py` with the clock moved to May 6 2026 10:30 ET (using the
   `time-machine` package; `freezegun` crashed Python 3.13).
3. `shots.js` drives Chromium through every view, with the browser clock started at
   the same moment.

In that sandbox the network blocked the map-tile and library CDNs. Leaflet, Chart.js
and D3 were installed from the npm registry, the Space Grotesk font was fetched once,
and the subway lines from the repo's GTFS data were drawn as a stand-in for the map
tiles (`STANDIN_BASEMAP=1`). A real visitor's browser loads the normal tiles. With
`*.basemaps.cartocdn.com` allowed, `shots.js` uses the real CARTO tiles by default.
