import {chromium} from 'playwright';
const FLEET=[['multitek-ns','M NS','GREEN'],['multitek-s','M S','YELLOW'],['optimpp-1','O1','GREEN'],['optimpp-2','O2','RED'],['pac-flash-1','P1','SERVICE'],['pac-flash-2','P2','DEAD-LINE'],['koehler-cp','K','UNKNOWN']];
const b=await chromium.launch({args:['--use-angle=metal']});const p=await b.newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&time=16&hud=0',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
for (const [name,spread] of [['compact',2.05],['wide',14*2.05]]) {
  console.log(name, await p.evaluate(([f,sp])=>{
    const w=window.__lemWorld,t=w.subsystems.get('terrain');
    const pos=f.map((_,i)=>[ (i%3)*sp, Math.floor(i/3)*sp ]);
    w.setMachines(f.map(([uid,title,status],i)=>({machine_uid:uid,title,status,pos:pos[i],reason:'t',sub_statuses:{qc:status,pm:'GREEN',calibration:'GREEN'},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]})));
    const t0=performance.now();
    t._teardownMeshes(); t._rebuild(w.plan);
    return {ms:Math.round(performance.now()-t0), core:Math.round(t.core.size), seg:t.core.N};
  },[FLEET,spread]));
}
await b.close();
