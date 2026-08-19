import {chromium} from 'playwright';
import fs from 'fs';
const url = process.argv[2];
const outdir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs=[]; p.on('console', m=>{ if(m.type()==='error') errs.push(m.text()); });
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(11000);
await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  window.__base = v.materials.map(m => {
    const u = m.userData.lem||{}; const o={};
    for (const k of Object.keys(u)) o[k] = u[k].value?.toArray ? u[k].value.toArray() : u[k].value;
    return o;
  });
});
const cases = JSON.parse(process.argv[3]);
for (const [name, patch] of Object.entries(cases)) {
  await p.evaluate(pa=>{
    const v = window.__lemWorld.subsystems.get('vegetation');
    v.materials.forEach((m,i)=>{
      const u = m.userData.lem; if(!u) return;
      const bs = window.__base[i];
      for (const k of Object.keys(u)) {
        const bv = bs[k];
        if (Array.isArray(bv)) u[k].value.fromArray(bv); else u[k].value = bv;
      }
      for (const k of Object.keys(pa)) {
        if (!u[k]) continue;
        const val = pa[k];
        if (Array.isArray(val)) u[k].value.fromArray(val);
        else if (typeof val === 'object' && val.mul !== undefined) u[k].value *= val.mul;
        else u[k].value = val;
      }
    });
  }, patch);
  await p.waitForTimeout(2500);
  fs.writeFileSync(outdir+'AB6-'+name+'.png', await p.screenshot());
}
if (errs.length) console.error('CONSOLE ERRORS', errs.slice(0,5));
await b.close();
