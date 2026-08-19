/* twho.mjs — which map is actually painting the shadows on this site?
 *
 * The consists are provably IN three's near shadow map (tmapdiff.mjs draws
 * them) and provably absent from the ground beside them, while a relay box
 * three metres away casts correctly. A lookup cannot be selective per caster —
 * unless the shadows we can see are not coming from that map at all. So: turn
 * cascade 0 off and see what survives; then enrol the trains in the coarse
 * cascades and see what appears.
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
const OUT = path.resolve(args.out || '../shots/twho');
fs.mkdirSync(path.dirname(OUT), {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await (await browser.newContext({viewport: {width: 1280, height: 720}})).newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(6000);
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.engine.updaters = w.engine.updaters.filter(m => m !== w.subsystems.get('trains'));
  w.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(700);
await page.screenshot({path: OUT + '-0-asis.png'});

console.log(JSON.stringify(await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  return {csm: gi._csm.map(c => ({i: c.i, layer: c.layer, n: c.casters.length,
                                  size: c.rt?.width, spec: c.spec})),
          uniforms: Object.keys(gi.uniforms).filter(k => /csm|cascade|box/i.test(k))};
})));

/* 1. cascade 0 off. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.subsystems.get('gi').sun.castShadow = false;
  w.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(700);
await page.screenshot({path: OUT + '-1-no-cascade0.png'});

/* 2. cascade 0 back, coarse cascades emptied. */
const off = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  gi.sun.castShadow = true;
  const saved = [];
  for (const c of gi._csm) { saved.push(c.rt); c.dirty = true; }
  /* Zero the strength uniform if there is one; otherwise hide every caster. */
  const hidden = [];
  for (const c of gi._csm) for (const o of c.casters) {
    if (!hidden.includes(o)) hidden.push(o);
  }
  window.__hidden = hidden;
  for (const o of hidden) o.visible = false;
  for (const c of gi._csm) c.dirty = true;
  w.engine.shadowNeedsUpdate = true;
  return hidden.length;
});
console.log('coarse casters hidden:', off);
await page.waitForTimeout(1200);
await page.screenshot({path: OUT + '-2-no-coarse-casters.png'});
await page.evaluate(() => { for (const o of window.__hidden) o.visible = true; });

/* 3. trains enrolled in the coarse cascades. */
const en = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), tr = w.subsystems.get('trains');
  let n = 0;
  tr.root.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh)) return;
    if (o === tr.pMesh || o === tr.contactMesh) return;
    if (o.material?.isMeshBasicMaterial) return;
    o.userData.lemCastBase = true;
    o.userData.lemKeepShadow = true;
    n++;
  });
  gi._adopt();
  for (const c of gi._csm) c.dirty = true;
  w.engine.shadowNeedsUpdate = true;
  return {n, csm: gi._csm.map(c => c.casters.length),
          enrolled: (() => { let k = 0; tr.root.traverse(o => {
            if (o.isMesh && gi._csm.some(c => o.layers.isEnabled(c.layer))) k++; }); return k; })()};
});
console.log('enrolled', JSON.stringify(en));
await page.waitForTimeout(1500);
await page.screenshot({path: OUT + '-3-trains-enrolled.png'});

await browser.close();
console.log('wrote', OUT + '-*.png');
