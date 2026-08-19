/* gimapdump.mjs — read three's own shadow map back and write it as an image.
 *
 * Everything else in this investigation infers the map's contents from the
 * frame. This looks at the map. If a consist is a readable rake of vehicles in
 * here and a blob on the ground, the fault is in the lookup; if it is a blob in
 * here, the fault is in what was drawn.
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
const OUT = path.resolve(args.out || '../shots/gimap');
fs.mkdirSync(OUT, {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);
await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__p = setInterval(() => w.parse(uids[i++ % uids.length], 'L-MAP'), 900);
});
await page.waitForTimeout(parseInt(args.warm || '9000', 10));
await page.evaluate(() => {
  clearInterval(window.__p);
  window.__lemWorld.subsystems.get('trains').update = () => {};
  window.__lemWorld.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(1200);
await page.screenshot({path: path.join(OUT, 'beauty.png')});

const info = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const rn = w.engine.renderer;
  const map = gi.sun.shadow.map;
  const out = {
    mapSize: gi.sun.shadow.mapSize.toArray(),
    have: !!map,
    w: map?.width, h: map?.height,
    texType: map?.texture?.type, texFormat: map?.texture?.format,
    depthTexture: !!map?.depthTexture,
    shadowType: rn.shadowMap.type, autoUpdate: rn.shadowMap.autoUpdate,
    tier: gi.tier?.name,
    bias: gi.sun.shadow.bias, normalBias: gi.sun.shadow.normalBias,
    camera: {left: gi.sun.shadow.camera.left, right: gi.sun.shadow.camera.right,
             near: gi.sun.shadow.camera.near, far: gi.sun.shadow.camera.far},
    lightPos: gi.sun.position.toArray().map(v => +v.toFixed(1)),
    targetPos: gi.sun.target.position.toArray().map(v => +v.toFixed(1)),
  };
  if (!map) return out;
  const N = map.width;
  const buf = new Uint8Array(N * N * 4);
  try { rn.readRenderTargetPixels(map, 0, 0, N, N, buf); }
  catch (e) { out.readError = String(e).slice(0, 120); return out; }
  /* Downsample to 1024 for transport, min-filtered so a thin caster survives. */
  const M = Math.min(1024, N), s = N / M;
  const cvs = document.createElement('canvas');
  cvs.width = cvs.height = M;
  const g = cvs.getContext('2d');
  const img = g.createImageData(M, M);
  let lo = 255, hi = 0, nz = 0;
  for (let y = 0; y < M; y++) {
    for (let x = 0; x < M; x++) {
      let v = 255;
      for (let j = 0; j < s; j++) {
        for (let i = 0; i < s; i++) {
          const sy = N - 1 - Math.min(N - 1, (y * s + j) | 0);
          const sx = Math.min(N - 1, (x * s + i) | 0);
          const p = buf[(sy * N + sx) * 4];
          if (p < v) v = p;
        }
      }
      if (v < lo) lo = v;
      if (v > hi) hi = v;
      if (v < 250) nz++;
      const o = (y * M + x) * 4;
      img.data[o] = img.data[o + 1] = img.data[o + 2] = v;
      img.data[o + 3] = 255;
    }
  }
  g.putImageData(img, 0, 0);
  out.lo = lo; out.hi = hi; out.occupancy = +(nz / (M * M)).toFixed(4);
  out.png = cvs.toDataURL('image/png');
  return out;
});
const png = info.png; delete info.png;
console.log(JSON.stringify(info, null, 1));
if (png) {
  fs.writeFileSync(path.join(OUT, 'nearmap.png'),
                   Buffer.from(png.split(',')[1], 'base64'));
  console.log('map ->', path.join(OUT, 'nearmap.png'));
}
await browser.close();
