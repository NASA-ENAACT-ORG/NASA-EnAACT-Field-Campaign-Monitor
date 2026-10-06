// Screenshot every page/view of the May 5 2026 dashboard build.
// Usage: NODE_PATH=$(npm root -g) node shots.js <workdir> <outdir> [only-regex]
//
// <workdir>/vendor/  optional local copies of CDN assets (leaflet-1.9.4/, chart.js-4.4.0/,
//                    d3-7.9.0/ from `npm pack`, fonts/space-grotesk.css + .woff2). Missing
//                    copies are fetched from the network instead.
// <workdir>/april_pages/  algorithm_diagram.html and architecture_map.html from commit 30f3762.
// STANDIN_BASEMAP=1  for networks that block CARTO: transparent tiles plus the subway network
//                    (vendor/subway.geojson, vendor/transparent.png) drawn as a stand-in.
// Expects the dashboard server at BASE (default http://127.0.0.1:8765).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const S = process.argv[2];
const OUT = process.argv[3];
const ONLY = process.argv[4] ? new RegExp(process.argv[4]) : null;
const BASE = process.env.BASE || 'http://127.0.0.1:8765';
const STANDIN = process.env.STANDIN_BASEMAP === '1';
const V = path.join(S, 'vendor');
const FIXED = new Date('2026-05-06T10:30:00-04:00');
const SUBWAY = STANDIN ? JSON.parse(fs.readFileSync(path.join(V, 'subway.geojson'), 'utf8')) : null;
const PNG_1PX = STANDIN ? fs.readFileSync(path.join(V, 'transparent.png')) : null; // fully transparent tile
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = [];

async function setupRoutes(context) {
  const local = (route, file, contentType) =>
    fs.existsSync(file) ? route.fulfill({ path: file, contentType }) : route.continue();
  // Google Fonts: served from a local copy fetched with curl (the browser runs without the proxy).
  await context.route(/fonts\.googleapis\.com\/css2/, (route) =>
    local(route, path.join(V, 'fonts/space-grotesk.css'), 'text/css'));
  await context.route(/fonts\.gstatic\.com\/.+\/([^/?]+\.woff2)/, (route) =>
    local(route, path.join(V, 'fonts', route.request().url().match(/([^/?]+\.woff2)/)[1]), 'font/woff2'));
  if (STANDIN) {
    await context.route(/basemaps\.cartocdn\.com/, (route) => route.fulfill({ body: PNG_1PX, contentType: 'image/png' }));
  }
  await context.route(/unpkg\.com\/leaflet@1\.9\.4\/dist\/([^?]+)/, (route) => {
    const rel = route.request().url().match(/dist\/([^?]+)/)[1];
    const ct = rel.endsWith('.css') ? 'text/css' : rel.endsWith('.js') ? 'application/javascript' : 'image/png';
    return local(route, path.join(V, 'leaflet-1.9.4/package/dist', rel), ct);
  });
  await context.route(/cdn\.jsdelivr\.net\/npm\/chart\.js@4\.4\.0\//, (route) =>
    local(route, path.join(V, 'chart.js-4.4.0/package/dist/chart.umd.js'), 'application/javascript'));
  await context.route(/d3js\.org\/d3\.v7(\.min)?\.js/, (route) =>
    local(route, path.join(V, 'd3-7.9.0/package/dist/d3.min.js'), 'application/javascript'));
}

// Stand-in basemap: CARTO tiles are blocked in this sandbox, so draw the real
// subway network (from the repo's GTFS data) faintly beneath the app's layers.
async function injectStandin(page) {
  if (!STANDIN) return 'off (real CARTO tiles)';
  return page.evaluate((geo) => {
    let m = null;
    try { m = map; } catch (e) { return false; } // eslint-disable-line no-undef
    if (!m || !window.L) return false;
    const pane = m.getPane('standin') || m.createPane('standin');
    pane.style.zIndex = 250;
    pane.style.pointerEvents = 'none';
    L.geoJSON(geo, { pane: 'standin', interactive: false, style: { color: '#3b424c', weight: 1.3, opacity: 0.9 } }).addTo(m);
    return true;
  }, SUBWAY);
}

let page;
async function shot(name, opts = {}) {
  if (ONLY && !ONLY.test(name)) return;
  await sleep(opts.wait ?? 700);
  const file = path.join(OUT, name + '.png');
  if (opts.selector) await page.locator(opts.selector).screenshot({ path: file });
  else await page.screenshot({ path: file, fullPage: !!opts.fullPage });
  console.log('saved', name);
}
const click = (sel) => page.locator(sel).first().click();
const tab = (view) => click(`.tab-btn[data-view="${view}"]`);

