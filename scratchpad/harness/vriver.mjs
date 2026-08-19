/* vriver.mjs — stand the camera AT the river, not at the site.
 *
 *   node vriver.mjs --out ../shots/river.png [--season autumn] [--time 16]
 *                   [--dist 220] [--pitch 0.10] [--yaw 1.2] [--along 0]
 *
 * The water is a ribbon along the valley axis several hundred metres from the
 * pad, and every judged camera looks away from it. A fault on the bank cannot
 * be photographed from a camera whose frustum excludes the bank.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  const k = process.argv[i];
  if (!k.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k.slice(2)] = true; else { a[k.slice(2)] = n; i++; }
}
const mods = a.mods || 'sky,gi,terrain,vegetation,weather';
let url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}` +
          `&cam=wide&time=${a.time || 16}&hud=0&quality=${a.quality || 'ultra'}`;
if (a.season) url += '&season=' + a.season;
if (a.weather) url += '&weather=' + a.weather;
const out = path.resolve(a.out || '../shots/river.png');
fs.mkdirSync(path.dirname(out), {recursive: true});

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errors = [];
p.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});

const info = await p.evaluate(({dist, pitch, yaw, along, off}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  /* Walk out from the hub perpendicular to the valley until the ground drops
   * below the waterline: that is the bank, and it is where the camera goes. */
  const hub = w.plan?.hub || {x: 0, z: 0};
  const wy = t?.waterY ?? 0;
  let best = null;
  for (let ang = 0; ang < 360; ang += 3) {
    const r = ang * Math.PI / 180;
    for (let d = 60; d < 2600; d += 12) {
      const x = hub.x + Math.cos(r) * d, z = hub.z + Math.sin(r) * d;
      if (t.heightAt(x, z) < wy) {
        if (!best || d < best.d) best = {x, z, d, ang: r};
        break;
      }
    }
  }
  if (!best) return {found: false};
  const zAlong = Number(along || 0);
  const cx = best.x + Math.cos(best.ang + Math.PI / 2) * zAlong;
  const cz = best.z + Math.sin(best.ang + Math.PI / 2) * zAlong;
  w.rig.goalTarget.set(cx, t.heightAt(cx, cz) + Number(off || 6), cz);
  w.rig.goalYaw = Number(yaw ?? best.ang + Math.PI * 0.5);
  w.rig.goalPitch = Number(pitch || 0.16);
  w.rig.goalDistance = Number(dist || 220);
  w.rig.apply(1);
  w.rig.idleDrift = false;
  return {found: true, x: +cx.toFixed(1), z: +cz.toFixed(1), waterY: +wy.toFixed(2),
          dist: +best.d.toFixed(0)};
}, {dist: a.dist, pitch: a.pitch, yaw: a.yaw, along: a.along, off: a.off});

await p.waitForTimeout(Number(a.seconds || 5) * 1000);
await p.screenshot({path: out});
const stats = await p.evaluate(() => window.__lemWorld.stats?.());
console.log(JSON.stringify({info, stats, errors}, null, 1));
await b.close();
