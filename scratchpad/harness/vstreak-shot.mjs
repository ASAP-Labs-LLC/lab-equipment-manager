/* vstreak-shot.mjs — capture a 1280x720 frame and dump RAW RGB bytes.
 *
 *   node vstreak-shot.mjs --cam low --time 9 --tag low-t9
 *
 * Writes <tag>.png, <tag>.rgb (W*H*3 raw bytes) and <tag>.meta.json.
 * Raw bytes, not the PNG, because there is no PNG decoder installed here and
 * because any decode step is one more place a value could be altered.
 * Page-load + settle boilerplate copied from shot.mjs / sk-strip.mjs.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];

const cam = a.cam || 'low';
const time = a.time || '9';
const mods = a.mods || 'sky,gi,terrain';
const tag = a.tag || `${cam}-t${time}`;
const dir = path.resolve('/Users/rynatical/LAB-lem/scratchpad/harness/vstreak');
fs.mkdirSync(dir, {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;

const W = 1280, H = 720;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader',
         '--force-color-profile=srgb', '--disable-lcd-text']});
const page = await b.newPage({viewport: {width: W, height: H}, deviceScaleFactor: 1});

const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 300)));
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 300)); });

/* Record what the world was built from, so a frame captured mid-edit cannot be
 * quietly folded into a comparison (shot.mjs's stamp check). */
const WORLD_DIR = '/Users/rynatical/LAB-lem/LEM Web Server/static/world';
const stamp = () => fs.readdirSync(WORLD_DIR).filter(f => f.endsWith('.js')).sort()
  .map(f => `${f}:${fs.statSync(path.join(WORLD_DIR, f)).mtimeMs}`).join('|');
const stampBefore = stamp();

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

/* Freeze the rig so the frame is not a moving target, and read the camera
 * geometry we need to convert screen rows to elevation angles. */
const meta = await page.evaluate(() => {
  const w = window.__lemWorld, cam = w.camera;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  cam.updateMatrixWorld(true);
  const e = cam.matrixWorld.elements;
  const fy = -e[9];
  const s = w.stats?.() || {};
  return {
    camY: +cam.position.y.toFixed(2),
    pitchDeg: +(Math.asin(fy) * 180 / Math.PI).toFixed(3),
    fovDeg: cam.fov, aspect: cam.aspect,
    drawCalls: s.drawCalls, triangles: s.triangles, tier: s.tier,
  };
});
await page.waitForTimeout(600);

const buf = await page.screenshot({type: 'png'});
fs.writeFileSync(path.join(dir, tag + '.png'), buf);

/* Decode through canvas in-page and hand back the raw bytes. */
const rgbB64 = await page.evaluate(async (src) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  const out = new Uint8Array(im.width * im.height * 3);
  for (let i = 0, j = 0; i < d.length; i += 4) { out[j++] = d[i]; out[j++] = d[i + 1]; out[j++] = d[i + 2]; }
  let s = '';
  const CH = 0x8000;
  for (let i = 0; i < out.length; i += CH) s += String.fromCharCode.apply(null, out.subarray(i, i + CH));
  return btoa(s);
}, 'data:image/png;base64,' + buf.toString('base64'));

const raw = Buffer.from(rgbB64, 'base64');
fs.writeFileSync(path.join(dir, tag + '.rgb'), raw);

const stampAfter = stamp();
const rep = {tag, url, cam, time, W, H, ...meta,
             rawBytes: raw.length,
             buildStable: stampBefore === stampAfter && stampBefore !== '',
             errors: errors.slice(0, 10), at: new Date().toISOString()};
fs.writeFileSync(path.join(dir, tag + '.meta.json'), JSON.stringify(rep, null, 2));
console.log(JSON.stringify(rep));
await b.close();