(async () => {
  // Playwright forces loopback through a configured proxy unless this is set; the local
  // dashboard server must stay direct while CDN and tile requests use the proxy.
  process.env.PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK = '1';
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY, bypass: '127.0.0.1,localhost' } : undefined;
  const browser = await chromium.launch(proxy ? { proxy } : {});
  const failed = [];
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 2,
    timezoneId: 'America/New_York',
    locale: 'en-US',
  });
  // Start the page clock at May 6 10:30 and let it tick, so animations (Chart.js) complete.
  await context.clock.install({ time: FIXED });
  await context.clock.resume();
  await setupRoutes(context);
  page = await context.newPage();
  page.on('pageerror', (e) => log.push('pageerror ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') log.push('console ' + m.text().slice(0, 160)); });
  page.on('requestfailed', (r) => failed.push(r.url().replace(/\?.*/, '').slice(0, 90) + ' ' + ((r.failure() || {}).errorText || '')));

  // ── Dashboard: Campaign Monitor ─────────────────────────────────────────
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => { const l = document.getElementById('loading'); return l && getComputedStyle(l).display === 'none'; }, null, { timeout: 20000 });
  console.log('standin dashboard:', await injectStandin(page));
  await shot('01_map_overview', { wait: 3600 }); // let the load toast fade

  await page.evaluate(() => openPanel('QN_LA')); // eslint-disable-line no-undef
  await shot('02_map_route_panel', { wait: 1400 });
  await page.evaluate(() => closePanel()); // eslint-disable-line no-undef

  await click('#filters-btn');
  await shot('03_map_filters');
  await click('#filters-btn');

  await click('#route-groups-btn');
  await shot('04_map_route_groups');
  await click('#route-groups-btn');

  await click('#sched-unlock-btn');
  await page.locator('#auth-who').selectOption({ index: 1 }).catch(() => {});
  await shot('05_admin_login_modal');
  await click('#auth-modal-submit');
  await sleep(800);

  await click('#collector-homes-btn');
  await shot('06_map_collector_areas_admin', { wait: 900 });
  await click('#collector-homes-btn');

  await tab('collector-view');
  await sleep(1500);
  const fullH = await page.evaluate(() => document.getElementById('collector-view').scrollHeight + 62);
  await page.setViewportSize({ width: 1600, height: Math.max(1000, fullH) });
  await shot('07_collectors_full', { wait: 1500 });
  await page.setViewportSize({ width: 1600, height: 1000 });

  // ── Dashboard: Scheduling ───────────────────────────────────────────────
  await tab('calendar-view');
  await shot('08_calendar_this_week', { wait: 1200 });
  await page.evaluate(() => { const n = document.getElementById('cal-nav'); n.scrollLeft = n.scrollWidth; });
  await shot('08b_calendar_nav_scrolled_right', { selector: '#cal-nav', wait: 400 });
  await page.evaluate(() => { document.getElementById('cal-nav').scrollLeft = 0; });

  await click('#cal-next');
  await shot('09_calendar_next_week');
  await click('#cal-prev');

  for (let i = 0; i < 4; i++) { await click('#cal-prev'); await sleep(150); }
  await shot('10_calendar_past_week_apr5');
  for (let i = 0; i < 4; i++) { await click('#cal-next'); await sleep(150); }

  await click('.cal-cell[data-date="2026-05-07"][data-tod="AM"]');
  await shot('11_calendar_claim_modal', { wait: 900 });
  await click('#slot-sched-close').catch(() => page.keyboard.press('Escape'));
  await sleep(300);

  await click('.bp-status-button[data-bp="B"]');
  await shot('12a_backpack_status_confirm');
  await click('#bp-status-yes');
  await shot('12b_backpack_status_edit');
  await click('#bp-status-cancel').catch(() => {});
  await sleep(300);

  await click('.cb-log-btn[data-bp="A"]');
  await shot('13_log_calibration_modal');
  await click('#recal-modal-cancel').catch(() => {});
  await sleep(300);

  await tab('availability-view');
  await shot('14_availability', { wait: 1000 });
  await page.locator('#avail-tbl-a tr').nth(2).locator('td').nth(4).hover();
  await shot('14b_availability_hover_tooltip', { wait: 500 });
  await page.mouse.move(5, 500);

  // ── Dashboard: header tools ─────────────────────────────────────────────
  await click('#notify-btn');
  await shot('15_reminders_modal', { wait: 1500 });
  await click('#notify-modal-close').catch(() => {});
  await sleep(300);

  await click('.upload-data-btn');
  await shot('16_upload_data_modal', { wait: 900 });

  // ── Standalone pages ────────────────────────────────────────────────────
  await page.goto(BASE + '/collector_map.html', { waitUntil: 'networkidle' });
  console.log('standin collector map:', await injectStandin(page));
  await shot('17_collector_map', { wait: 1500 });
  await page.locator('.leg-row').first().click();
  await shot('17b_collector_map_detail_sidebar', { wait: 1200 });

  await page.goto(BASE + '/availability_heatmap.html', { waitUntil: 'networkidle' });
  await shot('18_availability_heatmap_page', { fullPage: true });

  await page.goto(BASE + '/student_schedule.html', { waitUntil: 'networkidle' });
  await shot('19_student_schedule_page', { fullPage: true });

  // ── Bonus: April-only pages (removed in the Apr 23 reorg) ───────────────
  await page.goto('file://' + path.join(S, 'april_pages/algorithm_diagram.html'), { waitUntil: 'networkidle' });
  await shot('20_bonus_april_algorithm_flowchart', { wait: 1200 });
  for (let i = 0; i < 3; i++) { await click('#zi'); await sleep(250); }
  await shot('20b_bonus_april_algorithm_flowchart_zoomed', { wait: 800 });
  await page.goto('file://' + path.join(S, 'april_pages/architecture_map.html'), { waitUntil: 'networkidle' });
  await shot('21_bonus_april_architecture_map', { wait: 2500 });

  await browser.close();
  const tileFails = failed.filter((u) => u.includes('cartocdn')).length;
  if (tileFails) log.push(`${tileFails} CARTO tile request(s) failed: allow basemaps.cartocdn.com or set STANDIN_BASEMAP=1`);
  log.push(...[...new Set(failed.filter((u) => !u.includes('cartocdn')))].map((u) => 'failed ' + u));
  fs.writeFileSync(path.join(OUT, '_browser_log.txt'), log.join('\n') + '\n');
  console.log('log entries:', log.length);
})().catch((e) => { console.error(e); fs.writeFileSync(path.join(OUT, '_browser_log.txt'), log.join('\n') + '\n' + e.stack); process.exit(1); });
