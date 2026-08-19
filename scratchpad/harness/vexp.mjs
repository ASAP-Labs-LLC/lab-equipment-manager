/* vexp.mjs — would an EXPOSURE term vary at a fixed distance from the coast?
 *
 *   node vexp.mjs
 *
 * The fringe is a constant-width band because the only coastal driver in the
 * file is `_coastDist`. The proposed fix is a second coastal number — how much
 * open sea surrounds the point — so a wind-blasted headland and the head of a
 * sheltered inlet at the SAME distance from the water get different woods.
 *
 * That is only worth building if it actually varies at fixed coast distance.
 * Four rules in this file have shipped inert because nobody asked that
 * question first, so ask it: for each candidate radius, report the spread of
 * seaFrac WITHIN each coast band, and its correlation with coastDist (a term
 * that is just coastDist again buys nothing).
 *
 * Also measures the alternative — a directional fetch, the mean over 16
 * bearings of how far you can walk before hitting the sea — since a fraction
 * inside a disc and a distance along a ray are different shapes.
 */
import {chromium} from 'playwright';

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,vegetation' +
  '&cam=wide&time=16&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(9000);

const out = await p.evaluate(() => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const isl = veg.island;
  const wy = veg.waterY;
  const sea = (x, z) => veg._ground(x, z) <= wy;

  /* Sea fraction inside a disc, sampled on a ring lattice — cheap and
     isotropic, which a box is not. */
  const seaFrac = (x, z, R) => {
    let n = 0, s = 0;
    for (let ri = 1; ri <= 3; ri++) {
      const r = R * ri / 3, k = 8 * ri;
      for (let a = 0; a < k; a++) {
        const th = (a + 0.5) * 2 * Math.PI / k;
        n++; if (sea(x + Math.cos(th) * r, z + Math.sin(th) * r)) s++;
      }
    }
    return s / n;
  };
  /* Fetch: mean over 16 bearings of the distance to the sea, capped. */
  const fetch = (x, z, cap) => {
    let s = 0;
    for (let a = 0; a < 16; a++) {
      const th = a * Math.PI / 8, cx = Math.cos(th), cz = Math.sin(th);
      let d = cap;
      for (let t = 12; t <= cap; t += 12) {
        if (sea(x + cx * t, z + cz * t)) { d = t; break; }
      }
      s += d;
    }
    return s / 16 / cap;
  };

  const pts = [];
  const STEP = 10;
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += STEP) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += STEP) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      if (sea(x, z)) continue;
      pts.push({x, z, c: veg._coastDist(x, z)});
    }
  }
  /* Thin, so the ray marches stay affordable. */
  const take = pts.filter((_, i) => i % 3 === 0);
  const stat = (a) => {
    if (!a.length) return {n: 0};
    const m = a.reduce((s, v) => s + v, 0) / a.length;
    const sd = Math.sqrt(a.reduce((s, v) => s + (v - m) * (v - m), 0) / a.length);
    return {n: a.length, mean: +m.toFixed(3), sd: +sd.toFixed(3),
            cv: +(sd / (m || 1e-9)).toFixed(3),
            min: +Math.min(...a).toFixed(2), max: +Math.max(...a).toFixed(2)};
  };
  const corr = (a, bq) => {
    const n = a.length, ma = a.reduce((s, v) => s + v, 0) / n, mb = bq.reduce((s, v) => s + v, 0) / n;
    let sab = 0, saa = 0, sbb = 0;
    for (let k = 0; k < n; k++) { const u = a[k] - ma, v = bq[k] - mb; sab += u * v; saa += u * u; sbb += v * v; }
    return +(sab / Math.sqrt(saa * sbb + 1e-9)).toFixed(3);
  };

  const bands = [[0, 40], [40, 90], [90, 150], [150, 260]];
  const res = {};
  for (const R of [90, 150, 240]) {
    for (const q of take) q['s' + R] = seaFrac(q.x, q.z, R);
    res['seaFrac' + R] = {
      overall: stat(take.map(q => q['s' + R])),
      rWithCoast: corr(take.map(q => q['s' + R]), take.map(q => q.c)),
      withinBand: bands.map(bd => {
        const sel = take.filter(q => q.c >= bd[0] && q.c < bd[1]);
        return {band: bd[0] + '-' + bd[1], ...stat(sel.map(q => q['s' + R]))};
      }),
    };
  }
  for (const q of take) q.f = fetch(q.x, q.z, 300);
  res.fetch300 = {
    overall: stat(take.map(q => q.f)),
    rWithCoast: corr(take.map(q => q.f), take.map(q => q.c)),
    withinBand: bands.map(bd => {
      const sel = take.filter(q => q.c >= bd[0] && q.c < bd[1]);
      return {band: bd[0] + '-' + bd[1], ...stat(sel.map(q => q.f))};
    }),
  };
  return {land: pts.length, sampled: take.length, islandR: +isl.r.toFixed(0), res};
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
