import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], out = process.argv[3];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:800,height:600}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(800);
const data = await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  const src = v.atlas.image;
  const cv = document.createElement('canvas'); cv.width=1024; cv.height=1024;
  const g = cv.getContext('2d');
  // checkerboard so alpha is visible
  for (let y=0;y<1024;y+=32) for (let x=0;x<1024;x+=32){ g.fillStyle=((x/32+y/32)%2)?'#6688aa':'#8899bb'; g.fillRect(x,y,32,32); }
  g.drawImage(src,0,0,1024,1024);
  return cv.toDataURL('image/png');
});
fs.writeFileSync(out, Buffer.from(data.split(',')[1],'base64'));
await b.close();
console.log('ok');
