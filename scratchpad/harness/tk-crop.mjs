/* tk-crop.mjs — crop and upscale a region of a PNG so a 15-pixel tank can be
 * looked at. node tk-crop.mjs in.png out.png x y w h [zoom]  */
import {chromium} from 'playwright';
import fs from 'fs';
const [inp, outp, x, y, w, h, z] = process.argv.slice(2);
const b = await chromium.launch({headless: true, channel: 'chromium'});
const p = await b.newPage();
const src = 'data:image/png;base64,' + fs.readFileSync(inp).toString('base64');
const data = await p.evaluate(async ({src, x, y, w, h, z}) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas');
  cv.width = w * z; cv.height = h * z;
  const g = cv.getContext('2d');
  g.imageSmoothingEnabled = false;
  g.drawImage(im, x, y, w, h, 0, 0, w * z, h * z);
  return cv.toDataURL('image/png');
}, {src, x: +x, y: +y, w: +w, h: +h, z: +(z || 4)});
fs.writeFileSync(outp, Buffer.from(data.split(',')[1], 'base64'));
await b.close();
console.log('wrote', outp);
