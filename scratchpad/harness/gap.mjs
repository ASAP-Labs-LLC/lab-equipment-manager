/* gap.mjs — when two workings come within the soak's fouling distance, what
 * are they actually doing? The soak reports the distance and nothing else, and
 * "5.0 → 3.9m and stopped" is a queue closing up, not a train driving through
 * another one — but which of those it is decides the fix. */
import {chromium} from 'playwright';
const FLEET=['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const POS=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.evaluate(([f,pos])=>window.__lemWorld.setMachines(f.map((uid,i)=>({machine_uid:uid,title:uid,status:'GREEN',pos:pos[i],
  sub_statuses:{},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,POS]);
await p.waitForTimeout(2500);
await p.evaluate(()=>{
  const T=window.__lemWorld.subsystems.get('trains');
  window.__g={worst:1e9, rec:null, lens:{}, docks:{}};
  /* Resolve a point the way soak.mjs does: the sampled route a consist carries
   * has getPointAt + len, NOT pointAtDistance. Guessing at one property name is
   * how this probe reported a perfect railway the first time it ran. */
  const at=(c,s)=>{ const r=c.route; if(!r) return null;
    if(typeof r.pointAtDistance==='function') return r.pointAtDistance(s);
    const len=r.totalLength||r.len||(r.getLength?r.getLength():0);
    if(typeof r.getPointAt==='function'&&len) return r.getPointAt(Math.min(1,Math.max(0,s/len)));
    return null; };
  const body=c=>[0,0.5,1].map(f=>at(c,c.s-c.length*f)).filter(Boolean);
  const tick=()=>{
    const live=T.consists.filter(c=>c&&c.group&&c.group.visible&&c.route&&!c.shunt);
    for(const c of live) window.__g.lens[c.slot]=+c.length.toFixed(1);
    for(let i=0;i<live.length;i++)for(let j=i+1;j<live.length;j++){
      const A=body(live[i]),B=body(live[j]);
      let d=1e9; for(const a of A)for(const bb of B) d=Math.min(d,Math.hypot(a.x-bb.x,a.y-bb.y,a.z-bb.z));
      if(d<window.__g.worst){ window.__g.worst=d;
        window.__g.rec=[live[i],live[j]].map(c=>({slot:c.slot,st:c.state,s:+c.s.toFixed(1),
          len:+c.length.toFixed(1),line:c.line,L:+c.L.toFixed(0),wait:!!c.waiting,
          holds:[...(c.holds||[])].sort()}));
        window.__g.docks[live[i].line]=(live[i].docks||[]).map(x=>+x.s.toFixed(1));
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
for(let i=0;i<200;i++){ await p.evaluate(u=>window.__lemWorld.parse(u,'L-G'),FLEET[i%7]); await p.waitForTimeout(300); }
await p.waitForTimeout(30000);
console.log(JSON.stringify(await p.evaluate(()=>window.__g),null,1));
await b.close();
