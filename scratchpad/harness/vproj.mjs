/* vproj.mjs — which instances are the pixels the critic is pointing at?
 *
 *   node vproj.mjs --rect 1330,875,1450,975
 *
 * Projects every placed vegetation instance through the judged camera and lists
 * the ones landing inside a named screen rectangle, with the ground facts at
 * each. A raycast answers what is nearest; this answers what is THERE, which is
 * the question when the thing under discussion is a mat lying on the ground
 * under a scatter of trees.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const rect = (arg('rect', '1330,875,1450,975')).split(',').map(Number);
const W = 1920, H = 1080;

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html?cam=far&time=9&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: W, height: H}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(9000);

const out = await p.evaluate(async ({rect, W, H}) => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const THREE = await import('three');
  const camera = (w.rig && w.rig.camera) || w.camera || w.engine.camera;
  camera.updateMatrixWorld();
  const v = new THREE.Vector3();
  const wy = veg.waterY;
  const g = (x, z) => veg._ground(x, z);
  const toPx = (x, y, z) => {
    v.set(x, y, z).project(camera);
    return [(v.x * 0.5 + 0.5) * W, (-v.y * 0.5 + 0.5) * H, v.z];
  };
  const hit = (x, y, z) => { const q = toPx(x, y, z);
    return q[2] < 1 && q[0] >= rect[0] && q[0] <= rect[2] && q[1] >= rect[1] && q[1] <= rect[3]; };

  const rows = {tree: [], clutter: [], sward: [], grass: 0};
  for (const e of (veg.trees || [])) for (let i = 0; i < e.list.length; i++) {
    const x = e.xs[i], z = e.zs[i], y = e.mats[i * 16 + 13];
    if (!hit(x, y, z)) continue;
    const sy = Math.hypot(e.mats[i * 16 + 4], e.mats[i * 16 + 5], e.mats[i * 16 + 6]);
    rows.tree.push({x: +x.toFixed(0), z: +z.toFixed(0), altM: +(g(x, z) - wy).toFixed(2),
                    coast: +veg._coastDist(x, z).toFixed(1), h: +(sy * (e.spec.refH || 18)).toFixed(1),
                    spec: e.spec.name || e.spec.id});
  }
  for (const c of (veg.clutter || [])) for (let i = 0; i < c.count; i++) {
    const x = c.xs[i], z = c.zs[i], y = c.mats[i * 16 + 13];
    if (!hit(x, y, z)) continue;
    rows.clutter.push({x: +x.toFixed(0), z: +z.toFixed(0), altM: +(g(x, z) - wy).toFixed(2),
                       coast: +veg._coastDist(x, z).toFixed(1)});
  }
  for (const s of (veg.sward || [])) for (let i = 0; i < s.count; i++) {
    const x = s.xs[i], z = s.zs[i], y = s.mats[i * 16 + 13];
    if (!hit(x, y, z)) continue;
    const cd = veg._coastDist(x, z);
    const site = veg._site(x, z, 0);
    const sh = veg._shore({coast: cd, x, z});
    rows.sward.push({x: +x.toFixed(0), z: +z.toFixed(0), altM: +(g(x, z) - wy).toFixed(2),
                     coast: +cd.toFixed(1), beach: +sh.beach.toFixed(3), salt: +sh.salt.toFixed(3),
                     wet: site ? +site.wet.toFixed(3) : null,
                     kind: site && veg._terrain && veg._terrain.biomeAt
                       ? veg._terrain.biomeAt(x, z).kind : null,
                     open: +veg._openness(x, z, true).toFixed(3),
                     stand: veg._standAt ? +veg._standAt(x, z).toFixed(3) : null});
  }
  if (veg.grass) for (let i = 0; i < veg.grass.count; i++) {
    const x = veg.grass.mats[i * 16 + 12], y = veg.grass.mats[i * 16 + 13], z = veg.grass.mats[i * 16 + 14];
    if (hit(x, y, z)) rows.grass++;
  }
  return {rect, camPos: [camera.position.x | 0, camera.position.y | 0, camera.position.z | 0], rows};
}, {rect, W, H});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
