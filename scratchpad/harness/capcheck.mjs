/* Does capping the cadence remove the beating? Same 2x load, cap off vs 60. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:800}, deviceScaleFactor:2})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.evaluate(()=>window.__lemWorld.engine.setResolutionMode('x2'));
await p.waitForTimeout(8000);
for (const cap of ['off','60']) {
  await p.evaluate(c=>window.__lemWorld.engine.setFrameCap(c), cap);
  await p.waitForTimeout(2500);
  const r = await p.evaluate(()=>new Promise(res=>{
    const f=[]; let last=performance.now(); const stop=last+8000;
    const tick=n=>{ f.push(n-last); last=n;
      if(n<stop) requestAnimationFrame(tick);
      else{ const drawn=f.filter(v=>v>2);   // rAF ticks we actually drew on
        const s=[...drawn].sort((a,b)=>a-b); const q=x=>+s[Math.min(s.length-1,Math.floor(s.length*x))].toFixed(1);
        const m=s.reduce((a,v)=>a+v,0)/s.length;
        const sd=Math.sqrt(s.reduce((a,v)=>a+(v-m)**2,0)/s.length);
        res({p50:q(.5),p95:q(.95),max:+s[s.length-1].toFixed(1),
             jitterCv:+(sd/m).toFixed(3), fps:Math.round(1000/m)});}};
    requestAnimationFrame(tick);
  }));
  console.log(`cap=${cap.padEnd(3)}`, JSON.stringify(r));
}
await b.close();
