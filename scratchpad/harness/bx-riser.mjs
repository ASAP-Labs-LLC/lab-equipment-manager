/* bx-riser.mjs — is the batter ON THE GROUND, and how long is it?
 *
 * For every riser the terrace declares, walk a z-transect at many x and report
 * the steepest 4 m window within +/-20 m of the riser's own centre, on the
 * FINISHED surface. A riser that only exists in `_designAt` shows up here as an
 * x band where the ground slope is the natural hillside's and not the batter's.
 */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail,trains&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const r2 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(2) : v;
  const T = t._terrace;
  if (!T) return {terrace: false};
  const out = [];
  for (const r of T.risers) {
    const zc = r.z0 + r.run * 0.5;
    const cols = [];
    for (let x = -80; x <= 440; x += 10) {
      let best = 0, bz = 0;
      for (let z = zc - 22; z <= zc + 22; z += 1) {
        const d = (t._gradedHeight(x, z + 2) - t._gradedHeight(x, z - 2)) / 4;
        if (Math.abs(d) > Math.abs(best)) { best = d; bz = z; }
      }
      /* the design surface's own step here, for comparison */
      const dd = (t._designAt(x, zc + r.run * 0.5 + 1) - t._designAt(x, zc - r.run * 0.5 - 1));
      cols.push({x, groundDeg: r2(Math.atan(Math.abs(best)) * 180 / Math.PI),
                 atZ: r2(bz), designStepM: r2(dd),
                 dFoot: r2(t._distances(x, zc, null))});
    }
    const built = cols.filter(c => c.groundDeg > 10);
    out.push({rise: r2(r.rise), run: r2(r.run), zCentre: r2(zc),
              faceDeg: r2(Math.atan(Math.abs(r.rise) / r.run) * 180 / Math.PI),
              /* how much of the 520 m of x the batter is actually cut into */
              xWithFaceOver10deg: built.length * 10,
              xSampled: cols.length * 10,
              medianGroundDeg: r2(cols.map(c => c.groundDeg).sort((a, b2) => a - b2)[cols.length >> 1]),
              cols});
  }
  /* and the same question for the yard's own perimeter, so the risers can be
   * read against the faces the site already had */
  return {terrace: true, risers: out};
}), null, 1));
await b.close();
