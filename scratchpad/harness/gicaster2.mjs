/* gicaster2.mjs — the caster census, done with `visible` rather than
 * `castShadow`.
 *
 * The first attempt toggled `castShadow` and measured nothing, which was a
 * false negative: `_nearCull` rewrites that flag from `lemCastBase` on every
 * adopt sweep, so the toggle was undone before the shutter. `visible` is not a
 * flag any of this module drives, and three's shadow pass honours it.
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
const OUT = path.resolve(args.out || '../shots/gicaster2');
fs.mkdirSync(OUT, {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);
await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__p = setInterval(() => w.parse(uids[i++ % uids.length], 'L-CAST'), 900);
});
await page.waitForTimeout(parseInt(args.warm || '9000', 10));
await page.evaluate(() => {
  clearInterval(window.__p);
  window.__lemWorld.subsystems.get('trains').update = () => {};
});
await page.waitForTimeout(2500);

const kids = await page.evaluate(() => {
  const w = window.__lemWorld;
  return w.scene.children
    .filter(o => o.visible)
    .map((o, i) => ({i, name: o.name || o.type, type: o.type,
                     kids: o.children.length}));
});
console.log(JSON.stringify(kids));

const shot = async name => {
  await page.waitForTimeout(1000);
  await page.screenshot({path: path.join(OUT, name + '.png')});
  console.log('  ' + name);
};
await shot('base');

for (const k of kids) {
  const ok = await page.evaluate(i => {
    const o = window.__lemWorld.scene.children[i];
    if (!o) return false;
    window.__o = o; o.visible = false;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
    return true;
  }, k.i);
  if (!ok) continue;
  await shot('hide-' + String(k.i).padStart(2, '0') + '-' + k.name.replace(/[^a-z0-9]+/gi, '_'));
  await page.evaluate(() => {
    window.__o.visible = true;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  });
}
await browser.close();
