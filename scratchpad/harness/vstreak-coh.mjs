/* vstreak-coh.mjs — is there structure COHERENT DOWN COLUMNS?
 *
 *   node vstreak-coh.mjs low-t9 [x0 y0 x1 y1 label]...
 *
 * "Vertical streaking" means a per-x deviation that persists over many rows.
 * Run-length stats cannot see that: a streak 1 code deep with dither on top has
 * a run length of 1 and is still plainly visible, because the eye averages down
 * the column.  So model the patch as v(x,y) = rowMean(y) + col(x) + noise, and
 * ask how big col(x) is compared with what pure per-pixel noise would produce
 * by chance (sigma / sqrt(nRows)).
 *
 *   ratioV  = std_x(col profile) / (sigma / sqrt(nRows))
 *             1.0  = no vertical structure beyond noise
 *            >2.0  = real streaks
 *   ratioH  = the same test rotated, for horizontal banding.
 *
 * With no rect given it uses the auto-detected dome, then reports the mainland
 * band and the foreground for comparison.
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

function coherence(x0, y0, x1, y1, c) {
  const nx = x1 - x0, ny = y1 - y0;
  const v = new Float64Array(nx * ny);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) v[j * nx + i] = px(x0 + i, y0 + j, c);
  /* remove the per-row mean, so a pure vertical gradient cannot masquerade as
   * vertical structure, then also remove a smooth degree-3 trend in x from the
   * column profile so the gradient itself is not counted as a streak. */
  const r = new Float64Array(nx * ny);
  for (let j = 0; j < ny; j++) {
    let m = 0;
    for (let i = 0; i < nx; i++) m += v[j * nx + i];
    m /= nx;
    for (let i = 0; i < nx; i++) r[j * nx + i] = v[j * nx + i] - m;
  }
  const col = new Float64Array(nx);
  for (let i = 0; i < nx; i++) {
    let s = 0;
    for (let j = 0; j < ny; j++) s += r[j * nx + i];
    col[i] = s / ny;
  }
  /* detrend col(x) with a cubic — the horizontal gradient is signal, not streak */
  const cm = [0, 0, 0, 0].map(() => 0);
  let sx = 0;
  for (let i = 0; i < nx; i++) sx += col[i];
  const mean = sx / nx;
  /* simple: subtract a moving average of 65 px, leaving only fine x-structure */
  const K = 32;
  const det = new Float64Array(nx);
  for (let i = 0; i < nx; i++) {
    let s = 0, n = 0;
    for (let k = -K; k <= K; k++) { const j = i + k; if (j >= 0 && j < nx) { s += col[j]; n++; } }
    det[i] = col[i] - s / n;
  }
  let vv = 0;
  for (let i = 0; i < nx; i++) vv += det[i] * det[i];
  const colAmp = Math.sqrt(vv / nx);
  /* per-pixel sigma after the same high-pass, measured row by row */
  let ss = 0, n2 = 0;
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      let s = 0, n = 0;
      for (let k = -K; k <= K; k++) { const q = i + k; if (q >= 0 && q < nx) { s += r[j * nx + q]; n++; } }
      const d = r[j * nx + i] - s / n;
      ss += d * d; n2++;
    }
  }
  const sigma = Math.sqrt(ss / n2);
  const expected = sigma / Math.sqrt(ny);
  /* rotated: horizontal banding */
  const r2 = new Float64Array(nx * ny);
  for (let i = 0; i < nx; i++) {
    let m = 0;
    for (let j = 0; j < ny; j++) m += v[j * nx + i];
    m /= ny;
    for (let j = 0; j < ny; j++) r2[j * nx + i] = v[j * nx + i] - m;
  }
  const rowp = new Float64Array(ny);
  for (let j = 0; j < ny; j++) {
    let s = 0;
    for (let i = 0; i < nx; i++) s += r2[j * nx + i];
    rowp[j] = s / nx;
  }
  const KY = Math.min(32, Math.max(4, ny >> 2));
  const detY = new Float64Array(ny);
  for (let j = 0; j < ny; j++) {
    let s = 0, n = 0;
    for (let k = -KY; k <= KY; k++) { const q = j + k; if (q >= 0 && q < ny) { s += rowp[q]; n++; } }
    detY[j] = rowp[j] - s / n;
  }
  let vy = 0;
  for (let j = 0; j < ny; j++) vy += detY[j] * detY[j];
  const rowAmp = Math.sqrt(vy / ny);
  let ssy = 0, ny2 = 0;
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < ny; j++) {
      let s = 0, n = 0;
      for (let k = -KY; k <= KY; k++) { const q = j + k; if (q >= 0 && q < ny) { s += r2[q * nx + i]; n++; } }
      const d = r2[j * nx + i] - s / n;
      ssy += d * d; ny2++;
    }
  }
  const sigmaY = Math.sqrt(ssy / ny2);
  return {
    ch: CH[c], nx, ny,
    sigmaPerPx: +sigma.toFixed(3),
    colAmp: +colAmp.toFixed(4), colExpectedFromNoise: +expected.toFixed(4),
    ratioV: +(colAmp / expected).toFixed(2),
    rowAmp: +rowAmp.toFixed(4), rowExpectedFromNoise: +(sigmaY / Math.sqrt(nx)).toFixed(4),
    ratioH: +(rowAmp / (sigmaY / Math.sqrt(nx))).toFixed(2),
  };
}

/* auto sky rect: full-width rows above the shallowest horizon */
let minH = H;
for (let x = 0; x < W; x++) minH = Math.min(minH, horiz[x]);
const patches = [];
/* widest full-width dome rect we can get, plus a mid-width one that skips the
 * cloud columns by taking the middle 60% of the frame */
patches.push({label: 'sky-fullwidth', x0: 0, x1: W, y0: 0, y1: Math.max(8, minH - MARGIN)});
{
  const x0 = (W * 0.55) | 0, x1 = (W * 0.98) | 0;
  let m = H;
  for (let x = x0; x < x1; x++) m = Math.min(m, horiz[x]);
  patches.push({label: 'sky-right-side', x0, x1, y0: 0, y1: Math.max(8, m - MARGIN)});
}
for (let i = 3; i + 4 < process.argv.length; i += 5) {
  patches.push({x0: +process.argv[i], y0: +process.argv[i + 1],
                x1: +process.argv[i + 2], y1: +process.argv[i + 3],
                label: process.argv[i + 4]});
}

const out = {tag, horizonMin: minH, patches: []};
for (const p of patches) {
  if (p.y1 - p.y0 < 8 || p.x1 - p.x0 < 40) continue;
  out.patches.push({...p, ch: [0, 1, 2].map(c => coherence(p.x0, p.y0, p.x1, p.y1, c))});
}
console.log(JSON.stringify(out, null, 2));
fs.writeFileSync(path.join(DIR, tag + '.coh.json'), JSON.stringify(out, null, 2));
