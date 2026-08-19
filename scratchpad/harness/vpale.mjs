/* vpale.mjs — is a pale pixel in the canopy foliage, or background seen through
 * it? Paint the near foliage material flat magenta and shoot again: whatever
 * stays pale is not a tree. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3] || 'vpale';
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(5000);
await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  v.matNear.color.setRGB(6,0,6); v.matNear.needsUpdate = true;
  v.matFar.color.setRGB(0,0,6); v.matFar.needsUpdate = true;
  v.matBark.color.setRGB(6,4,0); v.matBark.needsUpdate = true;
});
await p.waitForTimeout(1500);
fs.writeFileSync(dir+tag+'-flat.png', await p.screenshot());
await b.close();
console.log('ok');
