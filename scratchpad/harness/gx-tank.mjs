/* gx-tank.mjs — for every pad pixel, ask three independent questions and
 * cross-tabulate the answers:
 *
 *   1. GEOMETRY   does a CPU ray from this point to the sun hit anything, and
 *                 what, and how high above the pad?
 *   2. THE MAP    what does `lemFarShadow` return here, re-run on the CPU
 *                 against the read-back cascade targets?
 *   3. THE FRAME  what is the pixel worth, with the coarse cascades gated on
 *                 and gated off, with the stop frozen?
 *
 * A caster that is missing shows up as a cell with GEOMETRY = occluded, MAP = 1
 * and a frame delta of zero. A caster that is present but invisible shows up as
 * GEOMETRY = occluded, MAP = 0 and a frame delta that is real but small against
 * the fill.
 *
 *   node gx-tank.mjs [--cam far] [--time 9] [--step 2]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9', step = parseInt(a.step || '2', 10);
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(10000);
await page.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } w.camera.updateMatrixWorld(true); });
await page.waitForTimeout(600);

const pick = await page.evaluate((step) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera, rn = w.engine.renderer;
  const d = gi.sunDirection.clone();
  const maps = gi._csm.map(c => {
    const N = c.rt.width, buf = new Uint8Array(N * N * 4);
    try { rn.readRenderTargetPixels(c.rt, 0, 0, N, N, buf); } catch (e) { void e; }
    return {i: c.i, N, buf};
  });
  const unpack = (m, ix, iy) => {
    ix = Math.min(m.N - 1, Math.max(0, ix)); iy = Math.min(m.N - 1, Math.max(0, iy));
    const o = (iy * m.N + ix) * 4;
    return m.buf[o] / 255 + m.buf[o + 1] / 65025 + m.buf[o + 2] / 16581375 + m.buf[o + 3] / 4228250625;
  };
  const TAPS = [[-0.8, 0.4], [0.4, 0.8], [0.8, -0.4], [-0.4, -0.8]];
  const cascadeAt = (m, P, N3) => {
    const e = gi.uniforms[`lemCsmMat${m.i}`].value.elements;
    const par = gi.uniforms[`lemCsmParam${m.i}`].value;
    const X = P.x + N3.x * par.w, Y = P.y + N3.y * par.w, Z = P.z + N3.z * par.w;
    const cw = e[3] * X + e[7] * Y + e[11] * Z + e[15];
    const px = (e[0] * X + e[4] * Y + e[8] * Z + e[12]) / cw;
    const py = (e[1] * X + e[5] * Y + e[9] * Z + e[13]) / cw;
    const pz = (e[2] * X + e[6] * Y + e[10] * Z + e[14]) / cw;
    if (px < 0 || px > 1 || py < 0 || py > 1 || pz > 1 || pz < 0) return {s: 1, gap: null};
    const dd = pz - par.z;
    let s = 0, best = 1;
    for (const [tx, ty] of TAPS) {
      const dep = unpack(m, Math.floor((px + tx * par.x) * m.N), Math.floor((py + ty * par.y) * m.N));
      best = Math.min(best, dep);
      if (dd <= dep) s++;
    }
    /* how far in front of this receiver the nearest recorded caster is, in metres */
    const range = 3467;
    return {s: s * 0.25, gap: +((pz - best) * range).toFixed(2)};
  };
  const right = gi.uniforms.lemLightRight.value, up = gi.uniforms.lemLightUp.value;
  const ss = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
  const boxW = (P, c, r) => {
    const dx = P.x - c.x, dy = P.y - c.y, dz = P.z - c.z;
    const q = Math.max(Math.abs(dx * right.x + dy * right.y + dz * right.z),
                       Math.abs(dx * up.x + dy * up.y + dz * up.z));
    return 1 - ss(r * 0.80, r * 0.97, q);
  };

  const occ = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible &&
    !/^terrain|ocean|horizon|weather/.test(o.name || '') && o.geometry) occ.push(o); });
  const pads = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && /:concrete$/.test(o.name || '')) pads.push(o); });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  const pts = [];
  const W = innerWidth, H = innerHeight;
  for (let sy = 0; sy < H; sy += step) for (let sx = 0; sx < W; sx += step) {
    ndc.set((sx + .5) / W * 2 - 1, -((sy + .5) / H * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(pads, false)[0];
    if (!h || !h.face) continue;
    const n = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    if (n.y < 0.92) continue;
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.08), d, 0.05, 500);
    sr.layers.enableAll();
    const hit = sr.intersectObjects(occ, false)[0];
    const nw = boxW(h.point, gi.uniforms.lemNearCentre.value, gi.uniforms.lemNearRadius.value);
    let s = 1, gap = null;
    if (maps[1] && gi.uniforms.lemCsmReady1.value > 0.5) { const r = cascadeAt(maps[1], h.point, n); s = r.s; gap = r.gap; }
    if (maps[0] && gi.uniforms.lemCsmReady0.value > 0.5) {
      const bx = gi.uniforms.lemCsmBox0.value;
      const r0 = cascadeAt(maps[0], h.point, n);
      const wgt = boxW(h.point, {x: bx.x, y: bx.y, z: bx.z}, bx.w);
      s = s + (r0.s - s) * wgt;
      if (wgt > 0.5 && r0.gap !== null) gap = r0.gap;
    }
    const fs = s + (1 - s) * nw;
    let cls = 'open';
    if (hit) {
      const rise = hit.point.y - h.point.y;
      const nm = hit.object.name || '';
      if (/vegetation|tree|canopy|trunk|foliage/i.test(nm) || hit.object.isInstancedMesh) cls = 'tree';
      else if (rise > 18) cls = 'tall';          // stack, gantry, tall shed
      else if (rise > 3) cls = 'mid';            // tank shell, warehouse wall
      else cls = 'low';
      pts.push({sx, sy, cls, rise: +rise.toFixed(1), nm: nm.slice(0, 40),
                dist: +hit.distance.toFixed(1), fs: +fs.toFixed(3), gap, nw: +nw.toFixed(2)});
    } else {
      pts.push({sx, sy, cls, rise: 0, nm: '', dist: 0, fs: +fs.toFixed(3), gap, nw: +nw.toFixed(2)});
    }
  }
  return {pts, sun: d.toArray().map(v => +v.toFixed(3)),
          elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2)};
}, step);

