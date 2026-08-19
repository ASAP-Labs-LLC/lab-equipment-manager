/* shot.mjs — screenshot + measure the 3D lab floor.
 *
 *   node shot.mjs --url "http://127.0.0.1:5599/static/world/dev/solo.html?mods=terrain&cam=low" \
 *                 --out ../shots/terrain-low.png [--headed] [--seconds 6] [--w 1920] [--h 1080]
 *
 * Writes the PNG plus a sidecar .json with fps, draw calls, triangles, the
 * quality tier the engine settled on, the GPU string (so you know whether the
 * fps is real or software), console errors, and time-to-first-frame.
 *
 * IMPORTANT: headless chromium falls back to SwiftShader, which renders
 * correctly but at software speed. Screenshots from headless are fine for
 * looking at; fps from headless is meaningless. Use --headed for any number
 * you intend to quote.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) {
    const key = a.slice(2);
    const next = process.argv[i + 1];
    if (!next || next.startsWith('--')) args[key] = true;
    else { args[key] = next; i++; }
  }
}

let url = args.url;
if (!url) { console.error('need --url'); process.exit(2); }
/* Judged shots must not carry our debug HUD — it identifies the frame as ours
 * the moment a critic looks at it. Pass --hud to keep it for eyeballing. */
if (!args.hud && url.includes('solo.html') && !/[?&]hud=/.test(url)) {
  url += (url.includes('?') ? '&' : '?') + 'hud=0';
}
/* Judged shots are taken at a fixed tier. The ladder now climbs from the floor
 * tier, so a short capture would otherwise photograph the lowest quality the
 * renderer offers and call it our best work. */
if (args.season && !/[?&]season=/.test(url)) url += '&season=' + args.season;
if (url.includes('solo.html') && !/[?&]quality=/.test(url)) {
  url += '&quality=' + (args.quality || 'ultra');
}
const out = path.resolve(args.out || 'shot.png');
const seconds = parseFloat(args.seconds || '6');
const width = parseInt(args.w || '1920', 10);
const height = parseInt(args.h || '1080', 10);
const headed = !!args.headed;

fs.mkdirSync(path.dirname(out), {recursive: true});

const gpuFlags = [
  '--ignore-gpu-blocklist',
  '--enable-gpu-rasterization',
  '--enable-zero-copy',
  '--use-angle=metal',
  '--enable-unsafe-swiftshader',
];

const browser = await chromium.launch({
  headless: !headed,
  channel: headed ? undefined : 'chromium',
  args: gpuFlags,
});
const page = await browser.newPage({viewport: {width, height},
                                    deviceScaleFactor: 1});

const errors = [];
/* The app serves no favicon, so Chromium's automatic request for one 404s on
 * every single run. Reporting it would make "this run had console errors" mean
 * nothing, which is the opposite of what the check is for. */
const NOISE = /favicon\.ico|Failed to load resource: the server responded with a status of 404 \(NOT FOUND\)$/;
page.on('console', m => {
  const text = m.text();
  if (m.type() === 'error' && !NOISE.test(text)) errors.push(text.slice(0, 400));
});
/* Chromium's console line for a 404 does not name the file, so filtering it
 * above would hide a builder's typo'd import. These two do name it, which is
 * why the filter above is safe: a genuinely missing module still lands here,
 * with its URL. */
page.on('requestfailed', r => {
  if (!/favicon\.ico/.test(r.url())) {
    errors.push(`request failed: ${r.url()} ${r.failure()?.errorText || ''}`);
  }
});
page.on('response', r => {
  if (r.status() >= 400 && !/favicon\.ico/.test(r.url())) {
    errors.push(`HTTP ${r.status()}: ${r.url()}`);
  }
});
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 400)));

/* Never photograph a moving target.
 *
 * The dev server reads the modules off disk on every load, so a shot taken
 * while a builder is mid-round is a picture of whatever happened to be saved at
 * that instant. That is not a hypothetical: four identical runs of this script
 * once split 23% / 23% / 7.9% / 7.9% green, and the "regression" was three
 * module files being written between run 2 and run 3. Two false findings came
 * out of it before the mtimes were checked.
 *
 * So record what the world was built from, before and after, and refuse the
 * shot if it moved. A measurement that cannot say what it measured is worse
 * than no measurement, because it gets believed. */
const WORLD_DIR = path.resolve(
  process.env.LEM_WORLD_DIR ||
  path.join(process.env.HOME, 'LAB-lem/LEM Web Server/static/world'));
const stamp = () => {
  try {
    return fs.readdirSync(WORLD_DIR).filter(f => f.endsWith('.js')).sort()
      .map(f => `${f}:${fs.statSync(path.join(WORLD_DIR, f)).mtimeMs}`).join('|');
  } catch { return ''; }
};
const stampBefore = stamp();

