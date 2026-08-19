/* Two questions the critic raised, measured:
 *   1. what is the sun's elevation at the judged hour, and how long a shadow
 *      does the plant's tallest thing therefore throw?
 *   2. is that tall thin geometry actually in the shadow-caster set, or culled
 *      by CSM_MIN_SIZE (cascade 1 wants a 4.0 m footprint; a stack has ~2)? */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(12000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, gi=w.subsystems.get('gi'), bld=w.subsystems.get('buildings');
  const sun = gi && gi.sun ? gi.sun.position.clone().normalize() : null;
  const elev = sun ? +(Math.asin(sun.y)*180/Math.PI).toFixed(1) : null;
  const out={sunElevDeg:elev, shadowLenPer10m: elev? +(10/Math.tan(elev*Math.PI/180)).toFixed(1):null, tall:[]};
  const root=(bld&&bld.group)||w.scene; const box=new (window.__lemWorld.ctx.THREE.Box3)();
  root.traverse(o=>{
    if(!o.isMesh && !o.isInstancedMesh) return;
    box.setFromObject(o);
    const rise=box.max.y-box.min.y;
    const foot=Math.max(box.max.x-box.min.x, box.max.z-box.min.z);
    if(rise < 8) return;                       // only tall things
    out.tall.push({name:o.name||'(unnamed)', rise:+rise.toFixed(1), foot:+foot.toFixed(1),
      cast:o.castShadow, onC0:o.layers.isEnabled(6), onC1:o.layers.isEnabled(7),
      throwsM: elev? +(rise/Math.tan(elev*Math.PI/180)).toFixed(1):null});
  });
  out.tall.sort((a,b)=>b.rise-a.rise); out.tall=out.tall.slice(0,10);
  return out;
}),null,1));
await b.close();
