import {chromium} from 'playwright';
const FLEET=[['multitek-ns'],['multitek-s'],['optimpp-1'],['optimpp-2'],['pac-flash-1'],['pac-flash-2'],['koehler-cp']];
const BAY=2.05; const pos=FLEET.map((_,i)=>[0,i*BAY]);
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal']});
const p=await b.newPage({viewport:{width:900,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail&cam=yard&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.evaluate(([f,pp])=>window.__lemWorld.setMachines(f.map(([uid],i)=>({machine_uid:uid,title:uid,status:'GREEN',pos:pp[i],sub_statuses:{},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,pos]);
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, rail=w.subsystems.get('rail'), g=w.ctx.ground;
  const out=[];
  for(const t of rail.tracks){
    const f=t.frames; if(!f) continue;
    let railWorst=0, groundWorst=0, prevG=null;
    for(let i=1;i<f.count;i++){
      railWorst=Math.max(railWorst, Math.abs(f.pos[i*3+1]-f.pos[(i-1)*3+1])/f.step);
    }
    for(let i=0;i<f.count;i++){
      const gg=g(f.pos[i*3],f.pos[i*3+2]);
      if(prevG!==null) groundWorst=Math.max(groundWorst, Math.abs(gg-prevG)/f.step);
      prevG=gg;
    }
    out.push({name:t.name,len:Math.round(t.length),rail:+railWorst.toFixed(3),ground:+groundWorst.toFixed(3)});
  }
  return out;
}),null,0));
await b.close();
