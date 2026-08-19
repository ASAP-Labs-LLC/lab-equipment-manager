/* sh-solve.mjs — design a fog curve against ANCHORS instead of sweeping it.
 *
 * Reads sk-geodump.mjs's dump (every ground sample's true view depth and world
 * height) and evaluates candidate shapes on the real geometry, so a band figure
 * is the mean over the pixels that are actually in that band rather than over a
 * distance the model was asked to imagine.
 *
 * Shapes:
 *   plain   tau                                        (what ships today)
 *   hard    max(tau - t0, 0)                           a clear shell, C1 break
 *   soft    tau - t0*(1 - exp(-tau/t0))                a clear shell, smooth
 *
 * The three anchors this round has to hold at once:
 *   SHADOW  land at 750-1050 m   — the haze over the subject, must fall
 *   DECAL   land at 1150-1300 m  — the canopy ladder's top rung, must hold
 *   JOIN    sea  at 1900-2100 m  — must stay at/below terrain.js's own 0.542
 *
 *   node sh-solve.mjs /tmp/far.geo.json [--shape soft] [--p 2.0,2.5,3.0]
 */
import fs from 'fs';
const argv = process.argv.slice(2), file = argv[0], a = {};
for (let i = 1; i < argv.length; i++) if (argv[i].startsWith('--')) a[argv[i].slice(2)] = argv[++i];
const g = JSON.parse(fs.readFileSync(file, 'utf8'));
const K = (a.k || '0.82,1.00,1.24').split(',').map(Number);
const MAX = +(a.max || 0.88), H = +(a.h || 900);
const camY = g.camY, D0 = g.density, P0 = 3.25;

const shapes = {
  plain: (tau) => tau,
  hard: (tau, t0) => Math.max(tau - t0, 0),
  soft: (tau, t0) => t0 <= 0 ? tau : tau - t0 * (1 - Math.exp(-tau / t0)),
  /* softplus: max(tau-t0,0) with a knee W wide, in the stable form the shader
   * uses. W -> 0 is `hard`; W large is a gentle bend with no onset contour. */
  sp: (tau, t0, W) => { const x = tau - t0;
    return Math.max(x, 0) + W * Math.log(1 + Math.exp(-Math.abs(x) / W)); },
};
const W = +(process.env.SHW || 0.10);
function fac(dep, h1, D, P, t0, shape) {
  const A = Math.exp(-Math.max(camY, -600) / H), B = Math.exp(-Math.max(h1, -600) / H);
  const dy = h1 - camY;
  let avg = Math.abs(dy) < 1 ? 0.5 * (A + B) : H * (A - B) / dy;
  avg = Math.min(Math.max(avg, 0), 6);
  const tau = shapes[shape](D * dep * avg, t0, W);
  const u = Math.pow(Math.max(tau, 1e-5), P);
  const f = K.map(k => MAX * (1 - Math.exp(-u * k)));
  return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
}
const land = g.hits.filter(h => h.land), sea = g.hits.filter(h => !h.land);
const setOf = (src, lo, hi) => src.filter(h => h.dist >= lo && h.dist < hi);
const SETS = {
  SHADOW: setOf(land, 750, 1050), DECAL: setOf(land, 1150, 1300),
  JOIN: setOf(sea, 1900, 2100),
};
const mean = (S, D, P, t0, sh) => S.length ? S.reduce((s, h) => s + fac(h.dep, h.h, D, P, t0, sh), 0) / S.length : NaN;
const BANDS = [300, 450, 600, 750, 900, 1050, 1200, 1400, 1800, 2200, 3000];

const base = {};
for (const k in SETS) base[k] = mean(SETS[k], D0, P0, 0, 'plain');
console.log(`geo ${file}  land ${land.length}  sea ${sea.length}  camY ${camY}`);
console.log(`SHIPPING  P ${P0}  d ${D0.toExponential(4)}  ->  SHADOW ${base.SHADOW.toFixed(4)}`
  + `  DECAL ${base.DECAL.toFixed(4)}  JOIN ${base.JOIN.toFixed(4)}\n`);

/* For a shape and an exponent, solve (D, t0) so DECAL and JOIN both land on
 * their shipping values; then report what SHADOW came out at. Two equations,
 * two unknowns, both monotone in D, so it is a nested bisection. */
function solve(P, shape, decalT, joinT) {
  let lo = 0, hi = 4.0;                      // t0 in optical-depth units
  let out = null;
  for (let it = 0; it < 48; it++) {
    const t0 = (lo + hi) / 2;
    /* density that puts DECAL on target for this t0 */
    let dl = D0 * 0.05, dh = D0 * 40, D = D0;
    for (let k = 0; k < 60; k++) {
      D = (dl + dh) / 2;
      if (mean(SETS.DECAL, D, P, t0, shape) < decalT) dl = D; else dh = D;
    }
    const j = mean(SETS.JOIN, D, P, t0, shape);
    out = {P, shape, t0, D, join: j, decal: mean(SETS.DECAL, D, P, t0, shape),
           shadow: mean(SETS.SHADOW, D, P, t0, shape)};
    /* a bigger shell needs more density to hold DECAL, which overshoots JOIN */
    if (j > joinT) hi = t0; else lo = t0;
  }
  return out;
}
const Ps = (a.p || '1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25').split(',').map(Number);
const shape = a.shape || 'soft';
console.log(`shape ${shape}: hold DECAL ${base.DECAL.toFixed(4)} and JOIN ${base.JOIN.toFixed(4)}, read SHADOW`);
console.log('     P        t0     density    SHADOW    x base   ' + BANDS.slice(0, -1).map((b, i) => `${b}`.padStart(8)).join(''));
for (const P of Ps) {
  const s = solve(P, shape, base.DECAL, base.JOIN);
  const row = BANDS.slice(0, -1).map((b, i) => {
    const S = setOf(b < 1500 ? land : g.hits, b, BANDS[i + 1]);
    const v = mean(S, s.D, P, s.t0, shape);
    return (Number.isFinite(v) ? v.toFixed(4) : '—').padStart(8);
  });
  console.log(`  ${P.toFixed(2).padStart(4)}  ${s.t0.toFixed(4).padStart(8)}  ${s.D.toExponential(3)}`
    + `  ${s.shadow.toFixed(4).padStart(7)}  ${(s.shadow / base.SHADOW).toFixed(3).padStart(6)}   ` + row.join(''));
}
console.log('\n  the shipping curve on the same bands:');
{
  const row = BANDS.slice(0, -1).map((b, i) => {
    const S = setOf(b < 1500 ? land : g.hits, b, BANDS[i + 1]);
    const v = mean(S, D0, P0, 0, 'plain');
    return (Number.isFinite(v) ? v.toFixed(4) : '—').padStart(8);
  });
  console.log(`  ${P0.toFixed(2).padStart(4)}  ${'0'.padStart(8)}  ${D0.toExponential(3)}`
    + `  ${base.SHADOW.toFixed(4).padStart(7)}  ${'1.000'.padStart(6)}   ` + row.join(''));
}
