/* sk-mainedge.mjs — WHO OWNS THE HARD EDGE AT THE MAINLAND'S FOOT?
 *
 * Two blind critiques independently named "a hard un-fogged base where the
 * distant landmass meets the ocean". Before anything in sky.js moves, this
 * establishes which surface the discontinuity is on, by measuring the rendered
 * step across it and the amount of atmosphere each side claims to have:
 *
 *   - the mainland (terrain's `_rangeMaterial`) is `fog: false` and computes its
 *     own haze, `far = 1 - exp(-dist * 0.00033)` scaled by 1.12 at the foot.
 *   - the ocean is a standard material and takes `scene.fog`, i.e. sky.js's
 *     chunk, at the SAME distance.
 *
 * So it prints both numbers at the same range, and the measured RGB profile
 * down a set of screen columns crossing the join.
 *
 *   node sk-mainedge.mjs [--cam far] [--time 9] [--cols 9] [--out x.png]
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const COLS = +(a.cols || 9);
const K0 = [0.82, 1.0, 1.24];
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${a.mods || 'sky,gi,terrain'}`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
if (a.fog) await page.addInitScript(`window.__lemFog = ${a.fog};`);
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null;
const t1 = Date.now();
while (Date.now() - t1 < 25000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => { const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 10) break;
}

/* Where the mainland's shoreline row lands on screen, and what haze each side
 * of it is carrying. */
