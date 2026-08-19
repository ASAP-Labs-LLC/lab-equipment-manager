import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
const errs=[],logs=[];
p.on('pageerror',e=>errs.push(String(e).slice(0,200)));
p.on('console',m=>{if(m.type()==='error'||m.type()==='warning')logs.push(m.text().slice(0,200));});
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(14000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, t=w.subsystems.get('terrain'), r=w.subsystems.get('rail');
  const plan=t._plan||w.plan||(w.ctx&&w.ctx.plan)||{};
  return {stations:(plan.stations||[]).length, hub:plan.hub, bounds:plan.bounds,
          features:(t.features||[]).length, siteRadial:t.siteRadial, islandR:t.islandR,
          coreSize:t.coreSize, railKeys:r?Object.keys(r).slice(0,25):null,
          failed:w.failed};
}),null,1));
console.log('pageerrors',JSON.stringify(errs.slice(0,5)));
console.log('console',JSON.stringify(logs.slice(0,8)));
await b.close();
