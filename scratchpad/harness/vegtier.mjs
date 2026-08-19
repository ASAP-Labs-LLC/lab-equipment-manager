import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs=[]; p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{if(m.type()==='error')errs.push(m.text());});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(1500);
for (const t of [0,2,3,4]) {
  const r = await p.evaluate((t)=>{ const w=window.__lemWorld; w.engine.setTier(t,{force:true}); return null; }, t);
  await p.waitForTimeout(900);
  console.log(JSON.stringify(await p.evaluate(()=>{
    const w=window.__lemWorld, v=w.subsystems.get('vegetation');
    let tri=0,d=0; for(const m of v.meshes){ if(m.count>0){d++; tri+=(m.geometry.index.count/3)*m.count;} }
    return {tier:w.engine.tier.name, vegDraws:d, vegTris:Math.round(tri),
            near:v.trees.reduce((a,e)=>a+e.near.count,0), far:v.trees.reduce((a,e)=>a+e.far.count,0),
            trunk:v.trees.reduce((a,e)=>a+(e.trunk?e.trunk.count:0),0), grass:v.grass.count,
            scene:w.stats()};
  })));
}
console.log('errors', JSON.stringify(errs));
await b.close();
