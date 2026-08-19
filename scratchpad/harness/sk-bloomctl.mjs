/* sk-bloomctl.mjs — the positive control for the bloom result.
 *
 * sk-mainbloom.mjs got a zero image difference at uBloom 0, 0.55, 4 and 20.
 * That is only meaningful if a uniform written from page.evaluate reaches the
 * GPU at all. So: drop the bright pass's own threshold from 1.05 to 0.02, which
 * must make bloom appear if the path is live, and separately move uExposure,
 * which must move every pixel. If those two move the frame and uBloom does not,
 * the bloom texture is genuinely black and bloom cannot be making a rim.
 *
 *   node sk-bloomctl.mjs
 */
import {chromium} from 'playwright';

const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation,buildings,rail,trains'
          + '&cam=far&time=9&hud=0&quality=ultra&weather=clear';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null; const t1 = Date.now();
while (Date.now() - t1 < 30000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => { const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now; if (stable >= 10) break;
}
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.rig.idleDrift = false; w.rig.apply(1); w.parse = () => {};
  w.engine.clock.getDelta = () => 0;
  w.engine._passes.composite.material.uniforms.uFilmGrain.value = 0;
  window.__caps = {};
});
const grab = async key => {
  await page.waitForTimeout(400);
  const buf = await page.screenshot({type: 'png'});
  await page.evaluate(async ({key, src}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    window.__caps[key] = {w: im.width, h: im.height, d: g.getImageData(0, 0, im.width, im.height).data};
  }, {key, src: 'data:image/png;base64,' + buf.toString('base64')});
};
await grab('base');
await page.evaluate(() => { window.__lemWorld.engine._passes.composite.material.uniforms.uExposure.value *= 1.15; });
await grab('exposure');
await page.evaluate(() => { const P = window.__lemWorld.engine._passes;
  P.composite.material.uniforms.uExposure.value /= 1.15;
  P.bright.material.uniforms.uThreshold.value = 0.02;
  P.bright.material.uniforms.uSoft.value = 0.02; });
await grab('lowThreshold');
await page.evaluate(() => { window.__lemWorld.engine._passes.composite.material.uniforms.uBloom.value = 0; });
await grab('lowThresholdNoBloom');

const out = await page.evaluate(() => {
  const C = window.__caps, W = C.base.w, H = C.base.h;
  const L = (c, x, y) => { const o = (y * c.w + x) * 4; return 0.2126 * c.d[o] + 0.7152 * c.d[o + 1] + 0.0722 * c.d[o + 2]; };
  const cmp = k => { let m = 0, s = 0, n = 0;
    for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) { const o = (y * W + x) * 4;
      for (let c = 0; c < 3; c++) { const d = Math.abs(C[k].d[o + c] - C.base.d[o + c]); m = Math.max(m, d); s += d; n++; } }
    return {maxDiff: m, meanDiff: +(s / n).toFixed(3)}; };
  /* shoreline rim under a threshold low enough that bloom certainly fires */
  const rim = k => [[45, 185], [594, 128], [1142, 173]].map(([x, y]) =>
    ({x, prof: [-8, -4, -1, 0, 1, 4, 8].map(d => +L(C[k], x, Math.max(0, y + d)).toFixed(1))}));
  return {exposure: cmp('exposure'), lowThreshold: cmp('lowThreshold'),
          lowThresholdNoBloom: cmp('lowThresholdNoBloom'),
          rimBase: rim('base'), rimBloomForced: rim('lowThreshold')};
});
console.log(JSON.stringify(out, null, 1));
await b.close();
