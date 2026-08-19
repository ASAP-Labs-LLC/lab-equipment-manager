/* vedge.mjs — sweep the new alpha-edge window (and the mip bias with it) on a
 * live page, screenshotting the same crop each time, so the edge quality can be
 * chosen by looking rather than by argument. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3] || 'vedge';
const sets = JSON.parse(process.argv[4]);      // [[edge, biasNear, biasFar], ...]
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = [];
p.on('console', m => { const t = m.text();
  if (m.type()==='error' && !/404/.test(t)) errs.push(t); });
p.on('pageerror', e => errs.push(String(e)));
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(5000);
const has = await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  return !!(v.matNear.userData.lem && v.matNear.userData.lem.uVegEdge);
});
console.log('uVegEdge uniform present:', has);
for (const [edge, bn, bf] of sets) {
  await p.evaluate(({edge,bn,bf})=>{
    const v = window.__lemWorld.subsystems.get('vegetation');
    v.matNear.userData.lem.uVegEdge.value = edge;
    v.matFar.userData.lem.uVegEdge.value = edge;
    if (bn !== null) v.matNear.userData.lem.uVegAlphaBias.value = bn;
    if (bf !== null) v.matFar.userData.lem.uVegAlphaBias.value = bf;
  }, {edge,bn,bf});
  await p.waitForTimeout(900);
  const name = `${tag}-e${edge}-b${bn}`;
  fs.writeFileSync(dir+name+'.png', await p.screenshot());
  console.log(name);
}
if (errs.length) console.log('ERRORS', errs.slice(0,6));
await b.close();
