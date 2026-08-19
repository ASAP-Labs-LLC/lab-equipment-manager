/* sh-bar.mjs — sn-bar.mjs with a `--fog` init override so the fog curve's
 * before/after can be taken back to back under one protocol. Otherwise verbatim.
 *
 * ORIGINAL HEADER FOLLOWS.
 *
 * sn-bar.mjs — the instrument nobody built: WHERE THE SHADOW BAR ACTUALLY FALLS.
 *
 * gx-tank / gy-edge sample `:concrete` pads only. A 12 m stack at 24 degrees
 * throws 27 m, most of it onto TERRAIN off the pad. This samples the GROUND —
 * terrain AND pads — classifies every sample by ray-casting at the sun, and
 * reports, in ONE page session with the stop PINNED to one value:
 *
 *   SHADOW STEP   median L of sun-occluded ground vs open ground, in stops.
 *   BAR           the same restricted to samples occluded by something >6 m
 *                 tall that is NOT vegetation — the "bar across the yard".
 *   FORM          terrain samples that are NOT occluded, binned by n·sun, so a
 *                 lit hill face and a shaded hill face can be compared without
 *                 any cast shadow in the number.
 *   BLACK POINT   frame histogram, whole frame and ground-only.
 *
 * Sweeps `gi.setFillTrim()` so the key-to-fill balance is ablated in-session
 * against the same pixels and the same stop.
 *
 *   node sn-bar.mjs [--time 9] [--trims 1,0.7,0.5,0.35] [--step 4] [--png pfx]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const cam = a.cam || 'far', time = a.time || '9';
const step = parseInt(a.step || '4', 10);
const trims = String(a.trims || '1').split(',').map(Number);
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
if (a.fog) await page.addInitScript(`window.__lemFog = ${a.fog};`);
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(11000);

/* freeze camera, then PIN the stop by assignment (never defineProperty — that
 * made gi throw every frame and report zero change for a visible effect). */
const pin = await page.evaluate((forced) => {
  const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  const gi = w.subsystems.get('gi');
  const v = forced !== null ? forced : gi._expNow;
  gi._expNow = v; gi.setExposureLocked(true);
  return {pinned: gi._expNow, locked: gi.exposureLocked,
          hasTrim: typeof gi.setFillTrim === 'function'};
}, a.pin ? Number(a.pin) : null);
await page.waitForTimeout(1200);

/* ---- geometry, once. Does not depend on the lighting. ---- */
const geo = await page.evaluate((step) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera;
  const d = gi.sunDirection.clone();
  const occ = [], ground = [];
  w.scene.traverse(o => {
    if (!o.visible || !o.geometry) return;
    if (!(o.isMesh || o.isInstancedMesh)) return;
    const n = o.name || '';
    if (/^ocean|^horizon|^weather|^sky/.test(n)) return;
    if (/^terrain/.test(n) || /:concrete$/.test(n)) ground.push(o);
    else occ.push(o);
  });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  const pts = [];
  const W = innerWidth, H = innerHeight;
  const y0 = Math.floor(H * 0.30);
  for (let sy = y0; sy < H; sy += step) for (let sx = 0; sx < W; sx += step) {
    ndc.set((sx + .5) / W * 2 - 1, -((sy + .5) / H * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(ground, false)[0];
    if (!h || !h.face) continue;
    const nm = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    if (nm.y < 0.1) continue;                         // cliff faces, skip
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.10), d, 0.05, 600);
    sr.layers.enableAll();
    const hit = sr.intersectObjects(occ, false)[0];
    let cls = 'open';
    if (hit) {
      const rise = hit.point.y - h.point.y;
      const hn = hit.object.name || '';
      if (/veg|tree|canopy|trunk|foliage|grass|shrub/i.test(hn) || hit.object.isInstancedMesh) cls = 'tree';
      else if (rise > 6) cls = 'built-tall';
      else if (rise > 1.5) cls = 'built-mid';
      else cls = 'built-low';
    }
    const isPad = /:concrete$/.test(h.object.name || '');
    pts.push({sx, sy, cls, pad: isPad,
              sun: +nm.dot(d).toFixed(3), ny: +nm.y.toFixed(3),
              dist: +h.distance.toFixed(1)});
  }
  return {pts, sun: d.toArray().map(v => +v.toFixed(4)),
          elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2), W, H};
}, step);

