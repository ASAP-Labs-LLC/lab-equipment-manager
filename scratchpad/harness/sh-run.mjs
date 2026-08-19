/* sh-run.mjs — one page session, one fog curve, every number this round is
 * judged on, at a stop the CALLER pins so two page loads are two fog curves and
 * not two stops.
 *
 * The classification is sn-decomp's, verbatim, so `stops` here is comparable to
 * sn-decomp's `base` row. What it adds:
 *
 *   - the DISTANCE of every shadow sample, so "the haze over the subject" has a
 *     number instead of an adjective;
 *   - the per-pixel fog factor, evaluated from the COMPILED chunk read back out
 *     of THREE.ShaderChunk in the live page, so the instrument cannot drift
 *     from the thing it measures;
 *   - the frame's black point (p1/p5/%under) — constraint 2;
 *   - the land ladder by true distance band — constraint 1;
 *   - the free-running exposure gi WOULD have chosen, read before the pin, so
 *     the meter's give-back is visible instead of hidden.
 *
 *   node sh-run.mjs --tag base --pin 3.20 [--fog '{"p":3.25,"h":900}'] [--dens 6.377e-4]
 *                   [--cam far] [--time 9] [--png out.png] [--json out.json]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const cam = a.cam || 'far', time = a.time || '9', step = parseInt(a.step || '5', 10);
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
/* the shape constants are compiled into every material before the first one
 * exists, so they can only be set before the module runs. */
if (a.fog) await page.addInitScript(o => { globalThis.__lemFog = o; }, JSON.parse(a.fog));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(11000);

const pin = await page.evaluate(({pinv, dens}) => {
  const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  const gi = w.subsystems.get('gi'), sky = w.subsystems.get('sky');
  const free = gi._expNow;                 // what the meter chose for THIS haze
  gi._expNow = pinv == null ? free : pinv; // ASSIGN, never defineProperty
  gi.setExposureLocked(true);
  if (dens != null && sky && sky.setFogDensity) sky.setFogDensity(dens);
  return {free: +free.toFixed(4), pinned: +gi._expNow.toFixed(4),
          locked: gi.exposureLocked, density: w.scene.fog.density};
}, {pinv: a.pin ? Number(a.pin) : null, dens: a.dens ? Number(a.dens) : null});
await page.waitForTimeout(2500);

/* ---- geometry + the compiled chunk's own fog factor, once ---- */
const geo = await page.evaluate((step) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera;
  const d = gi.sunDirection.clone();
  const occ = [], ground = [];
  w.scene.traverse(o => {
    if (!o.visible || !o.geometry || !(o.isMesh || o.isInstancedMesh)) return;
    const n = o.name || '';
    if (/^ocean|^horizon|^weather|^sky/.test(n)) return;
    if (/^terrain/.test(n) || /:concrete$/.test(n)) ground.push(o); else occ.push(o);
  });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2(); const pts = [];
  const W = innerWidth, H = innerHeight;
  const fwd = new THREE.Vector3(); cam.getWorldDirection(fwd);
  for (let sy = Math.floor(H * 0.30); sy < H; sy += step) for (let sx = 0; sx < W; sx += step) {
    ndc.set((sx + .5) / W * 2 - 1, -((sy + .5) / H * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(ground, false)[0];
    if (!h || !h.face) continue;
    const nm = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    if (nm.y < 0.85) continue;                 // near-flat ground only
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.10), d, 0.05, 600);
    sr.layers.enableAll();
    const hit = sr.intersectObjects(occ, false)[0];
    let cls = 'open';
    if (hit) {
      const rise = hit.point.y - h.point.y, hn = hit.object.name || '';
      if (/veg|tree|canopy|trunk|foliage/i.test(hn) || hit.object.isInstancedMesh) cls = 'tree';
      else if (rise > 6) cls = 'tall'; else cls = 'low';
    }
    /* view depth is what the shader uses, not the ray length */
    const rel = h.point.clone().sub(cam.position);
    pts.push({sx, sy, cls, dist: +h.distance.toFixed(1),
              dep: +rel.dot(fwd).toFixed(1), wy: +h.point.y.toFixed(2)});
  }
  return {pts, elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2),
          camY: +cam.position.y.toFixed(2),
          chunk: w.ctx.THREE.ShaderChunk.fog_fragment,
          fogColour: [w.scene.fog.color.r, w.scene.fog.color.g, w.scene.fog.color.b]};
}, step);

