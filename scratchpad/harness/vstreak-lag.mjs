/* vstreak-lag.mjs — column-coherent structure vs. STREAK WIDTH.
 *
 *   node vstreak-lag.mjs low-t9 [x0 y0 x1 y1 label]...
 *
 * coh2's lag-1 second difference annihilates smooth gradients, but it also
 * annihilates a soft 30-px-wide streak, so "ratioV = 1" from it is not on its
 * own a clean acquittal.  Sweep the lag:
 *
 *   col(x)   = mean over rows of (pixel - that row's mean)
 *   D_L(x)   = col(x-L) - 2col(x) + col(x+L)
 *
 * D_L is still exactly zero for any straight gradient at every L, but it is
 * maximally sensitive to structure about L px wide.  For independent noise of
 * sigma, rms(D_L) = sqrt(6)*sigma/sqrt(ny) for every L, so a single expected
 * value covers the whole sweep and ratio(L) is directly comparable across L.
 *
 * sigma is measured from the same statistic applied WITHIN single rows, where
 * nothing is coherent, which also makes the estimate immune to the gradient.
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
const LAGS = [1, 2, 4, 8, 16, 32, 64];

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

function dL(a, L) {
  let ss = 0, n = 0;
  for (let i = L; i < a.length - L; i++) {
    const d = a[i - L] - 2 * a[i] + a[i + L];
    ss += d * d; n++;
  }
  return n ? Math.sqrt(ss / n) : 0;
}

function test(x0, y0, x1, y1, c) {
  const nx = x1 - x0, ny = y1 - y0;
  const v = new Float64Array(nx * ny);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) v[j * nx + i] = px(x0 + i, y0 + j, c);
  /* strip the per-row mean so the vertical gradient cannot leak in */
  for (let j = 0; j < ny; j++) {
    let m = 0;
    for (let i = 0; i < nx; i++) m += v[j * nx + i];
    m /= nx;
    for (let i = 0; i < nx; i++) v[j * nx + i] -= m;
  }
  const col = new Float64Array(nx);
  for (let i = 0; i < nx; i++) {
    let s = 0;
    for (let j = 0; j < ny; j++) s += v[j * nx + i];
    col[i] = s / ny;
  }
  const res = {ch: CH[c], nx, ny, lag: {}};
  /* sigma per lag, from single rows */
  for (const L of LAGS) {
    if (nx < 4 * L) { res.lag[L] = null; continue; }
    let ss = 0, k = 0;
    for (let j = 0; j < ny; j++) {
      const row = new Float64Array(nx);
      for (let i = 0; i < nx; i++) row[i] = v[j * nx + i];
      const r = dL(row, L);
      ss += r * r; k++;
    }
    const sigma = Math.sqrt(ss / k / 6);
    const obs = dL(col, L);
    const exp = Math.sqrt(6) * sigma / Math.sqrt(ny);
    res.lag[L] = {sigma: +sigma.toFixed(3), obs: +obs.toFixed(4),
                  exp: +exp.toFixed(4), ratio: +(obs / exp).toFixed(2),
                  /* excess amplitude in codes attributable to real structure */
                  excessCodes: +Math.sqrt(Math.max(0, obs * obs - exp * exp) / 6).toFixed(3)};
  }
  return res;
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

const out = {tag, lags: LAGS, patches: []};
for (const p of patches) {
  if (p.y1 - p.y0 < 8 || p.x1 - p.x0 < 40) continue;
  out.patches.push({...p, ch: [0, 1, 2].map(c => test(p.x0, p.y0, p.x1, p.y1, c))});
}
console.log(JSON.stringify(out, null, 2));
fs.writeFileSync(path.join(DIR, tag + '.lag.json'), JSON.stringify(out, null, 2));
