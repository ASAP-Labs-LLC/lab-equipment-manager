/* gicaster.mjs — who casts the blob?
 *
 * Freezes the world, then switches `castShadow` off one group at a time and
 * measures the mean luminance of a rectangle the caller points at the blob
 * with. Whatever makes the rectangle jump is the caster. This exists because
 * five rounds of critics have called this shape caster-less and every previous
 * search for its caster was done by looking, which cannot separate a shadow
 * from a dark hillside.
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
const OUT = path.resolve(args.out || '../shots/gicaster');
fs.mkdirSync(OUT, {recursive: true});
const RECT = (args.rect || '820,430,180,90').split(',').map(Number);

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
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
  const T = window.__lemWorld.subsystems.get('trains');
  T.update = () => {};
});
await page.waitForTimeout(2500);

/* The census: every mesh with castShadow on, tagged with the scene root it
 * hangs under, so a whole subsystem can be switched off at once. */
const roots = await page.evaluate(() => {
  const w = window.__lemWorld;
  const map = new Map();
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
    if (!o.castShadow) return;
    let p = o, top = o;
    while (p.parent && p.parent !== w.scene) { p = p.parent; top = p; }
    const key = top.name || top.type + '#' + top.id;
    map.set(key, (map.get(key) || 0) + 1);
  });
  window.__rootKey = o => {
    let p = o, top = o;
    while (p.parent && p.parent !== window.__lemWorld.scene) { p = p.parent; top = p; }
    return top.name || top.type + '#' + top.id;
  };
  return [...map].sort((a, b) => b[1] - a[1]);
});
console.log('casting roots:', JSON.stringify(roots));

const shot = async name => {
  await page.waitForTimeout(900);
  const p = path.join(OUT, name + '.png');
  await page.screenshot({path: p});
  return p;
};

const results = [];
await shot('base');
for (const [key] of roots) {
  await page.evaluate(k => {
    const w = window.__lemWorld;
    window.__off = [];
    w.scene.traverse(o => {
      if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
      if (!o.castShadow) return;
      if (window.__rootKey(o) !== k) return;
      window.__off.push(o);
      o.castShadow = false;
    });
    w.engine.shadowNeedsUpdate = true;
  }, key);
  await shot('off-' + key.replace(/[^a-z0-9]+/gi, '_'));
  await page.evaluate(() => {
    for (const o of window.__off) o.castShadow = true;
    window.__lemWorld.engine.shadowNeedsUpdate = true;
  });
  results.push(key);
}
fs.writeFileSync(path.join(OUT, 'roots.json'),
                 JSON.stringify({roots, rect: RECT, errors}, null, 2));
console.log('errors', errors.slice(0, 3));
await ctx.close();
await browser.close();
