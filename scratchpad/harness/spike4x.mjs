/* Ryan: "increase the resolution scale to 4x or something to be sure, this PC
 * is powerful. The computers running this are not." Load this machine past what
 * a bench PC would ever do and see whether the pacing fault appears. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
for (const res of ['full','x2','x3','x4']) {
  const p=await (await b.newContext({viewport:{width:1280,height:800}, deviceScaleFactor:2})).newPage();
  await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
  await p.evaluate(m=>window.__lemWorld.engine.setResolutionMode(m), res);
  await p.waitForTimeout(9000);
  const r = await p.evaluate(()=>new Promise(resolve=>{
    const e=window.__lemWorld.engine;
    const f=[]; let last=performance.now(); const stop=last+9000;
    const tick=n=>{ f.push(n-last); last=n;
      if(n<stop) requestAnimationFrame(tick);
      else{ const s=[...f].sort((a,b)=>a-b); const q=x=>+s[Math.min(s.length-1,Math.floor(s.length*x))].toFixed(1);
        const med=q(.5);
        resolve({backing:`${e.width}x${e.height}`, mpx:+((e.width*e.height)/1e6).toFixed(1),
                 p50:med, p95:q(.95), p99:q(.99), max:+s[s.length-1].toFixed(1),
                 over1_5x:f.filter(v=>v>med*1.5).length, frames:f.length});}};
    requestAnimationFrame(tick);
  }));
  console.log(res.padEnd(5), JSON.stringify(r));
  await p.context().close();
}
await b.close();
