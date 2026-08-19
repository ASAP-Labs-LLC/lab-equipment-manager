/* Corrected: gi renders each cascade with cam.layers.set(CSM_LAYERS[i]), so
 * LAYER MEMBERSHIP decides what casts, not the castShadow flag. Toggle the
 * layers. Control: do the same to the trees, which the critic says DO cast. */
import {chromium} from 'playwright';
const LAY=[6,7];
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for (const t of [9,14,16]) {
  const p=await (await b.newContext({viewport:{width:1600,height:900}})).newPage();
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=${t}&weather=clear&hud=0&quality=ultra`,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  await p.waitForTimeout(11000);
  const r = await p.evaluate(async (LAY) => {
    const w=window.__lemWorld;
    const bld=w.subsystems.get('buildings'), veg=w.subsystems.get('vegetation');
    const pick = sub => { const a=[]; const root=(sub&&sub.group)||null;
      if(root) root.traverse(o=>{ if((o.isMesh||o.isInstancedMesh) && LAY.some(L=>o.layers.isEnabled(L))) a.push(o); });
      return a; };
    const B=pick(bld), V=pick(veg);
    const grab = () => new Promise(res=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
      const c=w.engine.renderer.domElement;
      const t=document.createElement('canvas'); t.width=c.width; t.height=c.height;
      t.getContext('2d').drawImage(c,0,0);
      res(t.getContext('2d').getImageData(Math.round(t.width*0.42), Math.round(t.height*0.44),
                                          Math.round(t.width*0.24), Math.round(t.height*0.16)).data);
    })));
    const lum = d => { let s=0,n=0; for(let i=0;i<d.length;i+=4){s+=0.2126*d[i]+0.7152*d[i+1]+0.0722*d[i+2];n++;} return s/n; };
    const off = list => list.forEach(o=>LAY.forEach(L=>o.layers.disable(L)));
    const on  = list => list.forEach(o=>LAY.forEach(L=>o.layers.enable(L)));
    const base = lum(await grab());
    off(B); const noB = lum(await grab()); on(B);
    off(V); const noV = lum(await grab()); on(V);
    return {bld:B.length, veg:V.length, base:+base.toFixed(2),
            bldShadowL:+(noB-base).toFixed(2), vegShadowL:+(noV-base).toFixed(2)};
  }, LAY);
  console.log(`time=${String(t).padStart(2)}  enrolled: ${r.bld} building, ${r.veg} vegetation  |  yard ${r.base} L  |  buildings' shadows are worth ${r.bldShadowL} L   trees' ${r.vegShadowL} L`);
  await p.context().close();
}
await b.close();
