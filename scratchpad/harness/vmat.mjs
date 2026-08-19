import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
for (const t of [1000, 3000, 6000]) {
  await p.waitForTimeout(t === 1000 ? 1000 : 2000);
  const r = await p.evaluate(()=>{
    const w = window.__lemWorld, v = w.subsystems.get('vegetation'), gi = w.subsystems.get('gi');
    const bad = [];
    for (const e of v.trees) {
      if (e.far.material !== v.matFar) bad.push(['far', e.spec.id, e.far.material?.type, e.far.material?.name]);
      if (e.near.material !== v.matNear) bad.push(['near', e.spec.id, e.near.material?.type]);
      if (e.trunk && e.trunk.material !== v.matBark) bad.push(['trunk', e.spec.id, e.trunk.material?.type]);
    }
    return {bad: bad.slice(0, 8), nbad: bad.length,
            farCasters: gi?._farCasters?.length,
            farDepth: gi?._farDepthMat?.type};
  });
  console.log(JSON.stringify(r));
}
await b.close();
