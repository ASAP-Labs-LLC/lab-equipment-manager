/* gichild.mjs — hide one child of one scene group at a time. The root-level
 * census says which subsystem owns the dark band; this says which mesh. */
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
const OUT = path.resolve(args.out || '../shots/gichild');
const GROUP = args.group || 'rail';
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
  window.__p = setInterval(() => w.parse(uids[i++ % uids.length], 'L-CH'), 900);
});
await page.waitForTimeout(parseInt(args.warm || '9000', 10));
await page.evaluate(() => {
  clearInterval(window.__p);
  window.__lemWorld.subsystems.get('trains').update = () => {};
});
await page.waitForTimeout(2500);

const kids = await page.evaluate(g => {
  const w = window.__lemWorld;
  const grp = w.scene.children.find(o => o.name === g);
  window.__grp = grp;
  return grp.children.map((o, i) => ({
    i, name: o.name || o.type, type: o.type, vis: o.visible,
    cast: !!o.castShadow, count: o.count | 0,
    tris: o.geometry?.index ? o.geometry.index.count / 3
                            : (o.geometry?.attributes?.position?.count || 0) / 3,
    r: +((o.geometry?.boundingSphere?.radius) || 0).toFixed(1),
    kids: o.children.length,
  }));
}, GROUP);
console.log(JSON.stringify(kids, null, 0));

const shot = async name => {
  await page.waitForTimeout(950);
  await page.screenshot({path: path.join(OUT, name + '.png')});
};
await shot('base');
for (const k of kids) {
  if (!k.vis) continue;
  await page.evaluate(i => {
    window.__k = window.__grp.children[i];
    window.__k.visible = false;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  }, k.i);
  await shot('hide-' + String(k.i).padStart(2, '0') + '-' + k.name.replace(/[^a-z0-9]+/gi, '_'));
  await page.evaluate(() => {
    window.__k.visible = true;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  });
  console.log('  ' + k.i + ' ' + k.name);
}
await browser.close();
