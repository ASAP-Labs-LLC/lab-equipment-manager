/* Frame PACING, not throughput. A steady 60fps with occasional 40ms frames
 * reads as stutter; a mean framerate hides it entirely. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1440,height:900}})).newPage();
await p.goto('http://127.0.0.1:5612/floor',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>!!window.__lemWorld,null,{timeout:45000});
await p.waitForTimeout(16000);
const r = await p.evaluate(()=>new Promise(res=>{
  const f=[]; let last=performance.now(); const stop=last+12000;
  const tick=n=>{f.push(n-last); last=n;
    if(n<stop) requestAnimationFrame(tick);
    else{ const s=[...f].sort((a,b)=>a-b), n2=s.length;
      const q=x=>s[Math.min(n2-1,Math.floor(n2*x))];
      // a "long frame" is one that missed its vsync slot by 50%
      const budget=1000/60;
      res({frames:n2, p50:+q(.5).toFixed(2), p90:+q(.9).toFixed(2),
           p99:+q(.99).toFixed(2), max:+s[n2-1].toFixed(2),
           over1_5x:s.filter(v=>v>budget*1.5).length,
           over3x:s.filter(v=>v>budget*3).length,
           shadowUpdatesPerSec: window.__shadowTicks|0});}};
  requestAnimationFrame(tick);
}));
console.log(JSON.stringify(r,null,1));
await b.close();
