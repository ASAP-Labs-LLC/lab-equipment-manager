/* vstreak-run2.mjs — run-length / gradient / periodicity analysis of the sky.
 *
 *   node vstreak-run2.mjs low-t9
 *
 * Same intent as vstreak-run.mjs but the sky region is a per-row CONTIGUOUS
 * SEGMENT rather than a full-width rectangle.  A rectangle is bounded by the
 * single highest thing in frame — for cam=low that is a cloud patch at row 39,
 * which threw away 100 rows of perfectly good dome.  A per-row segment routes
 * around both the mainland ridge and the clouds and measures the dome itself.
 *
 * PER CHANNEL, along X and down Y:
 *   run lengths of constant 8-bit value, the jump size at each run boundary,
 *   the code budget the gradient spends, and the spectrum of a detrended row.
 * Measurement only.
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

/* ---------- 1. per-column horizon --------------------------------------- */
/* The dome is smooth in Y; every non-dome thing here (mainland ridge, sea,
 * foreground, and the stippled cloud patches) meets it across a step that
 * PERSISTS below the edge.  Compare 7-row means either side of y. */
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
const skyTop = y => x => horiz[x] - MARGIN > y;

/* longest contiguous run of sky columns at row y */
function segment(y) {
  let best = null, s = -1;
  const ok = skyTop(y);
  for (let x = 0; x <= W; x++) {
    const in_ = x < W && ok(x);
    if (in_ && s < 0) s = x;
    if (!in_ && s >= 0) { if (!best || x - s > best[1] - best[0]) best = [s, x]; s = -1; }
  }
  return best;
}
const rows = [];
for (let y = 0; y < H; y++) {
  const g = segment(y);
  if (g && g[1] - g[0] >= 400) rows.push({y, x0: g[0], x1: g[1]});
}
const maxRow = rows.length ? rows[rows.length - 1].y : 0;