const chunk = await page.evaluate(async () => {
  const T = await import('three');
  const src = T.ShaderChunk?.fog_fragment || '';
  const num = re => { const m = src.match(re); return m ? m.slice(1).map(Number) : null; };
  return {patched: !!T.ShaderChunk.__lemAerial,
          H: num(/lemA = exp\( - max\( lemH0, -600.0 \) \/ ([\d.]+) \)/)?.[0],
          P: num(/pow\( max\( lemTau, 1e-5 \), ([\d.]+) \)/)?.[0],
          MAX: num(/vec3\( ([\d.]+) \)\s*\n?\s*\* \( 1.0 - exp/)?.[0],
          K: num(/exp\( - lemU \* vec3\( ([\d.]+), ([\d.]+), ([\d.]+) \) \)/),
          /* 2026-08-09: baked scale + clear shell, parsed the same way. A build
           * without them reads S = 1, T0 = 0, W = 0 and nothing below changes. */
          S: num(/lemTau = fogDensity \* ([\d.]+) \* vFogDepth/)?.[0] ?? 1,
          T0: num(/lemX = lemTau - ([\d.]+)/)?.[0]
              ?? num(/lemTau = max\( lemTau - ([\d.]+)/)?.[0] ?? 0,
          SW: num(/\+ ([\d.]+) \* log\( 1\.0 \+ exp/)?.[0] ?? 0};
});

const geom = await page.evaluate(({COLS, CH}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  let mesh = null;
  w.scene.traverse(o => { if (o.name === 'terrain-mainland') mesh = o; });
  if (!mesh) return {err: 'no terrain-mainland in the scene'};
  const g = mesh.geometry;
  const pos = g.attributes.position, aUp = g.attributes.aUp;
  const W = window.innerWidth, H = window.innerHeight;
  const V = new (cam.position.constructor)();
  const f = w.scene.fog;
  /* the shoreline row is aUp == 0 */
  const shore = [];
  for (let i = 0; i < pos.count; i++) {
    if (aUp && aUp.getX(i) > 0.001) continue;
    V.set(pos.getX(i), pos.getY(i), pos.getZ(i)).applyMatrix4(mesh.matrixWorld);
    const wx = V.x, wy = V.y, wz = V.z;
    const dist = V.distanceTo(cam.position);
    V.project(cam);
    if (V.z > 1) continue;
    const sx = (V.x * 0.5 + 0.5) * W, sy = (-V.y * 0.5 + 0.5) * H;
    if (sx < 0 || sx >= W || sy < 0 || sy >= H) continue;
    shore.push({sx, sy, dist, wy: +wy.toFixed(1)});
  }
  shore.sort((p, q) => p.sx - q.sx);
  const picks = [];
  for (let k = 0; k < COLS; k++) {
    const i = Math.floor((k + 0.5) / COLS * shore.length);
    if (shore[i]) picks.push(shore[i]);
  }
  /* the two haze models, at the same range */
  const rows = picks.map(p => {
    const d = p.dist;
    const far = 1 - Math.exp(-d * 0.00033);
    const mainHazeFoot = Math.min(far * 1.12, 0.92);
    const mainHazeCrest = Math.min(far * 0.86, 0.92);
    /* the ocean pixel a few metres in front, through sky.js's chunk */
    const H0 = cam.position.y, H1 = t.waterY, Hh = CH.H;
    const A = Math.exp(-Math.max(H0, -600) / Hh), B = Math.exp(-Math.max(H1, -600) / Hh);
    const dy = H1 - H0;
    const avg = Math.min(Math.max(Hh * (A - B) / dy, 0), 6);
    const dep = d;   // near enough: the join is dead ahead of the camera
    let tau = f.density * (CH.S ?? 1) * dep * avg;
    const T0 = CH.T0 ?? 0, SW = CH.SW ?? 0;
    if (T0 > 0) { const xs = tau - T0;
      tau = SW > 0 ? Math.max(xs, 0) + SW * Math.log(1 + Math.exp(-Math.abs(xs) / SW))
                   : Math.max(xs, 0); }
    const u = Math.pow(Math.max(tau, 1e-5), CH.P);
    const ff = CH.K.map(k2 => CH.MAX * (1 - Math.exp(-u * k2)));
    return {sx: Math.round(p.sx), sy: Math.round(p.sy), dist: Math.round(d),
            mainHazeFoot: +mainHazeFoot.toFixed(3), mainHazeCrest: +mainHazeCrest.toFixed(3),
            seaFogFactor: +(0.2126 * ff[0] + 0.7152 * ff[1] + 0.0722 * ff[2]).toFixed(4),
            hterm: +avg.toFixed(3)};
  });
  return {rows, camY: +cam.position.y.toFixed(1), waterY: +t.waterY.toFixed(1),
          density: f.density, mainlandR: t.mainlandR,
          fogColour: [f.color.r, f.color.g, f.color.b].map(v => +v.toFixed(4))};
}, {COLS, CH: chunk});

const shoot = async (out) => {
  const buf = await page.screenshot({type: 'png'});
  if (out) fs.writeFileSync(out, buf);
  return 'data:image/png;base64,' + buf.toString('base64');
};
const src = await shoot(a.out);

const profile = await page.evaluate(async ({src, rows}) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas');
  cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  const px = (x, y) => { const o = (y * im.width + x) * 4; return [d[o], d[o + 1], d[o + 2]]; };
  const L = c => +(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]).toFixed(1);
  const sc = im.width / 1280;
  return rows.map(r => {
    const x = Math.min(im.width - 1, Math.round(r.sx * sc));
    const y0 = Math.round(r.sy * sc);
    const col = [];
    for (let dy = -12; dy <= 12; dy += 2) {
      const y = Math.min(im.height - 1, Math.max(0, y0 + dy));
      const c = px(x, y);
      col.push({dy, rgb: c, L: L(c), BR: c[2] - c[0]});
    }
    /* the step across the join: mean of 4 px above vs 4 px below */
    const above = col.filter(c => c.dy <= -4).slice(-3);
    const below = col.filter(c => c.dy >= 4).slice(0, 3);
    const m = v => v.reduce((s, c) => s + c.L, 0) / v.length;
    const mb = v => v.reduce((s, c) => s + c.BR, 0) / v.length;
    return {...r, land: +m(above).toFixed(1), sea: +m(below).toFixed(1),
            stepL: +(m(above) - m(below)).toFixed(1),
            landBR: +mb(above).toFixed(1), seaBR: +mb(below).toFixed(1), col};
  });
}, {src, rows: geom.rows});

console.log(JSON.stringify({cam, time, chunk, ...geom, rows: undefined,
  profile: profile.map(p => ({...p, col: undefined})),
  sample: profile[Math.floor(profile.length / 2)]?.col,
  errors: errors.slice(0, 3)}, null, 1));
await b.close();
