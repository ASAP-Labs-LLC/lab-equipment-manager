/* Ryan: "zoom in on the trains in chromium, they stutter down the railroad,
 * only on Ultra, Safari doesn't."
 *
 * So measure the MOTION, not the frame rate: sample every consist's arc length
 * each frame together with the frame's own dt, and look at ds/dt. If frames are
 * even but ds is not, the integration is at fault, not the renderer. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:800}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(12000);
const r = await p.evaluate(()=>new Promise(res=>{
  const T = window.__lemWorld.subsystems.get('trains');
  const rows=[]; let last=performance.now(); const stop=last+12000;
  const tick=n=>{
    const dt=n-last; last=n;
    const cs=(T&&T.consists?T.consists:[]).filter(c=>c&&c.state==='out'&&c.v>1);
    if(cs.length) rows.push({dt, s:cs[0].s, v:cs[0].v, slot:cs[0].slot});
    if(n<stop) requestAnimationFrame(tick);
    else {
      // ds per frame for one continuous run of the same slot
      const runs={};
      for(let i=1;i<rows.length;i++){
        if(rows[i].slot!==rows[i-1].slot) continue;
        const ds=rows[i].s-rows[i-1].s; if(ds<=0||ds>10) continue;
        (runs[rows[i].slot] ||= []).push({ds, dt:rows[i].dt, rate:ds/(rows[i].dt/1000)});
      }
      const best=Object.values(runs).sort((a,b)=>b.length-a.length)[0]||[];
      const stat=a=>{const s=[...a].sort((x,y)=>x-y);const q=f=>s[Math.min(s.length-1,Math.floor(s.length*f))];
        const m=s.reduce((x,y)=>x+y,0)/s.length;
        return {n:s.length,p50:+q(.5).toFixed(3),p95:+q(.95).toFixed(3),max:+s[s.length-1].toFixed(3),
                mean:+m.toFixed(3),cv:+(Math.sqrt(s.reduce((a2,v)=>a2+(v-m)**2,0)/s.length)/m).toFixed(3)};};
      res({samples:best.length,
           frameMs: stat(best.map(x=>x.dt)),
           stepM:   stat(best.map(x=>x.ds)),
           speedMs: stat(best.map(x=>x.rate))});
    }};
  requestAnimationFrame(tick);
}));
console.log(JSON.stringify(r,null,1));
await b.close();
