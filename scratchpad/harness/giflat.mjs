/* giflat.mjs — what the no-GI rung actually saves, measured on one page.
 *
 *   node giflat.mjs [--cam yard] [--time 16] [--frames 900]
 *
 * The floor tier's draw-call and triangle counts barely move, because nothing
 * this round removed was a draw call: it was per-frame CPU (fitting and
 * snapping the shadow ortho, sweeping every instance matrix on the site to
 * decide what is inside it) and one pipeline stall (the meter readback). A
 * screenshot cannot see any of that and neither can shot.mjs's sidecar.
 *
 * So this holds ONE page open at quality=floor, samples the frame, then turns
 * gi.js's `_flat` switch back off in place — which restores exactly the work
 * the tier used to do, on the same world, the same camera and the same driver
 * state — and samples again. A/B on one page is the only version of this
 * measurement worth having; two page loads differ by more than the effect.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}

const MODS = args.mods ||
  'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const FRAMES = parseInt(args.frames || '900', 10);
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=${args.cam || 'yard'}` +
  `&time=${args.time || 16}&weather=${args.weather || 'clear'}&hud=0` +
  `&quality=${args.quality || 'floor'}`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720}});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);

/* Keep the railway working through both halves, or the second sample is an
 * empty yard being compared with a busy one. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  setInterval(() => w.parse(uids[i++ % uids.length], 'L-FLAT'), 700);
});

const sample = async (label, frames) => page.evaluate(async ([label, frames]) => {
  const w = window.__lemWorld;
  const gi = w.subsystems.get('gi');
  const eng = w.engine;
  const ms = [];
  let giMs = 0, giCalls = 0;
  const orig = gi.update.bind(gi);
  gi.update = (dt, t) => {
    const t0 = performance.now();
    orig(dt, t);
    giMs += performance.now() - t0;
    giCalls++;
  };
  let last = performance.now();
  await new Promise(res => {
    let n = 0;
    const tick = () => {
      const now = performance.now();
      ms.push(now - last); last = now;
      if (++n >= frames) return res();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  gi.update = orig;
  ms.sort((a, b) => a - b);
  const info = eng.renderer.info.render;
  return {
    label,
    p50: +ms[Math.floor(ms.length * 0.5)].toFixed(3),
    p95: +ms[Math.floor(ms.length * 0.95)].toFixed(3),
    giMsPerFrame: +(giMs / Math.max(1, giCalls)).toFixed(4),
    draws: info.calls, tris: info.triangles,
    flat: gi._flat, grid: !!gi.grid, csm: gi._csm.length, pool: gi._pool.length,
    env: !!w.scene.environment, flatSpec: gi.uniforms.lemFlatSpec.value,
    emissive: gi.uniforms.lemEmissiveGain.value,
    giStrength: +gi.uniforms.lemGIStrength.value.toFixed(4),
    exposure: +(gi.exposure || 0).toFixed(3),
  };
}, [label, frames]);

const flatRun = await sample('flat (gi:false honoured)', FRAMES);

/* Put the old behaviour back in place: the same tier, with `gi` no longer
 * false, which is precisely "the same path turned down" that this round
 * replaced. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  const gi = w.subsystems.get('gi');
  const tier = Object.assign({}, gi.tier, {gi: true, lighting: 0.45});
  gi.onQuality(tier);
});
await page.waitForTimeout(2500);
const litRun = await sample('same tier, GI machinery left on', FRAMES);

console.log(JSON.stringify({url, frames: FRAMES, errors,
                            a: flatRun, b: litRun}, null, 1));
await browser.close();
