/* tlayers.mjs — dump terrain's layer array + detail map straight out of the
 * page as PNGs, so "what does that texture actually look like" is looked at
 * rather than reasoned about from the generator source. */
import {chromium} from 'playwright';
import fs from 'node:fs';
import zlib from 'node:zlib';

function png(path, w, h, rgb) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 3 + 1)] = 0;
    rgb.copy ? rgb.copy(raw, y * (w * 3 + 1) + 1, y * w * 3, (y + 1) * w * 3)
             : Buffer.from(rgb).copy(raw, y * (w * 3 + 1) + 1, y * w * 3, (y + 1) * w * 3);
  }
  const chunk = (k, b) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(b.length);
    const kb = Buffer.from(k);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32 ? zlib.crc32(Buffer.concat([kb, b])) : crc32(Buffer.concat([kb, b])));
    return Buffer.concat([len, kb, b, crc]);
  };
  const T = [];
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; T.push(c >>> 0); }
  function crc32(buf) { let c = 0xffffffff; for (const b of buf) c = T[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 2;
  fs.writeFileSync(path, Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]));
}

const url = process.argv[2], prefix = process.argv[3];
const b = await chromium.launch({headless: true, channel: 'chromium', args: ['--use-angle=metal']});
const p = await b.newPage({viewport: {width: 640, height: 400}});
await p.goto(url, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
const dump = await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const L = t.layerTex, D = t.detailTex;
  return {S: L.image.width, N: L.image.depth,
          layers: Array.from(L.image.data),
          dS: D.image.width, detail: Array.from(D.image.data)};
});
await b.close();

const {S, N} = dump;
for (let l = 0; l < N; l++) {
  const rgb = Buffer.alloc(S * S * 3);
  const a = Buffer.alloc(S * S * 3);
  for (let i = 0; i < S * S; i++) {
    const o = (l * S * S + i) * 4;
    rgb[i * 3] = dump.layers[o]; rgb[i * 3 + 1] = dump.layers[o + 1]; rgb[i * 3 + 2] = dump.layers[o + 2];
    a[i * 3] = a[i * 3 + 1] = a[i * 3 + 2] = dump.layers[o + 3];
  }
  png(`${prefix}-L${l}.png`, S, S, rgb);
  png(`${prefix}-L${l}a.png`, S, S, a);
}
const dS = dump.dS;
const drgb = Buffer.alloc(dS * dS * 3), dbb = Buffer.alloc(dS * dS * 3);
for (let i = 0; i < dS * dS; i++) {
  drgb[i * 3] = dump.detail[i * 4]; drgb[i * 3 + 1] = dump.detail[i * 4 + 1]; drgb[i * 3 + 2] = 128;
  dbb[i * 3] = dbb[i * 3 + 1] = dbb[i * 3 + 2] = dump.detail[i * 4 + 2];
}
png(`${prefix}-detailN.png`, dS, dS, drgb);
png(`${prefix}-detailB.png`, dS, dS, dbb);
console.log('wrote', prefix, S, N);
