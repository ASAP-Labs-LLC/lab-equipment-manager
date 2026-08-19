/* vcover.mjs — how much of the hillside is covered by plant, measured by
 * ablation so the haze cannot answer for it.
 *
 *   node vcover.mjs [--dists 250,600,1400,2600] [--quality ultra]
 *
 * The colour test in vsame.mjs cannot separate "fewer trees" from "the same
 * trees behind more air": sky.js's aerial perspective desaturates a distant
 * crown until a green-largest test stops calling it foliage at all, so the
 * number falls whether the wood thinned or not.
 *
 * This takes two frames from every viewpoint — one with vegetation visible and
 * one with the subsystem hidden and nothing else changed — and counts the
 * pixels that differ. That is coverage as the eye gets it: a plant counts where
 * it changes what is there, and a plant so far off that it cannot be told from
 * the ground behind it does not count, which is the right answer rather than a
 * limitation. The field of view is scaled as 1/d so the same ground subtends
 * the same pixels; equal population means a flat column.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const DISTS = arg('dists', '250,600,1400,2600').split(',').map(Number);
const quality = arg('quality', 'ultra');
const mods = arg('mods', 'sky,gi,terrain,vegetation');
const OUT = path.resolve(arg('out', '../shots/vcover'));
fs.mkdirSync(OUT, {recursive: true});
const REF = DISTS[0];

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text())) errs.push(m.text().slice(0, 200)); });
p.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 200)));

await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=wide&time=16&hud=0`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(4500);
await p.evaluate(t => window.__lemWorld.engine.setQualityMode(t), quality);
await p.waitForTimeout(2500);

const patch = await p.evaluate(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const xs = [], zs = [];
  for (const e of (v.trees || [])) for (let i = 0; i < e.list.length; i++) { xs.push(e.xs[i]); zs.push(e.zs[i]); }
  let best = null; const R = 130;
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

const place = async d => p.evaluate(({x, z, d, ref}) => {
  const w = window.__lemWorld, r = w.rig, cam = w.camera;
  r.maxDistance = Math.max(r.maxDistance || 0, 8000);
  if (cam.__baseFov === undefined) cam.__baseFov = cam.fov;
  r.goalTarget.set(x, w.ground ? w.ground(x, z) : 0, z);
  r.target.copy(r.goalTarget);
  r.goalDistance = d; r.distance = d;
  r.goalYaw = -0.7; r.yaw = -0.7;
  r.goalPitch = 0.30; r.pitch = 0.30;
  r.idleDrift = false;
  const t0 = Math.tan(cam.__baseFov * Math.PI / 360) * ref / d;
  cam.fov = Math.atan(t0) * 360 / Math.PI;
  cam.updateProjectionMatrix();
  r.apply(1);
  const v = w.subsystems.get('vegetation');
  v._repartition(true);
}, {x: patch.x, z: patch.z, d, ref: REF});

const setVeg = async on => p.evaluate(v => {
  const W = window.__lemWorld, s = W.subsystems.get('vegetation');
  if (s && s.group) s.group.visible = v;
  W.engine.shadowNeedsUpdate = true;
}, on);

const pairs = [];
for (const d of DISTS) {
  await place(d);
  await p.waitForTimeout(1200);
  const on = path.join(OUT, `on${d}.png`);
  await p.screenshot({path: on});
  await setVeg(false);
  await p.waitForTimeout(900);
  const off = path.join(OUT, `off${d}.png`);
  await p.screenshot({path: off});
  await setVeg(true);
  await p.waitForTimeout(500);
  pairs.push({d, on, off});
}

const stats = await p.evaluate(async srcs => {
  const load = s => new Promise(r => { const im = new Image(); im.onload = () => r(im); im.src = s; });
  const cv = document.createElement('canvas');
  cv.width = 1280; cv.height = 720;
  const g = cv.getContext('2d', {willReadFrequently: true});
  const grab = async (src, x0, y0, w, h) => { const im = await load(src);
    g.clearRect(0, 0, 1280, 720); g.drawImage(im, 0, 0);
    return g.getImageData(x0, y0, w, h).data; };
  const x0 = 400, y0 = 250, w = 480, h = 250;
  const out = [];
  for (const s of srcs) {
    const A = await grab(s.on, x0, y0, w, h);
    const B = await grab(s.off, x0, y0, w, h);
    let n = 0, cov = 0, strong = 0, R = 0, G = 0, Bl = 0;
    for (let i = 0; i < A.length; i += 4) {
      n++;
      const dd = Math.abs(A[i] - B[i]) + Math.abs(A[i + 1] - B[i + 1]) + Math.abs(A[i + 2] - B[i + 2]);
      if (dd > 12) { cov++; R += A[i]; G += A[i + 1]; Bl += A[i + 2]; }
      if (dd > 60) strong++;
    }
    out.push({d: s.d, coverPct: +(100 * cov / n).toFixed(1),
              strongPct: +(100 * strong / n).toFixed(1),
              vegRGB: cov ? [Math.round(R / cov), Math.round(G / cov), Math.round(Bl / cov)] : null});
  }
  return out;
}, pairs.map(r => ({d: r.d,
  on: 'data:image/png;base64,' + fs.readFileSync(r.on).toString('base64'),
  off: 'data:image/png;base64,' + fs.readFileSync(r.off).toString('base64')})));

await b.close();
console.log(JSON.stringify({quality, patch, ref: REF, stats, errs}, null, 1));
