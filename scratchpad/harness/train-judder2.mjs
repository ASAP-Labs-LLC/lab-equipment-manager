/* Pair ds with the ENGINE'S OWN dt, not with a parallel rAF timestamp — a
 * separate loop can drift a frame and manufacture variance that isn't there.
 * Also count how often the world updates versus how often it presents. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:800}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(12000);
const r = await p.evaluate(()=>new Promise(res=>{
  const w=window.__lemWorld, T=w.subsystems.get('trains');
  const rows=[]; let updates=0;
  const orig = T.update.bind(T);
  T.update = (dt, t) => {
    updates++;
    const before = (T.consists||[]).filter(c=>c&&c.state==='out'&&c.v>1)[0];
    const s0 = before ? before.s : null, slot = before ? before.slot : null;
    const out = orig(dt, t);
    if (before) rows.push({dt, ds: before.s - s0, v: before.v, slot});
    return out;
  };
  let frames=0; const t0=performance.now();
  const tick=n=>{ frames++;
    if(n < t0+12000) requestAnimationFrame(tick);
    else {
      T.update = orig;
      const runs={};
      for(const r2 of rows){ if(r2.ds>0 && r2.ds<10 && r2.dt>0)
        (runs[r2.slot] ||= []).push(r2); }
      const best=Object.values(runs).sort((a,b)=>b.length-a.length)[0]||[];
      const stat=a=>{const s=[...a].sort((x,y)=>x-y);const q=f=>s[Math.min(s.length-1,Math.floor(s.length*f))];
        const m=s.reduce((x,y)=>x+y,0)/s.length;
        return {p50:+q(.5).toFixed(3),p95:+q(.95).toFixed(3),max:+s[s.length-1].toFixed(3),
                cv:+(Math.sqrt(s.reduce((a2,v)=>a2+(v-m)**2,0)/s.length)/m).toFixed(3)};};
      res({frames, updates, updatesPerFrame:+(updates/frames).toFixed(3),
           samples:best.length,
           engineDtMs: stat(best.map(x=>x.dt*1000)),
           stepM:      stat(best.map(x=>x.ds)),
           speed_ds_over_engineDt: stat(best.map(x=>x.ds/x.dt)),
           v_reported: stat(best.map(x=>x.v))});
    }};
  requestAnimationFrame(tick);
}));
console.log(JSON.stringify(r,null,1));
await b.close();
