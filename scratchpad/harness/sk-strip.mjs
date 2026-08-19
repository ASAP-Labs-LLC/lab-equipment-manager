/* sk-strip.mjs — what is actually in the top of the judged frame.
 *
 *   node sk-strip.mjs [--cam far] [--time 9] [--hide terrain-mainland] [--out x.png]
 *
 * Prints a row-band profile (mean RGB, B-R) down the frame, so "the sky is an
 * untextured two-stop gradient" and "the distant landmass is a flat band" can be
 * separated: at a pitched-down camera there may be no sky in shot at all.
 */
import {chromium} from 'playwright';
import fs from 'fs';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${a.mods || 'sky,gi,terrain'}`
          + `&cam=${a.cam || 'far'}&time=${a.time || '9'}&hud=0&quality=ultra&weather=clear`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null; const t1 = Date.now();
while (Date.now() - t1 < 25000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => { const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now; if (stable >= 10) break;
}
const meta = await page.evaluate(() => {
  const w = window.__lemWorld, cam = w.camera; w.rig.idleDrift = false; w.rig.apply(1);
  cam.updateMatrixWorld(true);
  const e = cam.matrixWorld.elements;
  /* forward is -Z of the camera basis */
  const fy = -e[9];
  return {camY: +cam.position.y.toFixed(1), pitchDeg: +(Math.asin(fy) * 180 / Math.PI).toFixed(2),
          fov: cam.fov, dist: +Math.hypot(cam.position.x - w.rig.target.x, cam.position.z - w.rig.target.z).toFixed(0)};
});
if (a.hide) await page.evaluate(n => {
  window.__lemWorld.scene.traverse(o => { if (o.name === n) o.visible = false; });
}, a.hide);
await page.waitForTimeout(700);
const buf = await page.screenshot({type: 'png'});
if (a.out) fs.writeFileSync(a.out, buf);
const rows = await page.evaluate(async (src) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  const out = [];
  for (let y0 = 0; y0 < im.height; y0 += 16) {
    let r = 0, gg = 0, bb = 0, n = 0;
    for (let y = y0; y < Math.min(y0 + 16, im.height); y += 2)
      for (let x = 0; x < im.width; x += 4) {
        const o = (y * im.width + x) * 4; r += d[o]; gg += d[o + 1]; bb += d[o + 2]; n++;
      }
    r /= n; gg /= n; bb /= n;
    out.push({y: y0, rgb: [r, gg, bb].map(v => +v.toFixed(1)),
              L: +(0.2126 * r + 0.7152 * gg + 0.0722 * bb).toFixed(1), BR: +(bb - r).toFixed(1)});
  }
  return out;
}, 'data:image/png;base64,' + buf.toString('base64'));
console.log(JSON.stringify({...meta, hide: a.hide || null, rows}, null, 0));
await b.close();
