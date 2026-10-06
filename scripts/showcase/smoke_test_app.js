// Smoke test for the packaged static app: opens index.html straight from disk (file://)
// with the real system clock and exercises every server-backed feature.
// Usage: NODE_PATH=$(npm root -g) node smoke_test_app.js <app-dir> [screenshot-dir]
// APP_URL=<url>: test a hosted copy instead (e.g. http://127.0.0.1:8000 serving the folder).
// STANDIN_GEOJSON=<file>: draw that GeoJSON under the map when CARTO tiles are blocked.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const APP = path.resolve(process.argv[2]);
const SHOTS = process.argv[3] ? path.resolve(process.argv[3]) : null;
const STANDIN = process.env.STANDIN_GEOJSON ? JSON.parse(fs.readFileSync(process.env.STANDIN_GEOJSON, 'utf8')) : null;
const TILE = /basemaps\.cartocdn\.com/;
const BASE_URL = process.env.APP_URL ? process.env.APP_URL.replace(/\/$/, '') : 'file://' + APP;
const results = [];
const problems = [];
const external = [];

function check(name, ok, detail = '') {
  results.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function watch(page) {
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    const where = (m.location() || {}).url || '';
    if (m.type() === 'error' && !TILE.test(where) && !TILE.test(m.text())) problems.push(`console: ${m.text()}`);
  });
  page.on('request', (r) => {
    const u = r.url();
    if (!/^(file|data|blob):/.test(u) && !u.startsWith(BASE_URL) && !TILE.test(u)) external.push(u);
  });
  page.on('dialog', (d) => d.accept());
}

async function shot(page, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await sleep(600);
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
}

async function standin(page) {
  if (!STANDIN) return;
  await page.evaluate((geo) => {
    const pane = map.getPane('standin') || map.createPane('standin'); // eslint-disable-line no-undef
    pane.style.zIndex = 250;
    pane.style.pointerEvents = 'none';
    L.geoJSON(geo, { pane: 'standin', interactive: false, style: { color: '#3b424c', weight: 1.3, opacity: 0.9 } }).addTo(map); // eslint-disable-line no-undef
  }, STANDIN);
}

