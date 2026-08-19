/* tz-ab.mjs — the same frame with rail's declared earthworks applied and with
 * them thrown away, in ONE session, so the difference is the earthworks and
 * nothing else. The "off" frame is what shipped before this round: terrain's
 * own reproduced corridor and no formation under the real alignment. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const CAM = process.argv[2] || 'far';
const MODS = process.argv[3] || 'sky,gi,terrain,rail';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=${MODS}&cam=${CAM}&time=9&weather=clear&quality=ultra&hud=0`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 140)));
await p.goto(url, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(12000);

const stats = () => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain'), rail = w.subsystems.get('rail');
  let below = 0, worst = 0, above = 0, worstUp = 0, n = 0;
  for (const st of w.plan.stations) {
    const r = rail.route && rail.route(st.uid);
    if (!r || !r.getPointAt) continue;
    for (let i = 0; i <= 300; i++) {
      const q = r.getPointAt(i / 300), g = t.heightAt(q.x, q.z);
      if (!isFinite(g)) continue;
      n++;
      const d = q.y - g;
      if (d < -0.3) { below++; if (d < worst) worst = d; }
      if (d > 0.9) { above++; if (d > worstUp) worstUp = d; }
    }
  }
  return {samples: n, below, worstCuttingM: +worst.toFixed(1),
          above, worstEmbankmentM: +worstUp.toFixed(1),
          earthworks: t._ework ? t._ework.segments : 0};
};

console.log('WITH earthworks  ', JSON.stringify(await p.evaluate(stats)));
fs.writeFileSync('/tmp/tz-ab-on.png', await p.screenshot());

await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  t._ework = null;
  t._teardownMeshes();
  t._buildField(); t._buildCore();
  t._buildRing(t.ringSize, t.ringSeg, t.coreSize, 40);
  t._buildOcean(); t._buildHorizon(); t._buildMainland();
  t._syncEnvironment();
});
await p.waitForTimeout(6000);
console.log('WITHOUT earthworks', JSON.stringify(await p.evaluate(stats)));
fs.writeFileSync('/tmp/tz-ab-off.png', await p.screenshot());
if (errs.length) console.log('errors:', errs.slice(0, 4));
await b.close();
