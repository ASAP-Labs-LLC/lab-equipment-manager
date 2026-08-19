import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await b.newPage({viewport:{width:900,height:500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail,trains&cam=far&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const t=window.__lemWorld.subsystems.get('terrain');
  return {hasEwork: !!t._ework, ewPasses: t._ewPasses, ewSig: t._ewSig? t._ewSig.slice(0,40):null,
          segs: t._ework? t._ework.ax.length : 0, benchPasses: t._benchPasses, terrace: !!t._terrace};
})));
await b.close();
