import {chromium} from 'playwright';
const FLEET=['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const BAY=2.05;
function layouts(n){const out=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
  const all=[out]; let seed=12345; const rnd=()=>(seed=(seed*1103515245+12345)&0x7fffffff)/0x7fffffff;
  for(let L=1;L<n;L++){const kind=L%4,pos=[];
    for(let i=0;i<FLEET.length;i++){ if(kind===0)pos.push([Math.round(rnd()*8)*BAY,Math.round(rnd()*8)*BAY]);
      else if(kind===1)pos.push([i*BAY,0]); else if(kind===2)pos.push([0,i*BAY]);
      else pos.push([Math.round(rnd()*14)*BAY,Math.round(rnd()*14)*BAY]);}
    if(kind===3)pos[1]=pos[0].slice(); all.push(pos);} return all;}
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage({viewport:{width:1920,height:1080}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=16&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
for(const L of [0,2,3,7]){
  await p.evaluate(([f,pp])=>window.__lemWorld.setMachines(f.map((uid,i)=>({machine_uid:uid,title:uid,status:'GREEN',pos:pp[i],
    sub_statuses:{},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,layouts(10)[L]]);
  await p.waitForTimeout(4000);
  const s=await p.evaluate(()=>{const w=window.__lemWorld,r=w.subsystems.get('rail');
    let laid=0; for(const t of r.tracks) if(t.frames) laid+=Math.min(t.renderTo,t.length)-(t.renderFrom||0);
    return {...w.stats(), km:+(laid/1000).toFixed(2), tier:w.ctx.quality?.name};});
  console.log('L'+L, JSON.stringify(s));
}
await b.close();
