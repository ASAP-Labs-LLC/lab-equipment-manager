/* tk-sweep.mjs — one page load, N material variants, the same pixels each time.
 *
 * Picks the tank-shell and pad samples ONCE off a raycast (geometry never
 * changes), then for each variant mutates only live material scalars, waits,
 * screenshots and re-reads the same screen positions. Everything is therefore a
 * within-session delta: no other module's round can move it.
 *
 *   node tk-sweep.mjs [--cam far] [--time 9]
 */
import {chromium} from 'playwright';
import fs from 'fs';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
          + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(9000);
const meta = await page.evaluate(() => {
  const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  return {buildStable: window.__buildStable !== false,
          toneMapping: w.ctx.renderer.toneMapping,
          toneExposure: +w.ctx.renderer.toneMappingExposure.toFixed(3)};
});
await page.waitForTimeout(400);

const uids = (a.uids || 'pac-flash-1,pac-flash-2').split(',');
const pick = await page.evaluate((uids) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), B = w.subsystems.get('buildings');
  const THREE = w.ctx.THREE, cam = w.camera;
  const sunDir = gi.sun.position.clone().normalize();
  const occluders = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && o.name.includes(':')) occluders.push(o); });
  const shell = [], pad = [];
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2(), pv = new THREE.Vector3();
  for (const uid of uids) {
    const site = B.sites.get(uid); if (!site) continue;
    const targets = []; site.root.traverse(o => { if (o.isMesh && o.visible) targets.push(o); });
    const c = site.root.position;
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const dx of [-60, 0, 60]) for (const dz of [-60, 0, 60]) for (const dy of [0, 30]) {
      pv.set(c.x + dx, c.y + dy, c.z + dz).project(cam);
      x0 = Math.min(x0, (pv.x * .5 + .5) * innerWidth); x1 = Math.max(x1, (pv.x * .5 + .5) * innerWidth);
      y0 = Math.min(y0, (-pv.y * .5 + .5) * innerHeight); y1 = Math.max(y1, (-pv.y * .5 + .5) * innerHeight);
    }
    for (let sy = Math.max(0, Math.floor(y0)); sy <= Math.min(innerHeight - 1, Math.ceil(y1)); sy++) {
      for (let sx = Math.max(0, Math.floor(x0)); sx <= Math.min(innerWidth - 1, Math.ceil(x1)); sx++) {
        ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
        rc.setFromCamera(ndc, cam);
        const h = rc.intersectObjects(targets, false)[0];
        if (!h || !h.face) continue;
        const n = h.face.normal.clone().applyNormalMatrix(
          new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
        const rel = h.point.y - site.root.position.y;
        const NL = n.dot(sunDir);
        if (Math.abs(n.y) < 0.30 && rel > 2.5 && /:(steel|rust)$/.test(h.object.name)) {
          /* is the SHELL point itself in sun? a shell pixel shaded by a
           * neighbouring tank is not a terminator sample */
          const sr = new THREE.Raycaster(h.point.clone().addScaledVector(sunDir, 0.05),
                                         sunDir, 0.02, 400);
          sr.layers.enableAll();
          shell.push({sx, sy, NL: +NL.toFixed(3), rust: /:rust$/.test(h.object.name),
                      sunlit: sr.intersectObjects(occluders, false).length === 0});
        } else if (n.y > 0.9 && rel < 3.0 && /:concrete$/.test(h.object.name)) {
          const sr = new THREE.Raycaster(h.point.clone().addScaledVector(sunDir, 0.05),
                                         sunDir, 0.02, 400);
          sr.layers.enableAll();
          pad.push({sx, sy, occ: sr.intersectObjects(occluders, false).length > 0});
        }
      }
    }
  }
  return {shell, pad, sun: [sunDir.x, sunDir.y, sunDir.z].map(v => +v.toFixed(3))};
}, uids);

