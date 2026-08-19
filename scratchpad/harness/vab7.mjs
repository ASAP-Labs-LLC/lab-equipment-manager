import {chromium} from 'playwright';
import fs from 'fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(11000);
const cases = JSON.parse(process.argv[3]);
for (const [name, pa] of Object.entries(cases)) {
  await p.evaluate(pa=>{
    const v = window.__lemWorld.subsystems.get('vegetation');
    v._frozen = true;
    if (pa.__season !== undefined) { v.shared.uVegSeason.value = pa.__season; v._lockSeason = pa.__season; }
    if (pa.__hideFar) for (const e of v.trees) e.far.visible = false;
    if (pa.__hideNear) for (const e of v.trees) { e.near.visible = false; if(e.trunk) e.trunk.visible=false; }
    if (pa.__show) for (const e of v.trees) { e.far.visible = true; e.near.visible = true; if(e.trunk) e.trunk.visible=true; }
    v.materials.forEach(m => { const u = m.userData.lem; if(!u) return;
      for (const k of Object.keys(pa)) { if(k.startsWith('__')||!u[k]) continue;
        const val = pa[k];
        if (Array.isArray(val)) u[k].value.fromArray(val); else u[k].value = val; } });
  }, pa);
  // keep season pinned across update()
  await p.evaluate(()=>{ const v=window.__lemWorld.subsystems.get('vegetation');
    if (v._lockSeason !== undefined && !v._patched) { v._patched=true;
      const up=v.update.bind(v); v.update=(dt,t)=>{up(dt,t); v.shared.uVegSeason.value=v._lockSeason;}; } });
  await p.waitForTimeout(2500);
  fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/AB7-'+name+'.png', await p.screenshot());
}
await b.close();
