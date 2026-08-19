/* railgrade.mjs — WHY the railway floats. Decomposes the fill.
 *
 * For every track, walk its own frames, read terrain.heightAt on the
 * centreline, and compute:
 *   ground      the natural profile under the alignment
 *   slope       |dg/ds| of that profile — is the terrain even railway-able?
 *   lipsX       the minimal g-Lipschitz function lying above ground, for a
 *               few candidate ruling grades. This is the fill a real railway
 *               is FORCED to build if it is never allowed to cut.
 *   lipsCut     the minimal g-Lipschitz function nearest the ground when
 *               cutting IS allowed (fill above / cut below, balanced) — the
 *               fill a real railway builds when it can also excavate.
 * The gap between those two is exactly what terrain.js would have to remove.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[a.slice(2)] = true; else { args[a.slice(2)] = n; i++; }
}
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 900, height: 600}});
const errors = [];
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
await page.goto(args.url ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=yard&time=16&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await page.waitForTimeout(600);

const out = await page.evaluate(() => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const terr = w.subsystems.get('terrain');
  const H = (x, z) => terr.heightAt(x, z);
  const stat = a => {
    if (!a.length) return null;
    const b = a.slice().sort((p, q) => p - q);
    const q = f => +b[Math.min(b.length - 1, Math.floor(b.length * f))].toFixed(3);
    return {n: b.length, min: +b[0].toFixed(3), med: q(0.5), p90: q(0.9),
            max: +b[b.length - 1].toFixed(3)};
  };
  const envAbove = (g, step, grade) => {
    const y = Float64Array.from(g);
    for (let i = 1; i < y.length; i++) y[i] = Math.max(y[i], y[i - 1] - grade * step);
    for (let i = y.length - 2; i >= 0; i--) y[i] = Math.max(y[i], y[i + 1] - grade * step);
    return y;
  };
  const envBelow = (g, step, grade) => {
    const y = Float64Array.from(g);
    for (let i = 1; i < y.length; i++) y[i] = Math.min(y[i], y[i - 1] + grade * step);
    for (let i = y.length - 2; i >= 0; i--) y[i] = Math.min(y[i], y[i + 1] + grade * step);
    return y;
  };
  /* The best g-Lipschitz fit in L-infinity is the midpoint of the tightest
   * envelopes above and below — i.e. what an engineer laying a line that may
   * both cut and fill would choose. */
  const envBalanced = (g, step, grade) => {
    const U = envAbove(g, step, grade), L = envBelow(g, step, grade);
    return Float64Array.from(U, (v, i) => (v + L[i]) / 2);
  };
  const rows = {};
  const allSlope = [], allF25 = [], allF50 = [], allC25 = [], allCut25 = [];
  for (const t of (rail.tracks || [])) {
    const f = t.frames; if (!f) continue;
    const step = f.step, N = f.count;
    const g = new Float64Array(N), lay = new Float64Array(N);
    for (let i = 0; i < N; i++) {
      g[i] = H(f.pos[i * 3], f.pos[i * 3 + 2]);
      lay[i] = f.pos[i * 3 + 1];
    }
    const slope = [];
    for (let i = 1; i < N; i++) slope.push(Math.abs(g[i] - g[i - 1]) / step);
    const sweep = {};
    for (const gr of [0.02, 0.025, 0.035, 0.05, 0.07]) {
      const e = envAbove(g, step, gr), b = envBalanced(g, step, gr);
      const fo = [], bf = [], bc = [];
      for (let i = 0; i < N; i++) {
        fo.push(e[i] - g[i]);
        bf.push(Math.max(0, b[i] - g[i]));
        bc.push(Math.max(0, g[i] - b[i]));
      }
      sweep[gr] = {fillOnly: stat(fo), balFill: stat(bf), balCut: stat(bc)};
    }
    const e25 = envAbove(g, step, 0.025), e50 = envAbove(g, step, 0.05);
    const b25 = envBalanced(g, step, 0.025);
    const f25 = [], f50 = [], c25 = [], cut25 = [], laid = [];
    for (let i = 0; i < N; i++) {
      f25.push(e25[i] - g[i]); f50.push(e50[i] - g[i]);
      c25.push(Math.max(0, b25[i] - g[i]));
      cut25.push(Math.max(0, g[i] - b25[i]));
      laid.push(lay[i] - g[i]);
    }
    rows[t.name] = {len: +t.length.toFixed(0), slope: stat(slope),
                    laid: stat(laid), fill25: stat(f25), fill50: stat(f50),
                    balFill25: stat(c25), balCut25: stat(cut25), sweep};
    allSlope.push(...slope); allF25.push(...f25); allF50.push(...f50);
    allC25.push(...c25); allCut25.push(...cut25);
  }
  return {rows, all: {slope: stat(allSlope), fillOnly25: stat(allF25),
                      fillOnly50: stat(allF50), balancedFill25: stat(allC25),
                      balancedCut25: stat(allCut25)}};
});
console.log(JSON.stringify(out.all, null, 1));
for (const k in out.rows) {
  const r = out.rows[k];
  console.log(k.padEnd(16), 'len', String(r.len).padStart(5),
    '| ground slope med', r.slope.med, 'p90', r.slope.p90, 'max', r.slope.max,
    '| LAID med', r.laid.med, 'max', r.laid.max,
    '| fill-only@2.5% med', r.fill25.med, '| balanced fill', r.balFill25.med,
    'cut', r.balCut25.med, '(max cut', r.balCut25.max + ')');
  for (const gr in r.sweep) {
    const s = r.sweep[gr];
    console.log('   grade', gr, ' fillOnly med/p90/max',
      s.fillOnly.med, s.fillOnly.p90, s.fillOnly.max,
      ' | withCut fill med/max', s.balFill.med, s.balFill.max,
      ' cut med/p90/max', s.balCut.med, s.balCut.p90, s.balCut.max);
  }
}
if (errors.length) console.log('ERRORS', errors);
await browser.close();
