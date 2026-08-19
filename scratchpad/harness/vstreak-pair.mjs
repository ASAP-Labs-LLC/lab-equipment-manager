/* vstreak-pair.mjs — the A/B in ONE page load.
 *
 *   node vstreak-pair.mjs --cam street --time 18.4 --tag pair-street-t184
 *
 * The earlier shipping captures and dither-off captures were ten minutes
 * apart, and static/world/*.js is being rewritten by something else every ~40
 * seconds, so those two sets cannot be assumed to be the same build.  Here both
 * frames come from one load of one build: screenshot with uFilmGrain at its
 * shipped value, then set the uniform to 0 and screenshot again.  Nothing but
 * that uniform differs, and nothing on disk is touched.
 *
 * Refuses to start until the world modules have held still, and re-checks the
 * directory stamp after, so a frame taken mid-edit cannot be quoted.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'street', time = a.time || '18.4';
const tag = a.tag || `pair-${cam}-t${time}`;
const DIR = '/Users/rynatical/LAB-lem/scratchpad/harness/vstreak';
const WORLD = '/Users/rynatical/LAB-lem/LEM Web Server/static/world';
fs.mkdirSync(DIR, {recursive: true});
const W = 1280, H = 720;

const stamp = () => fs.readdirSync(WORLD).filter(f => f.endsWith('.js')).sort()
  .map(f => `${f}:${fs.statSync(path.join(WORLD, f)).mtimeMs}`).join('|');

/* Wait for the builder to stop writing: the stamp must hold for 45 s. */
const HOLD = 45000, CAP = 600000;
let s0 = stamp(), held = 0;
const t0 = Date.now();
while (held < HOLD && Date.now() - t0 < CAP) {
  await new Promise(r => setTimeout(r, 5000));
  const s1 = stamp();
  if (s1 === s0) held += 5000;
  else { s0 = s1; held = 0; console.error('world modules changed, restarting hold…'); }
}
if (held < HOLD) { console.error('world never settled'); process.exit(5); }
const stampBefore = s0;
console.error(`build held still for ${held / 1000}s — capturing`);

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader',
         '--force-color-profile=srgb']});
const page = await b.newPage({viewport: {width: W, height: H}, deviceScaleFactor: 1});
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null; const t1 = Date.now();
while (Date.now() - t1 < 25000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => { const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now; if (stable >= 10) break;
}
const meta = await page.evaluate(() => {
  const w = window.__lemWorld, cam = w.camera;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  cam.updateMatrixWorld(true);
  return {camY: +cam.position.y.toFixed(2),
          pitchDeg: +(Math.asin(-cam.matrixWorld.elements[9]) * 180 / Math.PI).toFixed(3),
          fovDeg: cam.fov};
});

/* locate the uniform once, keep the handle for both legs */
const found = await page.evaluate(() => {
  const w = window.__lemWorld, eng = w.engine || w, hits = [], seen = new Set();
  const walk = (o, d) => {
    if (!o || typeof o !== 'object' || d > 5 || seen.has(o)) return;
    seen.add(o);
    if (o.uniforms && o.uniforms.uFilmGrain) hits.push(o.uniforms.uFilmGrain);
    for (const k of Object.keys(o)) { try { walk(o[k], d + 1); } catch {} }
  };
  walk(eng, 0);
  window.__grainHits = hits;
  window.__grainShipped = hits.length ? hits[0].value : null;
  return {n: hits.length, shipped: window.__grainShipped};
});
if (!found.n) { console.error('no uFilmGrain'); await b.close(); process.exit(4); }

async function grab(name, value) {
  await page.evaluate(v => {
    window.__grainPin = v;
    if (!window.__grainPinning) {
      window.__grainPinning = true;
      const pin = () => { for (const u of window.__grainHits) u.value = window.__grainPin; requestAnimationFrame(pin); };
      pin();
    }
  }, value);
  await page.waitForTimeout(1200);
  const buf = await page.screenshot({type: 'png'});
  fs.writeFileSync(path.join(DIR, name + '.png'), buf);
  const b64 = await page.evaluate(async (src) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const out = new Uint8Array(im.width * im.height * 3);
    for (let i = 0, j = 0; i < d.length; i += 4) { out[j++] = d[i]; out[j++] = d[i + 1]; out[j++] = d[i + 2]; }
    let s = ''; const CH = 0x8000;
    for (let i = 0; i < out.length; i += CH) s += String.fromCharCode.apply(null, out.subarray(i, i + CH));
    return btoa(s);
  }, 'data:image/png;base64,' + buf.toString('base64'));
  fs.writeFileSync(path.join(DIR, name + '.rgb'), Buffer.from(b64, 'base64'));
  fs.writeFileSync(path.join(DIR, name + '.meta.json'), JSON.stringify(
    {tag: name, url, cam, time, W, H, ...meta, grain: value, shippedGrain: found.shipped}, null, 2));
  return name;
}

const onName = tag + '-on', offName = tag + '-off';
await grab(onName, found.shipped);
await grab(offName, 0);
await b.close();

const ok = stamp() === stampBefore;
console.log(JSON.stringify({tag, shippedGrain: found.shipped, uniformsFound: found.n,
                            on: onName, off: offName, buildStable: ok, ...meta}));
if (!ok) { console.error('UNSTABLE BUILD during pair capture'); process.exitCode = 3; }
