/* gy-nearbox.mjs — what the NEAR shadow map actually contains, and where every
 * shadow box actually sits, at a given camera.
 *
 * Prints, from the live page and nothing inferred:
 *   the rig (target, distance) and the camera's own position
 *   where the ground first enters the frustum, and where the camera is aimed
 *   each box: centre, radius, texel, and its height ABOVE the ground reference
 *   how many objects `_nearCull` has left casting into three's own map, and
 *   the draw/triangle cost of one forced redraw of that map
 *
 *   node gy-nearbox.mjs [--cam far] [--time 9]
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
const p = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(10000);

const res = await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera, rig = w.rig, rn = w.engine.renderer;
  cam.updateMatrixWorld(true);
  const eye = cam.position.clone();
  const f = new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion).normalize();
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(cam.quaternion).normalize();
  const tanH = Math.tan((cam.fov || 42) * 0.5 * Math.PI / 180);
  const bot = f.clone().addScaledVector(up, -tanH).normalize();
  const gy = rig ? rig.target.y : 0;
  const dNear = bot.y < -1e-5
    ? (eye.y - gy) * Math.hypot(bot.x, bot.z) / -bot.y : null;
  const dAim = rig ? Math.hypot(rig.target.x - eye.x, rig.target.z - eye.z) : null;

  /* who is casting into three's own map right now */
  const nearCasters = [];
  let nearTris = 0;
  w.scene.traverse(o => {
    if ((o.isMesh || o.isInstancedMesh) && o.castShadow && o.parent) {
      nearCasters.push(o.name || o.type);
      const g = o.geometry;
      const n = g?.index ? g.index.count / 3 : (g?.attributes?.position?.count || 0) / 3;
      nearTris += n * (o.isInstancedMesh ? Math.max(1, o.count) : 1);
    }
  });

  const boxes = [];
  const push = (name, c, r, size) => boxes.push({
    name, centre: [+c.x.toFixed(1), +c.y.toFixed(1), +c.z.toFixed(1)],
    radius: +r.toFixed(1), aboveGround: +(c.y - gy).toFixed(1),
    texel: +((r * 2) / size).toFixed(3),
    /* horizontal distance from the camera, along the ground */
    dFromCam: +Math.hypot(c.x - eye.x, c.z - eye.z).toFixed(1),
  });
  push('near(three)', gi.uniforms.lemNearCentre.value, gi.uniforms.lemNearRadius.value,
       gi.sun.shadow.mapSize.x);
  gi._csm.forEach(c => push(`cascade${c.i}`, c.fit, c.radius || 0, c.rt.width));

  return {
    rig: rig ? {target: rig.target.toArray().map(v => +v.toFixed(1)),
                distance: +rig.distance.toFixed(1), pitch: +rig.pitch.toFixed(3),
                yaw: +rig.yaw.toFixed(3)} : null,
    eye: eye.toArray().map(v => +v.toFixed(1)), fov: cam.fov,
    groundRef: gy, dNearGround: dNear === null ? null : +dNear.toFixed(1),
    dAim: dAim === null ? null : +dAim.toFixed(1),
    nearReach: gi._nearReach, shadowMap: gi.sun.shadow.mapSize.x,
    boxes,
    nearCasterCount: nearCasters.length,
    nearCasterTris: Math.round(nearTris),
    nearCasterNames: nearCasters.slice(0, 40),
    cascades: gi._csm.map(c => ({i: c.i, casters: c.casters.length, cost: c.cost,
                                 tris: c.tris, runs: c.runs, radius: c.radius})),
    frame: {draws: rn.info.render.calls, tris: rn.info.render.triangles},
  };
});
console.log(JSON.stringify({cam, time, ...res, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
