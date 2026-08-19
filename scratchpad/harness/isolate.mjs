import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(3500);
const names = await p.evaluate(()=>{
  const v = window.__lemWorld.subsystems.get('vegetation');
  const list = [];
  v.meshes.forEach((m,i)=>{
    let kind='?';
    if (m===v.grass?.mesh) kind='grass';
    else if (v.clutter.some((c,j)=>c.mesh===m && (kind='clutter'+j))) {}
    else if (v.trees.some(e=>e.near===m&&(kind='near'))) {}
    else if (v.trees.some(e=>e.trunk===m&&(kind='trunk'))) {}
    else if (v.trees.some(e=>e.far===m&&(kind='far'))) {}
    list.push(kind);
  });
  window.__vegList = list;
  return list;
});
console.log(names.join(','));
for (const only of ['clutter0','clutter1','clutter2','clutter3','clutter4','clutter5','grass']) {
  await p.evaluate((only)=>{
    const v = window.__lemWorld.subsystems.get('vegetation');
    v.meshes.forEach((m,i)=>{ m.visible = window.__vegList[i]===only; });
  }, only);
  await p.waitForTimeout(400);
  await p.screenshot({path:`/Users/rynatical/LAB-lem/scratchpad/shots/ISO-${only}.png`});
}
await b.close();
