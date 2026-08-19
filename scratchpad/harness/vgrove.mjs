/* vgrove.mjs — the same frame with the outer wood shown, hidden and wireframed,
 * plus the distance distribution of the groves actually drawn.
 *
 *   node vgrove.mjs <cam> <outPrefix> [--time 16] [--radius N] [--tail N]
 *
 * Three rounds of this file have chased a defect to the wrong subsystem by
 * reasoning about a screenshot. Hiding one set and shooting the identical
 * instant is the only thing that has ever settled it.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const cam = process.argv[2] || 'low';
const pre = process.argv[3] || '../shots/vgrove';
const arg = k => { const i = process.argv.indexOf('--' + k);
                   return i > 0 ? process.argv[i + 1] : null; };
const time = arg('time') || '16';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html` +
             `?cam=${cam}&time=${time}&weather=clear&hud=0`,
             {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(7000);

const dist = await p.evaluate(() => {
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const c = w.camera.position;
  const ds = [];
  for (const g of v.groves || []) {
    for (let i = 0; i < g.count; i++) {
      const dx = g.xs[i] - c.x, dz = g.zs[i] - c.z;
      ds.push(Math.hypot(dx, dz));
    }
  }
  let drawn = 0;
  for (const g of v.groves || []) drawn += g.mesh.count;
  ds.sort((a, b) => a - b);
  const q = f => ds.length ? Math.round(ds[Math.floor(f * (ds.length - 1))]) : 0;
  return {scattered: ds.length, drawn,
          p10: q(0.10), p50: q(0.50), p90: q(0.90), max: q(1)};
});
console.log(JSON.stringify(dist));

await p.screenshot({path: pre + '-on.png'});
await p.evaluate(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  for (const g of v.groves || []) g.mesh.visible = false;
});
await p.waitForTimeout(400);
await p.screenshot({path: pre + '-off.png'});
await p.evaluate(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  for (const g of v.groves || []) g.mesh.visible = true;
  v.matGrove.wireframe = true;
});
await p.waitForTimeout(400);
await p.screenshot({path: pre + '-wire.png'});
await b.close();
