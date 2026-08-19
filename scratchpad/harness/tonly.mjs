/* tonly.mjs — the trains, and nothing else, casting.
 *
 * Reading a shadow out of a full frame is guesswork: the apron beside a consist
 * is already under a gantry, a building and a tree line. So switch `castShadow`
 * off on every object in the scene except the rolling stock, refresh the map,
 * and photograph it. Every dark patch on the ground in the result was thrown by
 * a train. `--invert` does the opposite, for the control.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const OUT = path.resolve(args.out || '../shots/tonly');
fs.mkdirSync(path.dirname(OUT), {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}${args.at ? '&at=' + args.at : ''}` +
  `&time=${args.time || '16'}&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await (await browser.newContext({viewport: {width: 1280, height: 720}})).newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(6000);

const n = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), tr = w.subsystems.get('trains');
  /* Stop everything moving, and stop gi taking the flags back. */
  w.engine.updaters = [];
  const trainSet = new Set();
  tr.root.traverse(o => trainSet.add(o));
  let off = 0, on = 0;
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh)) return;
    if (trainSet.has(o)) { on++; return; }
    if (o.castShadow) { o.castShadow = false; off++; }
  });
  /* The coarse cascades hold their own caster lists; empty them or the
   * buildings keep shading the far field. */
  for (const c of gi._csm || []) { c.casters.length = 0; c.dirty = true; }
  w.engine.shadowNeedsUpdate = true;
  return {off, on, contact: !!tr.contactMesh?.visible};
});
console.log('casters silenced:', JSON.stringify(n));
await page.waitForTimeout(1500);
await page.screenshot({path: OUT + '-trains-only.png'});

/* And with the drawn contact patches off too, so the only darkening left is a
 * genuine cast shadow. */
await page.evaluate(() => {
  const tr = window.__lemWorld.subsystems.get('trains');
  if (tr.contactMesh) tr.contactMesh.visible = false;
  window.__lemWorld.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(900);
await page.screenshot({path: OUT + '-trains-only-nocontact.png'});
await browser.close();
console.log('wrote', OUT + '-trains-only.png');
