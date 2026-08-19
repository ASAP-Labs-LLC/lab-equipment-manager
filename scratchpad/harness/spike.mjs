/* Which frames are slow at ultra, and is it periodic? A spike every N frames is
 * a scheduled task (cascade refit, probe update, LOD sweep); random spikes are
 * GC or driver. Compare ultra against high to confirm it is tier-dependent. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
for (const q of ['ultra','high']) {
  const p=await (await b.newContext({viewport:{width:1280,height:800}})).newPage();
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=${q}`,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  await p.waitForTimeout(11000);
  const r = await p.evaluate(()=>new Promise(res=>{
    const f=[]; let last=performance.now(); const stop=last+12000;
    const tick=n=>{ f.push(+(n-last).toFixed(2)); last=n;
      if(n<stop) requestAnimationFrame(tick);
      else{
        const s=[...f].sort((a,b)=>a-b); const q2=x=>s[Math.min(s.length-1,Math.floor(s.length*x))];
        const med=q2(.5); const spikes=[];
        f.forEach((v,i)=>{ if(v>med*1.8) spikes.push(i); });
        const gaps=[]; for(let i=1;i<spikes.length;i++) gaps.push(spikes[i]-spikes[i-1]);
        gaps.sort((a,b)=>a-b);
        res({frames:f.length, p50:med, p95:q2(.95), p99:q2(.99), max:s[s.length-1],
             spikeCount:spikes.length,
             spikePctOfFrames:+(100*spikes.length/f.length).toFixed(1),
             gapMedian: gaps.length? gaps[gaps.length>>1] : null,
             worstFive: s.slice(-5)});
      }};
    requestAnimationFrame(tick);
  }));
  console.log(q.padEnd(6), JSON.stringify(r));
  await p.context().close();
}
await b.close();
