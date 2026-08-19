/* Does the instanced foliage go through the scene fog?
 * The critic says depth exists on water/sky/headland and not on vegetation. */
import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(10000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, seen=new Set(); const out={sceneFog:null, materials:[]};
  out.sceneFog = w.scene.fog ? {type:w.scene.fog.type||w.scene.fog.constructor.name,
                                density:w.scene.fog.density, color:w.scene.fog.color.getHexString()} : null;
  const veg=w.subsystems.get('vegetation');
  const root=(veg&&veg.group)||w.scene;
  root.traverse(o=>{
    if(!o.isInstancedMesh && !o.isMesh) return;
    const m=o.material; if(!m||seen.has(m.uuid)) return; seen.add(m.uuid);
    out.materials.push({name:m.name||o.name||'(unnamed)', type:m.type,
      fog:m.fog, instances:o.isInstancedMesh?o.count:1});
  });
  // and a control: what do terrain / buildings say?
  out.others=[];
  const seen2=new Set();
  w.scene.traverse(o=>{
    if(!o.isMesh) return;
    if(/terrain-core|terrain-ocean|terrain-mainland/.test(o.name||'')){
      if(seen2.has(o.name))return; seen2.add(o.name);
      out.others.push({name:o.name, fog:o.material&&o.material.fog});
    }
  });
  return out;
}),null,1));
await b.close();
