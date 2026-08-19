import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,160)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=far&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(9000);
console.log(await p.evaluate(()=>{const w=window.__lemWorld;
  return {subsystems:[...w.subsystems.keys()], failed:w.failed,
          propsPresent:!!w.subsystems.get('props'),
          regions:(w.subsystems.get('props')||{}).regions,
          stats:w.stats()};}));
if(errs.length) console.log('ERRORS:',errs.slice(0,3));
await b.close();
