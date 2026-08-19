/* sk-mainshade.mjs — WHICH TERM IN _rangeMaterial FLATTENS THE MAINLAND?
 *
 * sk-mainflat.mjs established that the band's internal variation is 1/58th of
 * the island's by mean adjacent-pixel difference, that scene.fog cannot be
 * responsible (fog:false, and a 6x fog A/B moves the band by 0.00 L), and that
 * bloom adds exactly zero codes (nothing in the frame passes the 1.05 bright
 * threshold). What is left is terrain.js's own shader, and this takes it apart.
 *
 * The mainland's material is a separate ShaderMaterial instance from the far
 * ranges' (`_rangeMaterial` is called twice), so its fragment source can be
 * hot-swapped in the live page without touching anything else on screen. Each
 * variant knocks out one term and the same pixels are re-measured:
 *
 *   stock     as shipped
 *   noHaze    haze = 0            — the relief the mesh actually has
 *   flatHaze  mix(1.12,0.86)->1.0 — the foot/crest split that makes the rim
 *   noSand    the strand removed  — the other candidate for the rim
 *   noLambert ndl pinned          — how much of the variation is the sun term
 *
 * Nothing on disk is modified; the swap lives and dies with the page.
 *
 *   node sk-mainshade.mjs
 */
import {chromium} from 'playwright';
import fs from 'fs';

const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation,buildings,rail,trains'
          + '&cam=far&time=9&hud=0&quality=ultra&weather=clear';
const OUT = '/Users/rynatical/LAB-lem/scratchpad/harness/mainshade';

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
  prev = now; if (stable >= 10) break;
}
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.rig.idleDrift = false; w.rig.apply(1); w.parse = () => {};
  w.engine.clock.getDelta = () => 0;
  const cu = w.engine._passes.composite.material.uniforms;
  cu.uFilmGrain.value = 0; cu.uBloom.value = 0;
  window.__caps = {};
  let M = null; w.scene.traverse(o => { if (o.name === 'terrain-mainland') M = o; });
  window.__M = M; window.__FS0 = M.material.fragmentShader;
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

const swap = async (subs) => page.evaluate(subs => {
  let s = window.__FS0;
  for (const [from, to] of subs) {
    if (s.indexOf(from) < 0) throw new Error('substitution missed: ' + from);
    s = s.split(from).join(to);
  }
  window.__M.material.fragmentShader = s;
  window.__M.material.needsUpdate = true;
}, subs);

const HAZE_LINE = 'haze = mix(haze, clamp(far * mix(1.12, 0.86, vUp), 0.0, 0.92), vLit);';
await grab('stock', OUT + '.stock.png');
await swap([[HAZE_LINE, 'haze = 0.0;']]);
await grab('noHaze', OUT + '.nohaze.png');
await swap([[HAZE_LINE, 'haze = mix(haze, clamp(far * 1.0, 0.0, 0.92), vLit);']]);
await grab('flatHaze', OUT + '.flathaze.png');
await swap([['vec3 sand = vec3(0.52, 0.47, 0.37) * lum;',
             'vec3 sand = vec3(0.13, 0.20, 0.10) * lum * 2.1;']]);
await grab('noSand', OUT + '.nosand.png');
await swap([['float ndl = clamp(dot(normalize(vN), normalize(uSunDir)), 0.0, 1.0);',
             'float ndl = 0.7;']]);
await grab('noLambert', null);
await swap([]);                        // back to stock
await page.evaluate(() => { window.__M.visible = false; });
await grab('hidden', null);
await page.evaluate(() => { window.__M.visible = true; });

const out = await page.evaluate(() => {
  const C = window.__caps, W = C.stock.w, H = C.stock.h;
  const L = (c, x, y) => { const o = (y * c.w + x) * 4; return 0.2126 * c.d[o] + 0.7152 * c.d[o + 1] + 0.0722 * c.d[o + 2]; };
  const RGB = (c, x, y) => { const o = (y * c.w + x) * 4; return [c.d[o], c.d[o + 1], c.d[o + 2]]; };
  /* the footprint, by difference against the frame with the mesh hidden */
  const m = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const o = (y * W + x) * 4;
    let d = 0; for (let c = 0; c < 3; c++) d = Math.max(d, Math.abs(C.stock.d[o + c] - C.hidden.d[o + c]));
    if (d >= 8) m[y * W + x] = 1;
  }
  const cols = [];
  for (let x = 0; x < W; x++) { let top = -1, bot = -1, n = 0;
    for (let y = 0; y < H; y++) if (m[y * W + x]) { if (top < 0) top = y; bot = y; n++; }
    cols.push({x, top, bot, n}); }
  const live = cols.filter(c => c.n > 6);
  const picks = [];
  for (let k = 0; k < 8; k++) { const c = live[Math.floor((k + 0.5) / 8 * live.length)]; if (c) picks.push(c); }

  const stat = key => {
    const cap = C[key]; const v = []; let adj = 0, an = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      if (!m[y * W + x]) continue;
      const l = L(cap, x, y); v.push(l);
      if (x + 1 < W && m[y * W + x + 1]) { adj += Math.abs(L(cap, x + 1, y) - l); an++; }
    }
    v.sort((p, q) => p - q);
    const mn = v.reduce((s, x) => s + x, 0) / v.length;
    const q = f => v[Math.floor(f * v.length)];
    return {n: v.length, mean: +mn.toFixed(2),
            sd: +Math.sqrt(v.reduce((s, x) => s + (x - mn) ** 2, 0) / v.length).toFixed(2),
            p5: +q(0.05).toFixed(1), p50: +q(0.5).toFixed(1), p95: +q(0.95).toFixed(1),
            p95_p5: +(q(0.95) - q(0.05)).toFixed(1),
            meanAbsAdjH: +(adj / an).toFixed(3)};
  };
  const rim = key => picks.map(c => {
    const cap = C[key], y0 = c.bot, p = [];
    for (let dy = -12; dy <= 12; dy++) p.push(+L(cap, c.x, Math.min(H - 1, Math.max(0, y0 + dy))).toFixed(1));
    const inside = p.slice(0, 7).reduce((s, x) => s + x, 0) / 7;
    const outside = p.slice(18, 25).reduce((s, x) => s + x, 0) / 7;
    return {x: c.x, y: y0, prof: p, interior12up: +inside.toFixed(1), sea: +outside.toFixed(1),
            peak: +Math.max(...p.slice(0, 13)).toFixed(1),
            overInterior: +(Math.max(...p.slice(0, 13)) - inside).toFixed(1)};
  });
  const rgbRim = picks.map(c => ({x: c.x, y: c.bot,
    interior: RGB(C.stock, c.x, Math.max(0, c.bot - 12)),
    rim: RGB(C.stock, c.x, c.bot),
    sea: RGB(C.stock, c.x, Math.min(H - 1, c.bot + 8)),
    bandTop: RGB(C.stock, c.x, Math.max(0, c.top + 4))}));

  const keys = ['stock', 'noHaze', 'flatHaze', 'noSand', 'noLambert'];
  const stats = {}; const rims = {};
  for (const k of keys) { stats[k] = stat(k); rims[k] = rim(k); }
  return {maskPx: cols.reduce((s, c) => s + c.n, 0), stats,
          rimStock: rims.stock, rimNoHaze: rims.noHaze, rimFlatHaze: rims.flatHaze,
          rimNoSand: rims.noSand, rgbRim};
});
console.log(JSON.stringify({out, errors}, null, 1));
await b.close();
