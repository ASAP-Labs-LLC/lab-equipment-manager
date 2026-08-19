/* vegwatch.mjs — is there a forest on the site right now?
 *
 * gi.js's acceptance for the no-GI rung is "the forest intact", and vegetation
 * is being rewritten by another agent in the same hour. A run that loads
 * `vegetation` without a console error but draws no trees looks identical, in
 * every sidecar this harness writes, to a run with a forest in it — and a
 * lighting change photographed against an empty field proves nothing.
 *
 * So: count the vegetation subsystem's visible instances before shooting.
 */
import {chromium} from 'playwright';

const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=yard&time=16&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 140)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(4000);
const out = await page.evaluate(() => {
  const w = window.__lemWorld;
  let meshes = 0, instances = 0, tris = 0;
  w.scene.traverse(o => {
    if (!o.visible) return;
    const tag = o.userData?.lemVeg || /veg|tree|leaf|canopy|trunk|frond|shrub|grass/i.test(o.name || '');
    if (!tag) return;
    meshes++;
    instances += o.isInstancedMesh ? (o.count | 0) : 1;
    const g = o.geometry;
    if (g?.index) tris += (g.index.count / 3) * (o.isInstancedMesh ? (o.count | 0) : 1);
  });
  return {
    loaded: w.subsystems.has('vegetation'),
    meshes, instances, tris: Math.round(tris),
    draws: w.engine.renderer.info.render.calls,
    sceneTris: w.engine.renderer.info.render.triangles,
  };
});
console.log(JSON.stringify({...out, errors}));
await browser.close();
process.exit(out.instances > 200 ? 0 : 1);
