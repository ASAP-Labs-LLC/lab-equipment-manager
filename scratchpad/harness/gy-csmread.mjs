/* gy-csmread.mjs — read the coarse cascade targets back and report the raw byte
 * histogram, with the anchor ON and OFF in one page session. Written because
 * `gx-csmmap` reported an all-zero buffer after the fit change and an all-zero
 * buffer is either a map that was never drawn or a readback that did not
 * happen, and those are opposite conclusions.
 *
 *   node gy-csmread.mjs [--cam far] [--time 9]
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

const read = async (tag) => {
  const r = await p.evaluate(() => {
    const w = window.__lemWorld, gi = w.subsystems.get('gi'), rn = w.engine.renderer;
    const out = [];
    for (const c of gi._csm) {
      const N = c.rt.width, buf = new Uint8Array(N * N * 4);
      let err = null;
      try { rn.readRenderTargetPixels(c.rt, 0, 0, N, N, buf); } catch (e) { err = String(e).slice(0, 120); }
      /* histogram of the top byte, which is the depth's most significant 8 bits */
      const h = new Array(8).fill(0);
      let zero = 0, ff = 0;
      for (let i = 0; i < buf.length; i += 4) {
        const v = buf[i];
        h[v >> 5]++;
        if (v === 0) zero++;
        if (v === 255) ff++;
      }
      const n = N * N;
      out.push({i: c.i, N, err, runs: c.runs, cost: c.cost, radius: c.radius,
                pctZero: +(zero / n * 100).toFixed(2), pctFF: +(ff / n * 100).toFixed(2),
                hist: h.map(v => +(v / n * 100).toFixed(2))});
    }
    return {maps: out, anchor: !gi._noAnchor,
            box0: gi.uniforms.lemCsmBox0.value.toArray().map(v => +v.toFixed(1)),
            ready: [gi.uniforms.lemCsmReady0.value, gi.uniforms.lemCsmReady1.value]};
  });
  console.log(tag, JSON.stringify(r));
};
await read('as-shipped');
await p.evaluate(() => window.__lemWorld.subsystems.get('gi').setShadowAnchor(false));
await p.waitForTimeout(4000);
await read('anchor-off ');
await p.evaluate(() => window.__lemWorld.subsystems.get('gi').setShadowAnchor(true));
await p.waitForTimeout(4000);
await read('anchor-on  ');
await b.close();
