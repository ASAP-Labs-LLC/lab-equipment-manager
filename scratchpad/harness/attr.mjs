import {chromium} from 'playwright';
const FLEET=[['multitek-ns','Multitek NS','GREEN'],['multitek-s','Multitek S','YELLOW'],['optimpp-1','OptiMPP 1','GREEN'],['optimpp-2','OptiMPP 2','RED'],['pac-flash-1','PAC Flash 1','SERVICE'],['pac-flash-2','PAC Flash 2','DEAD-LINE'],['koehler-cp','Koehler CP','UNKNOWN']];
function layouts(n){const BAY=2.05;const out=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];const all=[out];let seed=12345;const rnd=()=>(seed=(seed*1103515245+12345)&0x7fffffff)/0x7fffffff;
for(let L=1;L<n;L++){const kind=L%4;const pos=[];for(let i=0;i<FLEET.length;i++){if(kind===0)pos.push([Math.round(rnd()*8)*BAY,Math.round(rnd()*8)*BAY]);else if(kind===1)pos.push([i*BAY,0]);else if(kind===2)pos.push([0,i*BAY]);else pos.push([Math.round(rnd()*14)*BAY,Math.round(rnd()*14)*BAY]);}if(kind===3)pos[1]=pos[0].slice();all.push(pos);}return all;}
const b=await chromium.launch({args:['--use-angle=metal']});const p=await b.newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
const L=layouts(8);
await p.evaluate(([f,pos])=>window.__lemWorld.setMachines(f.map(([uid,title,status],i)=>({machine_uid:uid,title,status,pos:pos[i],reason:'x',sub_statuses:{qc:status,pm:'GREEN',calibration:'GREEN'},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,L[7]]);
await p.waitForTimeout(1500);
console.log(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  const rows=[];
  for(let x=-140;x<=20;x+=10){
    const z=449;
    const c=t.core, fx=(x-c.x0)/c.step, fz=(z-c.z0)/c.step;
    const gi=Math.round(fx), gj=Math.round(fz);
    rows.push({x, h:+t.heightAt(x,z).toFixed(1), grid:+c.h[gj*c.V+gi].toFixed(1),
               gbase:+c.base[gj*c.V+gi].toFixed(1), gfoot:+c.dFoot[gj*c.V+gi].toFixed(1),
               graded:+t._gradedHeight(x,z).toFixed(1),
               base:+t._baseHeight(x,z).toFixed(1),
               D:+t._designAt(x,z).toFixed(1), f:+t._distances(x,z,null).toFixed(1)});
  }
  return {cx:t.cx,cz:t.cz,core:t.core.size,step:+t.core.step.toFixed(2),rows};
}));
await b.close();
