import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium', args: ['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:800,height:600}});
const warns=[]; p.on('console', m => { if (m.type()!=='log') warns.push(m.text().slice(0,180)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,vegetation&cam=wide&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const v=window.__lemWorld.subsystems.get('vegetation');
  return {matGrove: !!v.matGrove, standCells: v._standCells? v._standCells.size : null,
          groves: v.groves.length, groveR: v.groveR, treeBuckets: v.trees.length,
          groveGeoOk: (()=>{ try { return !!v._groveGeo(0);} catch(e){ return 'THREW: '+e.message; } })()};
}),null,1));
console.log('warns', warns.slice(0,8));
await b.close();
