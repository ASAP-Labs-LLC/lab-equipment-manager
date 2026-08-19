/* vbehind.mjs — paint every non-vegetation subsystem's meshes flat red, so
 * whatever shows through the canopy names itself. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], tag = process.argv[3] || 'vbehind';
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(5000);
const seen = await p.evaluate(()=>{
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const mine = new Set();
  v.ctx.scene.traverse(o => {});
  const vegRoots = [];
  for (const k of Object.keys(v)) { const o = v[k];
    if (o && o.isObject3D) vegRoots.push(o); }
  const inVeg = o => { let q = o; while (q) { if (vegRoots.includes(q)) return true; q = q.parent; } return false; };
  const names = new Set();
  w.scene.traverse(o => {
    if (!o.isMesh && !o.isInstancedMesh && !o.isPoints) return;
    if (inVeg(o)) return;
    names.add((o.name || o.type) + ':' + (o.material && o.material.type));
    const ms = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of ms) { if (!m) continue;
      if (m.color) m.color.setRGB(8, 0, 0);
      if (m.emissive) m.emissive.setRGB(0,0,0);
      m.map = null; m.needsUpdate = true; }
  });
  return [...names].slice(0, 40);
});
console.log(seen.join('\n'));
await p.waitForTimeout(1500);
fs.writeFileSync(dir+tag+'-behind.png', await p.screenshot());
await b.close();
