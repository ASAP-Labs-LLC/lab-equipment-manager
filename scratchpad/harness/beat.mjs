/* If this is vsync beating, frame times cluster at integer multiples of the
 * refresh interval rather than spreading smoothly. Histogram them. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:800}, deviceScaleFactor:2})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.evaluate(()=>window.__lemWorld.engine.setResolutionMode('x2'));
await p.waitForTimeout(9000);
console.log(await p.evaluate(()=>new Promise(res=>{
  const f=[]; let last=performance.now(); const stop=last+9000;
  const tick=n=>{ f.push(n-last); last=n;
    if(n<stop) requestAnimationFrame(tick);
    else{
      const bins={};
      for(const v of f){ const k=Math.round(v/8.333); bins[k]=(bins[k]||0)+1; }
      const total=f.length;
      const lines=Object.keys(bins).sort((a,b)=>a-b).map(k=>
        `  ${k} refresh interval${k==1?'':'s'} (~${(k*8.333).toFixed(1)}ms): ` +
        `${String(bins[k]).padStart(4)}  ${(100*bins[k]/total).toFixed(1).padStart(5)}%`);
      res('frame times bucketed by refresh interval (8.333ms):\n'+lines.join('\n'));
    }};
  requestAnimationFrame(tick);
})));
await b.close();
