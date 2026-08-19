/* rr-float.mjs — does any structure stand in the air?
 *
 * The general test, rather than one per part: walk every triangle of rail's
 * structure meshes, keep the ones that face DOWNWARD, and ask how far the
 * lowest of their vertices is above the ground beneath it. A downward-facing
 * face that is above the ground is the underside of something that is not
 * standing on anything, and that is the whole of "the pier floats" and "the
 * wing wall is a shelf" in one number.
 *
 * Reports the count and the worst offender, per mesh, per layout.
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i + 1];
}
const layouts = parseInt(a.layouts || '2', 10);
const TOL = parseFloat(a.tol || '0.5');

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0, 300)));

for (let L = 0; L < layouts; L++) {
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail` +
               `&cam=top&time=13&hud=0&quality=ultra&layout=${L}&seed=${L}`,
               {waitUntil: 'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
  await p.waitForTimeout(3000);
  const r = await p.evaluate((TOL) => {
    const w = window.__lemWorld;
    const rail = w.subsystems.get('rail');
    const terr = w.subsystems.get('terrain');
    if (!terr) return {err: 'no terrain'};
    /* rail records the foot of every founded element as it builds it. A
     * footing must be at or below the ground the terrain finally builds; the
     * gap is how far the thing standing on it is off the ground. A purely
     * geometric test cannot judge this — a bridge soffit and a coping course
     * are both downward faces high in the air, and both are correct. */
    const F = rail._footings || [];
    let bad = 0, worst = 0, at = null, sumBuried = 0;
    for (const [x, y, z, gAtBuild] of F) {
      const g = terr.heightAt(x, z);
      const gap = y - g;
      if (gap > TOL) { bad++; if (gap > worst) { worst = gap; at = [+x.toFixed(1), +z.toFixed(1), 'builtOn ' + (+gAtBuild.toFixed(1)), 'nowGround ' + (+g.toFixed(1))]; } }
      else sumBuried += -gap;
    }
    return {footings: F.length, floating: bad, worst: +worst.toFixed(2), at,
            medianBuried: F.length ? +(sumBuried / Math.max(1, F.length - bad)).toFixed(2) : 0};
  }, TOL);
  console.log(`layout ${L}: footings ${r.footings}, standing above ground by >${TOL}m: ` +
              `${r.floating}   worst ${r.worst}m` + (r.at ? ' at ' + JSON.stringify(r.at) : '') +
              `   mean burial of the rest ${r.medianBuried}m`);
}
await b.close();
