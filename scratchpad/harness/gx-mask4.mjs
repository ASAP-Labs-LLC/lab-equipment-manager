/* gx-mask4.mjs — the mask measurement, with the ablations NAILED DOWN and the
 * instrument policing itself.
 *
 * TWO BUGS IN THE EARLIER PROBES, both of which produced confident wrong
 * numbers and both of which are worth keeping written down:
 *
 * 1. THE ABLATION WAS BEING REVERTED. gi's `onTime` fires off the world clock,
 *    calls `_readSky` -> `_fitFill`, and `_fitFill` writes `lemGIStrength` and
 *    `_readSky` writes `sun.intensity`. Any probe that assigns those once and
 *    then takes ten screenshots over thirty seconds is measuring a world that
 *    quietly put the fill back somewhere in the middle. gx-mask2 caught itself:
 *    two trials with byte-identical uniforms read 58.6 and 76.0. Here every
 *    ablated field is replaced with a get/set property that ignores writes, and
 *    every shot re-reads the live values and reports them, so a reverted state
 *    is visible in the output instead of being averaged into the answer.
 *
 * 2. THE PIXELS WERE NOT THE PIXELS. Raycasting only against the pad meshes
 *    accepts a pixel with a tank standing in front of it — the ray reaches the
 *    concrete behind. Deep-shadow points are exactly the ones most likely to
 *    have their caster in front of them, so the caster's sunlit flank was being
 *    read as "shadowed ground". The ray is now cast against everything and the
 *    first hit must be the pad.
 *
 *   node gx-mask4.mjs [--cam far] [--time 9]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
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

/* ---- pin everything that drifts, with properties that swallow writes ---- */
await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), scene = w.scene;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  const pin = (obj, key, initial) => {
    let v = initial;
    Object.defineProperty(obj, key, {configurable: true,
      get: () => v, set: () => {}, });
    return nv => { v = nv; };
  };
  window.__gx = {};
  /* the stop */
  if (typeof gi.setExposureLocked === 'function') { gi.setExposureLocked(true); window.__gx.lock = 'api'; }
  else { gi._applyGrade = () => {}; window.__gx.lock = 'stub'; }
  /* the cascades: no redraw, and the ready gates under our control */
  gi._serviceCascades = () => {};
  window.__gx.setReady0 = pin(gi.uniforms.lemCsmReady0, 'value', 1);
  window.__gx.setReady1 = pin(gi.uniforms.lemCsmReady1, 'value', 1);
  /* the fill */
  window.__gx.giBase = gi.uniforms.lemGIStrength.value;
  window.__gx.setGI = pin(gi.uniforms.lemGIStrength, 'value', window.__gx.giBase);
  window.__gx.envBase = scene.environment;
  window.__gx.setEnv = pin(scene, 'environment', window.__gx.envBase);
  /* the key */
  window.__gx.sunBase = gi.sun.intensity;
  window.__gx.setSun = pin(gi.sun, 'intensity', window.__gx.sunBase);
  /* the two biases, mutated in place by _renderCascade — which is now stubbed,
   * but pin the vectors' identity anyway by keeping our own copies */
  window.__gx.p0 = gi.uniforms.lemCsmParam0.value.clone();
  window.__gx.p1 = gi.uniforms.lemCsmParam1.value.clone();
});
await page.waitForTimeout(1500);

const pick = await page.evaluate(() => {
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
    const par = window.__gx[`p${m.i}`];
    const X = P.x + N3.x * par.w, Y = P.y + N3.y * par.w, Z = P.z + N3.z * par.w;
    const cw = e[3] * X + e[7] * Y + e[11] * Z + e[15];
    const px = (e[0] * X + e[4] * Y + e[8] * Z + e[12]) / cw;
    const py = (e[1] * X + e[5] * Y + e[9] * Z + e[13]) / cw;
    const pz = (e[2] * X + e[6] * Y + e[10] * Z + e[14]) / cw;
    if (px < 0 || px > 1 || py < 0 || py > 1 || pz > 1 || pz < 0) return 1;
    const dd = pz - par.z;
    let s = 0;
    for (const [tx, ty] of TAPS)
      if (dd <= unpack(m, Math.floor((px + tx * par.x) * m.N), Math.floor((py + ty * par.y) * m.N))) s++;
    return s * 0.25;
  };
  const right = gi.uniforms.lemLightRight.value, up = gi.uniforms.lemLightUp.value;
  const ss = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
  const boxW = (P, c, r) => {
    const dx = P.x - c.x, dy = P.y - c.y, dz = P.z - c.z;
    const q = Math.max(Math.abs(dx * right.x + dy * right.y + dz * right.z),
                       Math.abs(dx * up.x + dy * up.y + dz * up.z));
    return 1 - ss(r * 0.80, r * 0.97, q);
  };
  const bx = gi.uniforms.lemCsmBox0.value;
  const farShadow = (P, N3) => {
    const nw = boxW(P, gi.uniforms.lemNearCentre.value, gi.uniforms.lemNearRadius.value);
    if (nw >= 0.999) return 1;
    let s = maps[1] ? cascadeAt(maps[1], P, N3) : 1;
    if (maps[0]) s = s + (cascadeAt(maps[0], P, N3) - s) * boxW(P, {x: bx.x, y: bx.y, z: bx.z}, bx.w);
    return s + (1 - s) * nw;
  };
  const all = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible && o.geometry &&
    !/ocean|horizon|weather|mainland/.test(o.name || '')) all.push(o); });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  const sets = {deep: [], deepCore: [], open: []};
  for (let sy = 0; sy < innerHeight; sy += 2) for (let sx = 0; sx < innerWidth; sx += 2) {
    ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(all, false)[0];
    if (!h || !h.face || !/:concrete$/.test(h.object.name || '')) continue;
    const n = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    if (n.y < 0.92) continue;
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.08), d, 0.05, 500);
    sr.layers.enableAll();
    const hit = sr.intersectObjects(all, false)[0];
    const fs = farShadow(h.point, n);
    if (fs < 0.02 && hit) {
      sets.deep.push({sx, sy});
      let core = true;
      for (const [ox, oz] of [[3, 0], [-3, 0], [0, 3], [0, -3], [2, 2], [-2, -2]]) {
        const q = h.point.clone(); q.x += ox; q.z += oz;
        if (farShadow(q, n) > 0.02) { core = false; break; }
      }
      if (core) sets.deepCore.push({sx, sy});
    } else if (fs > 0.99 && !hit) sets.open.push({sx, sy});
  }
  return {sets, p0: window.__gx.p0.toArray(), p1: window.__gx.p1.toArray(),
          sunBase: window.__gx.sunBase, giBase: window.__gx.giBase};
});

