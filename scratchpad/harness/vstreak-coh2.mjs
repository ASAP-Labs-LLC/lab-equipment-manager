/* vstreak-coh2.mjs — clean test for column-coherent (vertical) structure.
 *
 *   node vstreak-coh2.mjs low-t9 [x0 y0 x1 y1 label]...
 *
 * coh.mjs high-passed the column profile with a moving average, which leaves
 * the curvature of the horizontal gradient behind and inflates the ratio.  Use
 * the SECOND DIFFERENCE instead: any smooth gradient, however steep, is
 * annihilated by it, while a contour step or a streak is not.
 *
 *   col(x)  = mean over rows of (pixel - that row's mean)
 *   d2(x)   = col(x-1) - 2col(x) + col(x+1)
 * For independent per-pixel noise of sigma, col has sd sigma/sqrt(ny) and d2
 * has sd sqrt(6)*sigma/sqrt(ny).  So
 *   ratioV = rms(d2) / (sqrt(6)*sigma/sqrt(ny))
 * is 1.0 when nothing but noise is coherent down the column, and >1 when real
 * vertical structure exists.  Same test rotated gives ratioH.
 *
 * Also reports the amplitude of the column-coherent component in CODES, which
 * is what decides whether anything here is visible at all.
 */
import fs from 'node:fs';
import path from 'node:path';

const tag = process.argv[2];
const DIR = '/Users/rynatical/LAB-lem/scratchpad/harness/vstreak';
const meta = JSON.parse(fs.readFileSync(path.join(DIR, tag + '.meta.json'), 'utf8'));
const W = meta.W, H = meta.H;
const buf = fs.readFileSync(path.join(DIR, tag + '.rgb'));
const px = (x, y, c) => buf[(y * W + x) * 3 + c];
const lum = (x, y) => 0.2126 * px(x, y, 0) + 0.7152 * px(x, y, 1) + 0.0722 * px(x, y, 2);
const CH = ['R', 'G', 'B'];

const EDGE = 10, RUNW = 7, MARGIN = 6;
const horiz = new Int32Array(W);
for (let x = 0; x < W; x++) {
  let h = H - 1;
  for (let y = RUNW + 1; y < H - RUNW - 1; y++) {
    let a = 0, b = 0;
    for (let k = 1; k <= RUNW; k++) { a += lum(x, y - k); b += lum(x, y + k - 1); }
    if (Math.abs(b / RUNW - a / RUNW) > EDGE) { h = y; break; }
  }
  horiz[x] = h;
}

const rms = a => Math.sqrt(a.reduce((s, v) => s + v * v, 0) / a.length);
function d2(a) {
  const o = [];
  for (let i = 1; i < a.length - 1; i++) o.push(a[i - 1] - 2 * a[i] + a[i + 1]);
  return o;
}

function test(x0, y0, x1, y1, c) {
  const nx = x1 - x0, ny = y1 - y0;
  const v = new Float64Array(nx * ny);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) v[j * nx + i] = px(x0 + i, y0 + j, c);

  /* ---- vertical structure: profile down columns ---- */
  const rv = new Float64Array(nx * ny);
  for (let j = 0; j < ny; j++) {
    let m = 0;
    for (let i = 0; i < nx; i++) m += v[j * nx + i];
    m /= nx;
    for (let i = 0; i < nx; i++) rv[j * nx + i] = v[j * nx + i] - m;
  }
  const col = [];
  for (let i = 0; i < nx; i++) {
    let s = 0;
    for (let j = 0; j < ny; j++) s += rv[j * nx + i];
    col.push(s / ny);
  }
  /* per-pixel sigma measured as the d2 of each individual row, scaled back */
  let ss = 0, n = 0;
  for (let j = 0; j < ny; j++) {
    const row = [];
    for (let i = 0; i < nx; i++) row.push(rv[j * nx + i]);
    for (const d of d2(row)) { ss += d * d; n++; }
  }
  const sigma = Math.sqrt(ss / n / 6);          // undo the sqrt(6) of d2
  const colD2 = rms(d2(col));
  const expV = Math.sqrt(6) * sigma / Math.sqrt(ny);

  /* ---- horizontal structure: profile across rows ---- */
  const rh = new Float64Array(nx * ny);
  for (let i = 0; i < nx; i++) {
    let m = 0;
    for (let j = 0; j < ny; j++) m += v[j * nx + i];
    m /= ny;
    for (let j = 0; j < ny; j++) rh[j * nx + i] = v[j * nx + i] - m;
  }
  const rowp = [];
  for (let j = 0; j < ny; j++) {
    let s = 0;
    for (let i = 0; i < nx; i++) s += rh[j * nx + i];
    rowp.push(s / nx);
  }
  let ss2 = 0, n2 = 0;
  for (let i = 0; i < nx; i++) {
    const colv = [];
    for (let j = 0; j < ny; j++) colv.push(rh[j * nx + i]);
    for (const d of d2(colv)) { ss2 += d * d; n2++; }
  }
  const sigmaY = Math.sqrt(ss2 / n2 / 6);
  const rowD2 = rms(d2(rowp));
  const expH = Math.sqrt(6) * sigmaY / Math.sqrt(nx);

  /* biggest single column-coherent step anywhere in x, in codes: a contour
   * would show up here even if the rms is small */
  let maxStep = 0, atX = 0;
  for (let i = 4; i < nx - 4; i++) {
    let a = 0, b = 0;
    for (let k = 1; k <= 4; k++) { a += col[i - k]; b += col[i + k - 1]; }
    const s = Math.abs(b / 4 - a / 4);
    if (s > maxStep) { maxStep = s; atX = x0 + i; }
  }

  return {ch: CH[c], nx, ny,
          sigmaPerPx: +sigma.toFixed(3),
          colD2rms: +colD2.toFixed(4), expectedV: +expV.toFixed(4),
          ratioV: +(colD2 / expV).toFixed(2),
          colCoherentAmpCodes: +Math.sqrt(Math.max(0, rms(col) ** 2 - sigma * sigma / ny)).toFixed(3),
          maxColStepCodes: +maxStep.toFixed(3), atX,
          rowD2rms: +rowD2.toFixed(4), expectedH: +expH.toFixed(4),
          ratioH: +(rowD2 / expH).toFixed(2)};
}

let minH = H;
for (let x = 0; x < W; x++) minH = Math.min(minH, horiz[x]);
const patches = [{label: 'sky-fullwidth', x0: 0, x1: W, y0: 0, y1: Math.max(8, minH - MARGIN)}];
{
  const x0 = (W * 0.55) | 0, x1 = (W * 0.98) | 0;
  let m = H;
  for (let x = x0; x < x1; x++) m = Math.min(m, horiz[x]);
  patches.push({label: 'sky-right-side', x0, x1, y0: 0, y1: Math.max(8, m - MARGIN)});
}
for (let i = 3; i + 4 < process.argv.length; i += 5)
  patches.push({x0: +process.argv[i], y0: +process.argv[i + 1], x1: +process.argv[i + 2],
                y1: +process.argv[i + 3], label: process.argv[i + 4]});

const out = {tag, patches: []};
for (const p of patches) {
  if (p.y1 - p.y0 < 8 || p.x1 - p.x0 < 40) continue;
  out.patches.push({...p, ch: [0, 1, 2].map(c => test(p.x0, p.y0, p.x1, p.y1, c))});
}
console.log(JSON.stringify(out, null, 2));
fs.writeFileSync(path.join(DIR, tag + '.coh2.json'), JSON.stringify(out, null, 2));
