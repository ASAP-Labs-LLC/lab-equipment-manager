import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation'), c=w.camera;
  const out=[];
  for (const e of v.trees) for (let i=0;i<e.list.length;i++){
    if (e.rank[i] > 1) continue;
    const d=Math.hypot(e.xs[i]-c.position.x, e.zs[i]-c.position.z);
    if (d<40) out.push({sp:e.spec.id, d:+d.toFixed(1), x:+e.xs[i].toFixed(1), z:+e.zs[i].toFixed(1),
                        open:+v._openness(e.xs[i],e.zs[i]).toFixed(2)});
  }
  out.sort((a,b)=>a.d-b.d);
  let cl=0; for (const cc of v.clutter) for(let i=0;i<cc.count;i++){
    if (Math.hypot(cc.xs[i]-c.position.x, cc.zs[i]-c.position.z)<8) cl++; }
  return {cam:[+c.position.x.toFixed(1), +c.position.y.toFixed(1), +c.position.z.toFixed(1)],
          corridors: v.corridors.length, clearings: v.clearings.length,
          nearClutter: cl, trees: out.slice(0,8)};
})));
await b.close();
