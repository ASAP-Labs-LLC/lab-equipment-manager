/* thide.mjs — does the rolling stock contribute anything to the shadow map?
 *
 * The flag is set and the objects reach the shadow pass (see tshadow.mjs), yet
 * six rounds of critics say the ground under a consist is unshaded. So this
 * asks the only question that settles it: photograph the frame, hide the trains
 * and force a shadow refresh, photograph it again. Whatever the trains were
 * putting on the ground is the difference between the two, minus the vehicles
 * themselves.
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
const OUT = path.resolve(args.out || '../shots/hide');
fs.mkdirSync(path.dirname(OUT), {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(args.mods || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather')}` +
  `&cam=${args.cam || 'yard'}${args.at ? '&at=' + args.at : ''}` +
  `&time=${args.time || 16}&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await (await browser.newContext({viewport: {width: 1280, height: 720}})).newPage();
await page.goto(url, {waitUntil: 'load'});
await page.waitForFunction(() => window.__lemWorld?.subsystems?.size > 0, null, {timeout: 60000});
await page.waitForTimeout(5000);

/* Freeze the world so the two frames differ by exactly one thing. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.engine.updaters = w.engine.updaters.filter(m => m !== w.subsystems.get('trains'));
  w.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(600);
await page.screenshot({path: OUT + '-with.png'});

await page.evaluate(() => {
  const w = window.__lemWorld;
  w.subsystems.get('trains').root.visible = false;
  w.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(600);
await page.screenshot({path: OUT + '-without.png'});
await browser.close();
console.log('wrote', OUT + '-with.png', OUT + '-without.png');
