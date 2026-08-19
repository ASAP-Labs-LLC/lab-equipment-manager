/* vstreak-run.mjs — run-length / gradient / periodicity analysis of the sky.
 *
 *   node vstreak-run.mjs low-t9 [--dump]
 *
 * Reads vstreak/<tag>.rgb (raw W*H*3) + <tag>.meta.json.  Measures, PER CHANNEL:
 *   - run lengths of constant 8-bit value ALONG X (rows) and DOWN Y (columns)
 *   - the total code budget the gradient spends in each direction
 *   - the size of the jump at each run boundary (1 code == quantisation step)
 *   - the spectrum of a detrended row, to test for LUT-texel periodicity
 * Measurement only.  Reads nothing but the captured frames.
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

/* ---------- 1. horizon, per column ------------------------------------- */
/* The dome is smooth in Y; every non-sky thing in these frames (mainland
 * ridge, sea, foreground) meets it across a hard silhouette edge.  So walk each
 * column down and stop at the first row whose vertical luminance step exceeds
 * EDGE.  The sky rectangle is then everything above the SHALLOWEST horizon
 * found in any column, minus a margin — every row in it is dome in every
 * column, which is what "unambiguously dome" has to mean. */
/* The step must PERSIST to count: a silhouette stays changed below the edge,
 * a 2-3 px cloud speck does not.  Comparing 7-row means either side of y
 * rejects the specks, which otherwise pull the horizon up to row 38. */
const EDGE = 10, RUNW = 7;
const horiz = new Int32Array(W);
for (let x = 0; x < W; x++) {
  let h = H - 1;
  for (let y = RUNW + 1; y < H - RUNW - 1; y++) {
    let a = 0, b2 = 0;
    for (let k = 1; k <= RUNW; k++) { a += lum(x, y - k); b2 += lum(x, y + k - 1); }
    if (Math.abs(b2 / RUNW - a / RUNW) > EDGE) { h = y; break; }
  }
  horiz[x] = h;
}
const MARGIN = 6;
let minH = H;
for (let x = 0; x < W; x++) minH = Math.min(minH, horiz[x]);
const Y0 = 0, Y1 = Math.max(0, minH - MARGIN);      // sky rows [Y0, Y1)
const hs = [...horiz].sort((a, b) => a - b);

/* ---------- 2. cloud mask ---------------------------------------------- */
/* Some frames carry small stippled cloud patches inside the sky rectangle.
 * They are sky, but they are not the gradient, so flag them and report the
 * numbers with and without.  A pixel is flagged if it sits more than CLOUD
 * codes off a degree-6 polynomial fit of its own row (per channel). */
function polyfit(ys, deg) {
  const n = ys.length, m = deg + 1;
  const A = Array.from({length: m}, () => new Float64Array(m));
  const b = new Float64Array(m);
  const pw = new Float64Array(2 * deg + 1);
  for (let i = 0; i < n; i++) {
    const t = (2 * i) / (n - 1) - 1;
    let p = 1;
    for (let k = 0; k <= 2 * deg; k++) { pw[k] += p; p *= t; }
    p = 1;
    for (let k = 0; k <= deg; k++) { b[k] += ys[i] * p; p *= t; }
  }
  for (let r = 0; r < m; r++) for (let c = 0; c < m; c++) A[r][c] = pw[r + c];
  /* gaussian elimination */
  for (let i = 0; i < m; i++) {
    let p = i;
    for (let r = i + 1; r < m; r++) if (Math.abs(A[r][i]) > Math.abs(A[p][i])) p = r;
    [A[i], A[p]] = [A[p], A[i]]; const t = b[i]; b[i] = b[p]; b[p] = t;
    for (let r = i + 1; r < m; r++) {
      const f = A[r][i] / A[i][i];
      for (let c = i; c < m; c++) A[r][c] -= f * A[i][c];
      b[r] -= f * b[i];
    }
  }
  const co = new Float64Array(m);
  for (let i = m - 1; i >= 0; i--) {
    let s = b[i];
    for (let c = i + 1; c < m; c++) s -= A[i][c] * co[c];
    co[i] = s / A[i][i];
  }
  const ev = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const t = (2 * i) / (n - 1) - 1;
    let p = 1, s = 0;
    for (let k = 0; k <= deg; k++) { s += co[k] * p; p *= t; }
    ev[i] = s;
  }
  return ev;
}

/* 6 codes sits well clear of the ~1-code per-pixel dither measured below, so
 * this flags the stippled cloud patches and nothing else. */
