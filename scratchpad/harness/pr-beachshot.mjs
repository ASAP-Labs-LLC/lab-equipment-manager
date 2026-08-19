/* pr-beachshot.mjs — stand on the beach and look at what was actually built.
 * solo.html's camera presets are all aimed at the plant; this drives the rig
 * straight to whatever `props` chose as its beach anchor, so the umbrellas, the
 * pier and the boats can be judged at the size a person would see them.
 *
 *   node pr-beachshot.mjs <out.png> [distance] [pitch] [yawOffset]
 */
import {chromium} from 'playwright';

const OUT = process.argv[2] || 'beach.png';
const DIST = parseFloat(process.argv[3] || '120');
const PITCH = parseFloat(process.argv[4] || '0.22');
const YAWOFF = parseFloat(process.argv[5] || '0');

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
  '?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather' +
  '&cam=far&time=9&weather=clear&hud=0&quality=ultra';

const b = await chromium.launch({headless: false,
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(9000);

const info = await p.evaluate(([d, pit, yo]) => {
  const w = window.__lemWorld;
  const pr = w.subsystems.get('props');
  const a = pr.beachAnchor;
  if (!a) return {err: 'no beach anchor'};
  /* Look from the land side out over the beach: the seaward direction is where
   * the water is, so stand opposite it. */
  const s = pr._seaward(a.x, a.z) || {x: 0, z: 1};
  const yaw = Math.atan2(-s.x, -s.z) + yo;
  const y = w.ctx.ground(a.x, a.z);
  /* goalTarget, not target: the rig lerps target -> goalTarget every frame and
   * setting the follower alone is undone within the second. Also kill the slow
   * auto-orbit, which walks the yaw ~0.012 rad/s out from under a still shot. */
  w.rig.goalTarget.set(a.x, y + 2, a.z);
  w.rig.target.set(a.x, y + 2, a.z);
  w.rig.orbit = false; w.rig.autoOrbit = false; w.rig.spin = false;
  Object.assign(w.rig, {goalYaw: yaw, goalPitch: pit, goalDistance: d,
                        yaw, pitch: pit, distance: d});
  return {anchor: a, pier: pr.pier, boats: pr.boatSites,
          umbrellas: (pr.umbrellaSites || []).length, birds: pr.birdCount,
          seaward: s, yaw: +yaw.toFixed(2)};
}, [DIST, PITCH, YAWOFF]);
console.log(JSON.stringify(info));
await p.waitForTimeout(3500);
await p.screenshot({path: OUT});
await b.close();
