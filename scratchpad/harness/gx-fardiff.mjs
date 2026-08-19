/* gx-fardiff.mjs — photograph the cast-shadow term on its own.
 *
 * Renders the judged frame twice with the stop frozen — once as shipped, once
 * with `lemCsmReady{0,1}` zeroed and `_serviceCascades` stubbed so nothing turns
 * them back on — and writes A, B and an amplified B-A. Whatever the coarse
 * cascades are putting on the ground is exactly the third picture, in its own
 * shape, with nothing else in it.
 *
 *   node gx-fardiff.mjs [--cam far] [--time 9] [--out /tmp/gx] [--gain 4]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9', OUT = a.out || '/tmp/gx';
const gain = parseFloat(a.gain || '4');
fs.mkdirSync(OUT, {recursive: true});
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
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
const A = await page.screenshot({type: 'png'});
fs.writeFileSync(path.join(OUT, `A-${cam}-${time}.png`), A);
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.__svc = gi._serviceCascades; gi._serviceCascades = () => {};
  gi.uniforms.lemCsmReady0.value = 0; gi.uniforms.lemCsmReady1.value = 0;
});
await page.waitForTimeout(1500);
const B = await page.screenshot({type: 'png'});
fs.writeFileSync(path.join(OUT, `B-${cam}-${time}.png`), B);

const diff = await page.evaluate(async ({sa, sb, gain}) => {
  const load = s => new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = s; });
  const ia = await load(sa), ib = await load(sb);
  const mk = im => { const c = document.createElement('canvas'); c.width = im.width; c.height = im.height;
    const g = c.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    return g.getImageData(0, 0, im.width, im.height).data; };
  const da = mk(ia), db = mk(ib);
  const c = document.createElement('canvas'); c.width = ia.width; c.height = ia.height;
  const g = c.getContext('2d');
  const img = g.createImageData(ia.width, ia.height);
  let hits = 0, sum = 0, mx = 0;
  for (let i = 0; i < da.length; i += 4) {
    const la = 0.2126 * da[i] + 0.7152 * da[i + 1] + 0.0722 * da[i + 2];
    const lb = 0.2126 * db[i] + 0.7152 * db[i + 1] + 0.0722 * db[i + 2];
    const d = lb - la;                       // positive = the shadow term darkened it
    if (d > 3) { hits++; sum += d; }
    if (d > mx) mx = d;
    const v = Math.max(0, Math.min(255, Math.round(d * gain)));
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  return {png: c.toDataURL('image/png'), w: ia.width, h: ia.height,
          coverage: +(hits / (da.length / 4)).toFixed(4),
          meanDepth: hits ? +(sum / hits).toFixed(2) : 0, maxDepth: +mx.toFixed(1)};
}, {sa: 'data:image/png;base64,' + A.toString('base64'),
    sb: 'data:image/png;base64,' + B.toString('base64'), gain});
fs.writeFileSync(path.join(OUT, `DIFF-${cam}-${time}.png`),
                 Buffer.from(diff.png.split(',')[1], 'base64'));
delete diff.png;
console.log(JSON.stringify({cam, time, gain, ...diff}, null, 1));
await b.close();
