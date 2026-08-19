import {chromium} from 'playwright';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=16&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto(url,{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld; const out=[];
  w.scene.traverse(o=>{ if(o.isLight) out.push({t:o.type,cast:!!o.castShadow,int:o.intensity,vis:o.visible,pos:o.position.toArray().map(n=>+n.toFixed(0)),map:!!o.shadow?.map,mapW:o.shadow?.map?.width}); });
  const gi=w.subsystems.get('gi');
  return {lights:out, giSunIsIndex:null, giMapW:gi.sun.shadow.map?.width};
}),null,1));
await b.close();
