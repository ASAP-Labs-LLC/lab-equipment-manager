/* What the sward costs the first frame. First-frame time is the budget this
 * round is judged against and it moved 300 ms between two runs an hour apart,
 * so attributing that to any one file from the wall clock is guesswork. This
 * re-runs the two pieces of work this tier adds — painting its page and
 * scattering it — on the live page and times them directly. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=wide&time=16&hud=0&quality=ultra', {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(6000);
const out = await p.evaluate(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const PAL = {grass: {leaf: [0.26, 0.38, 0.20], warm: [0.40, 0.42, 0.25],
                       stem: [0.28, 0.40, 0.21], bark: [0.2, 0.16, 0.1]}};
  const paint = [], scat = [];
  for (let i = 0; i < 3; i++) {
    let t = performance.now(); const tex = v._makeSward(PAL); paint.push(performance.now() - t);
    tex.dispose?.();
  }
  const keep = v.sward;
  for (let i = 0; i < 3; i++) {
    const t = performance.now(); v._scatterSward(); scat.push(performance.now() - t);
    for (const s of v.sward) { v.group.remove(s.mesh); s.mesh.geometry.dispose(); }
  }
  v.sward = keep;
  const mid = a => a.sort((x, y) => x - y)[1];
  let placed = 0; for (const s of keep) placed += s.count;
  return {paintMs: +mid(paint).toFixed(1), scatterMs: +mid(scat).toFixed(1),
          placed, buildMs: v._buildMs | 0, stats: v._swardStats};
});
await b.close();
console.log(JSON.stringify(out, null, 1));
