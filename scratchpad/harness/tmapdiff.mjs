/* tmapdiff.mjs — is the rolling stock actually IN the near shadow map?
 *
 * Reading the map as an image cannot answer this: it is packed depth, and the
 * top byte moves by one level per ~3.5 m over a 900 m frustum, so a tank car
 * four metres off the ballast is invisible in the picture whether or not it was
 * drawn. So read the map twice — once as shipped, once with the trains hidden —
 * and paint the texels that changed. Those, and only those, are the consist.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const OUT = path.resolve(args.out || '../shots/tmapdiff');
fs.mkdirSync(OUT, {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await (await browser.newContext({viewport: {width: 1280, height: 720}})).newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(6000);
await page.evaluate(() => {
  const w = window.__lemWorld;
  /* Freeze every subsystem, not just the trains: gi re-fits the shadow camera
   * as the world runs and a refit moves every texel, which drowns the one
   * difference this tool exists to isolate. */
  w.engine.updaters = [];
});

const grab = () => page.evaluate(() => new Promise(res => {
  const w = window.__lemWorld;
  w.engine.shadowNeedsUpdate = true;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const gi = w.subsystems.get('gi'), map = gi.sun.shadow.map;
    const N = map.width, buf = new Uint8Array(N * N * 4);
    w.engine.renderer.readRenderTargetPixels(map, 0, 0, N, N, buf);
    /* Pack to a 32-bit depth per texel so a change of one part in 2^24 counts. */
    const out = new Uint32Array(N * N);
    for (let i = 0; i < N * N; i++) {
      out[i] = (buf[i * 4] << 24) | (buf[i * 4 + 1] << 16) |
               (buf[i * 4 + 2] << 8) | buf[i * 4 + 3];
    }
    res({N, data: Array.from(out)});
  }));
}));

const a = await grab();
await page.evaluate(() => {
  window.__lemWorld.subsystems.get('trains').root.visible = false;
});
const b = await grab();
await browser.close();

const N = a.N;
let changed = 0;
const rows = new Array(N).fill(0);
const cols = new Array(N).fill(0);
for (let i = 0; i < N * N; i++) {
  if (a.data[i] !== b.data[i]) { changed++; rows[(i / N) | 0]++; cols[i % N]++; }
}
console.log('map', N, 'texels changed by hiding the trains:', changed,
            '(' + (100 * changed / (N * N)).toFixed(3) + '%)');
const span = arr => {
  let lo = -1, hi = -1;
  for (let i = 0; i < arr.length; i++) if (arr[i]) { if (lo < 0) lo = i; hi = i; }
  return [lo, hi];
};
console.log('rows', span(rows), 'cols', span(cols));

/* And a picture of exactly those texels. */
const M = 1024, s = N / M;
const img = Buffer.alloc(M * M * 3);
for (let y = 0; y < M; y++) {
  for (let x = 0; x < M; x++) {
    let hit = 0;
    for (let j = 0; j < s; j++) for (let i = 0; i < s; i++) {
      const sy = N - 1 - Math.min(N - 1, (y * s + j) | 0);
      const sx = Math.min(N - 1, (x * s + i) | 0);
      if (a.data[sy * N + sx] !== b.data[sy * N + sx]) hit = 255;
    }
    const o = (y * M + x) * 3;
    img[o] = img[o + 1] = img[o + 2] = hit;
  }
}
/* Minimal PNG writer: one filter byte per row, stored with zlib. */
const zlib = await import('node:zlib');
const raw = Buffer.alloc(M * (M * 3 + 1));
for (let y = 0; y < M; y++) {
  raw[y * (M * 3 + 1)] = 0;
  img.copy(raw, y * (M * 3 + 1) + 1, y * M * 3, (y + 1) * M * 3);
}
const chunk = (type, data) => {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
};
let table = null;
function crc32(buf) {
  if (!table) {
    table = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c;
    }
  }
  let c = -1;
  for (const byte of buf) c = table[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}
const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(M, 0); ihdr.writeUInt32BE(M, 4);
ihdr[8] = 8; ihdr[9] = 2;
const png = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
]);
fs.writeFileSync(path.join(OUT, 'trains-in-map.png'), png);
console.log('->', path.join(OUT, 'trains-in-map.png'));
