import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:900,height:900}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(800);
const d = await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  const src = v.atlas.image;   // 2048 canvas
  const out = document.createElement('canvas'); out.width=1200; out.height=420;
  const og = out.getContext('2d');
  for(let y=0;y<420;y+=20) for(let x=0;x<1200;x+=20){ og.fillStyle=((x/20+y/20)%2)?'#7799bb':'#99aacc'; og.fillRect(x,y,20,20); }
  // spruce crown tile index 2 (row0,col2) and pine crown 8 (row1,col2)
  const TP = 2048/6;
  const tiles = [2, 4, 8, 10, 14, 20];
  let ox = 0;
  for (const ti of tiles) {
    const c = ti % 6, r = (ti/6)|0;
    for (const mip of [2, 4, 5]) {
      const s = Math.max(2, Math.round(TP / (1<<mip)));
      const tmp = document.createElement('canvas'); tmp.width=s; tmp.height=s;
      const tg = tmp.getContext('2d');
      tg.imageSmoothingQuality='high';
      tg.drawImage(src, c*TP, r*TP, TP, TP, 0, 0, s, s);
      // threshold at 0.5 like alphaTest does
      const im = tg.getImageData(0,0,s,s);
      for(let i=0;i<im.data.length;i+=4) im.data[i+3] = im.data[i+3] >= 128 ? 255 : 0;
      tg.putImageData(im,0,0);
      og.imageSmoothingEnabled = false;
      og.drawImage(tmp, ox, mip===2?0:(mip===4?140:280), 130, 130);
    }
    ox += 200;
  }
  return out.toDataURL('image/png');
});
fs.writeFileSync(process.argv[3], Buffer.from(d.split(',')[1],'base64'));
await b.close(); console.log('ok');
