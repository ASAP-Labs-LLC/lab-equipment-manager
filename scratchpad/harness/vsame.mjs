/* vsame.mjs — the acceptance test, taken literally.
 *
 *   node vsame.mjs [--dists 250,600,1400,2600] [--quality ultra] [--out ../shots/vsame]
 *
 * "Photograph the same hillside from near and from far, normalise for framing,
 * and density, colour and silhouette must read the same."
 *
 * One patch of forested ground is chosen from the scatter list. The camera is
 * flown to it from each distance at the same bearing and elevation, and the
 * vertical field of view is scaled as 1/d so the patch subtends the same number
 * of pixels in every frame — that is the normalisation, and it is the only one
 * applied. Nothing else is touched: the haze is left in, because the haze is
 * supposed to differ.
 *
 * Then each frame is measured on the same centre crop: what fraction of it is
 * foliage (green the largest channel by a margin, and dark enough not to be
 * sky), and the mean colour of those pixels. Equal density means the fraction
 * is flat down the column; equal colour means the RGB is.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const DISTS = arg('dists', '250,600,1400,2600').split(',').map(Number);
const quality = arg('quality', 'ultra');
const time = arg('time', '16');
const mods = arg('mods', 'sky,gi,terrain,vegetation');
const OUT = path.resolve(arg('out', '../shots/vsame'));
fs.mkdirSync(OUT, {recursive: true});
const REF = DISTS[0];

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text())) errs.push(m.text().slice(0, 200)); });
p.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 200)));

await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=wide&time=${time}&hud=0`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(4500);
await p.evaluate(t => window.__lemWorld.engine.setQualityMode(t), quality);
await p.waitForTimeout(2500);

const patch = await p.evaluate(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const xs = [], zs = [];
  for (const e of (v.trees || [])) for (let i = 0; i < e.list.length; i++) { xs.push(e.xs[i]); zs.push(e.zs[i]); }
  let best = null;
  const R = 130;
  for (let k = 0; k < xs.length; k += Math.max(1, (xs.length / 400) | 0)) {
    let n = 0;
    for (let j = 0; j < xs.length; j += 3) {
      const dx = xs[j] - xs[k], dz = zs[j] - zs[k];
      if (dx * dx + dz * dz < R * R) n++;
    }
    if (!best || n > best.n) best = {x: xs[k], z: zs[k], n: n * 3};
  }
  return best;
});

const shots = [];
for (const d of DISTS) {
  const fov = await p.evaluate(({x, z, d, ref}) => {
    const w = window.__lemWorld, r = w.rig, cam = w.camera;
    r.maxDistance = Math.max(r.maxDistance || 0, 8000);
    if (cam.__baseFov === undefined) cam.__baseFov = cam.fov;
    r.goalTarget.set(x, w.ground ? w.ground(x, z) : 0, z);
    r.target.copy(r.goalTarget);
    r.goalDistance = d; r.distance = d;
    r.goalYaw = -0.7; r.yaw = -0.7;
    r.goalPitch = 0.30; r.pitch = 0.30;
    r.idleDrift = false;
    /* Half-angle scaled so a fixed span at the target plane covers the same
     * pixels: tan(fov/2) goes as 1/d. */
    const t0 = Math.tan(cam.__baseFov * Math.PI / 360) * ref / d;
    cam.fov = Math.atan(t0) * 360 / Math.PI;
    cam.updateProjectionMatrix();
    r.apply(1);
    return cam.fov;
  }, {x: patch.x, z: patch.z, d, ref: REF});
  await p.waitForTimeout(1400);
  const f = path.join(OUT, `d${d}.png`);
  await p.screenshot({path: f});
  shots.push({d, fov: +fov.toFixed(2), file: f});
}

/* Measure inside the page: no image library on a lab bench. */
const stats = await p.evaluate(async srcs => {
  const load = s => new Promise(r => { const im = new Image(); im.onload = () => r(im); im.src = s; });
  const cv = document.createElement('canvas');
  cv.width = 1280; cv.height = 720;
  const g = cv.getContext('2d', {willReadFrequently: true});
  const out = [];
  for (const s of srcs) {
    const im = await load(s.src);
    g.clearRect(0, 0, 1280, 720); g.drawImage(im, 0, 0);
    /* The centre band: the patch is at the crosshair and the crop is the same
     * screen area in every frame, which is what the fov scaling bought. */
    const x0 = 400, x1 = 880, y0 = 250, y1 = 500;
    const px = g.getImageData(x0, y0, x1 - x0, y1 - y0).data;
    let n = 0, fol = 0, R = 0, G = 0, B = 0, lum = 0;
    for (let i = 0; i < px.length; i += 4) {
      const r = px[i], gg = px[i + 1], bb = px[i + 2];
      n++; lum += 0.2126 * r + 0.7152 * gg + 0.0722 * bb;
      /* Foliage: green clearly the largest channel. Ground here is tan (red
       * largest) and sky is blue largest, so the test separates the three
       * without knowing anything about the palette. */
      if (gg > r + 6 && gg > bb + 6) { fol++; R += r; G += gg; B += bb; }
    }
    out.push({d: s.d, fov: s.fov, foliagePct: +(100 * fol / n).toFixed(1),
              rgb: fol ? [Math.round(R / fol), Math.round(G / fol), Math.round(B / fol)] : null,
              meanLum: +(lum / n).toFixed(1)});
  }
  return out;
}, shots.map(s => ({d: s.d, fov: s.fov, src: 'data:image/png;base64,' + fs.readFileSync(s.file).toString('base64')})));

await b.close();
console.log(JSON.stringify({quality, patch, ref: REF, stats, errs}, null, 1));
