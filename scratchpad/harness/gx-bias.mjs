/* gx-bias.mjs — sweep the coarse cascades' two biases against ground truth,
 * on the CPU, over ONE read-back of the maps.
 *
 * The maps do not change when the bias changes — only the comparison does. So
 * the whole sweep is free: read the two cascade targets once, raycast the pad
 * and the terrain once to get geometric truth, then re-run `lemCascade`'s
 * comparison for every (normalBias, depthBias) pair and score it.
 *
 *   falseLit    a point the sun geometrically cannot reach, that the map says
 *               is lit. Peter-panning: the shadow has come off its caster.
 *   falseDark   a point in open sun that the map says is shadowed. Acne.
 *   contact     of the occluded points within 6 m of the occluder's own base,
 *               the fraction the map gets fully dark. This is the number the
 *               art director is describing when he says the plant floats.
 *
 * Shipped values are marked. Nothing is written; this only reports.
 *
 *   node gx-bias.mjs [--cam far] [--time 9]
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

const out = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera, rn = w.engine.renderer;
  const d = gi.sunDirection.clone();
  const maps = gi._csm.map(c => {
    const N = c.rt.width, buf = new Uint8Array(N * N * 4);
    try { rn.readRenderTargetPixels(c.rt, 0, 0, N, N, buf); } catch (e) { void e; }
    return {i: c.i, N, buf, mat: gi.uniforms[`lemCsmMat${c.i}`].value.elements.slice(),
            par: gi.uniforms[`lemCsmParam${c.i}`].value.clone(), radius: c.radius};
  });
  const unpack = (m, ix, iy) => {
    ix = Math.min(m.N - 1, Math.max(0, ix)); iy = Math.min(m.N - 1, Math.max(0, iy));
    const o = (iy * m.N + ix) * 4;
    return m.buf[o] / 255 + m.buf[o + 1] / 65025 + m.buf[o + 2] / 16581375 + m.buf[o + 3] / 4228250625;
  };
  const TAPS = [[-0.8, 0.4], [0.4, 0.8], [0.8, -0.4], [-0.4, -0.8]];
  const cascadeAt = (m, P, N3, nb, db) => {
    const e = m.mat;
    const X = P.x + N3.x * nb, Y = P.y + N3.y * nb, Z = P.z + N3.z * nb;
    const cw = e[3] * X + e[7] * Y + e[11] * Z + e[15];
    const px = (e[0] * X + e[4] * Y + e[8] * Z + e[12]) / cw;
    const py = (e[1] * X + e[5] * Y + e[9] * Z + e[13]) / cw;
    const pz = (e[2] * X + e[6] * Y + e[10] * Z + e[14]) / cw;
    if (px < 0 || px > 1 || py < 0 || py > 1 || pz > 1 || pz < 0) return 1;
    const dd = pz - db;
    let s = 0;
    for (const [tx, ty] of TAPS)
      if (dd <= unpack(m, Math.floor((px + tx * m.par.x) * m.N), Math.floor((py + ty * m.par.y) * m.N))) s++;
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
  const nc = gi.uniforms.lemNearCentre.value, nr = gi.uniforms.lemNearRadius.value;
  const farShadow = (P, N3, nbScale, dbScale) => {
    const nw = boxW(P, nc, nr);
    if (nw >= 0.999) return 1;
    let s = 1;
    if (maps[1]) s = cascadeAt(maps[1], P, N3, maps[1].par.w * nbScale, maps[1].par.z * dbScale);
    if (maps[0]) s = s + (cascadeAt(maps[0], P, N3, maps[0].par.w * nbScale, maps[0].par.z * dbScale) - s)
                        * boxW(P, {x: bx.x, y: bx.y, z: bx.z}, bx.w);
    return s + (1 - s) * nw;
  };

  /* --- ground truth, sampled on a METRIC grid over the pads rather than
   *     through the screen. Screen raycasting against terrain-core was minutes
   *     of work for the same answer; the pads are where the plant's shadows are
   *     supposed to land and they are what the art director is looking at. --- */
  const occ = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible &&
    !/^terrain|ocean|horizon|weather|mainland/.test(o.name || '') && o.geometry) occ.push(o); });
  const pads = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && /:concrete$/.test(o.name || '')) pads.push(o); });
  const up3 = new THREE.Vector3(0, 1, 0);
  const down = new THREE.Vector3(0, -1, 0);
  const pts = [];
  const drop = new THREE.Raycaster(); drop.layers.enableAll();
  const bbox = new THREE.Box3();
  for (const pad of pads) {
    bbox.setFromObject(pad);
    for (let x = bbox.min.x; x <= bbox.max.x; x += 3)
      for (let z = bbox.min.z; z <= bbox.max.z; z += 3) {
        drop.set(new THREE.Vector3(x, bbox.max.y + 40, z), down);
        const h = drop.intersectObject(pad, false)[0];
        if (!h || !h.face) continue;
        const n = h.face.normal.clone().applyNormalMatrix(
          new THREE.Matrix3().getNormalMatrix(pad.matrixWorld)).normalize();
        if (n.y < 0.92) continue;
        if (boxW(h.point, nc, nr) > 0.05) continue;   // three's own near map owns this
        const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.05), d, 0.02, 400);
        sr.layers.enableAll();
        const hit = sr.intersectObjects(occ, false)[0];
        pts.push({P: h.point.clone(), N: n, occluded: !!hit,
                  foot: hit ? Math.hypot(hit.point.x - h.point.x, hit.point.z - h.point.z) : 1e9,
                  rise: hit ? hit.point.y - h.point.y : 0});
      }
  }
  void up3;
  const occN = pts.filter(p => p.occluded).length;
  const openN = pts.length - occN;
  const contactN = pts.filter(p => p.occluded && p.foot < 6).length;

  const score = (nb, db) => {
    let falseLit = 0, falseDark = 0, contactDark = 0, deep = 0;
    for (const p of pts) {
      const s = farShadow(p.P, p.N, nb, db);
      if (p.occluded) {
        if (s > 0.9) falseLit++;
        if (s < 0.05) deep++;
        if (p.foot < 6 && s < 0.3) contactDark++;
      } else if (s < 0.9) falseDark++;
    }
    return {nb: +nb.toFixed(2), db: +db.toFixed(2),
      falseLitPct: +(100 * falseLit / Math.max(1, occN)).toFixed(2),
      falseDarkPct: +(100 * falseDark / Math.max(1, openN)).toFixed(2),
      deepPct: +(100 * deep / Math.max(1, occN)).toFixed(2),
      contactPct: +(100 * contactDark / Math.max(1, contactN)).toFixed(2)};
  };
  const rows = [];
  for (const nb of [0, 0.15, 0.3, 0.45, 0.6, 0.75, 1.0, 1.3])
    for (const db of [0.25, 0.5, 0.75, 1.0, 1.5])
      rows.push(score(nb, db));
  return {
    shipped: {c0: {normalBiasM: maps[0]?.par.w, depthBiasNorm: maps[0]?.par.z, radius: maps[0]?.radius},
              c1: {normalBiasM: maps[1]?.par.w, depthBiasNorm: maps[1]?.par.z, radius: maps[1]?.radius}},
    sunElevDeg: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2),
    /* what a metre of normal bias costs horizontally at this sun */
    metresPerMetreOfBias: +(1 / Math.tan(Math.asin(d.y))).toFixed(2),
    n: {pts: pts.length, occluded: occN, open: openN, contact: contactN},
    rows,
  };
});
console.log(JSON.stringify({cam, time, ...out, pageErrors: errs.slice(0, 4)}, null, 1));
await b.close();