async function sample() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return {buf, L: await page.evaluate(async ({src, pts}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth;
    const S = pts.map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
      return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(2); });
    const all = [], gnd = [];
    const W = im.width, H = im.height;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x += 2) {
      const o = (y * W + x) * 4;
      const l = 0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2];
      all.push(l); if (y > H * 0.45) gnd.push(l);
    }
    const q = (arr, p) => { arr.sort((x, y) => x - y);
      return +arr[Math.min(arr.length - 1, Math.floor(arr.length * p))].toFixed(1); };
    const hist = arr => { const c = [...arr];
      return {min: q(c, 0), p001: q(c, 0.001), p01: q(c, 0.01), p05: q(c, 0.05),
              p50: q(c, 0.5), mean: +(c.reduce((s, v) => s + v, 0) / c.length).toFixed(1),
              p95: q(c, 0.95), p99: q(c, 0.99),
              under32: +(100 * c.filter(v => v < 32).length / c.length).toFixed(2),
              under20: +(100 * c.filter(v => v < 20).length / c.length).toFixed(2)}; };
    return {S, frame: hist(all), ground: hist(gnd)};
  }, {src, pts: geo.pts})};
}

const med = v => { if (!v.length) return null; const s = [...v].sort((x, y) => x - y); return +s[s.length >> 1].toFixed(1); };
const stops = (hi, lo) => (hi > 0 && lo > 0 ? +(Math.log2(hi / lo)).toFixed(2) : null);
/* byte -> approximate display-linear, so a "stop" is a stop and not a gamma
 * artefact. The composite writes sRGB-encoded bytes. */
const lin = v => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const medLin = v => { const m = med(v); return m === null ? null : lin(m); };

const out = [];
for (const t of trims) {
  await page.evaluate(k => window.__lemWorld.subsystems.get('gi').setFillTrim(k), t);
  await page.waitForTimeout(3500);
  const st = await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
    return {fillE: +gi._fillE.toFixed(4), keyE: +gi._keyE.toFixed(4),
            ratio: +(gi._fillE / gi._keyE).toFixed(4), giScale: +gi.giScale.toFixed(5),
            exp: +gi._expNow.toFixed(4), trim: gi._fillTrim}; });
  const {buf, L} = await sample();
  if (a.png) fs.writeFileSync(`${a.png}-t${time}-trim${t}.png`, buf);

  const bag = {};
  geo.pts.forEach((p, i) => { (bag[p.cls] = bag[p.cls] || []).push(L.S[i]); });
  const openL = bag.open || [];
  /* the BAR: tall built casters, on ground of any kind */
  const barIdx = geo.pts.map((p, i) => p.cls === 'built-tall' ? i : -1).filter(i => i >= 0);
  const barL = barIdx.map(i => L.S[i]);
  /* open ground matched to the bar's neighbourhood (same distance decile) so the
   * comparison is not bar-in-the-plant vs open-on-the-far-shore */
  const barD = barIdx.length ? med(barIdx.map(i => geo.pts[i].dist)) : null;
  const nearOpen = barD === null ? [] : geo.pts
    .map((p, i) => (p.cls === 'open' && Math.abs(p.dist - barD) < barD * 0.25) ? L.S[i] : null)
    .filter(v => v !== null);

  /* FORM: unoccluded terrain only, binned by n·sun */
  const form = {};
  geo.pts.forEach((p, i) => {
    if (p.cls !== 'open' || p.pad) return;
    const k = p.sun < 0 ? 'away' : p.sun < 0.25 ? 'graze' : p.sun < 0.5 ? 'mid' : 'facing';
    (form[k] = form[k] || []).push(L.S[i]);
  });
  const formOut = {};
  for (const k of ['facing', 'mid', 'graze', 'away'])
    if (form[k]) formOut[k] = {n: form[k].length, L: med(form[k])};
  const hillStep = (formOut.facing && formOut.away)
    ? stops(lin(formOut.facing.L), lin(formOut.away.L)) : null;

  out.push({trim: t, state: st,
    counts: Object.fromEntries(Object.entries(bag).map(([k, v]) => [k, v.length])),
    L: Object.fromEntries(Object.entries(bag).map(([k, v]) => [k, med(v)])),
    shadowStep_stops: (bag.open && (bag['built-tall'] || bag['built-mid']))
      ? stops(medLin(openL), medLin([...(bag['built-tall'] || []), ...(bag['built-mid'] || [])])) : null,
    bar: {n: barL.length, L: med(barL), nearOpenN: nearOpen.length, nearOpenL: med(nearOpen),
          stops: (barL.length && nearOpen.length) ? stops(medLin(nearOpen), medLin(barL)) : null},
    form: formOut, hillStep_stops: hillStep,
    frame: L.frame, groundHist: L.ground});
}
await page.evaluate(() => window.__lemWorld.subsystems.get('gi').setFillTrim(1));
console.log(JSON.stringify({cam, time, step, pin, sun: geo.sun, elev: geo.elev,
  samples: geo.pts.length, out, errs: errs.slice(0, 4)}, null, 1));
await b.close();
