/* gy-shadowclock.mjs — WHO asks for three's shadow map to be redrawn, and how
 * often. The near map's whole cost depends on the answer: 112 extra casters
 * cost 112 draws a frame if the map redraws every frame, and 112 draws every
 * few seconds if it does not.
 *
 * Traps a setter on `engine.shadowNeedsUpdate` and tallies call sites by stack.
 *
 *   node gy-shadowclock.mjs [--cam far] [--time 9] [--seconds 8]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const secs = parseFloat(a.seconds || '8');
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(9000);
await p.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } });

const res = await p.evaluate((secs) => new Promise(res => {
  const w = window.__lemWorld, eng = w.engine, rn = eng.renderer;
  const tally = {};
  let raw = eng.shadowNeedsUpdate, sets = 0;
  Object.defineProperty(eng, 'shadowNeedsUpdate', {
    configurable: true,
    get() { return raw; },
    set(v) {
      if (v) {
        sets++;
        const st = (new Error().stack || '').split('\n').slice(2, 4)
          .map(s => s.trim().replace(/^at\s+/, '').replace(/https?:\/\/[^ )]*\//, '')).join(' <- ');
        tally[st] = (tally[st] || 0) + 1;
      }
      raw = v;
    },
  });
  /* and count how many frames actually rendered the map */
  let frames = 0, redraws = 0, t0 = performance.now();
  const tick = () => {
    frames++;
    if (rn.shadowMap.needsUpdate) redraws++;      // set true for the frame about to render
    if (performance.now() - t0 < secs * 1000) requestAnimationFrame(tick);
    else res({seconds: +((performance.now() - t0) / 1000).toFixed(2), frames, sets,
              setsPerSecond: +(sets / ((performance.now() - t0) / 1000)).toFixed(2),
              tally, drawsNow: rn.info.render.calls});
  };
  requestAnimationFrame(tick);
}), secs);
console.log(JSON.stringify({cam, time, ...res}, null, 1));
await b.close();
