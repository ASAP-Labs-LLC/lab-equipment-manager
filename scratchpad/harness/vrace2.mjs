import {chromium} from 'playwright';
import fs from 'node:fs';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for(let run=1;run<=3;run++){
  const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
  await p.goto(URL,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  let prev=0;
  for(const t of [300,1200,2500,5000,9000]){
    await p.waitForTimeout(t-prev); prev=t;
    await p.screenshot({path:`/tmp/vr-${run}-${t}.png`});
  }
  // what textures do the vegetation materials actually hold?
  const mats=await p.evaluate(()=>{const seen=new Set(); const out=[];
    window.__lemWorld.scene.traverse(o=>{if(!o.isInstancedMesh)return;
      const m=o.material; if(!m||seen.has(m.uuid))return; seen.add(m.uuid);
      out.push({name:o.name||m.name||'?', map:!!(m.map&&m.map.image),
                w:m.map&&m.map.image?m.map.image.width:0});});
    return out.slice(0,10);});
  console.log(`run ${run} materials:`, JSON.stringify(mats));
  await p.context().close();
}
await b.close();
