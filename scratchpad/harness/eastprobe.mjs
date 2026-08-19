/* eastprobe.mjs — where should the return alignment stand?
 *
 * rail.js has always put its trunk WEST because of a comment saying the valley
 * falls away east. A one-way circuit needs a return alignment on the other side
 * of the benches, so the claim is measured rather than inherited — and the
 * chooser below is the one rail.js uses: score a handful of candidate corridors
 * by the relief along them and take the quietest. */
import {chromium} from 'playwright';
const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const BAY = 2.05;
function layouts(n){
  const out=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
  const all=[out]; let seed=12345;
  const rnd=()=> (seed=(seed*1103515245+12345)&0x7fffffff)/0x7fffffff;
  for(let L=1;L<n;L++){
    const kind=L%4; const pos=[];
    for(let i=0;i<FLEET.length;i++){
      if(kind===0) pos.push([Math.round(rnd()*8)*BAY, Math.round(rnd()*8)*BAY]);
      else if(kind===1) pos.push([i*BAY,0]);
      else if(kind===2) pos.push([0,i*BAY]);
      else pos.push([Math.round(rnd()*14)*BAY, Math.round(rnd()*14)*BAY]);
    }
    if(kind===3) pos[1]=pos[0].slice();
    all.push(pos);
  }
  return all;
}
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal']});
const p = await b.newPage({viewport:{width:900,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=yard&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
const LS = layouts(10);
for (let L=0;L<LS.length;L++){
  await p.evaluate(([f,pp])=>window.__lemWorld.setMachines(f.map((uid,i)=>({
    machine_uid:uid,title:uid,status:'GREEN',pos:pp[i],sub_statuses:{},
    module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,LS[L]]);
  await p.waitForTimeout(1400);
  const r = await p.evaluate(()=>{
    const w=window.__lemWorld, g=w.ctx.ground, plan=w.plan;
    const xs=plan.stations.map(s=>s.x), zs=plan.stations.map(s=>s.z);
    const minX=Math.min(...xs), maxX=Math.max(...xs), maxZ=Math.max(...zs);
    const hub=plan.hub, ZY=hub.z+26;
    const southEnd=maxZ-34.4+150;
    const ref=g(hub.x, hub.z);
    const score = x => {
      let worst=0, prev=null, sum=0, n=0, lo=1e9;
      for(let z=ZY; z<=southEnd; z+=8){
        const h=g(x,z); lo=Math.min(lo,h);
        if(prev!==null){ const d=Math.abs(h-prev)/8; worst=Math.max(worst,d); sum+=d*d; n++; }
        prev=h;
      }
      const rms=Math.sqrt(sum/Math.max(1,n));
      return {x:Math.round(x), worst:+worst.toFixed(3), rms:+rms.toFixed(3),
              below:+(ref-lo).toFixed(1),
              score:+(worst + rms*1.2 + (x-(maxX+150))*0.0016).toFixed(3)};
    };
    const cands=[];
    for(let k=0;k<6;k++) cands.push(score(maxX + 150 + k*28));
    cands.sort((a,b)=>a.score-b.score);
    const west=score(Math.min(minX-270, hub.x-290));
    return {minX:Math.round(minX), maxX:Math.round(maxX), best:cands[0],
            worstCand:cands[cands.length-1], west};
  });
  console.log('L'+L, JSON.stringify(r));
}
await b.close();
