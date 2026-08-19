/* tcontact.mjs — the drawn contact patch, on and off, at the judged camera.
 *
 * The real cast shadow works: with the trains as the only casters, three's near
 * map paints a proper articulated consist on the ground (tterm.mjs), and hiding
 * the trains lifts the ground under one by 36 codes. So the patch is not making
 * up for a missing shadow — the question is only whether it is the "hard-edged
 * pure-black quad" six rounds of critics have described. Two frames, same page.
 */
import {chromium} from 'playwright';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const OUT = path.resolve(args.out || '../shots/tcontact');
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'street'}&at=${args.at || 'multitek-ns'}` +
  `&time=${args.time || '16'}&weather=clear&hud=0&quality=ultra`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await (await b.newContext({viewport: {width: 1920, height: 1080}})).newPage();
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await p.goto(url, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(7000);
await p.evaluate(() => {
  const w = window.__lemWorld;
  w.engine.updaters = w.engine.updaters.filter(m => m !== w.subsystems.get('trains'));
  w.engine.shadowNeedsUpdate = true;
});
await p.waitForTimeout(700);
await p.screenshot({path: OUT + '-patch-on.png'});
await p.evaluate(() => {
  const w = window.__lemWorld, tr = w.subsystems.get('trains');
  if (tr.contactMesh) tr.contactMesh.visible = false;
  w.engine.shadowNeedsUpdate = true;
});
await p.waitForTimeout(700);
await p.screenshot({path: OUT + '-patch-off.png'});
await b.close();
console.log('wrote', OUT + '-patch-on.png', OUT + '-patch-off.png');
