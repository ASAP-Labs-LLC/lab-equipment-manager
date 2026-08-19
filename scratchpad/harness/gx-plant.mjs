/* gx-plant.mjs — the plant, magnified, three ways: as shipped, with the coarse
 * cascades gated off, and the amplified difference. The crop is computed from
 * the sites' own world boxes, so it is the plant and not a fixed rectangle that
 * may or may not contain it (an earlier probe's mistake).
 *
 *   node gx-plant.mjs [--cam far] [--time 9] [--out /tmp/gx] [--scale 3]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9', OUT = a.out || '/tmp/gx';
const scale = parseFloat(a.scale || '3'), gain = parseFloat(a.gain || '4');
fs.mkdirSync(OUT, {recursive: true});
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}, deviceScaleFactor: scale});
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(10000);
await page.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } w.camera.updateMatrixWorld(true); });
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  if (typeof gi.setExposureLocked === 'function') gi.setExposureLocked(true);
  else { gi.__grade = gi._applyGrade; gi._applyGrade = () => {}; }
});
await page.waitForTimeout(1200);

const clip = await page.evaluate((sel) => {
  const w = window.__lemWorld, THREE = w.ctx.THREE, cam = w.camera;
  const v = new THREE.Vector3();
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  const sites = [];
  w.scene.traverse(o => { if (/^site:/.test(o.name || '')) sites.push(o); });
  for (const s of sites) {
    if (sel && !s.name.includes(sel)) continue;
    s.updateWorldMatrix(true, false);
    const e = s.matrixWorld.elements;
    for (const dx of [-70, 0, 70]) for (const dz of [-70, 0, 70]) for (const dy of [0, 32]) {
      v.set(e[12] + dx, e[13] + dy, e[14] + dz).project(cam);
      x0 = Math.min(x0, (v.x * .5 + .5) * innerWidth); x1 = Math.max(x1, (v.x * .5 + .5) * innerWidth);
      y0 = Math.min(y0, (-v.y * .5 + .5) * innerHeight); y1 = Math.max(y1, (-v.y * .5 + .5) * innerHeight);
    }
  }
  const pad = 20;
  x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad);
  x1 = Math.min(innerWidth, x1 + pad); y1 = Math.min(innerHeight, y1 + pad);
  return {x: Math.round(x0), y: Math.round(y0),
          width: Math.max(8, Math.round(x1 - x0)), height: Math.max(8, Math.round(y1 - y0))};
}, a.site || null);
console.log('clip', JSON.stringify(clip));

const A = await page.screenshot({type: 'png', clip});
fs.writeFileSync(path.join(OUT, `plantA-${cam}-${time}.png`), A);
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.__svc = gi._serviceCascades; gi._serviceCascades = () => {};
  gi.uniforms.lemCsmReady0.value = 0; gi.uniforms.lemCsmReady1.value = 0;
});
await page.waitForTimeout(1500);
const B = await page.screenshot({type: 'png', clip});
fs.writeFileSync(path.join(OUT, `plantB-${cam}-${time}.png`), B);

const out = await page.evaluate(async ({sa, sb, gain}) => {
  const load = s => new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = s; });
  const ia = await load(sa), ib = await load(sb);
  const mk = im => { const c = document.createElement('canvas'); c.width = im.width; c.height = im.height;
    const g = c.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    return g.getImageData(0, 0, im.width, im.height).data; };
  const da = mk(ia), db = mk(ib);
  const c = document.createElement('canvas'); c.width = ia.width; c.height = ia.height;
  const g = c.getContext('2d');
  const img = g.createImageData(ia.width, ia.height);
  let hits = 0, sum = 0;
  for (let i = 0; i < da.length; i += 4) {
    const la = 0.2126 * da[i] + 0.7152 * da[i + 1] + 0.0722 * da[i + 2];
    const lb = 0.2126 * db[i] + 0.7152 * db[i + 1] + 0.0722 * db[i + 2];
    const d = lb - la;
    if (d > 3) { hits++; sum += d; }
    const v = Math.max(0, Math.min(255, Math.round(d * gain)));
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  return {png: c.toDataURL('image/png'), coverage: +(hits / (da.length / 4)).toFixed(4),
          meanDepth: hits ? +(sum / hits).toFixed(2) : 0};
}, {sa: 'data:image/png;base64,' + A.toString('base64'),
    sb: 'data:image/png;base64,' + B.toString('base64'), gain});
fs.writeFileSync(path.join(OUT, `plantD-${cam}-${time}.png`),
                 Buffer.from(out.png.split(',')[1], 'base64'));
delete out.png;
console.log(JSON.stringify({cam, time, clip, ...out}));
await b.close();
