/* sk-geodump.mjs — dump (view depth, world height, true distance, what) for every
 * ground sample in a frame, once, so any number of fog curves can be evaluated
 * offline instead of costing a page load each.
 *
 *   node sk-geodump.mjs --cam far --out far.geo.json
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'sky,gi,terrain,vegetation,buildings,rail,trains';
const cam = a.cam || 'far';
const G = +(a.grid || 220);

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
          + `&cam=${cam}&time=${a.time || '9'}&hud=0&quality=ultra&weather=clear`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null, t1 = Date.now();
while (Date.now() - t1 < 25000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => {
    const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 10) break;
}
const out = await page.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cm = w.camera; cm.updateMatrixWorld(true);
  const o = {x: cm.position.x, y: cm.position.y, z: cm.position.z};
  const e = cm.matrixWorld.elements;
  const bx = {x: e[0], y: e[1], z: e[2]}, by = {x: e[4], y: e[5], z: e[6]};
  const bz = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cm.fov * Math.PI / 360), tx = ty * cm.aspect;
  const HH = Math.round(G * 9 / 16);
  const hits = [];
  for (let j = 0; j < HH; j++) for (let i = 0; i < G; i++) {
    const nx = ((i + 0.5) / G) * 2 - 1, ny0 = 1 - ((j + 0.5) / HH) * 2;
    let vx = bx.x * nx * tx + by.x * ny0 * ty - bz.x;
    let vy = bx.y * nx * tx + by.y * ny0 * ty - bz.y;
    let vz = bx.z * nx * tx + by.z * ny0 * ty - bz.z;
    const L = Math.hypot(vx, vy, vz); vx /= L; vy /= L; vz /= L;
    let pr = 0, hit = -1, step = 4;
    for (let s = step; s < 9000; s += step) {
      const gap = o.y + vy * s - t.heightAt(o.x + vx * s, o.z + vz * s);
      if (gap <= 0) { let lo = pr, hi = s;
        for (let k = 0; k < 22; k++) { const m = (lo + hi) * 0.5;
          const g2 = o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m);
          if (g2 <= 0) hi = m; else lo = m; }
        hit = (lo + hi) * 0.5; break; }
      pr = s; step = Math.min(60, Math.max(4, gap * 0.55));
    }
    if (hit < 0) continue;
    const x = o.x + vx * hit, z = o.z + vz * hit, h = t.heightAt(x, z);
    hits.push({i, j, H: HH, dist: +hit.toFixed(1),
      dep: +((x - o.x) * -bz.x + (h - o.y) * -bz.y + (z - o.z) * -bz.z).toFixed(1),
      h: +h.toFixed(2), land: h > t.waterY + 0.05});
  }
  const f = w.scene.fog;
  return {hits, camY: +o.y.toFixed(2), waterY: t.waterY, density: f.density,
          fogColour: [f.color.r, f.color.g, f.color.b], G, tier: w.stats().tier};
}, {G});
fs.writeFileSync(a.out || `${cam}.geo.json`, JSON.stringify(out));
console.log(`${a.out || cam + '.geo.json'}  hits ${out.hits.length}  camY ${out.camY}  density ${out.density} tier ${out.tier}`);
await b.close();
