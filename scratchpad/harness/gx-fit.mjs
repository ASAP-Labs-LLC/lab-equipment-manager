/* gx-fit.mjs — is a cascade's box fitted to the ground, or to the air above it?
 *
 * `_fitOrtho` bounds the eight corners of a view-frustum slice. From a camera
 * that stands 407 m up and looks down, the 104..704 m slice cascade 0 is given
 * is a cone of empty sky: its centroid is 220 m above the ground and 400 m short
 * of the site, so a 640 m box lands half-way between the eye and the thing being
 * looked at. This compares three fits, per camera, and scores each by how many
 * of the eight sites' pads it actually covers and by how much of the visible
 * ground falls inside it:
 *
 *   asShipped   the eight raw corners
 *   clampY      corners with their Y clamped into the world band (refuted
 *               earlier: clamping Y leaves X and Z where they were, and the box
 *               is off along the light's RIGHT axis, which is mostly X and Z)
 *   rayClip     each corner walked ALONG ITS OWN RAY from the eye until it
 *               enters the band — which moves X and Z as well
 *
 *   node gx-fit.mjs [--cams far,wide,yard] [--time 9]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cams = (a.cams || 'far,wide,yard').split(',');
const time = a.time || '9';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const out = [];
for (const cam of cams) {
  const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
    + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
  const page = await b.newPage({viewport: {width: 1600, height: 900}});
  page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 160)));
  await page.goto(url, {waitUntil: 'load', timeout: 120000});
  await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
  await page.waitForTimeout(9000);
  await page.evaluate(() => { const w = window.__lemWorld;
    if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } w.camera.updateMatrixWorld(true); });
  await page.waitForTimeout(500);
  out.push(await page.evaluate((cam) => {
    const w = window.__lemWorld, gi = w.subsystems.get('gi');
    const THREE = w.ctx.THREE, camera = w.camera;
    const DEG = Math.PI / 180;
    const right = gi.uniforms.lemLightRight.value, up = gi.uniforms.lemLightUp.value;
    const ss = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
    const boxW = (P, c, r) => {
      const dx = P.x - c.x, dy = P.y - c.y, dz = P.z - c.z;
      const q = Math.max(Math.abs(dx * right.x + dy * right.y + dz * right.z),
                         Math.abs(dx * up.x + dy * up.y + dz * up.z));
      return 1 - ss(r * 0.80, r * 0.97, q);
    };
    /* the ground actually on screen */
    const all = [];
    w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible && o.geometry &&
      !/ocean|horizon|weather|mainland|^terrain/.test(o.name || '')) all.push(o); });
    const rc = new THREE.Raycaster(); rc.layers.enableAll();
    const ndc = new THREE.Vector2();
    const seen = [];
    for (let sy = 0; sy < innerHeight; sy += 16) for (let sx = 0; sx < innerWidth; sx += 16) {
      ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
      rc.setFromCamera(ndc, camera);
      const h = rc.intersectObjects(all, false)[0];
      if (h) seen.push(h.point.clone());
    }
    let yLo = 1e9, yHi = -1e9;
    for (const p of seen) { yLo = Math.min(yLo, p.y); yHi = Math.max(yHi, p.y); }
    const sites = [];
    w.scene.traverse(o => { if (/^site:/.test(o.name || '')) { o.updateWorldMatrix(true, false);
      const e = o.matrixWorld.elements; sites.push(new THREE.Vector3(e[12], e[13], e[14])); } });

    const corners = (near, far) => {
      const tanH = Math.tan((camera.fov || 42) * 0.5 * DEG), tanW = tanH * (camera.aspect || 1.78);
      const pts = [];
      for (const z of [near, far]) for (const sx of [-1, 1]) for (const sy of [-1, 1])
        pts.push(new THREE.Vector3(sx * tanW * z, sy * tanH * z, -z).applyMatrix4(camera.matrixWorld));
      return pts;
    };
    const bound = (pts, quant, cap) => {
      const c = new THREE.Vector3();
      for (const p of pts) c.add(p);
      c.multiplyScalar(1 / pts.length);
      let r = 1;
      for (const p of pts) r = Math.max(r, p.distanceTo(c));
      const rq = Math.min(cap, Math.max(quant, Math.ceil(r / quant) * quant));
      return {c, rRaw: r, r: rq};
    };
    const eye = camera.position;
    const rayClip = (pts, lo, hi) => pts.map(p => {
      if (p.y <= hi && p.y >= lo) return p.clone();
      const target = p.y > hi ? hi : lo;
      const dy = p.y - eye.y;
      if (Math.abs(dy) < 1e-6) return p.clone();
      const t = (target - eye.y) / dy;               // where this ray crosses the band
      if (t <= 0) return p.clone();                  // band is behind the eye
      return eye.clone().lerp(p, Math.min(1, t));
    });
    const clampY = (pts, lo, hi) => pts.map(p => {
      const q = p.clone(); q.y = Math.max(lo, Math.min(hi, q.y)); return q; });

    const score = (fit) => ({
      c: fit.c.toArray().map(v => +v.toFixed(1)), rRaw: +fit.rRaw.toFixed(0), r: fit.r,
      texelCm: +((fit.r * 2) / 2048 * 100).toFixed(1),
      sites: sites.filter(s => boxW(s, fit.c, fit.r) > 0.5).length,
      pctVisibleGround: seen.length
        ? +(100 * seen.filter(p => boxW(p, fit.c, fit.r) > 0.5).length / seen.length).toFixed(1) : null,
    });
    const dist = w.rig?.distance ?? 200, nearReach = gi._nearReach;
    const specs = [{i: 0, from: 0.62, reach: 3.0, cap: 320, quant: 24},
                   {i: 1, from: 2.6, reach: 8.0, cap: 820, quant: 64}];
    const lo = yLo - 15, hi = yHi + 45;
    const cascades = specs.map(sp => {
      const nearZ = Math.max(camera.near, nearReach * sp.from);
      const farZ = Math.max(nearReach * (sp.from + 1.4), Math.min(dist * sp.reach, sp.cap * 2.2));
      const raw = corners(nearZ, farZ);
      return {i: sp.i, nearZ: +nearZ.toFixed(0), farZ: +farZ.toFixed(0),
        asShipped: score(bound(raw, sp.quant, sp.cap)),
        clampY: score(bound(clampY(raw, lo, hi), sp.quant, sp.cap)),
        rayClip: score(bound(rayClip(raw, lo, hi), sp.quant, sp.cap))};
    });
    const rawNear = corners(Math.max(camera.near, 0.5), nearReach);
    return {cam, camY: +camera.position.y.toFixed(1), dist, nearReach,
      groundBand: [+lo.toFixed(1), +hi.toFixed(1)], groundPts: seen.length,
      near: {asShipped: score(bound(rawNear, 8, 168)),
             rayClip: score(bound(rayClip(rawNear, lo, hi), 8, 168))},
      cascades};
  }, cam));
  await page.close();
}
console.log(JSON.stringify(out, null, 1));
await b.close();
