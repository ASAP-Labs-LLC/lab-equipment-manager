import {chromium} from 'playwright';
import fs from 'fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(11000);
await p.evaluate(()=>{ const v=window.__lemWorld.subsystems.get('vegetation');
  const up=v.update.bind(v); v.update=(dt,t)=>{up(dt,t); v.shared.uVegSeason.value=0;}; });
await p.waitForTimeout(2000);
fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/SH-on.png', await p.screenshot());
const info = await p.evaluate(()=>{
  const s=window.__lemWorld.scene; const out=[];
  s.traverse(o=>{ if(o.isDirectionalLight){ out.push({int:o.intensity, cast:o.castShadow,
      cam:o.shadow?{l:o.shadow.camera.left,r:o.shadow.camera.right,n:o.shadow.camera.near,f:o.shadow.camera.far}:null});
      o.castShadow=false; } });
  window.__lemWorld.engine.shadowNeedsUpdate = true;
  return out;
});
await p.waitForTimeout(2500);
fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/shots/SH-off.png', await p.screenshot());
console.log(JSON.stringify(info));
await b.close();
