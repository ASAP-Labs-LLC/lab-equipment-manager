/* Ablate the vegetation LODs by geometry signature, not by name — the meshes
 * are unnamed, and a name-based hide silently matches nothing while still
 * printing a number. Triangles-per-instance identifies them exactly: the grove
 * card is 8 (fifteen stems), the far card 6, the geometry trees 28-36. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=${process.argv[2]||'far'}&time=9&weather=clear&hud=0&quality=ultra`;
const WD = process.env.HOME + '/LAB-lem/LEM Web Server/static/world';
const st = () => fs.readdirSync(WD).filter(f=>f.endsWith('.js')).map(f=>f+fs.statSync(WD+'/'+f).mtimeMs).join();
const before = st();
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist','--disable-gpu-vsync','--disable-frame-rate-limit']});
const p = await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(3500);

await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  window.__vegRoot = (veg && veg.group) || null;
  window.__vegList = [];
  if (!window.__vegRoot) return;
  window.__vegRoot.traverse(o => {
    if (!o.isInstancedMesh) return;
    const g=o.geometry, idx=g.index?g.index.count:g.attributes.position.count;
    o.__per = idx/3; o.__vis = o.visible; window.__vegList.push(o);
  });
});
const groups = await p.evaluate(() => {
  const m = new Map();
  for (const o of window.__vegList) {
    const k = o.__per;
    const e = m.get(k) || {per:k, inst:0, meshes:0};
    e.inst += o.count; e.meshes++; m.set(k,e);
  }
  return [...m.values()].map(e=>({...e, tris:e.per*e.inst}))
                        .sort((a,b)=>b.tris-a.tris);
});
console.log('vegetation by triangles-per-instance:');
for (const g of groups)
  console.log(`  ${String(g.per).padStart(4)} tris/inst  ${String(g.inst).padStart(7)} inst  ` +
              `${String(g.meshes).padStart(3)} meshes  = ${g.tris.toLocaleString().padStart(9)} tris`);

async function measure(hidePer) {
  await p.evaluate(per => { for (const o of window.__vegList)
    o.visible = (per === null) ? o.__vis : (o.__per === per ? false : o.__vis); }, hidePer);
  await p.waitForTimeout(900);
  return await p.evaluate(() => new Promise(res => {
    const f=[]; let last=performance.now(); const stop=last+3000;
    const tick=n=>{f.push(n-last); last=n;
      if(n<stop) requestAnimationFrame(tick);
      else {f.sort((a,b)=>a-b); res({ms:+f[f.length>>1].toFixed(2)});}};
    requestAnimationFrame(tick);}));
}
const base = await measure(null);
console.log(`\nbaseline frame time: ${base.ms} ms  (${Math.round(1000/base.ms)} fps)`);
for (const g of groups) {
  const r = await measure(g.per);
  const save = base.ms - r.ms;
  console.log(`  without the ${String(g.per).padStart(3)}-tri LOD (${g.tris.toLocaleString()} tris): ` +
    `${r.ms} ms — ${save>=0?'saves':'costs'} ${Math.abs(save).toFixed(2)} ms ` +
    `(${(100*save/base.ms).toFixed(1)}%)`);
  await measure(null);
}
console.log('\nbuild stable during measurement:', before === st());
await b.close();