const CLOUD = 6;
const cloudy = new Uint8Array(W * H);          // 1 = off-gradient (cloud/haze speck)
for (let y = Y0; y < Y1; y++) {
  for (let c = 0; c < 3; c++) {
    const row = new Float64Array(W);
    for (let x = 0; x < W; x++) row[x] = px(x, y, c);
    const fit = polyfit(row, 6);
    for (let x = 0; x < W; x++) if (Math.abs(row[x] - fit[x]) > CLOUD) cloudy[y * W + x] = 1;
  }
}
const rowCloudFrac = [];
for (let y = Y0; y < Y1; y++) {
  let n = 0;
  for (let x = 0; x < W; x++) n += cloudy[y * W + x];
  rowCloudFrac.push(n / W);
}
/* Clean rows: essentially no off-gradient pixels. */
const cleanRows = [];
for (let y = Y0; y < Y1; y++) if (rowCloudFrac[y - Y0] < 0.002) cleanRows.push(y);

/* ---------- 3. run lengths --------------------------------------------- */
function stats(runs, total) {
  if (!runs.length) return {med: 0, p90: 0, max: 0, frac8: 0, n: 0};
  const s = [...runs].sort((a, b) => a - b);
  const q = p => s[Math.min(s.length - 1, Math.floor(s.length * p))];
  let cov = 0;
  for (const r of runs) if (r > 8) cov += r;
  return {med: q(0.5), p90: q(0.9), max: s[s.length - 1],
          frac8: +(cov / total).toFixed(4), n: runs.length};
}
/* Run lengths of identical 8-bit values along a 1-D sample, plus the size of
 * the jump at each boundary. */
function runsOf(vals) {
  const runs = [], steps = [];
  let len = 1;
  for (let i = 1; i < vals.length; i++) {
    if (vals[i] === vals[i - 1]) len++;
    else { runs.push(len); steps.push(Math.abs(vals[i] - vals[i - 1])); len = 1; }
  }
  runs.push(len);
  return {runs, steps};
}

const CH = ['R', 'G', 'B'];
const rowRes = {}, colRes = {}, rowResClean = {}, stepHist = {};
for (let c = 0; c < 3; c++) {
  let runs = [], steps = [], total = 0;
  let runsC = [], totalC = 0;
  for (let y = Y0; y < Y1; y++) {
    const v = new Uint8Array(W);
    for (let x = 0; x < W; x++) v[x] = px(x, y, c);
    const r = runsOf(v);
    runs.push(...r.runs); steps.push(...r.steps); total += W;
    if (rowCloudFrac[y - Y0] < 0.002) { runsC.push(...r.runs); totalC += W; }
  }
  rowRes[CH[c]] = stats(runs, total);
  rowResClean[CH[c]] = stats(runsC, totalC);
  const hist = {};
  for (const s of steps) hist[s] = (hist[s] || 0) + 1;
  stepHist[CH[c]] = hist;

  /* columns, over the same sky rows */
  let cruns = [], ctotal = 0;
  const nRows = Y1 - Y0;
  for (let x = 0; x < W; x++) {
    const v = new Uint8Array(nRows);
    for (let y = Y0; y < Y1; y++) v[y - Y0] = px(x, y, c);
    cruns.push(...runsOf(v).runs); ctotal += nRows;
  }
  colRes[CH[c]] = stats(cruns, ctotal);
}

/* Luminance, the way the previous measurement did it, for contrast. */
function lumRuns(dir) {
  const runs = []; let total = 0;
  if (dir === 'row') {
    for (let y = Y0; y < Y1; y++) {
      const v = new Float64Array(W);
      for (let x = 0; x < W; x++) v[x] = +lum(x, y).toFixed(6);
      runs.push(...runsOf(v).runs); total += W;
    }
  } else {
    for (let x = 0; x < W; x++) {
      const v = new Float64Array(Y1 - Y0);
      for (let y = Y0; y < Y1; y++) v[y - Y0] = +lum(x, y).toFixed(6);
      runs.push(...runsOf(v).runs); total += Y1 - Y0;
    }
  }
  return stats(runs, total);
}

/* ---------- 4. code budget --------------------------------------------- */
const budget = {};
for (let c = 0; c < 3; c++) {
  const rowSpans = [], colSpans = [];
  for (let y = Y0; y < Y1; y++) {
    let lo = 255, hi = 0;
    for (let x = 0; x < W; x++) { const v = px(x, y, c); if (v < lo) lo = v; if (v > hi) hi = v; }
    rowSpans.push(hi - lo);
  }
  for (let x = 0; x < W; x++) {
    let lo = 255, hi = 0;
    for (let y = Y0; y < Y1; y++) { const v = px(x, y, c); if (v < lo) lo = v; if (v > hi) hi = v; }
    colSpans.push(hi - lo);
  }
  const med = arr => { const s = [...arr].sort((a, b) => a - b); return s[s.length >> 1]; };
  budget[CH[c]] = {
    horizMedSpan: med(rowSpans), horizMaxSpan: Math.max(...rowSpans),
    vertMedSpan: med(colSpans), vertMaxSpan: Math.max(...colSpans),
  };
}

