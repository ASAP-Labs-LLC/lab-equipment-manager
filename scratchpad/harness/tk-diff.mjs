/* tk-diff.mjs — |A-B| as a heat image, so an ablation's effect can be SEEN.
 * node tk-diff.mjs a.png b.png out.png [x y w h zoom] [gain] */
import {chromium} from 'playwright';
import fs from 'fs';
const [A, B, out, x, y, w, h, z, gain] = process.argv.slice(2);
const br = await chromium.launch({headless: true, channel: 'chromium'});
const p = await br.newPage();
const load = f => 'data:image/png;base64,' + fs.readFileSync(f).toString('base64');
const data = await p.evaluate(async (o) => {
  const mk = async src => { const i = new Image(); i.src = src;
    await new Promise(r => { i.onload = r; }); return i; };
  const ia = await mk(o.a), ib = await mk(o.b);
  const cv = document.createElement('canvas'); cv.width = ia.width; cv.height = ia.height;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(ia, 0, 0); const da = g.getImageData(0, 0, cv.width, cv.height);
  g.clearRect(0, 0, cv.width, cv.height);
  g.drawImage(ib, 0, 0); const db = g.getImageData(0, 0, cv.width, cv.height);
  const dd = g.createImageData(cv.width, cv.height);
  for (let i = 0; i < da.data.length; i += 4) {
    const d = Math.min(255, Math.abs(
      (0.2126 * da.data[i] + 0.7152 * da.data[i + 1] + 0.0722 * da.data[i + 2]) -
      (0.2126 * db.data[i] + 0.7152 * db.data[i + 1] + 0.0722 * db.data[i + 2])) * o.gain);
    dd.data[i] = d; dd.data[i + 1] = d; dd.data[i + 2] = d; dd.data[i + 3] = 255;
  }
  g.putImageData(dd, 0, 0);
  const cw = o.w || cv.width, ch = o.h || cv.height, zz = o.z || 1;
  const o2 = document.createElement('canvas'); o2.width = cw * zz; o2.height = ch * zz;
  const g2 = o2.getContext('2d'); g2.imageSmoothingEnabled = false;
  g2.drawImage(cv, o.x || 0, o.y || 0, cw, ch, 0, 0, cw * zz, ch * zz);
  return o2.toDataURL('image/png');
}, {a: load(A), b: load(B), x: +(x || 0), y: +(y || 0), w: +(w || 0), h: +(h || 0),
    z: +(z || 1), gain: +(gain || 3)});
fs.writeFileSync(out, Buffer.from(data.split(',')[1], 'base64'));
await br.close();
console.log('wrote', out);
