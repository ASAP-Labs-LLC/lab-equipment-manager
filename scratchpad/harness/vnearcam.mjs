import {chromium} from 'playwright';
const cam = process.argv[2] || 'street';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}&time=16&hud=0&quality=ultra`,{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(5000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const W=window.__lemWorld, v=W.subsystems.get('vegetation'), c=W.camera;
  const cx=c.position.x, cz=c.position.z;
  const near=[];
  for(const e of v.trees||[]) for(let i=0;i<e.list.length;i++){
    const d=Math.hypot(e.xs[i]-cx, e.zs[i]-cz);
    if(d<60) near.push({sp:e.spec.id, d:+d.toFixed(1), x:+e.xs[i].toFixed(0), z:+e.zs[i].toFixed(0),
                        open:+v._openness(e.xs[i],e.zs[i]).toFixed(2),
                        rail:+(v._railDist(e.xs[i],e.zs[i],80)).toFixed(1)});
  }
  near.sort((a,z)=>a.d-z.d);
  const st=(v.plan?.stations||[]).map(s=>({t:s.title,d:+Math.hypot(s.x-cx,s.z-cz).toFixed(0)})).sort((a,z)=>a.d-z.d);
  return {cam:{x:+cx.toFixed(1),y:+c.position.y.toFixed(1),z:+cz.toFixed(1)},
          nearestStations:st.slice(0,3), clearings:v.clearings.map(q=>({r0:+q.r0.toFixed(0),r1:+q.r1.toFixed(0),d:+Math.hypot(q.x-cx,q.z-cz).toFixed(0)})).sort((a,z)=>a.d-z.d).slice(0,4),
          blockers:v.blockers.map(q=>({r:+q.r.toFixed(0),d:+Math.hypot(q.x-cx,q.z-cz).toFixed(0)})).sort((a,z)=>a.d-z.d).slice(0,3),
          within60:near.length, closest:near.slice(0,12)};
}),null,1));
await b.close();
