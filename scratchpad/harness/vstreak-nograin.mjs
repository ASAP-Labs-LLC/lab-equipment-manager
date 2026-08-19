/* vstreak-nograin.mjs — NEGATIVE CONTROL: the same sky with the output dither
 * turned off, set at RUNTIME on the live uniform.  No file is modified.
 *
 *   node vstreak-nograin.mjs --cam street --time 18.4 --tag street-t184-nograin
 *
 * engine.js's composite adds `(g - 0.5) * uFilmGrain` after sRGB encode, with
 * uFilmGrain = 0.012 (≈3.1 codes peak-to-peak).  If the sky's measured ~1-code
 * per-pixel noise is that dither, zeroing the uniform must make the flat runs
 * appear.  If the runs stay at 1 px, the noise is coming from somewhere else.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'street', time = a.time || '18.4';
const tag = a.tag || `${cam}-t${time}-nograin`;
const dir = '/Users/rynatical/LAB-lem/scratchpad/harness/vstreak';
fs.mkdirSync(dir, {recursive: true});
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;
const W = 1280, H = 720;

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

/* Find the composite material's uFilmGrain and hold it at the requested value.
 * The render loop rewrites uTime every frame but not this, so a one-shot set
 * sticks; re-set it in a rAF anyway in case the loop restores it. */
const grain = a.grain === undefined ? 0 : parseFloat(a.grain);
const applied = await page.evaluate(({g}) => {
  const w = window.__lemWorld;
  const eng = w.engine || w;
  const hits = [];
  const seen = new Set();
  const walk = (o, depth) => {
    if (!o || typeof o !== 'object' || depth > 5 || seen.has(o)) return;
    seen.add(o);
    if (o.uniforms && o.uniforms.uFilmGrain) hits.push(o.uniforms.uFilmGrain);
    for (const k of Object.keys(o)) {
      try { walk(o[k], depth + 1); } catch {}
    }
  };
  walk(eng, 0);
  if (!hits.length) return {found: 0};
  const before = hits[0].value;
  const pin = () => { for (const u of hits) u.value = g; requestAnimationFrame(pin); };
  pin();
  return {found: hits.length, before, now: hits[0].value};
}, {g: grain});
if (!applied.found) { console.error('could not reach uFilmGrain'); await b.close(); process.exit(4); }
await page.waitForTimeout(900);

const meta = await page.evaluate(() => {
  const w = window.__lemWorld, cam = w.camera;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  cam.updateMatrixWorld(true);
  const e = cam.matrixWorld.elements;
  return {camY: +cam.position.y.toFixed(2),
          pitchDeg: +(Math.asin(-e[9]) * 180 / Math.PI).toFixed(3),
          fovDeg: cam.fov};
});
await page.waitForTimeout(400);

const buf = await page.screenshot({type: 'png'});
fs.writeFileSync(path.join(dir, tag + '.png'), buf);
const rgbB64 = await page.evaluate(async (src) => {
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
fs.writeFileSync(path.join(dir, tag + '.rgb'), Buffer.from(rgbB64, 'base64'));
fs.writeFileSync(path.join(dir, tag + '.meta.json'),
  JSON.stringify({tag, url, cam, time, W, H, ...meta, grainSetTo: grain, uniform: applied}, null, 2));
console.log(JSON.stringify({tag, grain, applied, ...meta}));
await b.close();
