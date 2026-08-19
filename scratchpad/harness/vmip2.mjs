import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:800,height:600}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(1500);
const data = await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  const src = v.atlas.image;           // 2048 canvas
  // crown tile of oak variant 0 = species 3, tile index 3*6+2 = 20 -> col 2 row 3
  const T = 2048/6;
  const out = document.createElement('canvas'); out.width = 1400; out.height = 400;
  const g = out.getContext('2d');
  g.fillStyle='#5a7fa8'; g.fillRect(0,0,1400,400);
  // draw the same tile at successive downsamples then blown back up (a poor-man mip chain)
  const tiles = [[3,2],[3,3],[0,2],[2,3]];  // oak v0, oak v1, spruce v0, birch v1
  let x = 0;
  for (const [r,c] of tiles) {
    for (const lvl of [0,3,5]) {
      const s = Math.max(1, Math.round(T / Math.pow(2,lvl)));
      const tmp = document.createElement('canvas'); tmp.width=s; tmp.height=s;
      const tg = tmp.getContext('2d'); tg.imageSmoothingQuality='high';
      tg.drawImage(src, c*T, r*T, T, T, 0, 0, s, s);
      g.imageSmoothingEnabled = false;
      g.drawImage(tmp, 0,0,s,s, x, 20, 110, 110);
      // and the alpha-thresholded version at 0.5
      const id = tg.getImageData(0,0,s,s);
      for (let i=0;i<id.data.length;i+=4){ id.data[i+3] = id.data[i+3] >= 128 ? 255 : 0; }
      tg.putImageData(id,0,0);
      g.drawImage(tmp, 0,0,s,s, x, 150, 110, 110);
      x += 116;
    }
  }
  return out.toDataURL('image/png');
});
fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/MIP.png', Buffer.from(data.split(',')[1],'base64'));
await b.close(); console.log('ok');
