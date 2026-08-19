/* vprom.mjs — pick the prominence radius by measurement, not by taste.
 *
 *   node vprom.mjs [--mods terrain,vegetation]
 *
 * Round seventeen's finding is that `_exposure` — sea fraction in a disc — is a
 * SPIT detector: its most exposed quartile is the island's lowest ground, so it
 * never spoke for the seaward CREST the critique keeps naming. The missing half
 * is local prominence. The trap in adding it is the one this project pays for
 * over and over: at the sea fraction's own 150 m radius, prominence correlates
 * with normalised altitude at r = 0.87, i.e. it is the crest rule again wearing
 * a new name, and it would have shipped inside the fix for exactly that class of
 * bug.
 *
 * So: rebuild the prominence field at a sweep of radii, off the page's OWN coast
 * grid and the page's OWN height samples, and report for each radius its
 * correlation with the four fields the file already reads. The wanted radius is
 * the smallest one that still has spread and is no longer altitude.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const mods = arg('mods', 'terrain,vegetation');

const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}` +
  '&cam=wide&time=16&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(8000);

const out = await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  const C = veg.coast, isl = veg.island;
  if (!C || !C.H) return {error: 'no coast height field — _buildCoast must keep H'};
  const {D, H, n, x0, z0, cell} = C;
  const Wd = n + 1;
  const corr = (a, c) => {
    const N = a.length;
    let ma = 0, mc = 0;
    for (let i = 0; i < N; i++) { ma += a[i]; mc += c[i]; }
    ma /= N; mc /= N;
    let sa = 0, sc = 0, sac = 0;
    for (let i = 0; i < N; i++) {
      const u = a[i] - ma, v = c[i] - mc;
      sa += u * u; sc += v * v; sac += u * v;
    }
    return +(sac / Math.sqrt((sa * sc) || 1e-9)).toFixed(3);
  };
  /* The two summed-area tables the file builds, rebuilt here once and shared by
   * every radius — the radius is only the box, not the table. */
  const SH = new Float64Array(Wd * Wd), SN = new Float64Array(Wd * Wd);
  for (let j = 0; j < n; j++) {
    let rh = 0, rn = 0;
    for (let i = 0; i < n; i++) {
      const land = D[j * n + i] > 0 ? 1 : 0;
      rh += land ? H[j * n + i] : 0; rn += land;
      SH[(j + 1) * Wd + (i + 1)] = SH[j * Wd + (i + 1)] + rh;
      SN[(j + 1) * Wd + (i + 1)] = SN[j * Wd + (i + 1)] + rn;
    }
  }
  const cl = v => v < 0 ? 0 : v > n ? n : v;
  const box = (T, i, j, r) => {
    const i0 = cl(i - r), i1 = cl(i + r + 1), j0 = cl(j - r), j1 = cl(j + r + 1);
    return T[j1 * Wd + i1] - T[j0 * Wd + i1] - T[j1 * Wd + i0] + T[j0 * Wd + i0];
  };
  /* The comparison fields, sampled on the SAME lattice the prominence is on, so
   * nothing is being correlated against a differently-shaped sample. */
  const idx = [], alt = [], coastD = [], expo = [], slope = [], wet = [];
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const k = j * n + i;
      if (D[k] <= 0 || D[k] > 1e5) continue;
      const x = x0 + i * cell, z = z0 + j * cell;
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const s = veg._biome(x, z, H[k]);
      if (!s) continue;
      idx.push(k); alt.push(s.alt); coastD.push(D[k]);
      expo.push(veg._exposure(x, z)); slope.push(s.slope); wet.push(s.wet);
    }
  }
  const rows = [];
  for (const metres of [32, 48, 64, 80, 96, 112, 150, 200]) {
    const r = Math.max(2, Math.round(metres / cell));
    const raw = new Float64Array(idx.length);
    for (let q = 0; q < idx.length; q++) {
      const k = idx[q], i = k % n, j = (k - i) / n;
      const cntL = box(SN, i, j, r);
      raw[q] = cntL > 0 ? H[k] - box(SH, i, j, r) / cntL : 0;
    }
    const so = Array.from(raw).sort((a, c) => a - c);
    const at = f => so[Math.min(so.length - 1, Math.floor(so.length * f))];
    rows.push({
      metres, cells: r,
      p10: +at(0.10).toFixed(2), p50: +at(0.50).toFixed(2), p90: +at(0.90).toFixed(2),
      spreadM: +(at(0.90) - at(0.10)).toFixed(2),
      vsAlt: corr(raw, alt), vsCoast: corr(raw, coastD), vsExpo: corr(raw, expo),
      vsSlope: corr(raw, slope), vsWet: corr(raw, wet),
    });
  }
  return {n, cell: +cell.toFixed(1), landSamples: idx.length,
          note: 'want the smallest radius that keeps spread and is no longer altitude',
          rows};
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
