/* gx-leak.mjs — two questions the decomposition left open.
 *
 * 1. THE MASK. On pad pixels whose `lemFarShadow` is under 0.05, 16.5 % of the
 *    open key still arrives. Is that the GPU's mask disagreeing with the CPU's,
 *    or is it bloom bleeding in from the lit pad around it? Measured with the
 *    probe field and the environment zeroed, so the pixel is key x mask + fog
 *    and the fog cancels in the subtraction:
 *        mask = (farON - sunOFF) / (farOFF - sunOFF), in LINEAR light.
 *
 * 2. THE FIT. `_fitOrtho` bounds the eight corners of a view-frustum slice. At
 *    a camera 407 m up looking down, that slice is mostly empty air, so the box
 *    centre floats hundreds of metres above the ground everything stands on.
 *    This reports the current fit and the fit that results if the corners are
 *    first clamped into the band the world actually occupies, and how many of
 *    the eight sites each box covers.
 *
 *   node gx-leak.mjs [--cam far] [--time 9]
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
await page.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } w.camera.updateMatrixWorld(true); });
await page.waitForTimeout(600);

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
    const par = gi.uniforms[`lemCsmParam${m.i}`].value;
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
  const farShadow = (P, N3) => {
    const nw = boxW(P, gi.uniforms.lemNearCentre.value, gi.uniforms.lemNearRadius.value);
    if (nw >= 0.999) return 1;
    let s = 1;
    if (maps[1] && gi.uniforms.lemCsmReady1.value > 0.5) s = cascadeAt(maps[1], P, N3);
    if (maps[0] && gi.uniforms.lemCsmReady0.value > 0.5) {
      const bx = gi.uniforms.lemCsmBox0.value;
      s = s + (cascadeAt(maps[0], P, N3) - s) * boxW(P, {x: bx.x, y: bx.y, z: bx.z}, bx.w);
    }
    return s + (1 - s) * nw;
  };
  const occ = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible &&
    !/^terrain|ocean|horizon|weather/.test(o.name || '') && o.geometry) occ.push(o); });
  const pads = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && /:concrete$/.test(o.name || '')) pads.push(o); });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  const sets = {deep: [], open: []};
  for (let sy = 0; sy < innerHeight; sy += 2) for (let sx = 0; sx < innerWidth; sx += 2) {
    ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(pads, false)[0];
    if (!h || !h.face) continue;
    const n = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    if (n.y < 0.92) continue;
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.08), d, 0.05, 500);
    sr.layers.enableAll();
    const hit = sr.intersectObjects(occ, false)[0];
    const fs = farShadow(h.point, n);
    if (fs < 0.02 && hit) sets.deep.push({sx, sy});
    else if (fs > 0.99 && !hit) sets.open.push({sx, sy});
  }

  /* --- the fit, as it is and as it would be clamped to the world's band --- */
  const DEG = Math.PI / 180;
  const fitPts = (near, far, clampBand) => {
    const tanH = Math.tan((cam.fov || 42) * 0.5 * DEG), tanW = tanH * (cam.aspect || 1.78);
    const pts = [];
    for (const z of [near, far]) for (const sx of [-1, 1]) for (const sy of [-1, 1]) {
      const p = new THREE.Vector3(sx * tanW * z, sy * tanH * z, -z).applyMatrix4(cam.matrixWorld);
      if (clampBand) p.y = Math.max(clampBand[0], Math.min(clampBand[1], p.y));
      pts.push(p);
    }
    const c = new THREE.Vector3();
    for (const p of pts) c.add(p);
    c.multiplyScalar(1 / 8);
    let r = 1;
    for (const p of pts) r = Math.max(r, p.distanceTo(c));
    return {c, r};
  };
  const dist = w.rig?.distance ?? 200;
  const nearReach = gi._nearReach;
  const specs = [{from: 0.62, reach: 3.0, cap: 320, quant: 24},
                 {from: 2.6, reach: 8.0, cap: 820, quant: 64}];
  const sites = [];
  w.scene.traverse(o => { if (/^site:/.test(o.name || '')) { o.updateWorldMatrix(true, false);
    const e = o.matrixWorld.elements; sites.push({n: o.name, x: e[12], y: e[13], z: e[14]}); } });
  const cover = (c, r) => sites.filter(s => boxW(s, c, r) > 0.5).length;
  const fits = specs.map((sp, i) => {
    const near = Math.max(cam.near, nearReach * sp.from);
    const far = Math.max(nearReach * (sp.from + 1.4), Math.min(dist * sp.reach, sp.cap * 2.2));
    const now = fitPts(near, far, null);
    const band = fitPts(near, far, [-60, 90]);
    const q = (v) => Math.min(sp.cap, Math.max(sp.quant, Math.ceil(v / sp.quant) * sp.quant));
    return {i, near: +near.toFixed(1), far: +far.toFixed(1),
      now: {c: now.c.toArray().map(v => +v.toFixed(1)), rRaw: +now.r.toFixed(1), r: q(now.r),
            texel: +((q(now.r) * 2) / 2048).toFixed(3), sitesCovered: cover(now.c, q(now.r))},
      clamped: {c: band.c.toArray().map(v => +v.toFixed(1)), rRaw: +band.r.toFixed(1), r: q(band.r),
            texel: +((q(band.r) * 2) / 2048).toFixed(3), sitesCovered: cover(band.c, q(band.r))},
    };
  });
  return {sets, fits, nSites: sites.length, camY: +cam.position.y.toFixed(1), dist,
          nearReach, sun: d.toArray().map(v => +v.toFixed(3))};
});

