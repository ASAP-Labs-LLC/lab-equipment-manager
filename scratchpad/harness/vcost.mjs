import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(4000);
const f = async (tier) => p.evaluate((tier)=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation');
  if (tier!==null) v.onQuality({trees: tier});
  let draws=0, tris=0, cast=0;
  for (const m of v.meshes) {
    if (!m.count) continue;
    draws++; const t=(m.geometry.index?m.geometry.index.count/3:0)*m.count; tris+=t;
    if (m.castShadow) { cast++; }
  }
  return {tier, draws, tris:Math.round(tris), shadowDraws:cast,
          near:v.trees.reduce((a,e)=>a+e.near.count,0),
          far:v.trees.reduce((a,e)=>a+e.far.count,0),
          clutter:v.clutter.reduce((a,c)=>a+c.mesh.count,0),
          grass:v.grass.count, placed:v.trees.reduce((a,e)=>a+e.list.length,0),
          buildMs:Math.round(v._buildMs), scene:w.stats?.()};
}, tier);
console.log(JSON.stringify(await f(null)));
console.log(JSON.stringify(await f(0.4)));
console.log(JSON.stringify(await f(1)));
await b.close();
