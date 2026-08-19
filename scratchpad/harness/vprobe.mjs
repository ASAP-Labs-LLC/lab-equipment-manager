import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
const r = await p.evaluate(()=>{
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  let vtri=0, vdraw=0;
  for (const m of v.meshes) { vdraw += m.count>0?1:0; vtri += (m.geometry.index?m.geometry.index.count/3:0)*m.count; }
  return {entries:v.trees.length, meshes:v.meshes.length, activeDraws:vdraw,
          vegTris:Math.round(vtri), buildMs:Math.round(v._buildMs),
          near:v.trees.reduce((a,e)=>a+e.near.count,0),
          far:v.trees.reduce((a,e)=>a+e.far.count,0),
          placed:v.trees.reduce((a,e)=>a+e.list.length,0),
          grass:v.grass.count, scene:w.stats()};
});
console.log(JSON.stringify(r));
await b.close();
