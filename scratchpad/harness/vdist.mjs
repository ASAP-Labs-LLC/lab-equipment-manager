import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(9000);
console.log(process.argv[3], JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation'), cam=w.camera;
  const ds=[];
  for (const e of v.trees) {
    const m=e.far.instanceMatrix.array;
    for (let i=0;i<e.far.count;i++) ds.push(Math.hypot(m[i*16+12]-cam.position.x, m[i*16+14]-cam.position.z));
  }
  ds.sort((a,b)=>a-b);
  const q=f=>Math.round(ds[Math.floor(ds.length*f)]||0);
  return {n:ds.length, p10:q(.1), p25:q(.25), p50:q(.5), p75:q(.75), p90:q(.9), max:Math.round(ds[ds.length-1]||0)};
})));
await b.close();
