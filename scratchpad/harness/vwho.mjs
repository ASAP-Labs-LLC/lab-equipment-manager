import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(11000);
const set = (near,far,trunk)=>p.evaluate(([n,f,t])=>{
  const v=window.__lemWorld.subsystems.get('vegetation');
  for (const e of v.trees){ e.near.visible=n; e.far.visible=f; if(e.trunk) e.trunk.visible=t; }
},[near,far,trunk]);
const shot = async n => { await p.waitForTimeout(1600);
  fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/WHO-'+n+'.png', await p.screenshot()); };
await set(true,true,true);   await shot('all');
await set(true,false,true);  await shot('nofar');
await set(false,true,false); await shot('faronly');
await b.close(); console.log('ok');