async function readPixels() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, pts}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const dd = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth;
    return pts.map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
      return +(0.2126 * dd[o] + 0.7152 * dd[o + 1] + 0.0722 * dd[o + 2]).toFixed(2); });
  }, {src, pts: pick.pts});
}

await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  if (typeof gi.setExposureLocked === 'function') gi.setExposureLocked(true);
  else { gi.__grade = gi._applyGrade; gi._applyGrade = () => {}; }
  gi.__svc = gi._serviceCascades; gi._serviceCascades = () => {}; });
await page.waitForTimeout(1000);
const Lon = await readPixels();
await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  gi.uniforms.lemCsmReady0.value = 0; gi.uniforms.lemCsmReady1.value = 0; });
await page.waitForTimeout(1400);
const Loff = await readPixels();

const med = v => { if (!v.length) return null; const s = [...v].sort((x, y) => x - y); return +s[s.length >> 1].toFixed(1); };
const tab = {};
pick.pts.forEach((p, i) => {
  const mapSays = p.fs < 0.3 ? 'MAP:shadow' : p.fs < 0.9 ? 'MAP:penumbra' : 'MAP:lit';
  const k = `GEO:${p.cls.padEnd(4)} ${mapSays}`;
  (tab[k] = tab[k] || {n: 0, on: [], off: [], gap: [], rise: []});
  tab[k].n++; tab[k].on.push(Lon[i]); tab[k].off.push(Loff[i]);
  if (p.gap !== null) tab[k].gap.push(p.gap);
  tab[k].rise.push(p.rise);
});
const rows = Object.entries(tab).sort((x, y) => y[1].n - x[1].n).map(([k, v]) => ({
  cell: k, n: v.n, Lon: med(v.on), Loff: med(v.off),
  delta: +(med(v.off) - med(v.on)).toFixed(1),
  medGapM: med(v.gap), medRiseM: med(v.rise),
}));
console.log(JSON.stringify({cam, time, sun: pick.sun, elev: pick.elev, nPts: pick.pts.length,
  note: 'Lon = as shipped; Loff = coarse cascades gated off; delta = what the cast shadow is worth in the frame',
  rows, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
