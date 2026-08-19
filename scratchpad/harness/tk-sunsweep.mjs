/* Do the plant's shadows exist but fall out of sight at the judged hour?
 * Ablate the buildings' shadow casting in-session at several sun times and
 * measure how much the YARD darkens. If the yard barely changes at time=9 but
 * changes a lot at other hours, the shadows are real and the hour is hiding
 * them — a very different defect from "the plant is excluded from the pass". */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for (const t of [8,9,11,14,16,17]) {
  const p=await (await b.newContext({viewport:{width:1600,height:900}})).newPage();
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=${t}&weather=clear&hud=0&quality=ultra`,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  await p.waitForTimeout(11000);
  const r = await p.evaluate(async () => {
    const w=window.__lemWorld;
    const bl=[]; w.scene.traverse(o=>{ if((o.isMesh||o.isInstancedMesh) && o.castShadow) bl.push(o); });
    const sun = w.subsystems.get('gi');
    const elev = sun && sun._skyState ? +(sun._skyState().elevDeg ?? 0).toFixed(1) : null;
    const grab = () => new Promise(res=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
      const c=w.engine.renderer.domElement;
      const t=document.createElement('canvas'); t.width=c.width; t.height=c.height;
      t.getContext('2d').drawImage(c,0,0);
      res(t.getContext('2d').getImageData(Math.round(t.width*0.42), Math.round(t.height*0.44),
                                          Math.round(t.width*0.24), Math.round(t.height*0.16)).data);
    })));
    const lum = d => { let s=0,n=0; for(let i=0;i<d.length;i+=4){s+=0.2126*d[i]+0.7152*d[i+1]+0.0722*d[i+2];n++;} return s/n; };
    const on = lum(await grab());
    const prev = bl.map(o=>o.castShadow); bl.forEach(o=>o.castShadow=false);
    const off = lum(await grab());
    bl.forEach((o,i)=>o.castShadow=prev[i]);
    return {casters:bl.length, elev, on:+on.toFixed(2), off:+off.toFixed(2), darkening:+(off-on).toFixed(2)};
  });
  console.log(`time=${String(t).padStart(2)}  sun ${String(r.elev).padStart(5)}deg  casters ${r.casters}  yard L with shadows ${r.on}  without ${r.off}  -> shadows darken the yard by ${r.darkening} L`);
  await p.context().close();
}
await b.close();