/* evaluate the COMPILED chunk (parsed out of the string, not copied from source) */
const src = geo.chunk;
const num = re => { const m = src.match(re); return m ? Number(m[1]) : null; };
const HH = num(/exp\(\s*-\s*max\(\s*lemH0,\s*-600\.0\s*\)\s*\/\s*([0-9.]+)\s*\)/);
const PP = num(/pow\(\s*max\(\s*lemTau,\s*1e-5\s*\),\s*([0-9.]+)\s*\)/);
const MM = num(/vec3\(\s*([0-9.]+)\s*\)\s*\n?\s*\*\s*\(\s*1\.0\s*-\s*exp/);
const KK = (src.match(/exp\(\s*-\s*lem\w*\s*\*\s*vec3\(\s*([0-9.,\s]+)\)/) || [])[1];
const K = KK ? KK.split(',').map(Number) : [1, 1, 1];
/* the scale and the shell, if this build has them */
const SS = num(/lemTau\s*=\s*fogDensity\s*\*\s*([0-9.]+)\s*\*\s*vFogDepth/) ?? 1;
const T0 = num(/lemX\s*=\s*lemTau\s*-\s*([0-9.]+)/)
        ?? num(/lemTau\s*=\s*max\(\s*lemTau\s*-\s*([0-9.]+)/) ?? 0;
const SW = num(/\+\s*([0-9.]+)\s*\*\s*log\(\s*1\.0\s*\+\s*exp/) ?? 0;
const dens = pin.density;
function fac(dep, h1) {
  const A = Math.exp(-Math.max(geo.camY, -600) / HH);
  const B = Math.exp(-Math.max(h1, -600) / HH);
  const dy = h1 - geo.camY;
  let avg = Math.abs(dy) < 1 ? 0.5 * (A + B) : HH * (A - B) / dy;
  avg = Math.min(Math.max(avg, 0), 6);
  let tau = dens * SS * dep * avg;
  if (T0 > 0) { const x = tau - T0;
    tau = SW > 0 ? Math.max(x, 0) + SW * Math.log(1 + Math.exp(-Math.abs(x) / SW))
                 : Math.max(x, 0); }
  const u = Math.pow(Math.max(tau, 1e-5), PP);
  const f = K.map(k => MM * (1 - Math.exp(-u * k)));
  return +(0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]).toFixed(4);
}
for (const p of geo.pts) p.f = fac(p.dep, p.wy);

const tallD = (() => { const v = geo.pts.filter(p => p.cls === 'tall').map(p => p.dist).sort((x, y) => x - y);
  return v.length ? v[v.length >> 1] : null; })();
const sets = {
  tall: geo.pts.map((p, i) => p.cls === 'tall' ? i : -1).filter(i => i >= 0),
  open: geo.pts.map((p, i) => (p.cls === 'open' && tallD && Math.abs(p.dist - tallD) < tallD * 0.25) ? i : -1).filter(i => i >= 0),
};

const shot = await page.screenshot({type: 'png'});
if (a.png) fs.writeFileSync(a.png, shot);
const px = await page.evaluate(async ({src, pts}) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  const sc = im.width / innerWidth;
  const S = pts.map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
    return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(2); });
  const all = [];
  for (let y = 0; y < im.height; y++) for (let x = 0; x < im.width; x += 2) {
    const o = (y * im.width + x) * 4;
    all.push(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]);
  }
  all.sort((x, y) => x - y);
  const q = p => +all[Math.min(all.length - 1, Math.floor(all.length * p))].toFixed(1);
  return {S, frame: {p001: q(0.001), p01: q(0.01), p05: q(0.05), p50: q(0.5),
    mean: +(all.reduce((s, v) => s + v, 0) / all.length).toFixed(1), p95: q(0.95), p99: q(0.99),
    under20: +(100 * all.filter(v => v < 20).length / all.length).toFixed(2),
    under32: +(100 * all.filter(v => v < 32).length / all.length).toFixed(2)}};
}, {src: 'data:image/png;base64,' + shot.toString('base64'), pts: geo.pts});

const lin = v => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const med = arr => { if (!arr.length) return null; const s = [...arr].sort((x, y) => x - y); return +s[s.length >> 1].toFixed(1); };
const statL = ix => med(ix.map(i => px.S[i]));
const tl = statL(sets.tall), op = statL(sets.open);

/* the ladder: open, flat ground by true distance */
const EDGES = [300, 450, 600, 750, 900, 1100, 1400, 1800, 2600];
const ladder = [];
for (let i = 0; i < EDGES.length - 1; i++) {
  const ix = geo.pts.map((p, j) => (p.dist >= EDGES[i] && p.dist < EDGES[i + 1]) ? j : -1).filter(j => j >= 0);
  if (ix.length < 20) continue;
  ladder.push({band: `${EDGES[i]}-${EDGES[i + 1]}`, n: ix.length,
    L: med(ix.map(j => px.S[j])),
    f: +(ix.reduce((s, j) => s + geo.pts[j].f, 0) / ix.length).toFixed(4)});
}
/* the haze over the shadow samples themselves */
const fq = ix => { const v = ix.map(i => geo.pts[i].f).sort((x, y) => x - y);
  const d = ix.map(i => geo.pts[i].dist).sort((x, y) => x - y);
  return {fp10: +v[v.length / 10 | 0].toFixed(4), fmed: +v[v.length >> 1].toFixed(4),
          fp90: +v[v.length * 9 / 10 | 0].toFixed(4),
          dp10: d[d.length / 10 | 0], dmed: d[d.length >> 1], dp90: d[d.length * 9 / 10 | 0]}; };

const res = {tag: a.tag || 'run', cam, time, chunk: {H: HH, P: PP, MAX: MM, K, S: SS, t0: T0, w: SW},
  pin, elev: geo.elev, camY: geo.camY, fogColour: geo.fogColour.map(v => +v.toFixed(4)),
  n: {tall: sets.tall.length, open: sets.open.length},
  tallL: tl, openL: op, stops: (tl && op) ? +Math.log2(lin(op) / lin(tl)).toFixed(3) : null,
  haze: {tall: fq(sets.tall), open: fq(sets.open)},
  frame: px.frame, ladder, errs: errs.slice(0, 4)};
console.log(JSON.stringify(res, null, 1));
if (a.json) fs.writeFileSync(a.json, JSON.stringify(res, null, 1));
await b.close();
