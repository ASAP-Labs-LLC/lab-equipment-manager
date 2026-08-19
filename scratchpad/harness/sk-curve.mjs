/* sk-curve.mjs — sweep fog curves offline against a dumped frame's geometry.
 *
 * The fog chunk is a closed form and the geometry does not change, so there is
 * no reason to spend a page load per candidate. Given sk-geodump.mjs's output
 * this evaluates the EXACT chunk for any (P, H, density, K, MAX) and prints the
 * luminance-weighted fog factor by true-distance band, so a candidate can be
 * chosen to hold one end of the ladder while moving the other.
 *
 *   node sk-curve.mjs /tmp/far.geo.json --hold 1100,1400 --p 2.75,3.0,3.25,3.5
 */
import fs from 'fs';
const a = {};
const argv = process.argv.slice(2);
const file = argv[0];
for (let i = 1; i < argv.length; i++)
  if (argv[i].startsWith('--')) a[argv[i].slice(2)] = argv[++i];
const g = JSON.parse(fs.readFileSync(file, 'utf8'));

const K = (a.k || '0.82,1.00,1.24').split(',').map(Number);
const MAX = +(a.max || 0.88);
const H0 = +(a.h || 900);
const D0 = +(a.density || g.density);
const P0 = +(a.p0 || 2.75);
const holdBand = (a.hold || '1100,1400').split(',').map(Number);
const Ps = (a.p || '2.75,3.0,3.25,3.5,3.75').split(',').map(Number);
const BANDS = (a.bands || '450,600,750,900,1100,1400,2400').split(',').map(Number);

const land = g.hits.filter(h => h.land);
const camY = g.camY;

/* the chunk, exactly */
function fac(dep, h1, D, H, P) {
  const A = Math.exp(-Math.max(camY, -600) / H);
  const B = Math.exp(-Math.max(h1, -600) / H);
  const dy = h1 - camY;
  let avg = Math.abs(dy) < 1 ? 0.5 * (A + B) : H * (A - B) / dy;
  avg = Math.min(Math.max(avg, 0), 6);
  const tau = D * dep * avg;
  const u = Math.pow(Math.max(tau, 1e-5), P);
  const f = K.map(k => MAX * (1 - Math.exp(-u * k)));
  return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
}
const bandMean = (D, H, P, lo, hi) => {
  const v = land.filter(h => h.dist >= lo && h.dist < hi);
  return v.length ? v.reduce((s, h) => s + fac(h.dep, h.h, D, H, P), 0) / v.length : NaN;
};

/* the factor the current curve puts on the band we must not lose */
const target = bandMean(D0, H0, P0, holdBand[0], holdBand[1]);
console.log(`geometry ${file}  land ${land.length}px  camY ${camY}  H ${H0}  MAX ${MAX}  K [${K}]`);
console.log(`baseline P ${P0} density ${D0.toExponential(4)}  ->  hold band ${holdBand[0]}-${holdBand[1]} at f = ${target.toFixed(4)}\n`);

const hdr = ['P', 'density'].concat(
  BANDS.slice(0, -1).map((lo, i) => `${lo}-${BANDS[i + 1]}`));
console.log('  ' + hdr[0].padStart(5) + hdr[1].padStart(11)
  + hdr.slice(2).map(s => s.padStart(11)).join(''));

for (const P of Ps) {
  /* solve for the density that keeps the held band exactly where it is */
  let lo = D0 * 0.2, hi = D0 * 8, D = D0;
  for (let it = 0; it < 60; it++) {
    D = (lo + hi) * 0.5;
    if (bandMean(D, H0, P, holdBand[0], holdBand[1]) < target) lo = D; else hi = D;
  }
  const row = BANDS.slice(0, -1).map((b, i) => {
    const f = bandMean(D, H0, P, b, BANDS[i + 1]);
    return Number.isFinite(f) ? f.toFixed(4).padStart(11) : '—'.padStart(11);
  });
  console.log('  ' + P.toFixed(2).padStart(5) + D.toExponential(4).padStart(11) + row.join(''));
}

/* and the same, relative to the baseline, so the trade is legible */
console.log('\n  ratio against the current curve (1.00 = unchanged)');
console.log('  ' + 'P'.padStart(5) + 'density'.padStart(11)
  + BANDS.slice(0, -1).map((lo, i) => `${lo}-${BANDS[i + 1]}`.padStart(11)).join(''));
const base = BANDS.slice(0, -1).map((b, i) => bandMean(D0, H0, P0, b, BANDS[i + 1]));
for (const P of Ps) {
  let lo = D0 * 0.2, hi = D0 * 8, D = D0;
  for (let it = 0; it < 60; it++) {
    D = (lo + hi) * 0.5;
    if (bandMean(D, H0, P, holdBand[0], holdBand[1]) < target) lo = D; else hi = D;
  }
  const row = BANDS.slice(0, -1).map((b, i) => {
    const f = bandMean(D, H0, P, b, BANDS[i + 1]);
    return (f / base[i]).toFixed(3).padStart(11);
  });
  console.log('  ' + P.toFixed(2).padStart(5) + D.toExponential(4).padStart(11) + row.join(''));
}

/* transition width: the distance ratio over which f/MAX runs 10% -> 90% */
console.log('\n  transition width, in octaves of distance, for f/MAX 0.10 -> 0.90');
for (const P of Ps) {
  let lo = D0 * 0.2, hi = D0 * 8, D = D0;
  for (let it = 0; it < 60; it++) {
    D = (lo + hi) * 0.5;
    if (bandMean(D, H0, P, holdBand[0], holdBand[1]) < target) lo = D; else hi = D;
  }
  const hterm = 0.79;
  const tauAt = f => Math.pow(-Math.log(1 - f), 1 / P) / (D * hterm);
  const d10 = tauAt(0.10), d90 = tauAt(0.90);
  console.log(`  P ${P.toFixed(2)}   ${Math.round(d10)} m -> ${Math.round(d90)} m   `
    + `${(Math.log2(d90 / d10)).toFixed(2)} octaves`);
}
