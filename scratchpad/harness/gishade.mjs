/* gishade.mjs — what is the light actually doing at one pixel of shaded ground?
 *
 * Raycast the pixel into the world, then read back every term that decides its
 * colour: the probe field's answer, the open-field hemisphere it should be a
 * floor under, the sun's own irradiance, and where the point falls in the probe
 * grid. A shaded pixel at 10/255 is either a fill problem or a grid-coverage
 * problem and these two numbers tell them apart.
 *
 *   node gishade.mjs --px 700,470 --px 900,470 --cam yard --time 16
 */
import {chromium} from 'playwright';

const args = {px: []};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (k === 'px') { args.px.push(n); i++; continue; }
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
if (!args.px.length) args.px = ['700,470', '900,470', '300,600'];

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?cam=${args.cam || 'yard'}` +
  `&time=${args.time || 16}&weather=clear&hud=0`;
const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);

const out = await page.evaluate(async ({pxs, W, H}) => {
  const THREE = window.THREE || (await import('three'));
  const w = window.__lemWorld;
  const gi = w.subsystems.get('gi');
  const cam = w.camera || gi.ctx.camera;
  const rc = new THREE.Raycaster();
  const rows = [];
  const targets = [];
  /* Skip the sky dome and the weather volumes: they surround the camera, so
   * they are the first hit on every ray and none of them is ground. */
  const skip = new Set();
  for (const name of ['sky', 'weather', 'labels']) {
    const s = w.subsystems.get(name);
    for (const k of ['root', 'group', 'dome', 'mesh']) {
      if (s && s[k] && s[k].isObject3D) s[k].traverse(o => skip.add(o));
    }
  }
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh) || skip.has(o)) return;
    if (o.material && (o.material.transparent || o.material.depthWrite === false)) return;
    targets.push(o);
  });
  for (const p of pxs) {
    const [x, y] = p.split(',').map(Number);
    rc.setFromCamera(new THREE.Vector2((x / W) * 2 - 1, -(y / H) * 2 + 1), cam);
    const hit = rc.intersectObjects(targets, false).find(h => h.distance > 8);
    if (!hit) { rows.push({px: p, hit: null}); continue; }
    const n = hit.face
      ? hit.face.normal.clone().transformDirection(hit.object.matrixWorld)
      : new THREE.Vector3(0, 1, 0);
    const E = gi.irradianceAt(hit.point.x, hit.point.y, hit.point.z, n);
    const sky = gi.uniforms.lemSkyIrradiance.value;
    const gr = gi.uniforms.lemGroundIrradiance.value;
    const t = n.y * 0.5 + 0.5;
    const hemi = [gr.x + (sky.x - gr.x) * t, gr.y + (sky.y - gr.y) * t,
                  gr.z + (sky.z - gr.z) * t];
    const gmin = gi.uniforms.lemGridMin.value, ginv = gi.uniforms.lemGridInvSize.value;
    const uvw = [(hit.point.x - gmin.x) * ginv.x, (hit.point.y - gmin.y) * ginv.y,
                 (hit.point.z - gmin.z) * ginv.z];
    rows.push({
      px: p, obj: hit.object.name || hit.object.type,
      point: [+hit.point.x.toFixed(1), +hit.point.y.toFixed(1), +hit.point.z.toFixed(1)],
      dist: +hit.distance.toFixed(1),
      normal: [+n.x.toFixed(2), +n.y.toFixed(2), +n.z.toFixed(2)],
      NdotL: +n.dot(gi.sunDirection).toFixed(3),
      probeE: [+E.r.toFixed(4), +E.g.toFixed(4), +E.b.toFixed(4)],
      hemisphere: hemi.map(v => +v.toFixed(4)),
      giStrength: gi.uniforms.lemGIStrength.value,
      uvw: uvw.map(v => +v.toFixed(3)),
      insideGrid: uvw.every(v => v >= 0 && v <= 1),
    });
  }
  const cu = gi.ctx.engine?._passes?.composite?.material?.uniforms || {};
  const grade = {};
  for (const k of ['uExposure', 'uBlackPoint', 'uWhitePoint', 'uToe', 'uContrast',
                   'uSaturation', 'uVignette', 'uGain', 'uLift', 'uAOStrength']) {
    const v = cu[k]?.value;
    grade[k] = v && v.isVector3 ? v.toArray().map(n => +n.toFixed(4))
             : typeof v === 'number' ? +v.toFixed(4) : v;
  }
  return {
    grade, sceneEV: gi._sceneEV, sceneEVLow: gi._sceneEVLow,
    analytic: gi.analyticExposure, fillE: gi._fillE, keyE: gi._keyE,
    sun: {dir: gi.sunDirection.toArray().map(v => +v.toFixed(3)),
          intensity: +gi.sunIntensity.toFixed(3),
          colour: [gi.sunColour.r, gi.sunColour.g, gi.sunColour.b].map(v => +v.toFixed(3)),
          elevationDeg: +(Math.asin(gi.sunDirection.y) * 180 / Math.PI).toFixed(1)},
    exposure: +(gi.exposure ?? -1).toFixed(3),
    camera: (() => {
      const d = new THREE.Vector3();
      cam.getWorldDirection(d);
      const az = v => +(Math.atan2(v.x, -v.z) * 180 / Math.PI).toFixed(1);
      return {viewAz: az(d), sunAz: az(gi.sunDirection),
              sunToViewDeg: +(Math.acos(Math.max(-1, Math.min(1,
                d.dot(gi.sunDirection)))) * 180 / Math.PI).toFixed(1)};
    })(),
    nearRadius: gi.uniforms.lemNearRadius.value,
    grid: gi.grid ? {dims: [gi.grid.nx, gi.grid.ny, gi.grid.nz], step: gi.grid.step,
                     min: gi.uniforms.lemGridMin.value.toArray().map(v => +v.toFixed(1))}
                  : null,
    rows,
  };
}, {pxs: args.px, W: 1280, H: 720});

console.log(JSON.stringify(out, null, 2));
await browser.close();
