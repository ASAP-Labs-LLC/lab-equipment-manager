/* vcrowncard.mjs — the near tree's three full-height crown fill cards are the
 * first 18 indices of its canopy geometry. Draw the rest only, and see what the
 * pale slabs in the stand do. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3] || 'vcc';
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(5000);
const info = await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  const out = [];
  for (const e of v.trees) {
    const g = e.near.geometry;
    out.push(g.index.count);
    g.setDrawRange(18, g.index.count - 18);
  }
  return out.slice(0,3);
});
console.log('index counts', info);
await p.waitForTimeout(1200);
fs.writeFileSync(dir+tag+'-nocrowncard.png', await p.screenshot());
await p.evaluate(()=>{
  for (const e of window.__lemWorld.subsystems.get('vegetation').trees)
    e.near.geometry.setDrawRange(0, 18);
});
await p.waitForTimeout(1200);
fs.writeFileSync(dir+tag+'-crowncardonly.png', await p.screenshot());
await b.close();
