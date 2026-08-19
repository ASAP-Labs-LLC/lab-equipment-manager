/* Same test at RETINA scale, which is what Ryan is actually running. At dsf=2
 * with resolution 'full' the backing store is 4x the pixels of my earlier runs. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
for (const [q,res] of [['ultra','full'],['ultra','auto'],['high','full']]) {
  const p=await (await b.newContext({viewport:{width:1280,height:800}, deviceScaleFactor:2})).newPage();
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=${q}`,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  await p.evaluate(m=>window.__lemWorld.engine.setResolutionMode(m), res);
  await p.waitForTimeout(11000);
  const r = await p.evaluate(()=>new Promise(resolve=>{
    const e=window.__lemWorld.engine;
    const f=[]; let last=performance.now(); const stop=last+11000;
    const tick=n=>{ f.push(+(n-last).toFixed(2)); last=n;
      if(n<stop) requestAnimationFrame(tick);
      else{ const s=[...f].sort((a,b)=>a-b); const q2=x=>s[Math.min(s.length-1,Math.floor(s.length*x))];
        const med=q2(.5);
        resolve({backing:[e.width,e.height], p50:med, p95:q2(.95), p99:q2(.99),
                 max:s[s.length-1],
                 over1_5x:f.filter(v=>v>med*1.5).length,
                 over2x:f.filter(v=>v>med*2).length, frames:f.length});}};
    requestAnimationFrame(tick);
  }));
  console.log(`${q}/${res}`.padEnd(12), JSON.stringify(r));
  await p.context().close();
}
await b.close();
