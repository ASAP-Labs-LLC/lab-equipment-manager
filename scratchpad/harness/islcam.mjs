/* islcam.mjs — a screenshot from an arbitrary rig pose, for looking at the
 * island as a whole. solo.html's five presets all sit inside 420m of the site,
 * which is a third of the way to the coast: none of them can show whether the
 * island is an island. Also dumps a few terrain internals so the shape can be
 * checked as numbers as well as pixels. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
}
const mods = a.mods || 'sky,gi,terrain';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&hud=0&quality=ultra`
          + `&time=${a.time || 16}&weather=${a.weather || 'clear'}`
          + (a.season ? `&season=${a.season}` : '');
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errors = [];
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 300)); });
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(1500);

const info = await page.evaluate(({yaw, pitch, dist}) => {
  const w = window.__lemWorld;
  Object.assign(w.rig, {goalYaw: +yaw, goalPitch: +pitch, goalDistance: +dist});
  w.rig.idleDrift = false;
  w.rig.apply(1);
  const t = w.subsystems.get('terrain');
  const g = {};
  if (t) {
    g.islandR = t.islandR; g.wobble = t.coastWobble; g.siteReach = t.siteReach;
    g.coreSize = t.coreSize; g.ringSize = t.ringSize; g.ringSeg = t.ringSeg;
    g.waterY = t.waterY; g.yShift = t.yShift; g.season = t.season;
    g.stacks = (t.stacks || []).length;
    g.erosStats = t.erosStats;
    /* Land area and extent, measured off the surface rather than declared. */
    let land = 0, tot = 0, maxR = 0;
    const R = t.islandR;
    for (let j = 0; j < 400; j++) {
      for (let i = 0; i < 400; i++) {
        const x = t.cx + (i / 399 - 0.5) * 2.6 * R;
        const z = t.cz + (j / 399 - 0.5) * 2.6 * R;
        tot++;
        if (t.heightAt(x, z) > t.waterY) {
          land++;
          const r = Math.hypot(x - t.cx, z - t.cz);
          if (r > maxR) maxR = r;
        }
      }
    }
    const cell = (2.6 * R / 399) ** 2;
    g.landAreaKm2 = +(land * cell / 1e6).toFixed(3);
    g.landMaxRadius = Math.round(maxR);
    g.meshTris = 0; g.meshDraws = 0;
    for (const m of t.meshes) {
      const idx = m.geometry?.index;
      g.meshTris += idx ? idx.count / 3 : (m.geometry?.attributes?.position?.count || 0) / 3;
      g.meshDraws++;
    }
    g.perMesh = t.meshes.map(m => [m.name, (m.geometry?.index?.count || 0) / 3]);
  }
  return g;
}, {yaw: a.yaw || -0.7, pitch: a.pitch || 0.62, dist: a.dist || 3000});

await page.waitForTimeout(1200);
await page.screenshot({path: a.out || 'isl.png'});
console.log(JSON.stringify({...info, errors}, null, 1));
await browser.close();
