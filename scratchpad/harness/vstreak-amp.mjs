/* vstreak-amp.mjs — amplified visual of the sky residual, dither on vs off.
 *
 *   node vstreak-amp.mjs street-t184 street-t184-g0 out.png [gain]
 *
 * Takes the same sky rows from both frames, subtracts each row's own smooth
 * degree-6 fit (so the gradient itself goes away and only the departure from it
 * is left), multiplies by `gain`, and stacks the two as one image with a
 * separator.  Top = as shipped, bottom = with engine.js's output dither
 * uniform held at 0 for the capture.  Nothing on disk was modified to make it.
 */
import fs from 'node:fs';
import zlib from 'node:zlib';
import path from 'node:path';

const DIR = '/Users/rynatical/LAB-lem/scratchpad/harness/vstreak';
const [tA, tB, outName] = [process.argv[2], process.argv[3], process.argv[4] || 'amp.png'];
const GAIN = parseFloat(process.argv[5] || '20');
const W = 1280;

function load(tag) {
  const m = JSON.parse(fs.readFileSync(path.join(DIR, tag + '.meta.json'), 'utf8'));
  return {m, buf: fs.readFileSync(path.join(DIR, tag + '.rgb'))};
}
function polyfit(ys, deg) {
  const n = ys.length, mm = deg + 1;
  const A = Array.from({length: mm}, () => new Float64Array(mm));
  const b = new Float64Array(mm), pw = new Float64Array(2 * deg + 1);
  for (let i = 0; i < n; i++) {
    const t = (2 * i) / (n - 1) - 1;
    let p = 1;
    for (let k = 0; k <= 2 * deg; k++) { pw[k] += p; p *= t; }
    p = 1;
    for (let k = 0; k <= deg; k++) { b[k] += ys[i] * p; p *= t; }
  }
  for (let r = 0; r < mm; r++) for (let c = 0; c < mm; c++) A[r][c] = pw[r + c];
  for (let i = 0; i < mm; i++) {
    let p = i;
    for (let r = i + 1; r < mm; r++) if (Math.abs(A[r][i]) > Math.abs(A[p][i])) p = r;
    [A[i], A[p]] = [A[p], A[i]]; const t = b[i]; b[i] = b[p]; b[p] = t;
    for (let r = i + 1; r < mm; r++) {
      const f = A[r][i] / A[i][i];
      for (let c = i; c < mm; c++) A[r][c] -= f * A[i][c];
      b[r] -= f * b[i];
    }
  }
  const co = new Float64Array(mm);
  for (let i = mm - 1; i >= 0; i--) {
    let s = b[i];
    for (let c = i + 1; c < mm; c++) s -= A[i][c] * co[c];
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

const A = load(tA), B = load(tB);
const NY = parseInt(process.argv[6] || '190', 10);   // sky rows to show
const SEP = 6;
const OH = NY * 2 + SEP;
const img = Buffer.alloc(OH * (W * 3 + 1));          // filter byte per row

function band(src, dstRow) {
  for (let y = 0; y < NY; y++) {
    const o = (dstRow + y) * (W * 3 + 1);
    img[o] = 0;
    for (let c = 0; c < 3; c++) {
      const row = new Float64Array(W);
      for (let x = 0; x < W; x++) row[x] = src.buf[(y * W + x) * 3 + c];
      const fit = polyfit(row, 6);
      for (let x = 0; x < W; x++) {
        const v = 128 + (row[x] - fit[x]) * GAIN;
        img[o + 1 + x * 3 + c] = Math.max(0, Math.min(255, Math.round(v)));
      }
    }
  }
}
band(A, 0);
for (let y = NY; y < NY + SEP; y++) {
  const o = y * (W * 3 + 1);
  img[o] = 0;
  for (let x = 0; x < W; x++) { img[o + 1 + x * 3] = 220; img[o + 2 + x * 3] = 40; img[o + 3 + x * 3] = 40; }
}
band(B, NY + SEP);

/* minimal PNG writer */
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crcTab = [];
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crcTab[n] = c >>> 0; }
  let crc = 0xffffffff;
  for (const byte of td) crc = crcTab[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  const cb = Buffer.alloc(4); cb.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([len, td, cb]);
}
const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(W, 0); ihdr.writeUInt32BE(OH, 4);
ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
const png = Buffer.concat([
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
  chunk('IHDR', ihdr),
  chunk('IDAT', zlib.deflateSync(img, {level: 9})),
  chunk('IEND', Buffer.alloc(0)),
]);
const out = path.join(DIR, outName);
fs.writeFileSync(out, png);
console.log('wrote', out, W + 'x' + OH, 'gain', GAIN, '| top:', tA, ' bottom:', tB);
