/* gy-land.mjs — prove the landform depth bias ACTS, which the polygon offset it
 * replaced could not.
 *
 * The old mechanism was untestable by construction: polygon offset moves the
 * depth buffer and the cascade reads the colour attachment, so no value of it
 * could ever change a single byte of the map. The new one is patched into
 * `fragCoordZ` itself, so it must. This drives the slope uniform over three
 * decades and reports how many of the landform's texels moved, and by how much.
 *
 *   node gy-land.mjs [--cam far] [--time 9]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(10000);

const grab = async (slope) => {
  return await p.evaluate(async (slope) => {
    const w = window.__lemWorld, gi = w.subsystems.get('gi'), rn = w.engine.renderer;
    gi._landSlope.value = slope;
    /* redraw cascade 1 in place — that is the one the landform is enrolled in */
    const c = gi._csm[gi._csm.length - 1];
    for (let k = 0; k < 3; k++) { c.dirty = true; gi._renderCascade(c); }
    const N = c.rt.width, buf = new Uint8Array(N * N * 4);
    try { rn.readRenderTargetPixels(c.rt, 0, 0, N, N, buf); } catch (e) { void e; }
    const out = new Float64Array(N * N);
    let live = 0;
    for (let i = 0, j = 0; i < buf.length; i += 4, j++) {
      out[j] = buf[i] / 255 + buf[i + 1] / 65025 + buf[i + 2] / 16581375 + buf[i + 3] / 4228250625;
      if (buf[i] !== 0) live++;
    }
    return {slope, live, N, depth: Array.from(out.filter((_, k) => k % 37 === 0)),
            landCasters: c.casters.filter(o => o.userData?.lemLandform).length,
            bias: gi._landBias.value, cost: c.cost};
  }, slope);
};

const base = await grab(2.2);
const hot = await grab(400);
const back = await grab(2.2);
const cmp = (A, B) => {
  let moved = 0, sum = 0, n = 0, maxd = 0;
  for (let i = 0; i < A.depth.length; i++) {
    if (A.depth[i] > 0.999 && B.depth[i] > 0.999) continue;   // empty in both
    n++;
    const d = Math.abs(A.depth[i] - B.depth[i]);
    if (d > 1e-6) { moved++; sum += d; maxd = Math.max(maxd, d); }
  }
  return {sampled: n, movedTexels: moved, pctMoved: n ? +(100 * moved / n).toFixed(2) : null,
          meanDelta: moved ? +(sum / moved).toExponential(3) : 0, maxDelta: +maxd.toExponential(3)};
};
console.log(JSON.stringify({
  cam, time,
  landCastersInCascade: base.landCasters, biasNormalisedDepth: base.bias,
  'slope 2.2 -> 400': cmp(base, hot),
  'slope 2.2 -> 2.2 (control)': cmp(base, back),
}, null, 1));
await b.close();
