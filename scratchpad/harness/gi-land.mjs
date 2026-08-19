/* Is the terrain actually IN the shadow cascades, or only meant to be?
 * gi.js enrols landforms against their own castShadow=false, so the flag says
 * nothing. The layer membership does. */
import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(9000);
console.log(await p.evaluate(()=>{
  const w=window.__lemWorld, gi=w.subsystems.get('gi');
  const LAYERS=[6,7];
  const out={cascades: gi&&gi._csm? gi._csm.map(c=>({layer:c.layer, size:+(c.spec&&c.spec.size||0).toFixed(0)})) : 'none',
             onLayer:{}, landformFlagged:[], terrainMeshes:[]};
  for (const L of LAYERS) out.onLayer[L]=[];
  w.scene.traverse(o=>{
    if(!o.isMesh && !o.isInstancedMesh) return;
    const nm=o.name||'(unnamed)';
    if(/terrain/i.test(nm)){
      const bb=o.geometry.boundingSphere;
      out.terrainMeshes.push({name:nm, cast:o.castShadow, recv:o.receiveShadow,
        landform:!!(o.userData&&o.userData.lemLandform),
        r: bb? +bb.radius.toFixed(0):null,
        layers: LAYERS.filter(L=>o.layers.isEnabled(L))});
    }
    if(o.userData&&o.userData.lemLandform) out.landformFlagged.push(nm);
    for(const L of LAYERS) if(o.layers.isEnabled(L)) out.onLayer[L].push(nm);
  });
  for(const L of LAYERS) out.onLayer[L]={count:out.onLayer[L].length, sample:out.onLayer[L].slice(0,6)};
  return out;
}));
await b.close();
