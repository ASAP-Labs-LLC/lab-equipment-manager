/* laps.mjs — how long is a working, on every layout?
 * The soak gives a layout ~58s before it judges throughput, so the outbound
 * leg (dock → rack) is the number that decides whether the railway reads as
 * running or as dead. */
import {chromium} from 'playwright';
const FLEET=['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const BAY=2.05;
function layouts(n){const out=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
  const all=[out]; let seed=12345; const rnd=()=>(seed=(seed*1103515245+12345)&0x7fffffff)/0x7fffffff;
  for(let L=1;L<n;L++){const kind=L%4,pos=[];
    for(let i=0;i<FLEET.length;i++){
      if(kind===0)pos.push([Math.round(rnd()*8)*BAY,Math.round(rnd()*8)*BAY]);
      else if(kind===1)pos.push([i*BAY,0]); else if(kind===2)pos.push([0,i*BAY]);
      else pos.push([Math.round(rnd()*14)*BAY,Math.round(rnd()*14)*BAY]);}
    if(kind===3)pos[1]=pos[0].slice(); all.push(pos);} return all;}
const ACCEL=2.2,BRAKE=2.8;
const runTime=(L,vmax)=>{ const va=Math.min(vmax,Math.sqrt(2*L/(1/ACCEL+1/BRAKE)));
  const da=va*va/(2*ACCEL)+va*va/(2*BRAKE); return va/ACCEL+va/BRAKE+Math.max(0,L-da)/va; };
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal']});
const p=await b.newPage({viewport:{width:900,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail,trains&cam=yard&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
for(const [L,pos] of layouts(10).entries()){
  await p.evaluate(([f,pp])=>window.__lemWorld.setMachines(f.map((uid,i)=>({machine_uid:uid,title:uid,status:'GREEN',pos:pp[i],
    sub_statuses:{},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,pos]);
  await p.waitForTimeout(1800);
  const r=await p.evaluate(()=>{
    const T=window.__lemWorld.subsystems.get('trains');
    const out=[];
    for(const c of T.consists){ if(c.shunt||!c.uid||!c.cyc) continue;
      out.push({lap:Math.round(c.L), out:Math.round(c.terminal-c.s)}); }
    return out;
  });
  const outs=r.map(x=>x.out);
  console.log(`L${L} lap ${Math.min(...r.map(x=>x.lap))}..${Math.max(...r.map(x=>x.lap))}m  ` +
    `outbound ${Math.min(...outs)}..${Math.max(...outs)}m  ` +
    `= ${runTime(Math.min(...outs),34).toFixed(0)}..${runTime(Math.max(...outs),34).toFixed(0)}s`);
}
await b.close();
