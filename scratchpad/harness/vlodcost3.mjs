/* Paired ablation. Frame time drifts upward the longer the page runs, enough to
 * make hiding *nothing* look like it costs 6 ms. So every ablation is bracketed
 * by a fresh baseline and the pair is repeated; the reported saving is the
 * median of (baseline - ablated) across repeats, which cancels a linear drift.
 * A zero-triangle LOD is measured too, and is the control: it must read ~0. */
import {chromium} from 'playwright';
const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=${process.argv[2]||'far'}&time=9&weather=clear&hud=0&quality=ultra`;
const REPEATS = 5;
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist','--disable-gpu-vsync','--disable-frame-rate-limit']});
const p = await (await b.newContext({viewport:{width:3840,height:2160}})).newPage();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(4000);
await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  window.__vegList = [];
  ((veg && veg.group) || window.__lemWorld.scene).traverse(o => {
    if (!o.isInstancedMesh) return;
    const g=o.geometry, idx=g.index?g.index.count:g.attributes.position.count;
    o.__per=idx/3; o.__vis=o.visible; window.__vegList.push(o);
  });
});
const groups = await p.evaluate(() => {
  const m=new Map();
  for (const o of window.__vegList){const e=m.get(o.__per)||{per:o.__per,inst:0};
    e.inst+=o.count; m.set(o.__per,e);}
  return [...m.values()].map(e=>({...e,tris:e.per*e.inst})).sort((a,b)=>b.tris-a.tris);
});
async function ms(hidePer){
  await p.evaluate(per=>{for(const o of window.__vegList)
    o.visible = per===null ? o.__vis : (o.__per===per?false:o.__vis);}, hidePer);
  await p.waitForTimeout(500);
  return await p.evaluate(()=>new Promise(res=>{
    const f=[]; let last=performance.now(); const stop=last+1400;
    const t=n=>{f.push(n-last); last=n; if(n<stop) requestAnimationFrame(t);
      else {f.sort((a,b)=>a-b); res(f[f.length>>1]);}};
    requestAnimationFrame(t);}));
}
const med = a => {a=[...a].sort((x,y)=>x-y); return a[a.length>>1];};
console.log(`paired ablation, ${REPEATS} repeats each (median of baseline-minus-ablated)\n`);
const targets = groups.filter(g=>g.tris>0).slice(0,6)
                      .concat(groups.filter(g=>g.tris===0).slice(0,1));
for (const g of targets){
  const d=[];
  for(let i=0;i<REPEATS;i++){ const a=await ms(null), c=await ms(g.per); d.push(a-c); }
  const s=med(d), base=await ms(null);
  const tag = g.tris===0 ? '  <-- CONTROL, must read ~0' : '';
  console.log(`${String(g.per).padStart(4)} tris/inst  ${String(g.inst).padStart(6)} inst  ` +
    `${String(g.tris.toLocaleString()).padStart(9)} tris   saves ` +
    `${s.toFixed(2)} ms  (${(100*s/base).toFixed(1)}% of a ${base.toFixed(2)} ms frame)` +
    `   spread [${Math.min(...d).toFixed(2)}, ${Math.max(...d).toFixed(2)}]${tag}`);
}
await b.close();
