import {chromium} from 'playwright';
import fs from 'fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(11000);
// pin the weather so season/wet stop drifting between cases
await p.evaluate(()=>{ const w=window.__lemWorld; w._pin=()=>{}; 
  const v=w.subsystems.get('vegetation'); const up=v.update.bind(v);
  v.update=(dt,t)=>{ up(dt,t); if(v._sea!==undefined) v.shared.uVegSeason.value=v._sea; };
});
const cases = JSON.parse(process.argv[3]);
for (const [name, pa] of Object.entries(cases)) {
  const info = await p.evaluate(pa=>{
    const v = window.__lemWorld.subsystems.get('vegetation');
    v._sea = pa.__season;
    for (const e of v.trees) { e.far.visible = !pa.__hideFar; e.near.visible = !pa.__hideNear;
      if (e.trunk) e.trunk.visible = !pa.__hideNear; }
    return {season: v.shared.uVegSeason.value, temp: window.__lemWorld.weather.temperature};
  }, pa);
  await p.waitForTimeout(2200);
  fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/AB8-'+name+'.png', await p.screenshot());
  console.log(name, JSON.stringify(info));
}
await b.close();
