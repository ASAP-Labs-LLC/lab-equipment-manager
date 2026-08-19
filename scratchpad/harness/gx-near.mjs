/* gx-near.mjs — where is the near shadow box, and is any of the frame inside it?
 *
 * `_fitShadow` bounds the 0.5..`_nearReach` slice of the VIEW FRUSTUM. At a
 * camera that stands well above the site and looks down, that slice is a cone of
 * empty air: the ground under `cam=far` is nine hundred metres down the view
 * ray, and the near box stops at a hundred and sixty-eight. This reports, per
 * camera preset, how much of the ground the frame actually shows is served by
 * that box — and what it would cost to put the box where the camera is looking.
 *
 *   node gx-near.mjs [--cams far,wide,yard,low] [--time 9]
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
  await page.waitForTimeout(600);
  out.push(await page.evaluate((cam) => {
    const w = window.__lemWorld, gi = w.subsystems.get('gi');
    const THREE = w.ctx.THREE, camera = w.camera;
    const right = gi.uniforms.lemLightRight.value, up = gi.uniforms.lemLightUp.value;
    const ss = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
    const boxW = (P, c, r) => {
      const dx = P.x - c.x, dy = P.y - c.y, dz = P.z - c.z;
      const q = Math.max(Math.abs(dx * right.x + dy * right.y + dz * right.z),
                         Math.abs(dx * up.x + dy * up.y + dz * up.z));
      return 1 - ss(r * 0.80, r * 0.97, q);
    };
    /* the ground the frame actually shows */
    const all = [];
    w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible && o.geometry &&
      !/ocean|horizon|weather|mainland/.test(o.name || '')) all.push(o); });
    const rc = new THREE.Raycaster(); rc.layers.enableAll();
    const ndc = new THREE.Vector2();
    let hits = 0, inNear = 0, sumDist = 0;
    const centroid = new THREE.Vector3();
    for (let sy = 0; sy < innerHeight; sy += 24) for (let sx = 0; sx < innerWidth; sx += 24) {
      ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
      rc.setFromCamera(ndc, camera);
      const h = rc.intersectObjects(all, false)[0];
      if (!h) continue;
      hits++; sumDist += h.distance; centroid.add(h.point);
      if (boxW(h.point, gi.uniforms.lemNearCentre.value, gi.uniforms.lemNearRadius.value) > 0.5) inNear++;
    }
    if (hits) centroid.multiplyScalar(1 / hits);
    /* what the near box would cover if it were centred on what the camera is
     * looking at instead of on a slice of its frustum */
    let wouldCover = 0;
    for (let sy = 0; sy < innerHeight; sy += 24) for (let sx = 0; sx < innerWidth; sx += 24) {
      ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
      rc.setFromCamera(ndc, camera);
      const h = rc.intersectObjects(all, false)[0];
      if (!h) continue;
      if (boxW(h.point, centroid, gi.uniforms.lemNearRadius.value) > 0.5) wouldCover++;
    }
    let casters = 0, meshes = 0;
    w.scene.traverse(o => { if (o.isMesh || o.isInstancedMesh || o.isBatchedMesh) {
      meshes++; if (o.castShadow) casters++; } });
    return {cam,
      camPos: camera.position.toArray().map(v => +v.toFixed(1)),
      rigDistance: w.rig?.distance, rigPitch: +(w.rig?.pitch ?? 0).toFixed(3),
      rigTarget: w.rig?.target?.toArray?.().map(v => +v.toFixed(1)) ?? null,
      nearReach: gi._nearReach, nearRadius: gi.uniforms.lemNearRadius.value,
      nearCentre: gi.uniforms.lemNearCentre.value.toArray().map(v => +v.toFixed(1)),
      shadowMapSize: gi.sun.shadow.mapSize.x,
      texelCm: +((gi.uniforms.lemNearRadius.value * 2) / gi.sun.shadow.mapSize.x * 100).toFixed(1),
      groundHits: hits,
      meanGroundDistanceM: hits ? +(sumDist / hits).toFixed(1) : null,
      visibleGroundCentroid: centroid.toArray().map(v => +v.toFixed(1)),
      pctOfVisibleGroundInsideNearBox: hits ? +(100 * inNear / hits).toFixed(1) : null,
      pctIfBoxWereCentredOnWhatIsVisible: hits ? +(100 * wouldCover / hits).toFixed(1) : null,
      meshes, castShadowTrue: casters, drawCalls: w.engine.drawCalls, tris: w.engine.triangles,
    };
  }, cam));
  await page.close();
}
console.log(JSON.stringify(out, null, 1));
await b.close();
