/* worldsettled.mjs — wait until the site is worth photographing.
 *
 * Three other agents are rewriting terrain, vegetation and rail in the same
 * hour, and a lighting change photographed while the ground is an untextured
 * white blob or the forest has not been planted proves nothing about the
 * lighting. This polls one page until the frame passes three cheap sanity
 * tests, then exits 0:
 *
 *   subsystems   every requested module loaded, no console errors
 *   forest       the vegetation subsystem has planted something
 *   ground       the terrain frame has texture in it — a spread between the
 *                fifth and ninety-fifth percentile of luminance, which a flat
 *                white blob and a mirror both fail
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const TRIES = parseInt(args.tries || '40', 10);
const EVERY = parseInt(args.every || '45000', 10);
const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=yard&time=16&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});

for (let attempt = 1; attempt <= TRIES; attempt++) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e).slice(0, 120)));
  let r = null;
  try {
    await page.goto(url, {waitUntil: 'load', timeout: 60000});
    await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
    await page.waitForTimeout(4500);
    r = await page.evaluate(() => {
      const w = window.__lemWorld;
      let veg = 0;
      w.scene.traverse(o => {
        if (!o.visible) return;
        if (!/veg|tree|leaf|canopy|trunk|shrub|grass|frond/i.test(o.name || '')) return;
        veg += o.isInstancedMesh ? (o.count | 0) : 1;
      });
      /* Read the frame back off the canvas and take the ground half of it —
       * the bottom third, which at cam=yard is terrain and nothing else. */
      const cv = document.querySelector('canvas');
      const c2 = document.createElement('canvas');
      c2.width = 160; c2.height = 90;
      const g = c2.getContext('2d');
      g.drawImage(cv, 0, 0, 160, 90);
      const px = g.getImageData(0, 60, 160, 30).data;
      const L = [];
      for (let i = 0; i < px.length; i += 4) {
        L.push(0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]);
      }
      L.sort((a, b) => a - b);
      const q = f => Math.round(L[Math.floor(L.length * f)]);
      return {
        subsystems: [...w.subsystems.keys()],
        veg, p05: q(0.05), p50: q(0.5), p95: q(0.95),
        draws: w.engine.renderer.info.render.calls,
        tris: w.engine.renderer.info.render.triangles,
      };
    });
  } catch (e) { r = {error: String(e).slice(0, 120)}; }
  await page.close();

  const spread = r && r.p95 !== undefined ? r.p95 - r.p05 : 0;
  const ok = r && !r.error && errors.length === 0 &&
             r.subsystems.length === 8 && r.veg > 500 &&
             spread > 25 && r.p50 < 215;
  console.log(`${new Date().toISOString().slice(11, 19)} try ${attempt} ` +
              `veg=${r?.veg} ground p05/p50/p95=${r?.p05}/${r?.p50}/${r?.p95} ` +
              `spread=${spread} subs=${r?.subsystems?.length} ` +
              `errs=${errors.length} ${ok ? 'SETTLED' : ''}`);
  if (ok) { await browser.close(); process.exit(0); }
  await new Promise(res => setTimeout(res, EVERY));
}
await browser.close();
process.exit(1);
