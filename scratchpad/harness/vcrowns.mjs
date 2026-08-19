/* vcrowns.mjs — all fifteen crown tiles side by side over sky, at a chosen
 * downsample, so the far LOD's silhouette can be judged as the camera sees it. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], out = process.argv[3];
const px = Number(process.argv[4] ?? 0);   // 0 = full tile, else downsample to px
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:800,height:600}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(600);
const data = await p.evaluate(({px}) => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const src = v.atlas.image;
  const GRID = 6, T = src.width / GRID;
  const CELL = 300, W = CELL*5, H = CELL*3;
  const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
  const g = cv.getContext('2d');
  g.fillStyle = '#b9cbd9'; g.fillRect(0,0,W,H);
  for (let si = 0; si < 5; si++) for (let vi = 0; vi < 3; vi++) {
    const tile = si*GRID + 2 + vi;
    const c = tile % GRID, r = (tile / GRID) | 0;
    let img = src, sx = c*T, sy = r*T, sw = T, sh = T;
    if (px) {
      const nc = document.createElement('canvas'); nc.width = px; nc.height = px;
      const ng = nc.getContext('2d'); ng.imageSmoothingEnabled = true;
      ng.drawImage(src, c*T, r*T, T, T, 0, 0, px, px);
      img = nc; sx = 0; sy = 0; sw = px; sh = px;
    }
    g.imageSmoothingEnabled = !px;
    g.drawImage(img, sx, sy, sw, sh, si*CELL, vi*CELL, CELL, CELL);
  }
  return cv.toDataURL('image/png');
}, {px});
fs.writeFileSync(out, Buffer.from(data.split(',')[1],'base64'));
await b.close();
console.log('ok');
