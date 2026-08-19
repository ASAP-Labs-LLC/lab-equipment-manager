import {chromium} from 'playwright';
const b=await chromium.launch({args:['--use-angle=metal']});const p=await b.newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&time=16&hud=0',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  return t.meshes.map(m=>{
    const g=m.geometry; g.computeBoundingBox&&g.computeBoundingBox();
    const bb=g.boundingBox;
    return {name:m.name, tris:g.index?g.index.count/3:0, visible:m.visible,
      box:bb?[Math.round(bb.min.x),Math.round(bb.max.x),Math.round(bb.min.y),Math.round(bb.max.y),Math.round(bb.min.z),Math.round(bb.max.z)]:null};
  });
}),null,1));
await b.close();