async function readPixels() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, shell, pad}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const rd = p => { const o = (p.sy * im.width + p.sx) * 4;
      return [d[o], d[o + 1], d[o + 2]]; };
    return {shell: shell.map(rd), pad: pad.map(rd)};
  }, {src, shell: pick.shell, pad: pick.pad});
}
const L = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
const mean = v => v.length ? v.reduce((x, y) => x + y, 0) / v.length : null;
function stats(px) {
  const sh = pick.shell.map((p, i) => ({...p, L: L(px.shell[i]), rgb: px.shell[i]}))
    .filter(p => !p.rust && p.sunlit);
  const lit = sh.filter(p => p.NL > 0.35).map(p => p.L);
  const dark = sh.filter(p => p.NL < -0.15).map(p => p.L);
  const mid = sh.filter(p => p.NL >= -0.15 && p.NL <= 0.35).map(p => p.L);
  const pdOpen = pick.pad.map((p, i) => ({...p, L: L(px.pad[i])})).filter(p => !p.occ).map(p => p.L);
  const pdOcc = pick.pad.map((p, i) => ({...p, L: L(px.pad[i])})).filter(p => p.occ).map(p => p.L);
  const shellRGB = [0, 1, 2].map(k => mean(sh.map(p => p.rgb[k])));
  const clip = sh.filter(p => p.L > 200).length / Math.max(1, sh.length);
  return {
    n: sh.length,
    litMeanL: +mean(lit).toFixed(1), midMeanL: +mean(mid).toFixed(1),
    darkMeanL: +mean(dark).toFixed(1),
    formSpread: +(mean(lit) - mean(dark)).toFixed(1),
    formRatio: +(mean(dark) / mean(lit)).toFixed(3),
    shellMeanL: +mean(sh.map(p => p.L)).toFixed(1),
    shellRGB: shellRGB.map(v => +v.toFixed(1)),
    shellSat: +(Math.max(...shellRGB) - Math.min(...shellRGB)).toFixed(1),
    pctOver200: +(clip * 100).toFixed(1),
    padOpenL: +mean(pdOpen).toFixed(1), padOccL: +mean(pdOcc).toFixed(1),
    padShadowDepth: +(mean(pdOpen) - mean(pdOcc)).toFixed(1),
    shellMinusPad: +(mean(sh.map(p => p.L)) - mean(pdOpen)).toFixed(1),
  };
}

const variants = JSON.parse(a.variants || JSON.stringify([
  {name: 'baseline'},
  {name: 'albedo x0.80', mul: 0.80},
  {name: 'albedo x0.65', mul: 0.65},
  {name: 'albedo x0.50', mul: 0.50},
  {name: 'albedo x0.65 env0', mul: 0.65, env: 0},
  {name: 'albedo x0.50 cool', mul: 0.50, tint: [0.94, 0.99, 1.06]},
]));
const out = [];
for (const v of variants) {
  await page.evaluate(({uids, v}) => {
    const B = window.__lemWorld.subsystems.get('buildings');
    for (const uid of uids) {
      const s = B.sites.get(uid); if (!s) continue;
      for (const key of ['steel', 'steelDetail']) {
        const m = s.materials[key]; if (!m) continue;
        if (!m.userData.tkBase) m.userData.tkBase = m.color.clone();
        m.color.copy(m.userData.tkBase);
        if (v.mul) m.color.multiplyScalar(v.mul);
        if (v.tint) { m.color.r *= v.tint[0]; m.color.g *= v.tint[1]; m.color.b *= v.tint[2]; }
        if (v.metal !== undefined) {
          if (m.userData.tkM === undefined) m.userData.tkM = m.metalness;
          m.metalness = m.userData.tkM * v.metal;
        } else if (m.userData.tkM !== undefined) m.metalness = m.userData.tkM;
        if (v.rough !== undefined) {
          if (m.userData.tkR === undefined) m.userData.tkR = m.roughness;
          m.roughness = m.userData.tkR * v.rough;
        } else if (m.userData.tkR !== undefined) m.roughness = m.userData.tkR;
        if (v.env !== undefined) {
          if (m.userData.tkEnv === undefined) m.userData.tkEnv = m.envMapIntensity;
          m.envMapIntensity = v.env;
        } else if (m.userData.tkEnv !== undefined) m.envMapIntensity = m.userData.tkEnv;
        m.needsUpdate = false;
      }
    }
  }, {uids, v});
  await page.waitForTimeout(1200);
  out.push({variant: v.name, ...stats(await readPixels())});
}
console.log(JSON.stringify({cam, time, meta, sun: pick.sun,
  shellSamples: pick.shell.length, padSamples: pick.pad.length,
  rows: out, pageErrors: errs}, null, 1));
await b.close();