/* ---------- polyfit ------------------------------------------------------ */
function polyfit(ys, deg) {
  const n = ys.length, m = deg + 1;
  const A = Array.from({length: m}, () => new Float64Array(m));
  const b = new Float64Array(m), pw = new Float64Array(2 * deg + 1);
  for (let i = 0; i < n; i++) {
    const t = (2 * i) / (n - 1) - 1;
    let p = 1;
    for (let k = 0; k <= 2 * deg; k++) { pw[k] += p; p *= t; }
    p = 1;
    for (let k = 0; k <= deg; k++) { b[k] += ys[i] * p; p *= t; }
  }
  for (let r = 0; r < m; r++) for (let c = 0; c < m; c++) A[r][c] = pw[r + c];
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

/* ---------- 2. run lengths ---------------------------------------------- */
function stats(runs, total) {
  if (!runs.length) return {med: 0, p90: 0, p99: 0, max: 0, frac8: 0, n: 0};
  const s = Int32Array.from(runs).sort();
  const q = p => s[Math.min(s.length - 1, Math.floor(s.length * p))];
  let cov = 0;
  for (const r of runs) if (r > 8) cov += r;
  return {med: q(0.5), p90: q(0.9), p99: q(0.99), max: s[s.length - 1],
          frac8: +(cov / total).toFixed(5), n: runs.length};
}
function runsOf(v) {
  const runs = [], steps = [];
  let len = 1;
  for (let i = 1; i < v.length; i++) {
    if (v[i] === v[i - 1]) len++;
    else { runs.push(len); steps.push(Math.abs(v[i] - v[i - 1])); len = 1; }
  }
  runs.push(len);
  return {runs, steps};
}

const rowRuns = {}, colRuns = {}, stepHist = {}, budget = {};
for (let c = 0; c < 3; c++) {
  const runs = [], steps = []; let total = 0;
  const spans = [];
  for (const r of rows) {
    const n = r.x1 - r.x0, v = new Uint8Array(n);
    let lo = 255, hi = 0;
    for (let i = 0; i < n; i++) { const p = px(r.x0 + i, r.y, c); v[i] = p; if (p < lo) lo = p; if (p > hi) hi = p; }
    const q = runsOf(v);
    runs.push(...q.runs); steps.push(...q.steps); total += n;
    spans.push(hi - lo);
  }
  rowRuns[CH[c]] = stats(runs, total);
  const hist = {};
  for (const s of steps) hist[s] = (hist[s] || 0) + 1;
  stepHist[CH[c]] = hist;

  /* columns: sky rows of that column only */
  const cruns = []; let ctotal = 0; const cspans = [];
  for (let x = 0; x < W; x++) {
    const n = Math.min(horiz[x] - MARGIN, maxRow + 1);
    if (n < 40) continue;
    const v = new Uint8Array(n);
    let lo = 255, hi = 0;
    for (let y = 0; y < n; y++) { const p = px(x, y, c); v[y] = p; if (p < lo) lo = p; if (p > hi) hi = p; }
    cruns.push(...runsOf(v).runs); ctotal += n; cspans.push(hi - lo);
  }
  colRuns[CH[c]] = stats(cruns, ctotal);

  const med = a => { const s = [...a].sort((p, q2) => p - q2); return s[s.length >> 1]; };
  budget[CH[c]] = {horizMedSpan: med(spans), horizMaxSpan: Math.max(...spans),
                   vertMedSpan: med(cspans), vertMaxSpan: Math.max(...cspans)};
}

/* luminance, the way the previous measurement did it, for contrast */
function lumRuns(dir) {
  const runs = []; let total = 0;
  if (dir === 'row') {
    for (const r of rows) {
      const n = r.x1 - r.x0, v = new Float64Array(n);
      for (let i = 0; i < n; i++) v[i] = lum(r.x0 + i, r.y);
      runs.push(...runsOf(v).runs); total += n;
    }
  } else {
    for (let x = 0; x < W; x++) {
      const n = Math.min(horiz[x] - MARGIN, maxRow + 1);
      if (n < 40) continue;
      const v = new Float64Array(n);
      for (let y = 0; y < n; y++) v[y] = lum(x, y);
      runs.push(...runsOf(v).runs); total += n;
    }
  }
  return stats(runs, total);
}

/* ---------- 3. periodicity ---------------------------------------------- */
function spectrum(r, c) {
  const n = r.x1 - r.x0;
  const row = new Float64Array(n);
  for (let i = 0; i < n; i++) row[i] = px(r.x0 + i, r.y, c);
  const fit = polyfit(row, 6);
  const res = new Float64Array(n);
  let ss = 0;
  for (let i = 0; i < n; i++) { res[i] = row[i] - fit[i]; ss += res[i] * res[i]; }
  const rms = Math.sqrt(ss / n);
  const win = new Float64Array(n);
  for (let i = 0; i < n; i++) win[i] = res[i] * 0.5 * (1 - Math.cos(2 * Math.PI * i / (n - 1)));
  const mags = [];
  for (let k = 2; k <= n / 4; k++) {
    let re = 0, im = 0;
    for (let i = 0; i < n; i++) {
      const a = -2 * Math.PI * k * i / n;
      re += win[i] * Math.cos(a); im += win[i] * Math.sin(a);
    }
    mags.push({period: n / k, mag: Math.hypot(re, im) / n});
  }
  const tot = mags.reduce((a, m) => a + m.mag * m.mag, 0);
  const top = [...mags].sort((a, b) => b.mag - a.mag).slice(0, 5)
    .map(m => ({period: +m.period.toFixed(2), pctPower: +(100 * m.mag * m.mag / tot).toFixed(2)}));
  let lg = 0, ar = 0;
  for (const m of mags) { const p = m.mag * m.mag + 1e-18; lg += Math.log(p); ar += p; }
  /* power in the 20-40 px band, where a 256-texel azimuth LUT would land */
  let pl = 0;
  for (const m of mags) if (m.period >= 20 && m.period <= 40) pl += m.mag * m.mag;
  return {row: r.y, ch: CH[c], width: n, rmsCodes: +rms.toFixed(3),
          spectralFlatness: +(Math.exp(lg / mags.length) / (ar / mags.length)).toFixed(4),
          pctPower20to40px: +(100 * pl / tot).toFixed(2), top};
}

const fovV = meta.fovDeg * Math.PI / 180;
const fovH = 2 * Math.atan(Math.tan(fovV / 2) * (16 / 9));
const f = (W / 2) / Math.tan(fovH / 2);
const texRad = (2 * Math.PI) / 256;
const thEdge = Math.atan((W / 2) / f);
const wide = rows.filter(r => r.x1 - r.x0 > 900);
const pick = wide.length >= 3
  ? [wide[(wide.length * 0.15) | 0], wide[(wide.length * 0.5) | 0], wide[(wide.length * 0.85) | 0]]
  : rows.slice(0, 3);
const spectra = [];
for (const r of pick) for (const c of [0, 1, 2]) spectra.push(spectrum(r, c));

const qPred = {};
for (const ch of CH) qPred[ch] = +((rows.length ? (rows[0].x1 - rows[0].x0) : W) /
                                   Math.max(1, budget[ch].horizMedSpan)).toFixed(1);

const out = {
  tag, cam: meta.cam, time: meta.time, pitchDeg: meta.pitchDeg, fovDeg: meta.fovDeg,
  skyTopElevDeg: +(-meta.pitchDeg + meta.fovDeg / 2).toFixed(2),
  skyRowsUsed: rows.length, skyRowRange: rows.length ? [rows[0].y, maxRow] : null,
  medianSegmentWidth: rows.length ? rows[(rows.length / 2) | 0].x1 - rows[(rows.length / 2) | 0].x0 : 0,
  skyPixels: rows.reduce((a, r) => a + (r.x1 - r.x0), 0),
  horizonRow: {min: Math.min(...horiz), median: [...horiz].sort((a, b) => a - b)[W >> 1], max: Math.max(...horiz)},
  rowRuns, colRuns, lumRowRuns: lumRuns('row'), lumColRuns: lumRuns('col'),
  budget, qStepPredictionPx: qPred,
  lutTexelPx: {centre: +(f * texRad).toFixed(1), edge: +(f / Math.cos(thEdge) ** 2 * texRad).toFixed(1)},
  stepSizeHistogram: stepHist,
  spectra,
};
console.log(JSON.stringify(out, null, 2));
fs.writeFileSync(path.join(DIR, tag + '.a2.json'), JSON.stringify(out, null, 2));
