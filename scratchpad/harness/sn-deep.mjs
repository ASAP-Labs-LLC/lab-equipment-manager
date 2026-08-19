/* sn-deep.mjs — the shadow step measured where the shadow actually IS.
 *
 * sn-bar/sn-decomp sample every geometrically-occluded ground pixel, which at
 * cam=far (0.688 m of ground per screen pixel) is mostly penumbra and mostly
 * a different substrate from the open ground it is compared with. This:
 *
 *   * keeps only samples ERODED k lattice steps inside the occluded region
 *     (BFS on the sample lattice against the GEOMETRY's boundary), and
 *   * matches the open comparison set to the SAME substrate (pad vs terrain)
 *     and the same distance band, and
 *   * decomposes each set into key and fill by a sun-off pass, so the number
 *     is "how much of the key does the shadow actually remove", not a byte
 *     difference between two different materials.
 *
 *   node sn-deep.mjs [--time 9] [--step 4] [--erode 2]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const time = a.time || '9', step = parseInt(a.step || '4', 10);
const erode = parseInt(a.erode || '2', 10);
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
  gi.setExposureLocked(true);
  window.__snSave = {sunI: gi.sunIntensity};
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
    if (nm.y < 0.90) continue;
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.10), d, 0.05, 600);
    sr.layers.enableAll();
    const hit = sr.intersectObjects(occ, false)[0];
    let cls = 'open';
    if (hit) {
      const rise = hit.point.y - h.point.y, hn = hit.object.name || '';
      if (/veg|tree|canopy|trunk|foliage/i.test(hn) || hit.object.isInstancedMesh) cls = 'tree';
      else if (rise > 6) cls = 'tall'; else cls = 'low';
    }
    pts.push({sx, sy, cls, pad: /:concrete$/.test(h.object.name || ''), dist: +h.distance.toFixed(1)});
  }
  return {pts, elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2)};
}, step);

/* BFS erosion on the lattice: distance in samples from the nearest sample of
 * the opposite occlusion state. */
const key = (x, y) => x + ',' + y;
const idx = new Map(geo.pts.map((p, i) => [key(p.sx, p.sy), i]));
const occl = geo.pts.map(p => p.cls !== 'open');
const dist = new Array(geo.pts.length).fill(99);
{
  const q = [];
  geo.pts.forEach((p, i) => {
    for (const [dx, dy] of [[step, 0], [-step, 0], [0, step], [0, -step]]) {
      const j = idx.get(key(p.sx + dx, p.sy + dy));
      if (j === undefined || occl[j] !== occl[i]) { dist[i] = 0; q.push(i); break; }
    }
  });
  for (let h = 0; h < q.length; h++) {
    const i = q[h], p = geo.pts[i];
    for (const [dx, dy] of [[step, 0], [-step, 0], [0, step], [0, -step]]) {
      const j = idx.get(key(p.sx + dx, p.sy + dy));
      if (j === undefined || dist[j] !== 99 || occl[j] !== occl[i]) continue;
      dist[j] = dist[i] + 1; q.push(j);
    }
  }
}
const deepTall = geo.pts.map((p, i) => (p.cls === 'tall' && dist[i] >= erode) ? i : -1).filter(i => i >= 0);
const tallPad = deepTall.filter(i => geo.pts[i].pad).length;
const wantPad = tallPad * 2 > deepTall.length;
const dband = (() => { const v = deepTall.map(i => geo.pts[i].dist).sort((x, y) => x - y);
  return v.length ? v[v.length >> 1] : null; })();
const deepOpen = geo.pts.map((p, i) => (p.cls === 'open' && dist[i] >= erode && p.pad === wantPad &&
  dband && Math.abs(p.dist - dband) < dband * 0.30) ? i : -1).filter(i => i >= 0);

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
const medl = (L, ix) => { if (!ix.length) return null;
  const v = ix.map(i => lin(L[i])).sort((x, y) => x - y); return v[v.length >> 1]; };

const res = {};
for (const [tag, s] of [['fog-on', {fog: 1}], ['fog-off', {fog: 0}]]) {
  await page.evaluate(s => { const sky = window.__lemWorld.subsystems.get('sky');
    if (sky && sky.setFogDensity) sky.setFogDensity(s.fog ? null : 1e-9); }, s);
  await page.waitForTimeout(2500);
  const on = {};
  for (const [sub, k] of [['sun-on', 1], ['sun-off', 0]]) {
    await page.evaluate(k => { const gi = window.__lemWorld.subsystems.get('gi');
      gi.sunIntensity = window.__snSave.sunI * k; if (gi.sun) gi.sun.intensity = gi.sunIntensity; }, k);
    await page.waitForTimeout(2200);
    const L = await read();
    on[sub] = {tall: medl(L, deepTall), open: medl(L, deepOpen)};
  }
  await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
    gi.sunIntensity = window.__snSave.sunI; if (gi.sun) gi.sun.intensity = gi.sunIntensity; });
  const kt = on['sun-on'].tall - on['sun-off'].tall;
  const ko = on['sun-on'].open - on['sun-off'].open;
  res[tag] = {
    tallLit: +on['sun-on'].tall.toFixed(5), tallFill: +on['sun-off'].tall.toFixed(5),
    openLit: +on['sun-on'].open.toFixed(5), openFill: +on['sun-off'].open.toFixed(5),
    keyInShadow: +kt.toFixed(5), keyInOpen: +ko.toFixed(5),
    keyLeakPct: +(100 * kt / ko).toFixed(1),
    stepStops: +Math.log2(on['sun-on'].open / on['sun-on'].tall).toFixed(2),
    stepStopsIfNoLeak: +Math.log2(on['sun-on'].open / on['sun-off'].tall).toFixed(2),
  };
}
console.log(JSON.stringify({time, elev: geo.elev, pin, erode, step,
  n: {deepTall: deepTall.length, deepOpen: deepOpen.length, tallOnPadPct: +(100 * tallPad / Math.max(1, deepTall.length)).toFixed(0), matchedPad: wantPad},
  res, errs: errs.slice(0, 4)}, null, 1));
await b.close();
