/* gimatte.mjs — the clay render.
 *
 * Every material in the scene replaced by one white MeshStandardMaterial, no
 * fog, no environment, no probe fill. What is left on the ground is N·L times
 * the shadow mask and nothing else, so the *shape* of a shadow can be read
 * instead of guessed at through albedo, aerial perspective and a black point
 * that crushes every shadow interior to the same code.
 *
 * This is the only view in which "is that a train-shaped shadow" is a question
 * with an answer.
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
const OUT = path.resolve(args.out || '../shots/gimatte');
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
  window.__p = setInterval(() => w.parse(uids[i++ % uids.length], 'L-MAT'), 900);
});
await page.waitForTimeout(parseInt(args.warm || '9000', 10));
await page.evaluate(() => {
  clearInterval(window.__p);
  window.__lemWorld.subsystems.get('trains').update = () => {};
});
await page.waitForTimeout(2000);

await page.screenshot({path: path.join(OUT, 'beauty.png')});

await page.evaluate(() => {
  const w = window.__lemWorld;
  const THREE = w.ctx.THREE;
  const gi = w.subsystems.get('gi');
  const clay = new THREE.MeshStandardMaterial({color: 0xbbbbbb, roughness: 1,
                                               metalness: 0, fog: false});
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
    const m = Array.isArray(o.material) ? o.material[0] : o.material;
    if (!m || m.transparent || m.depthWrite === false) return;
    if (m.alphaTest > 0 || m.alphaMap) return;   // keep foliage cut-outs honest
    o.userData.__mat = o.material;
    o.material = clay;
  });
  w.scene.fog = null;
  w.scene.environmentIntensity = 0;
  gi.uniforms.lemGIStrength.value = 0.02;
  gi.uniforms.lemAOStrength.value = 0;
  gi.uniforms.lemSkyIrradiance.value.set(1, 1, 1);
  gi.uniforms.lemGroundIrradiance.value.set(1, 1, 1);
});
await page.waitForTimeout(1500);
await page.screenshot({path: path.join(OUT, 'clay.png')});
console.log('wrote', OUT);
await browser.close();
