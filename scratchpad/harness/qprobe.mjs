import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2000);
const out=[];
for (const t of [1, 0.55, 0.25]) {
  const r = await p.evaluate(tr=>{
    const v = window.__lemWorld.subsystems.get('vegetation');
    v.onQuality({trees: tr});
    let tri=0, dr=0;
    for (const m of v.meshes) { if (m.count>0) dr++; tri += (m.geometry.index?m.geometry.index.count/3:0)*m.count; }
    return {trees:tr, draws:dr, tris:Math.round(tri),
            near:v.trees.reduce((a,e)=>a+e.near.count,0),
            far:v.trees.reduce((a,e)=>a+e.far.count,0), grass:v.grass.count};
  }, t);
  out.push(r);
}
console.log(JSON.stringify(out));
await b.close();
