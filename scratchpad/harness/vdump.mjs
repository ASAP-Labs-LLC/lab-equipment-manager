import {chromium} from 'playwright';
import fs from 'node:fs';
let URL = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(6000);
const r = await p.evaluate(()=>{
  const v=window.__lemWorld.subsystems.get('vegetation');
  const out={};
  for (const [name,tex] of [['canopy',v.canopy],['atlas',v.atlas]]) {
    const cv=tex?.image; if(!cv||!cv.getContext) continue;
    out[name]=cv.toDataURL('image/png');
    // per-tile alpha coverage
    const g=cv.getContext('2d',{willReadFrequently:true});
    const d=g.getImageData(0,0,cv.width,cv.height).data;
    const cols = name==='canopy'?2:6, rows = name==='canopy'?4:6;
    const tw=cv.width/cols|0, th=cv.height/rows|0;
    const cover=[];
    for(let r0=0;r0<rows;r0++)for(let c0=0;c0<cols;c0++){
      let n=0,hi=0;
      for(let y=r0*th;y<(r0+1)*th;y++)for(let x=c0*tw;x<(c0+1)*tw;x++){
        n++; if(d[(y*cv.width+x)*4+3]>127)hi++; }
      cover.push(+(hi/n).toFixed(2));
    }
    out[name+'_cover']=cover;
  }
  return out;
});
for (const k of ['canopy','atlas']) if (r[k])
  fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/PAGE-'+k+'.png',
                   Buffer.from(r[k].split(',')[1],'base64'));
console.log('canopy per-tile cover', JSON.stringify(r.canopy_cover));
console.log('atlas  per-tile cover', JSON.stringify(r.atlas_cover));
await b.close();