async function readPixels() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, sets}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const dd = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth, out = {};
    for (const k in sets) {
      const v = sets[k].map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
        return 0.2126 * dd[o] + 0.7152 * dd[o + 1] + 0.0722 * dd[o + 2]; });
      v.sort((x, y) => x - y);
      out[k] = v.length ? +v[v.length >> 1].toFixed(2) : null;
    }
    return out;
  }, {src, sets: pick.sets});
}

await page.evaluate(() => { const w = window.__lemWorld, gi = w.subsystems.get('gi');
  if (typeof gi.setExposureLocked === 'function') gi.setExposureLocked(true);
  else { gi.__grade = gi._applyGrade; gi._applyGrade = () => {}; }
  gi.__svc = gi._serviceCascades; gi._serviceCascades = () => {};
  /* fill removed so the pixel is key x mask + fog */
  gi.__g = gi.uniforms.lemGIStrength.value; gi.uniforms.lemGIStrength.value = 0;
  w.__env = w.scene.environment; w.scene.environment = null; });
await page.waitForTimeout(2000);

const rows = [];
const shot = async (label) => rows.push({state: label, ...(await readPixels())});
await shot('fill off, far ON');
await page.evaluate(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.uniforms.lemCsmReady0.value = 0; g.uniforms.lemCsmReady1.value = 0; });
await page.waitForTimeout(1400); await shot('fill off, far OFF');
await page.evaluate(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.__i = g.sun.intensity; g.sun.intensity = 0; });
await page.waitForTimeout(1400); await shot('fill off, sun OFF (fog floor)');
/* and the same three with bloom killed */
const bloom = await page.evaluate(() => {
  const w = window.__lemWorld, c = w.engine._passes?.composite?.material?.uniforms;
  if (!c || !c.uBloom) return null;
  const v = c.uBloom.value; c.uBloom.value = 0; return v;
});
await page.waitForTimeout(1200); await shot(`bloom OFF (was ${bloom}), sun OFF`);
await page.evaluate(() => { const g = window.__lemWorld.subsystems.get('gi'); g.sun.intensity = g.__i;
  g.uniforms.lemCsmReady0.value = 0; g.uniforms.lemCsmReady1.value = 0; });
await page.waitForTimeout(1400); await shot('bloom OFF, far OFF');
await page.evaluate(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.uniforms.lemCsmReady0.value = 1; g.uniforms.lemCsmReady1.value = 1; });
await page.waitForTimeout(1400); await shot('bloom OFF, far ON');

const srgb = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
const R = n => rows.find(r => r.state === n);
const maskFrom = (on, off, floor) => {
  const A = srgb(R(on).deep), B = srgb(R(off).deep), F = srgb(R(floor).deep);
  return {maskDeep: +((A - F) / (B - F)).toFixed(4), keyOn: +(A - F).toFixed(4), keyOff: +(B - F).toFixed(4)};
};
console.log(JSON.stringify({cam, time, sun: pick.sun, camY: pick.camY, dist: pick.dist,
  nearReach: pick.nearReach, nSites: pick.nSites,
  n: {deep: pick.sets.deep.length, open: pick.sets.open.length},
  rows,
  maskWithBloom: maskFrom('fill off, far ON', 'fill off, far OFF', 'fill off, sun OFF (fog floor)'),
  maskNoBloom: maskFrom('bloom OFF, far ON', 'bloom OFF, far OFF', `bloom OFF (was ${bloom}), sun OFF`),
  fits: pick.fits, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
