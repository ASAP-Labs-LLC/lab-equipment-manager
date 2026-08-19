import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(11000);
const shot = async n => { await p.waitForTimeout(1500);
  fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/SP-'+n+'.png', await p.screenshot()); };
const only = ids => p.evaluate(ids=>{
  const v=window.__lemWorld.subsystems.get('vegetation');
  for (const e of v.trees){ const on = ids.length===0 || ids.includes(e.spec.id);
    e.near.visible=on; e.far.visible=on; if(e.trunk) e.trunk.visible=on; }
}, ids);
await only([]); await shot('all');
await only(['spruce','pine','oak']); await shot('nopale');
await only(['birch','aspen']); await shot('paleonly');
await b.close(); console.log('ok');
