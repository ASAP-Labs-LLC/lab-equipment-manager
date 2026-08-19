/* sk-mainfogab.mjs — does sky.js's fog reach the mainland at all?
 *
 * `_rangeMaterial` declares `fog: false`, its compiled fragment shader has no
 * fog chunk and its program has no USE_FOG define. That is the static case for
 * excluding sky.js. This is the dynamic one: move `scene.fog.density` by a
 * factor of six in the live page and measure the mainland band and the open sea
 * beside it. A surface that takes scene.fog moves; one that does not, does not.
 *
 * Also crops the mainland band out of the frame at 3x, so the measured rows can
 * be looked at rather than only read.
 *
 *   node sk-mainfogab.mjs
 */
import {chromium} from 'playwright';
import fs from 'fs';

const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation,buildings,rail,trains'
          + '&cam=far&time=9&hud=0&quality=ultra&weather=clear';
const OUT = '/Users/rynatical/LAB-lem/scratchpad/harness/mainfog';
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
const grab = async (key, file) => {
  await page.waitForTimeout(450);
  const buf = await page.screenshot({type: 'png'});
  if (file) fs.writeFileSync(file, buf);
  await page.evaluate(async ({key, src}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    window.__caps[key] = {w: im.width, h: im.height, d: g.getImageData(0, 0, im.width, im.height).data};
  }, {key, src: 'data:image/png;base64,' + buf.toString('base64')});
};

const d0 = await page.evaluate(() => window.__lemWorld.scene.fog.density);
await grab('fog1x', null);
await page.evaluate(d => { window.__lemWorld.scene.fog.density = d * 6; }, d0);
await grab('fog6x', null);
await page.evaluate(d => { window.__lemWorld.scene.fog.density = d * 0.05; }, d0);
await grab('fog005x', null);
await page.evaluate(d => { window.__lemWorld.scene.fog.density = d; }, d0);

/* the crop, at the delivered settings */
await page.evaluate(() => { window.__lemWorld.engine._passes.composite.material.uniforms.uFilmGrain.value = 0.012; });
await page.waitForTimeout(400);
const full = await page.screenshot({type: 'png'});
const crop = await page.evaluate(async src => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas');
  cv.width = 1280; cv.height = 230 * 3;
  const g = cv.getContext('2d');
  g.imageSmoothingEnabled = false;
  g.drawImage(im, 0, 0, 1280, 230, 0, 0, 1280, 230 * 3);
  return cv.toDataURL('image/png');
}, 'data:image/png;base64,' + full.toString('base64'));
fs.writeFileSync(OUT + '.band3x.png', Buffer.from(crop.split(',')[1], 'base64'));

const out = await page.evaluate(() => {
  const C = window.__caps, W = C.fog1x.w;
  const L = (c, x, y) => { const o = (y * c.w + x) * 4; return 0.2126 * c.d[o] + 0.7152 * c.d[o + 1] + 0.0722 * c.d[o + 2]; };
  const mean = (k, y0, y1, xs) => { let s = 0, n = 0;
    for (let y = y0; y <= y1; y += 2) for (const x of xs) { s += L(C[k], x, y); n++; }
    return +(s / n).toFixed(2); };
  const xs = [], seaXs = [];
  for (let x = 200; x < 1100; x += 20) xs.push(x);
  for (let x = 60; x < 360; x += 10) seaXs.push(x);
  const keys = ['fog1x', 'fog6x', 'fog005x'];
  const r = {};
  for (const k of keys) r[k] = {
    mainlandBand_rows20_110: mean(k, 20, 110, xs),
    mainlandBand_rows110_125: mean(k, 110, 125, xs),
    openSea_rows215_260: mean(k, 215, 260, seaXs),
    islandGround_rows430_470: mean(k, 430, 470, seaXs.map(x => x + 380)),
  };
  return r;
});
console.log(JSON.stringify({baseDensity: d0, out}, null, 1));
await b.close();
