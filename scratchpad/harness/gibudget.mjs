/* Whole-frame worst case with the far cascade counted. engine.js resets
 * renderer.info AFTER the updaters, so gi's own pass is invisible to
 * `stats()`; this adds it back and reports the frame it lands on. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(6000);
const out = await p.evaluate(async () => {
  const w = window.__lemWorld, e = w.engine, gi = w.subsystems.get('gi');
  const calls = [], tris = []; let runs = 0;
  for (let i = 0; i < 200; i++) {
    const before = gi ? (gi._farRuns | 0) : 0;
    await new Promise(r => requestAnimationFrame(r));
    /* _farCost only changes on a frame the pass actually ran; treat an
     * unchanged value as "did not run this frame" only if _farDirty stayed
     * false, which is the honest reading for a static camera. */
    const ran = gi && gi._farRuns !== before;
    calls.push(e.drawCalls + (ran ? gi._farCost : 0));
    tris.push(e.triangles + (ran ? gi._farTris : 0));
    if (ran) runs++;
  }
  const med = a => [...a].sort((x, y) => x - y)[a.length >> 1];
  return {medCalls: med(calls), maxCalls: Math.max(...calls),
          medTris: med(tris), maxTris: Math.max(...tris),
          farCost: gi?._farCost, farTris: gi?._farTris,
          casters: gi?._farCasters.length, fps: Math.round(e.fps), farRuns: runs,
          baseMax: Math.max(...calls.map((c,i)=>c))};
});
console.log(JSON.stringify(out));
await b.close();
