/* Trees cast, buildings do not. Both are on the cascade layers. So something
 * consults castShadow — and gi's near-culler TOGGLES castShadow per frame
 * (`if (obj.castShadow !== near) obj.castShadow = near`). If the plant is not
 * "near" at cam=far, that would remove it from the cascades too. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(12000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld;
  const tally = sub => {
    const s={n:0, cast:0, onC0:0, onC1:0, lemBase:0, cullable:0};
    const root=(sub&&sub.group); if(!root) return s;
    root.traverse(o=>{ if(!o.isMesh && !o.isInstancedMesh) return;
      s.n++; if(o.castShadow) s.cast++;
      if(o.layers.isEnabled(6)) s.onC0++; if(o.layers.isEnabled(7)) s.onC1++;
      if(o.userData && o.userData.lemCastBase) s.lemBase++; });
    return s; };
  const gi=w.subsystems.get('gi');
  return {
    buildings: tally(w.subsystems.get('buildings')),
    vegetation: tally(w.subsystems.get('vegetation')),
    props: tally(w.subsystems.get('props')),
    cullableLen: gi && gi._cullable ? (gi._cullable.size ?? gi._cullable.length) : null,
  };
}),null,1));
await b.close();
