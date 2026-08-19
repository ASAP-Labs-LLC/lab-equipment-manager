/* tz-cost.mjs — what the re-grade against rail's earthworks costs, in ms. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,rail&cam=top&time=13&hud=0',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const run = () => {
    const t0 = performance.now();
    t._teardownMeshes();
    t._buildField(); t._buildCore();
    t._buildRing(t.ringSize, t.ringSeg, t.coreSize, 40);
    t._buildOcean(); t._buildHorizon(); t._buildMainland();
    return performance.now() - t0;
  };
  const withEw = Math.round(run());
  const segs = t._ework ? t._ework.segments : 0;
  const reach = t._ework ? +t._ework.reach.toFixed(1) : 0;
  t._ework = null;
  const without = Math.round(run());
  return {regradeWithEarthworksMs: withEw, sameRegradeWithoutMs: without,
          earthworkSegments: segs, queryReachM: reach,
          coreVerts: t.core ? t.core.V * t.core.V : 0};
}), null, 1));
await b.close();