/* ---------- 5. periodicity --------------------------------------------- */
/* One row per frame, mid-sky, detrended by a degree-6 polynomial; DFT of the
 * residual.  Reports the top periods in px and the residual RMS in codes. */
function spectrum(y, c) {
  const row = new Float64Array(W);
  for (let x = 0; x < W; x++) row[x] = px(x, y, c);
  const fit = polyfit(row, 6);
  const res = new Float64Array(W);
  let ss = 0;
  for (let x = 0; x < W; x++) { res[x] = row[x] - fit[x]; ss += res[x] * res[x]; }
  const rms = Math.sqrt(ss / W);
  /* Hann window so a non-integer period does not smear across the whole band */
  const win = new Float64Array(W);
  for (let x = 0; x < W; x++) win[x] = res[x] * 0.5 * (1 - Math.cos(2 * Math.PI * x / (W - 1)));
  const mags = [];
  for (let k = 2; k <= W / 4; k++) {
    let re = 0, im = 0;
    for (let x = 0; x < W; x++) {
      const a = -2 * Math.PI * k * x / W;
      re += win[x] * Math.cos(a); im += win[x] * Math.sin(a);
    }
    mags.push({k, period: +(W / k).toFixed(2), mag: Math.hypot(re, im) / W});
  }
  const tot = mags.reduce((a, m) => a + m.mag * m.mag, 0);
  const top = [...mags].sort((a, b) => b.mag - a.mag).slice(0, 6)
    .map(m => ({period: m.period, mag: +m.mag.toFixed(4),
                pctPower: +(100 * m.mag * m.mag / tot).toFixed(1)}));
  /* flatness: geometric mean / arithmetic mean of the power spectrum.
   * 1.0 == white noise, ->0 == a few dominant lines. */
  let lg = 0, ar = 0;
  for (const m of mags) { const p = m.mag * m.mag + 1e-18; lg += Math.log(p); ar += p; }
  const flat = Math.exp(lg / mags.length) / (ar / mags.length);
  return {row: y, ch: CH[c], rmsCodes: +rms.toFixed(3), spectralFlatness: +flat.toFixed(4), top};
}

/* LUT azimuth texel spacing projected to screen, for comparison. */
const fovV = meta.fovDeg * Math.PI / 180;
const fovH = 2 * Math.atan(Math.tan(fovV / 2) * (16 / 9));
const f = (W / 2) / Math.tan(fovH / 2);                 // focal length in px
const texRad = (2 * Math.PI) / 256;                     // 256 texels over 360 deg
const texPxCentre = f * texRad;
const thEdge = Math.atan((W / 2) / f);
const texPxEdge = f / Math.cos(thEdge) ** 2 * texRad;

const midRow = Math.floor((Y0 + Y1) / 2);
const specRows = [Math.floor(Y1 * 0.25), midRow, Math.floor(Y1 * 0.8)];
const spec = [];
for (const y of specRows) for (const c of [0, 1, 2]) spec.push(spectrum(y, c));

/* Quantisation prediction: if the row is a monotone ramp of N codes across W
 * px, contour steps land ~W/N px apart. */
const qPred = {};
for (const ch of CH) qPred[ch] = +(W / Math.max(1, budget[ch].horizMedSpan)).toFixed(1);

const out = {
  tag, cam: meta.cam, time: meta.time, pitchDeg: meta.pitchDeg, fovDeg: meta.fovDeg,
  skyRows: [Y0, Y1], skyRowCount: Y1 - Y0,
  horizonRow: {min: hs[0], p10: hs[(W * 0.1) | 0], median: hs[W >> 1], max: hs[W - 1]},
  skyTopElevDeg: +(-meta.pitchDeg + meta.fovDeg / 2).toFixed(2),
  cleanRowCount: cleanRows.length,
  rowRuns: rowRes, rowRunsCloudFree: rowResClean, colRuns: colRes,
  lumRowRuns: lumRuns('row'), lumColRuns: lumRuns('col'),
  budget, qStepPredictionPx: qPred,
  lutTexelPx: {centre: +texPxCentre.toFixed(1), edge: +texPxEdge.toFixed(1)},
  stepSizeHistogram: stepHist,
  spectra: spec,
};
console.log(JSON.stringify(out, null, 2));
fs.writeFileSync(path.join(DIR, tag + '.analysis.json'), JSON.stringify(out, null, 2));