const state = () => page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  return {gi: +gi.uniforms.lemGIStrength.value.toFixed(4),
          env: !!w.scene.environment,
          sun: +gi.sun.intensity.toFixed(4),
          ev: +(w.engine._passes?.composite?.material?.uniforms?.uExposure?.value ?? -1).toFixed(4),
          r0: gi.uniforms.lemCsmReady0.value, r1: gi.uniforms.lemCsmReady1.value,
          nb1: +gi.uniforms.lemCsmParam1.value.w.toFixed(4),
          db1: +gi.uniforms.lemCsmParam1.value.z.toFixed(6)};
});
async function shot(label) {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  const px = await page.evaluate(async ({src, sets}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const dd = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth, out = {};
    for (const k in sets) {
      const v = sets[k].map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
        return 0.2126 * dd[o] + 0.7152 * dd[o + 1] + 0.0722 * dd[o + 2]; });
      v.sort((x, y) => x - y);
      out[k] = v.length ? +v[v.length >> 1].toFixed(3) : null;
    }
    return out;
  }, {src, sets: pick.sets});
  return {label, ...px, live: await state()};
}

/* the fill off, and staying off */
await page.evaluate(() => { window.__gx.setGI(0); window.__gx.setEnv(null); });
await page.waitForTimeout(2000);

/* calibration ladder: mask gated off, sun swept */
await page.evaluate(() => { window.__gx.setReady0(0); window.__gx.setReady1(0); });
await page.waitForTimeout(1400);
const KS = [0, 0.05, 0.1, 0.15, 0.22, 0.3, 0.42, 0.6, 0.8, 1.0];
const curve = [];
for (const k of KS) {
  await page.evaluate((k) => window.__gx.setSun(window.__gx.sunBase * k), k);
  await page.waitForTimeout(1000);
  const s = await shot(`k=${k}`);
  curve.push({k, deep: s.deep, deepCore: s.deepCore, open: s.open, live: s.live});
}
await page.evaluate(() => { window.__gx.setSun(window.__gx.sunBase);
  window.__gx.setReady0(1); window.__gx.setReady1(1); });
await page.waitForTimeout(1400);
const invert = (target, key) => {
  for (let i = 1; i < curve.length; i++) {
    const a0 = curve[i - 1][key], a1 = curve[i][key];
    if ((target >= a0 && target <= a1) || (target <= a0 && target >= a1)) {
      const t = a1 === a0 ? 0 : (target - a0) / (a1 - a0);
      return +(curve[i - 1].k + t * (curve[i].k - curve[i - 1].k)).toFixed(4);
    }
  }
  return target < curve[0][key] ? 0 : null;
};
const trials = [
  ['shipped', 'g.uniforms.lemCsmParam0.value.copy(X.p0); g.uniforms.lemCsmParam1.value.copy(X.p1);'],
  ['normalBias = 0', 'g.uniforms.lemCsmParam0.value.w = 0; g.uniforms.lemCsmParam1.value.w = 0;'],
  ['normalBias x0.3', 'g.uniforms.lemCsmParam0.value.w = X.p0.w*0.3; g.uniforms.lemCsmParam1.value.w = X.p1.w*0.3;'],
  ['depthBias = 0', 'g.uniforms.lemCsmParam0.value.copy(X.p0); g.uniforms.lemCsmParam1.value.copy(X.p1); g.uniforms.lemCsmParam0.value.z = 0; g.uniforms.lemCsmParam1.value.z = 0;'],
  ['both = 0', 'g.uniforms.lemCsmParam0.value.z = 0; g.uniforms.lemCsmParam0.value.w = 0; g.uniforms.lemCsmParam1.value.z = 0; g.uniforms.lemCsmParam1.value.w = 0;'],
  ['shipped again', 'g.uniforms.lemCsmParam0.value.copy(X.p0); g.uniforms.lemCsmParam1.value.copy(X.p1);'],
];
const rows = [];
for (const [label, body] of trials) {
  await page.evaluate(`(() => { const g = window.__lemWorld.subsystems.get('gi'), X = window.__gx; ${body} })()`);
  await page.waitForTimeout(1300);
  const s = await shot(label);
  rows.push({...s, maskDeep: invert(s.deep, 'deep'), maskCore: invert(s.deepCore, 'deepCore')});
}
console.log(JSON.stringify({cam, time,
  n: {deep: pick.sets.deep.length, deepCore: pick.sets.deepCore.length, open: pick.sets.open.length},
  shipped: {p0: pick.p0, p1: pick.p1, sunBase: pick.sunBase, giBase: pick.giBase},
  curve, rows, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
