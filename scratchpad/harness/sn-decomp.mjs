/* sn-decomp.mjs — WHAT IS ACTUALLY FILLING THE SHADOW BAR.
 *
 * sn-bar.mjs measured the bar at 0.83 stops and showed that cutting gi's fill
 * 6.7x (ratio 0.2551 -> 0.0383) buys only +0.18 stops. So the fill is not the
 * limiter. This decomposes the shadowed pixel into its terms by ablating them
 * one at a time over the SAME pixel sets in ONE session with the stop pinned:
 *
 *   sun      gi.sunIntensity -> 0        (what is left is fill + everything else)
 *   fill     gi.setFillTrim(0.02)        (the probe/irradiance term)
 *   fog      sky.setFogDensity(1e-9)     (aerial perspective, added after light)
 *   env      scene.environment -> null   (IBL specular)
 *
 *   node sn-decomp.mjs [--time 9] [--step 6]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const time = a.time || '9', step = parseInt(a.step || '6', 10);
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather'
  + `&cam=${a.cam || 'far'}&time=${time}&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(11000);
const pin = await page.evaluate(() => {
  const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  const gi = w.subsystems.get('gi');
  gi._expNow = gi._expNow; gi.setExposureLocked(true);
  window.__snSave = {sunI: gi.sunIntensity, env: w.scene.environment};
  return {pinned: gi._expNow};
});
await page.waitForTimeout(1200);

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
    pts.push({sx, sy, cls, dist: +h.distance.toFixed(1)});
  }
  return {pts, elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2)};
}, step);

const tallD = (() => { const v = geo.pts.filter(p => p.cls === 'tall').map(p => p.dist).sort((x, y) => x - y);
  return v.length ? v[v.length >> 1] : null; })();
const sets = {
  tall: geo.pts.map((p, i) => p.cls === 'tall' ? i : -1).filter(i => i >= 0),
  open: geo.pts.map((p, i) => (p.cls === 'open' && tallD && Math.abs(p.dist - tallD) < tallD * 0.25) ? i : -1).filter(i => i >= 0),
};

async function read() {
  const buf = await page.screenshot({type: 'png'});
  return await page.evaluate(async ({src, pts}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth;
    return pts.map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
      return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(2); });
  }, {src: 'data:image/png;base64,' + buf.toString('base64'), pts: geo.pts});
}
const lin = v => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const med = ix => { if (!ix.length) return null; return null; };

const STATES = [
  ['base',        {sun: 1, fill: 1,    fog: 1, env: 1}],
  ['no-sun',      {sun: 0, fill: 1,    fog: 1, env: 1}],
  ['no-fill',     {sun: 1, fill: 0.02, fog: 1, env: 1}],
  ['no-fog',      {sun: 1, fill: 1,    fog: 0, env: 1}],
  ['no-env',      {sun: 1, fill: 1,    fog: 1, env: 0}],
  ['no-fog+fill', {sun: 1, fill: 0.02, fog: 0, env: 1}],
  ['no-fog-env-fill', {sun: 1, fill: 0.02, fog: 0, env: 0}],
];
const out = [];
for (const [tag, s] of STATES) {
  await page.evaluate(s => {
    const w = window.__lemWorld, gi = w.subsystems.get('gi'), sky = w.subsystems.get('sky');
    gi.setFillTrim(s.fill);
    gi.sunIntensity = window.__snSave.sunI * s.sun;
    if (gi.sun) gi.sun.intensity = gi.sunIntensity;
    if (sky && sky.setFogDensity) sky.setFogDensity(s.fog ? null : 1e-9);
    w.scene.environment = s.env ? window.__snSave.env : null;
    w.scene.traverse(o => { if (o.material) { const m = Array.isArray(o.material) ? o.material : [o.material];
      for (const x of m) x.needsUpdate = true; } });
  }, s);
  await page.waitForTimeout(3000);
  const L = await read();
  const stat = ix => { if (!ix.length) return null;
    const v = ix.map(i => L[i]).sort((x, y) => x - y);
    return +v[v.length >> 1].toFixed(1); };
  const tl = stat(sets.tall), op = stat(sets.open);
  out.push({tag, tallL: tl, openL: op,
            stops: (tl && op) ? +Math.log2(lin(op) / lin(tl)).toFixed(2) : null,
            linTall: tl === null ? null : +lin(tl).toFixed(5),
            linOpen: op === null ? null : +lin(op).toFixed(5)});
}
console.log(JSON.stringify({time, elev: geo.elev, pin,
  n: {tall: sets.tall.length, open: sets.open.length}, out, errs: errs.slice(0, 4)}, null, 1));
await b.close();
