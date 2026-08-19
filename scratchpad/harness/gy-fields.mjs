/* gy-fields.mjs — measure the FIELDS gi.js thresholds against, before touching a
 * threshold. Prints what `ctx.weather` actually publishes (as opposed to what
 * gi.js's `??` defaults quietly supply), the fill ratio that comes out of it,
 * and how many objects are enrolled as landform casters — the population
 * `_depthLand` exists for.
 *
 *   node gy-fields.mjs [--cam far] [--time 9] [--weather clear]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9', wx = a.weather || 'clear';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=${wx}&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(9000);

const res = await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const ctxw = w.ctx.weather;
  const wmod = w.subsystems.get('weather');
  const own = {};
  if (ctxw) for (const k of Object.keys(ctxw)) {
    const v = ctxw[k];
    if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') own[k] = v;
  }
  /* the landform population */
  let land = 0, landNames = [], cascadeLand = [0, 0];
  w.scene.traverse(o => {
    if (o.userData?.lemLandform) { land++; if (landNames.length < 12) landNames.push(o.name || o.type); }
  });
  gi._csm.forEach(c => {
    cascadeLand[c.i] = c.casters.filter(o => o.userData?.lemLandform).length;
  });
  /* what the fill fit is delivering */
  const cloud = ctxw?.cloud, fog = ctxw?.fog;
  return {
    weatherKeys: Object.keys(ctxw || {}),
    weatherScalars: own,
    weatherModulePreset: wmod?.preset ?? null,
    cloudSeenByGi: Math.min(1, Math.max(0, cloud ?? 0.2)),
    fogSeenByGi: Math.min(1, Math.max(0, fog ?? 0.1)),
    cloudPublished: cloud === undefined ? 'UNDEFINED' : cloud,
    fogPublished: fog === undefined ? 'UNDEFINED' : fog,
    fillRatio: gi._fillRatio ?? null, fillDiffuse: gi._fillDiffuse ?? null,
    fillE: gi._fillE, keyE: gi._keyE, giScale: gi.giScale,
    exposure: gi.exposure ?? gi.uniforms?.lemExposure?.value ?? null,
    landformCasters: land, landformNames: landNames,
    cascadeLandformCount: cascadeLand,
    cascadeCasters: gi._csm.map(c => c.casters.length),
  };
});
console.log(JSON.stringify({cam, time, weather: wx, ...res, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
