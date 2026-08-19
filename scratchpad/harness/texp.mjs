/* texp.mjs — A/B the one runtime difference between rolling stock and the
 * trackside furniture that casts correctly beside it.
 *
 *   node texp.mjs --cam yard --time 16 --out ../shots/exp
 *
 * Shot A is the world as shipped. Shot B is the same page with
 * `userData.lemCastBase` forced true on every train mesh and gi's adopt sweep
 * re-run, which is the only thing that puts a caster into the two coarse
 * cascades. If B has train shadows and A does not, the cause is the base flag
 * being captured while the quality ladder was still at the floor tier.
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
const OUT = path.resolve(args.out || '../shots/exp');
fs.mkdirSync(path.dirname(OUT), {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(args.mods || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather')}` +
  `&cam=${args.cam || 'yard'}&time=${args.time || 16}&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await (await browser.newContext({viewport: {width: 1280, height: 720}})).newPage();
await page.goto(url, {waitUntil: 'load'});
await page.waitForFunction(() => window.__lemWorld?.subsystems?.size > 0, null, {timeout: 60000});
await page.waitForTimeout(5000);

const where = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), tr = w.subsystems.get('trains');
  const fit = gi?._shadowFit;
  const out = [];
  tr?.root?.traverse(o => {
    if (!o.isMesh || o.isInstancedMesh) return;
    if (!o.visible || !o.parent?.visible) return;
    const p = new (o.position.constructor)();
    o.getWorldPosition(p);
    let d = null;
    if (fit) {
      const rx = gi.uniforms.lemLightRight.value, ru = gi.uniforms.lemLightUp.value;
      const v = p.clone().sub(fit.centre);
      d = [Math.abs(v.dot(rx)).toFixed(0), Math.abs(v.dot(ru)).toFixed(0)];
    }
    out.push({p: p.toArray().map(n => +n.toFixed(0)), inBox: d, cast: o.castShadow,
              base: o.userData.lemCastBase,
              layers: [6, 7].map(b => o.layers.isEnabled(b))});
  });
  return {fitR: fit?.radius, fitC: fit?.centre?.toArray().map(n => +n.toFixed(0)),
          cam: w.engine.camera.position.toArray().map(n => +n.toFixed(0)), out};
});
console.log('fit r=%s c=%s cam=%s', where.fitR, JSON.stringify(where.fitC), JSON.stringify(where.cam));
for (const r of where.out.slice(0, 40)) console.log(JSON.stringify(r));

await page.screenshot({path: OUT + '-A.png'});

const patched = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), tr = w.subsystems.get('trains');
  let n = 0;
  tr.root.traverse(o => {
    if (o.isMesh || o.isInstancedMesh) {
      if (o.material?.transparent && o.material.type === 'MeshBasicMaterial') return;
      if (o === tr.pMesh || o === tr.contactMesh) return;
      o.userData.lemCastBase = true;
      o.userData.lemKeepShadow = true;
      n++;
    }
  });
  gi._adopt();
  w.engine.shadowNeedsUpdate = true;
  return {n, csm: gi._csm.map(c => c.casters.length)};
});
console.log('patched', JSON.stringify(patched));
await page.waitForTimeout(2500);
await page.screenshot({path: OUT + '-B.png'});

/* And a third: coarse cascades off entirely, so the contribution of cascade 0
 * alone is visible. */
await browser.close();
console.log('wrote', OUT + '-A.png', OUT + '-B.png');
