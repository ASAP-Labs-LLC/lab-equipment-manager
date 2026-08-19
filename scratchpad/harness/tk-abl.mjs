/* tk-abl.mjs — the in-session ablation the cross-session deltas cannot give.
 *
 * ONE page load. Sample the bund/pad, note which points the SUN is
 * geometrically blocked from by the tanks, screenshot; then hide the tank shell
 * meshes, let the cascades redraw, screenshot again, and read the SAME pixels.
 *
 *   pad L rises when the tank is hidden  -> a cast shadow exists, that deep
 *   pad L does not move                  -> the tank casts nothing onto its pad
 *
 *   node tk-abl.mjs [--cam far] [--time 9] [--stem /tmp/tk-abl]
 */
import {chromium} from 'playwright';
import fs from 'fs';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9', stem = a.stem || '/tmp/tk-abl';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
          + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const W = 1600, H = 900;
const page = await b.newPage({viewport: {width: W, height: H}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(9000);
const meta = await page.evaluate(() => {
  const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  const gi = w.subsystems.get('gi');
  return {buildStable: window.__buildStable !== false,
          nearRadius: gi._shadowFit ? +gi._shadowFit.radius.toFixed(1) : null,
          nearCentre: gi._shadowFit ? [gi._shadowFit.centre.x, gi._shadowFit.centre.y,
                                       gi._shadowFit.centre.z].map(v => +v.toFixed(0)) : null};
});
await page.waitForTimeout(500);

/* --- pass 1: pick the sample points off a raycast, and record their pixels --- */
const uids = (a.uids || 'pac-flash-1,pac-flash-2').split(',');
const pick = await page.evaluate((uids) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), B = w.subsystems.get('buildings');
  const THREE = w.ctx.THREE; const cam = w.camera;
  const sunDir = gi.sun.position.clone().normalize();
  const occluders = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && o.name.includes(':')) occluders.push(o); });
  const out = {sun: [sunDir.x, sunDir.y, sunDir.z].map(v => +v.toFixed(3)), pts: []};
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  for (const uid of uids) {
    const site = B.sites.get(uid); if (!site) continue;
    const targets = []; site.root.traverse(o => { if (o.isMesh && o.visible) targets.push(o); });
    const c = site.root.position;
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    const pv = new THREE.Vector3();
    for (const dx of [-60, 0, 60]) for (const dz of [-60, 0, 60]) for (const dy of [0, 30]) {
      pv.set(c.x + dx, c.y + dy, c.z + dz).project(cam);
      const sx = (pv.x * .5 + .5) * innerWidth, sy = (-pv.y * .5 + .5) * innerHeight;
      x0 = Math.min(x0, sx); x1 = Math.max(x1, sx); y0 = Math.min(y0, sy); y1 = Math.max(y1, sy);
    }
    for (let sy = Math.max(0, Math.floor(y0)); sy <= Math.min(innerHeight - 1, Math.ceil(y1)); sy++) {
      for (let sx = Math.max(0, Math.floor(x0)); sx <= Math.min(innerWidth - 1, Math.ceil(x1)); sx++) {
        ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
        rc.setFromCamera(ndc, cam);
        const hits = rc.intersectObjects(targets, false);
        if (!hits.length || !hits[0].face) continue;
        const h = hits[0];
        const n = h.face.normal.clone().applyNormalMatrix(
          new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
        const rel = h.point.y - site.root.position.y;
        if (!(n.y > 0.9 && rel < 3.0 && /:concrete$/.test(h.object.name))) continue;
        const orig = h.point.clone().addScaledVector(sunDir, 0.05);
        const sr = new THREE.Raycaster(orig, sunDir, 0.02, 400); sr.layers.enableAll();
        const hit = sr.intersectObjects(occluders, false)[0];
        out.pts.push({uid, sx, sy, occ: !!hit,
                      by: hit ? hit.object.name : null,
                      d: hit ? +hit.distance.toFixed(1) : null});
      }
    }
  }
  return out;
}, uids);

async function sample(tag) {
  const buf = await page.screenshot({type: 'png'});
  fs.writeFileSync(`${stem}-${tag}.png`, buf);
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, pts}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    return pts.map(p => {
      const o = (p.sy * im.width + p.sx) * 4;
      return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(2);
    });
  }, {src, pts: pick.pts});
}

const A = await sample('on');
/* --- hide the tank shells; keep everything else --- */
const hid = await page.evaluate((uids) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), B = w.subsystems.get('buildings');
  let n = 0;
  for (const uid of uids) {
    const s = B.sites.get(uid); if (!s) continue;
    for (const m of s.meshes) if (/:(steel|rust)$/.test(m.name)) { m.visible = false; n++; }
  }
  for (const c of gi._csm) c.dirty = true;
  if (w.ctx.engine) w.ctx.engine.shadowNeedsUpdate = true;
  return n;
}, uids);
await page.waitForTimeout(4000);
const Bv = await sample('off');

const groups = {};
pick.pts.forEach((p, i) => {
  const k = `${p.uid}|${p.occ ? 'occluded' : 'open'}`;
  (groups[k] || (groups[k] = [])).push(Bv[i] - A[i]);
});
const stat = arr => {
  const s = arr.slice().sort((x, y) => x - y);
  return {n: s.length, mean: +(s.reduce((x, y) => x + y, 0) / s.length).toFixed(2),
          p10: +s[Math.floor(s.length * .1)].toFixed(2), p50: +s[Math.floor(s.length * .5)].toFixed(2),
          p90: +s[Math.floor(s.length * .9)].toFixed(2), max: +s[s.length - 1].toFixed(2)};
};
const by = {};
for (const p of pick.pts) if (p.by) by[p.by] = (by[p.by] || 0) + 1;
console.log(JSON.stringify({cam, time, meta, hiddenMeshes: hid, sun: pick.sun,
  samples: pick.pts.length, occluderNames: by,
  deltaLWhenTanksHidden: Object.fromEntries(
    Object.entries(groups).map(([k, v]) => [k, stat(v)])),
  pageErrors: errs}, null, 1));
await b.close();
