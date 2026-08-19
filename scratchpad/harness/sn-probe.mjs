/* sn-probe.mjs — baseline read of gi's fill/key model and the frame's histogram
 * at the operator's camera. One page session, stop pinned. */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errs.push('c: ' + m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(10000);
await page.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true); });
await page.waitForTimeout(1500);

const model = await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi'), w = window.__lemWorld.ctx.weather || {};
  const u = gi.uniforms;
  return {
    fillE: gi._fillE, keyE: gi._keyE,
    deliveredRatio: gi._fillE / gi._keyE,
    fillRatioRule: gi._fillRatio, fillDiffuse: gi._fillDiffuse,
    diffuseFloor: gi._diffuseFloor,
    giScale: gi.giScale, expNow: gi._expNow, exposure: gi.exposure,
    analytic: gi.analyticExposure,
    sunIntensity: gi.sunIntensity, sunDir: gi.sunDirection.toArray().map(v => +v.toFixed(4)),
    sunElevDeg: +(Math.asin(gi.sunDirection.y) * 180 / Math.PI).toFixed(2),
    civil: gi.civil, dayFactor: gi.dayFactor,
    weather: {cloud: w.cloud, fog: w.fog, presets: !!w.presets},
    skyIrr: u.lemSkyIrradiance.value.toArray().map(v => +v.toFixed(4)),
    gndIrr: u.lemGroundIrradiance.value.toArray().map(v => +v.toFixed(4)),
    bounceFloor: u.lemBounceFloor ? u.lemBounceFloor.value : null,
    giStrength: u.lemGIStrength.value,
    aoStrength: u.lemAOStrength ? u.lemAOStrength.value : null,
    uniformNames: Object.keys(u),
    comp: (() => { const c = window.__lemWorld.engine?._passes?.composite?.material?.uniforms;
      if (!c) return null; const o = {};
      for (const k of Object.keys(c)) { const v = c[k].value; if (typeof v === 'number') o[k] = +v.toFixed(4); }
      return o; })(),
  };
});

const buf = await page.screenshot({type: 'png'});
const hist = await page.evaluate(async (src) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  const L = [];
  for (let i = 0; i < d.length; i += 4) L.push(0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]);
  L.sort((x, y) => x - y);
  const p = q => +L[Math.min(L.length - 1, Math.floor(L.length * q))].toFixed(1);
  // lower half of frame only (ground, no sky)
  const W = im.width, H = im.height;
  const G = [];
  for (let y = Math.floor(H * 0.45); y < H; y++) for (let x = 0; x < W; x += 2) {
    const o = (y * W + x) * 4; G.push(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]);
  }
  G.sort((x, y) => x - y);
  const gp = q => +G[Math.min(G.length - 1, Math.floor(G.length * q))].toFixed(1);
  return {w: im.width, h: im.height,
    frame: {min: +L[0].toFixed(1), p001: p(0.001), p01: p(0.01), p05: p(0.05), p50: p(0.5),
            mean: +(L.reduce((s, v) => s + v, 0) / L.length).toFixed(1), p95: p(0.95), p99: p(0.99), max: +L[L.length - 1].toFixed(1)},
    ground: {min: +G[0].toFixed(1), p001: gp(0.001), p01: gp(0.01), p05: gp(0.05), p50: gp(0.5),
             mean: +(G.reduce((s, v) => s + v, 0) / G.length).toFixed(1), p95: gp(0.95), p99: gp(0.99), max: +G[G.length - 1].toFixed(1)}};
}, 'data:image/png;base64,' + buf.toString('base64'));

console.log(JSON.stringify({cam, time, model, hist, errs: errs.slice(0, 5)}, null, 1));
await b.close();
