/* !! THIS INSTRUMENT GAVE A CONFIDENT WRONG ANSWER — READ BEFORE USING !!
 *
 * As written below it reported "0 changed pixels" for a decal layer that was
 * plainly visible in a screenshot. The cause is the exposure pin: defining
 * `gi._expNow` as non-writable makes gi's per-frame write throw, the world's
 * update loop dies, rendering freezes, and BOTH halves of the pair are the same
 * frozen frame — which compares as "no difference".
 *
 * Pinning the exposure across an A/B is right. Pin it with a no-op SETTER:
 *
 *     Object.defineProperty(gi, '_expNow',
 *       {get: () => v, set: () => {}, configurable: true});
 *
 * It is also confounded by animation: the sea moves between the two shots, so
 * a whole-frame pixel diff reports hundreds of thousands of changed pixels
 * whatever the decals do. Compare a window over LAND, or take alternating
 * pairs and use a median.
 *
 * The reliable ablation for "is this mesh reaching the framebuffer at all" is
 * the DRAW-CALL one: toggle `visible` and read `renderer.info.render`. It gave
 * the correct answer immediately (202 -> 200 calls, 776 triangles).
 */
/* pr-decal.mjs — is the drawn shade actually reaching the framebuffer?
 *
 * A paired ablation on the SAME pixels of the SAME frame: screenshot with the
 * decal mesh visible, screenshot with it hidden, and report the per-pixel
 * luminance delta over the region the props occupy. An effect that measures
 * zero here is not subtle, it is absent.
 *
 * The exposure is LOCKED and `_expNow` PINNED across both halves — otherwise
 * the meter adapts to the darkening and measures itself.
 *
 *   node pr-decal.mjs [--cam far]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import zlib from 'node:zlib';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) { args[a.slice(2)] = process.argv[i + 1]; i++; }
}
const W = 1600, H = 900;
const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await (await b.newContext({viewport: {width: W, height: H}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=' + MODS +
  '&cam=' + (args.cam || 'far') + '&time=9&weather=clear&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(8000);

/* LOCK AND PIN. gi.setExposureLocked freezes the stop; _expNow is pinned to the
 * same number in both halves so the two runs cannot adapt differently. */
const exp = await p.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.setExposureLocked?.(true);
  return gi._expNow;
});
await p.evaluate(v => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi._expNow = v;
  Object.defineProperty(gi, '_expNow', {value: v, writable: false, configurable: true});
}, exp);
await p.waitForTimeout(600);

const meta = await p.evaluate(() => {
  const pr = window.__lemWorld.subsystems.get('props');
  let d = null;
  pr.group.traverse(o => { if (o.name === 'props:decals') d = o; });
  const tri = d ? (d.geometry.index.count / 3) : 0;
  return {found: !!d, tris: tri, shade: pr.shade,
          visible: d ? d.visible : null,
          matType: d?.material?.type, blending: d?.material?.blending,
          premul: d?.material?.premultipliedAlpha, transparent: d?.material?.transparent,
          renderOrder: d?.renderOrder,
          inFrustum: d ? d.frustumCulled : null};
});

const shoot = async (on) => {
  await p.evaluate(v => {
    const pr = window.__lemWorld.subsystems.get('props');
    pr.group.traverse(o => { if (o.name === 'props:decals') o.visible = v; });
  }, on);
  await p.waitForTimeout(900);
  return await p.screenshot({type: 'png'});
};
const A = await shoot(true), B = await shoot(false);
fs.writeFileSync('/tmp/pr-decal-on.png', A);
fs.writeFileSync('/tmp/pr-decal-off.png', B);

/* compare in the page: cheaper than decoding PNGs here */
const cmp = await p.evaluate(async ([a, b, W, H]) => {
  const load = src => new Promise(res => {
    const i = new Image(); i.onload = () => res(i); i.src = src;
  });
  const ia = await load(a), ib = await load(b);
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d', {willReadFrequently: true});
  g.drawImage(ia, 0, 0); const da = g.getImageData(0, 0, W, H).data;
  g.clearRect(0, 0, W, H);
  g.drawImage(ib, 0, 0); const db = g.getImageData(0, 0, W, H).data;
  let changed = 0, sum = 0, worst = 0, wx = 0, wy = 0;
  const lum = (d, k) => 0.2126 * d[k] + 0.7152 * d[k + 1] + 0.0722 * d[k + 2];
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const k = (y * W + x) * 4;
      const dl = lum(db, k) - lum(da, k);      // off minus on: positive = darkened
      if (Math.abs(dl) > 1.5) { changed++; sum += dl; }
      if (dl > worst) { worst = dl; wx = x; wy = y; }
    }
  }
  return {changedPx: changed, meanDeltaL: changed ? +(sum / changed).toFixed(2) : 0,
          maxDeltaL: +worst.toFixed(1), at: [wx, wy]};
}, ['data:image/png;base64,' + A.toString('base64'),
    'data:image/png;base64,' + B.toString('base64'), W, H]);

console.log(JSON.stringify({exposurePinned: exp, ...meta, ...cmp}, null, 1));
await b.close();
