/* vstreak-mainland.mjs — characterise the vertical streaks in the mainland band.
 *
 *   node vstreak-mainland.mjs low-t9 300 165 1000 275
 *
 * The lag sweep found real column-coherent structure here (ratioV 6-9 at
 * 16-64 px) while the dome above it sits at 1.0.  Report what that structure
 * is: peak-to-peak amplitude in codes, and its spacing, so it can be matched
 * against a mesh feature rather than against a quantisation step.
 */
import fs from 'node:fs';
import path from 'node:path';

const [tag, X0, Y0, X1, Y1] = [process.argv[2], +process.argv[3], +process.argv[4],
                               +process.argv[5], +process.argv[6]];
const DIR = '/Users/rynatical/LAB-lem/scratchpad/harness/vstreak';
const meta = JSON.parse(fs.readFileSync(path.join(DIR, tag + '.meta.json'), 'utf8'));
const W = meta.W;
const buf = fs.readFileSync(path.join(DIR, tag + '.rgb'));
const px = (x, y, c) => buf[(y * W + x) * 3 + c];
const CH = ['R', 'G', 'B'];
const nx = X1 - X0, ny = Y1 - Y0;

const res = {tag, rect: [X0, Y0, X1, Y1], ch: []};
for (let c = 0; c < 3; c++) {
  const col = new Float64Array(nx);
  for (let i = 0; i < nx; i++) {
    let s = 0;
    for (let j = 0; j < ny; j++) s += px(X0 + i, Y0 + j, c);
    col[i] = s / ny;
  }
  /* high-pass at 128 px so the band's own left-right shading is not counted */
  const K = 64, hp = new Float64Array(nx);
  for (let i = 0; i < nx; i++) {
    let s = 0, n = 0;
    for (let k = -K; k <= K; k++) { const j = i + k; if (j >= 0 && j < nx) { s += col[j]; n++; } }
    hp[i] = col[i] - s / n;
  }
  const sorted = [...hp].sort((a, b) => a - b);
  const rms = Math.sqrt(hp.reduce((s, v) => s + v * v, 0) / nx);
  /* spectrum of the streak profile */
  const win = new Float64Array(nx);
  for (let i = 0; i < nx; i++) win[i] = hp[i] * 0.5 * (1 - Math.cos(2 * Math.PI * i / (nx - 1)));
  const mags = [];
  for (let k = 2; k <= nx / 4; k++) {
    let re = 0, im = 0;
    for (let i = 0; i < nx; i++) {
      const a = -2 * Math.PI * k * i / nx;
      re += win[i] * Math.cos(a); im += win[i] * Math.sin(a);
    }
    mags.push({period: nx / k, mag: Math.hypot(re, im) / nx});
  }
  const tot = mags.reduce((a, m) => a + m.mag * m.mag, 0);
  res.ch.push({
    ch: CH[c],
    streakRmsCodes: +rms.toFixed(3),
    p2pCodes: +(sorted[nx - 1] - sorted[0]).toFixed(2),
    p1to99Codes: +(sorted[(nx * 0.99) | 0] - sorted[(nx * 0.01) | 0]).toFixed(2),
    top: [...mags].sort((a, b) => b.mag - a.mag).slice(0, 6)
      .map(m => ({periodPx: +m.period.toFixed(1), pctPower: +(100 * m.mag * m.mag / tot).toFixed(1)})),
  });
}
console.log(JSON.stringify(res, null, 2));
