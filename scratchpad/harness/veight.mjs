import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(3500);
console.log(await p.evaluate(()=>{
  const veg=window.__lemWorld.subsystems.get('vegetation');
  const root=(veg&&veg.group)||window.__lemWorld.scene;
  const buckets=[];
  root.traverse(o=>{if(!o.isInstancedMesh)return;
    const g=o.geometry,i=g.index?g.index.count:g.attributes.position.count,per=i/3;
    if(per!==8&&per!==6&&per!==4)return;
    // which collection does it belong to?
    let where='?';
    for(const k of ['trees','groves','clutter','grass','shrubs','undergrowth'])
      if(Array.isArray(veg[k])&&veg[k].some(e=>e&&(e.mesh===o||e===o))) where=k;
    buckets.push({per,count:o.count,where,
      mat:(o.material&&o.material.name)||'',
      bb:o.geometry.boundingBox?
        [+(o.geometry.boundingBox.max.x-o.geometry.boundingBox.min.x).toFixed(1),
         +(o.geometry.boundingBox.max.y-o.geometry.boundingBox.min.y).toFixed(1)]:null});});
  const keys=['trees','groves','clutter','grass','shrubs','undergrowth']
    .filter(k=>Array.isArray(veg[k])).map(k=>`${k}=${veg[k].length}`);
  return {collections:keys.join(' '), buckets};
}));
await b.close();