const walkCount = (page) => page.evaluate(() => parseInt(document.getElementById('data-status').textContent, 10));
const loaded = (page) => page.waitForFunction(() => {
  const l = document.getElementById('loading');
  return l && getComputedStyle(l).display === 'none';
}, null, { timeout: 20000 });

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: SHOTS ? 2 : 1, locale: 'en-US' });
  if (STANDIN) {
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII=', 'base64');
    await context.route(TILE, (r) => r.fulfill({ body: png, contentType: 'image/png' }));
  }
  const page = await context.newPage();
  await watch(page);
  const indexUrl = BASE_URL + '/index.html';

  await page.goto(indexUrl);
  await loaded(page);
  await standin(page);
  const [y, mo, d] = await page.evaluate(() => { const n = new Date(); return [n.getFullYear(), n.getMonth() + 1, n.getDate()]; });
  check('clock opens on Wed May 6 2026', y === 2026 && mo === 5 && d === 6, `${y}-${mo}-${d}`);
  const t0 = await page.evaluate(() => Date.now()); await sleep(300);
  check('clock keeps ticking', (await page.evaluate(() => Date.now())) > t0);
  check('title marks the archive', /archived demo/.test(await page.title()));
  check('demo pill visible', await page.locator('#demo-pill').isVisible());
  const walks0 = await walkCount(page);
  check('walk log loaded', walks0 >= 100, `${walks0} walks`);
  await sleep(3200); // let the load toast fade
  await shot(page, 'app_01_map');

  await page.click('#demo-pill');
  check('About window opens', await page.locator('#demo-about-bg.open').isVisible());
  await shot(page, 'app_02_about');
  await page.click('#demo-about-close');

  await page.click('.tab-btn[data-view="collector-view"]');
  await sleep(800);
  const tiles = await page.locator('#cselector').innerText();
  check('collectors show pseudonyms', tiles.includes('Sonia') && !tiles.includes('Soteri'));
  await shot(page, 'app_03_collectors');

  await page.click('.tab-btn[data-view="availability-view"]');
  check('availability grid renders', (await page.locator('#avail-tbl-a td').count()) >= 21);

  await page.click('.tab-btn[data-view="calendar-view"]');
  await sleep(600);
  check('calendar opens on the pinned week', (await page.locator('#cal-title').innerText()) === 'May 5 - May 11, 2026',
    await page.locator('#cal-title').innerText());

  await page.click('#sched-unlock-btn');
  await page.fill('#auth-pin', 'any-pin');
  await page.click('#auth-modal-submit');
  await sleep(400);
  check('admin login accepts any PIN', await page.locator('#sched-unlock-btn.authed').count() === 1);

  // Claim an open slot, see it on the calendar, then hit the conflict rule and unclaim.
  const cell = '.cal-cell[data-date="2026-05-09"][data-tod="PM"]';
  const claimWith = async (bp) => {
    await page.click(cell);
    await page.selectOption('#slot-backpack', bp);
    await page.selectOption('#slot-route', { index: 1 });
    await page.selectOption('#slot-collector', { index: 1 });
    await page.click('#slot-claim-btn');
    await sleep(400);
    return page.locator('#slot-sched-msg').innerText();
  };
  let msg = await claimWith('B');
  check('claim a slot', /claimed successfully/i.test(msg), msg);
  await page.click('#slot-sched-close');
  await sleep(300);
  check('claim shows on the calendar', /backpack b/i.test(await page.locator(cell).innerText()));
  await shot(page, 'app_04_calendar_after_claim');
  msg = await claimWith('B');
  check('double claim is rejected', /already claimed/i.test(msg), msg);
  await page.click('.ss-unclaim-btn[data-date="2026-05-09"][data-tod="PM"][data-backpack="B"]');
  await sleep(400);
  msg = await page.locator('#slot-sched-msg').innerText();
  check('unclaim', /claim removed/i.test(msg), msg);
  await page.click('#slot-sched-close');

  await page.click('.bp-status-button[data-bp="B"]');
  await page.click('#bp-status-yes');
  await page.selectOption('#bp-status-choice', 'person:TER');
  await page.click('#bp-status-ok');
  await sleep(400);
  const holder = await page.locator('.bp-status-current[data-bp="B"]').innerText();
  const source = await page.locator('.bp-status-source[data-bp="B"]').innerText();
  check('backpack holder update', holder === 'Teo' && source === 'manual', `${holder} / ${source}`);

  await page.click('.cb-log-btn[data-bp="B"]');
  await page.fill('#recal-date', '2026-05-06');
  await page.check('input[name="recal-bp"][value="B"]');
  await page.click('#recal-modal-submit');
  await sleep(500);
  const days = await page.locator('.cal-bar[data-bp="B"] .cb-count-num').innerText();
  check('calibration log resets the day counter', days === '0', `${days} days`);

  await page.click('#notify-btn');
  await sleep(600);
  const preview = await page.locator('#notify-preview').innerText();
  check('reminder preview for tomorrow', /\d+\s+reminders?/.test(preview), preview.split('\n')[0]);
  await page.click('#notify-modal-close');

  await page.click('.upload-data-btn');
  await page.selectOption('#um-backpack', 'A');
  await page.selectOption('#um-tod', 'PM');
  await page.selectOption('#um-collector', 'SOT');
  await page.selectOption('#um-borough', 'MN');
  await page.selectOption('#um-route', 'HT');
  for (const f of ['start', 'walk', 'end']) {
    await page.check(`input[name="um-${f}-mode"][value="manual"]`);
    await page.fill(`#um-${f}-hh`, f === 'start' ? '17' : f === 'walk' ? '17' : '18');
    await page.fill(`#um-${f}-mm`, f === 'start' ? '05' : f === 'walk' ? '10' : '02');
    await page.fill(`#um-${f}-ss`, '00');
  }
  await page.setInputFiles('#um-gpx-file', { name: 'track.gpx', mimeType: 'application/gpx+xml', buffer: Buffer.from('<gpx/>') });
  await page.click('#upload-modal-submit');
  await sleep(600);
  msg = await page.locator('#upload-modal-status').innerText();
  check('upload records a walk', /A_SOT_MN_HT_20260506_PM recorded/.test(msg), msg);
  check('new walk is counted', (await walkCount(page)) === walks0 + 1, `${await walkCount(page)} walks`);

  // Rebuild reloads the page, which resets the private copy.
  await sleep(2200);
  const reload = page.waitForEvent('load', { timeout: 20000 });
  await page.click('#force-rebuild-btn');
  await reload;
  await loaded(page);
  check('rebuild resets the demo', (await walkCount(page)) === walks0, `${await walkCount(page)} walks`);

  for (const [file, probe] of [
    ['extras/algorithm-flowchart.html', 'svg'],
    ['extras/architecture-map.html', 'svg circle'],
    ['extras/student-schedule.html', 'table'],
  ]) {
    const p = await context.newPage();
    await watch(p);
    await p.goto(BASE_URL + '/' + file);
    await sleep(1500);
    const n = await p.locator(probe).count();
    const back = await p.locator('a[href="../index.html"]').count();
    check(`${file} renders`, n > 0 && back === 1, `${n} ${probe}`);
    if (file.includes('architecture')) await shot(p, 'app_05_architecture_map');
    if (file.includes('student')) {
      const text = await p.locator('body').innerText();
      check('student schedule uses team pseudonyms', text.includes('Team 1') && !/Robert|Nabidul/.test(text));
    }
    await p.close();
  }

  check('no outside requests except map tiles', external.length === 0, external.slice(0, 3).join(', '));
  check('no page or console errors', problems.length === 0, problems.slice(0, 3).join(' | '));
  await browser.close();
  const failed = results.filter((ok) => !ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
