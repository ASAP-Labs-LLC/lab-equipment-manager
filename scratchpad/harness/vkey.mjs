/* vkey.mjs — every vegetation material a different flat colour, alpha kept, so
 * a crop can be read off like a key.
 *   near magenta · far blue · bark orange · clutter cyan · grass yellow
 *   rock white · prop red */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3] || 'vkey';
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(5000);
const n = await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  const key = {matNear:[6,0,6], matFar:[0,0,6], matBark:[6,2,0], matClutter:[0,6,6],
               matGrass:[6,6,0], matRock:[6,6,6], matProp:[6,0,0]};
  let hit = 0;
  for (const [k, c] of Object.entries(key)) {
    const m = v[k]; if (!m) continue; hit++;
    m.color.setRGB(c[0], c[1], c[2]); m.needsUpdate = true;
  }
  /* anything else vegetation owns */
  const known = new Set(Object.keys(key).map(k => v[k]).filter(Boolean));
  let other = 0;
  for (const m of v.materials || []) if (!known.has(m)) { other++;
    if (m.color) m.color.setRGB(0, 6, 0); }
  return {hit, other, mats: (v.materials||[]).length};
});
console.log(JSON.stringify(n));
await p.waitForTimeout(1500);
fs.writeFileSync(dir+tag+'-key.png', await p.screenshot());
await b.close();
