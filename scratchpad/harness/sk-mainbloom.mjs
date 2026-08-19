/* sk-mainbloom.mjs — IS THE BLOOM TOGGLE REAL, AND DOES BLOOM TOUCH THE BAND?
 *
 * sk-mainflat.mjs found uBloom 0.55 and uBloom 0 producing byte-identical
 * frames over the mainland. That is only evidence if the uniform is live, so
 * this sweeps it 0 / 0.55 / 4 / 20 and reports the band's mean luminance and
 * shoreline rim at each — a uniform that moves nothing at 20 was never wired.
 * It also reads the threshold pass's own uniforms and the HDR luminance of the
 * brightest thing near the band, which is what decides whether anything in
 * this frame passes the bright-pass at all.
 *
 *   node sk-mainbloom.mjs
 */
import {chromium} from 'playwright';
import fs from 'fs';

const MODS = 'sky,gi,terrain,vegetation,buildings,rail,trains';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}`
          + `&cam=far&time=9&hud=0&quality=ultra&weather=clear`;
const OUT = '/Users/rynatical/LAB-lem/scratchpad/harness/mainbloom';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null; const t1 = Date.now();
while (Date.now() - t1 < 30000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => { const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 10) break;
}
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.rig.idleDrift = false; w.rig.apply(1); w.parse = () => {};
  w.engine.clock.getDelta = () => 0;
  w.engine._passes.composite.material.uniforms.uFilmGrain.value = 0;
  window.__caps = {};
});

const passes = await page.evaluate(() => {
  const P = window.__lemWorld.engine._passes;
  const u = P.bright.material.uniforms;
  const out = {};
  for (const k in u) { const v = u[k].value; out[k] = (v && v.isVector2) ? [v.x, v.y] : (typeof v === 'number' ? v : String(v && v.constructor && v.constructor.name)); }
  return {brightUniforms: out, tierBloom: window.__lemWorld.engine.tier.bloom,
          uHasBloom: P.composite.material.uniforms.uHasBloom.value};
});

const grab = async (key, file) => {
  await page.waitForTimeout(400);
  const buf = await page.screenshot({type: 'png'});
  if (file) fs.writeFileSync(file, buf);
  await page.evaluate(async ({key, src}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas');
    cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true});
    g.drawImage(im, 0, 0);
    window.__caps[key] = {w: im.width, h: im.height, d: g.getImageData(0, 0, im.width, im.height).data};
  }, {key, src: 'data:image/png;base64,' + buf.toString('base64')});
};

for (const v of [0, 0.55, 4, 20]) {
  await page.evaluate(x => { window.__lemWorld.engine._passes.composite.material.uniforms.uBloom.value = x; }, v);
  await grab('b' + v, v === 20 ? OUT + '.b20.png' : null);
}
/* and with the bloom pass switched off at the tier, which is the other lever */
await page.evaluate(() => { window.__lemWorld.engine._passes.composite.material.uniforms.uBloom.value = 0.55;
                            window.__lemWorld.engine.tier.bloom = false; });
await grab('tierOff', null);

const out = await page.evaluate(() => {
  const C = window.__caps, W = C.b0.w;
  const L = (c, x, y) => { const o = (y * c.w + x) * 4; return 0.2126 * c.d[o] + 0.7152 * c.d[o + 1] + 0.0722 * c.d[o + 2]; };
  const keys = ['b0', 'b0.55', 'b4', 'b20', 'tierOff'];
  /* the shoreline row measured in sk-mainflat, per sample column */
  const cols = [[45, 185], [320, 140], [594, 128], [868, 132], [1142, 173]];
  const rows = {};
  for (const k of keys) {
    rows[k] = {
      band: (() => { let s = 0, n = 0; for (let y = 20; y < 110; y += 2) for (let x = 0; x < W; x += 4) { s += L(C[k], x, y); n++; } return +(s / n).toFixed(2); })(),
      sky_wouldbe: +L(C[k], 640, 2).toFixed(1),
      rim: cols.map(([x, y]) => cols.length && ({x, prof: [-6, -3, -1, 0, 1, 3, 6].map(d => +L(C[k], x, Math.max(0, y + d)).toFixed(1))})),
      maxDiffVsB0: (() => { let m = 0; for (let y = 0; y < C[k].h; y += 2) for (let x = 0; x < W; x += 2) {
        const o = (y * W + x) * 4;
        for (let c = 0; c < 3; c++) m = Math.max(m, Math.abs(C[k].d[o + c] - C.b0.d[o + c])); } return m; })(),
    };
  }
  return rows;
});
console.log(JSON.stringify({passes, out, errors}, null, 1));
await b.close();