const t0 = Date.now();
await page.goto(url, {waitUntil: 'load', timeout: 60000});

/* First interactive frame: the world reports it once every subsystem has built
 * and the loop is running. */
let firstFrameMs = null;
try {
  await page.waitForFunction(
    () => window.__worldReady === true ||
          (window.__lemWorld && window.__lemWorld.engine && window.__lemWorld.engine.frame > 0),
    null, {timeout: 45000});
  firstFrameMs = Date.now() - t0;
} catch { /* recorded as null below */ }

/* Wait for the world to STOP CHANGING, not for a guessed number of
 * milliseconds.
 *
 * `__worldReady` fires well before the scene has finished populating. Measured:
 * at the old fixed ~1400 ms settle the frame held 217 draws / 1,529,006
 * triangles; at ~4900 ms the same build held 322 draws / 2,232,358 — 105 draw
 * calls and 700,000 triangles of content that simply were not there yet. Both
 * numbers were perfectly reproducible at their own settle, which is what made
 * this look like world non-determinism rather than a short wait. Every frame
 * judged at the default was a photograph of an unfinished world.
 *
 * So poll until draws and triangles hold still across consecutive samples.
 * `settledMs` and `settled` go in the sidecar: a frame that never converged is
 * still written, but it says so. */
const SETTLE_POLL = 350, SETTLE_HOLD = 10, SETTLE_CAP = 25000;
let settledMs = null, settled = false, stable = 0, prev = null;
{
  const t1 = Date.now();
  while (Date.now() - t1 < SETTLE_CAP) {
    await page.waitForTimeout(SETTLE_POLL);
    const now = await page.evaluate(() => {
      const s = window.__lemWorld && window.__lemWorld.stats
        ? window.__lemWorld.stats() : null;
      return s ? [s.drawCalls, s.triangles] : null;
    });
    if (!now) break;
    if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++;
    else stable = 0;
    prev = now;
    if (stable >= SETTLE_HOLD) { settled = true; break; }
  }
  settledMs = Date.now() - t1;
}
/* Never shorten an explicitly-asked-for wait. */
const floor = Math.max(1200, seconds * 1000 * 0.35);
if (settledMs < floor) await page.waitForTimeout(floor - settledMs);

const measured = await page.evaluate(async (secs) => {
  const gl = document.createElement('canvas').getContext('webgl2') ||
             document.createElement('canvas').getContext('webgl');
  const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'unknown';

  const frames = [];
  let last = performance.now();
  await new Promise(resolve => {
    const stop = performance.now() + secs * 1000;
    const tick = now => {
      frames.push(now - last);
      last = now;
      if (now < stop) requestAnimationFrame(tick); else resolve();
    };
    requestAnimationFrame(tick);
  });
  frames.shift();
  const sorted = [...frames].sort((a, b) => a - b);
  const pct = p => sorted[Math.min(sorted.length - 1,
                                   Math.floor(sorted.length * p))] || 0;
  const w = window.__lemWorld;
  const stats = w && w.stats ? w.stats() : {};
  return {
    gpu: renderer,
    frameCount: frames.length,
    fpsMean: 1000 / (frames.reduce((a, b) => a + b, 0) / frames.length),
    msP50: pct(0.5), msP95: pct(0.95), msMax: sorted[sorted.length - 1] || 0,
    drawCalls: stats.drawCalls, triangles: stats.triangles,
    tier: stats.tier, failed: stats.failed,
    subsystems: w && w.subsystems ? [...w.subsystems.keys()] : [],
  };
}, seconds);

await page.screenshot({path: out});

/* Transfer size of everything the page pulled, so the 8MB budget is a measured
 * number and not a hope. */
const bytes = await page.evaluate(() =>
  performance.getEntriesByType('resource')
    .reduce((sum, r) => sum + (r.transferSize || r.encodedBodySize || 0), 0));

const report = {
  url, out, headed, width, height,
  firstFrameMs, payloadBytes: bytes, settledMs, settled,
  ...measured,
  errors: errors.slice(0, 25),
  at: new Date().toISOString(),
};

/* The world moved while we were looking at it. Say so in the sidecar AND on
 * stderr with a non-zero exit, so a shot taken mid-round cannot be quietly
 * folded into a comparison later. The PNG is still written — it is evidence of
 * something, just not of a stable build. */
const stampAfter = stamp();
report.buildStable = stampBefore === stampAfter && stampBefore !== '';
if (!report.buildStable) {
  report.buildWarning =
    'world modules changed during capture — this frame is not comparable';
}
fs.writeFileSync(out.replace(/\.png$/, '') + '.json', JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
if (!report.buildStable) {
  console.error('\nUNSTABLE BUILD: static/world/*.js changed during this ' +
                'capture. Do not compare this frame against another.');
  process.exitCode = 3;
}

await browser.close();
