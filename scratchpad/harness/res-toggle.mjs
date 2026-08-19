/* Prove resolution is now independent of tier: floor tier at full resolution. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1920,height:1080}, deviceScaleFactor:1})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=far&time=9&hud=0&quality=floor',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(4000);
for (const mode of ['auto','full','max','quart']) {
  const r = await p.evaluate(m => {
    const e = window.__lemWorld.engine;
    e.setResolutionMode(m);
    const i = e.resolutionInfo();
    return {tier:e.tier.name, ...i};
  }, mode);
  console.log(`tier ${r.tier.padEnd(6)} resolution '${r.mode}'  ->  ${r.w}x${r.h} of ${r.cssW}x${r.cssH}  = ${r.pct}% of CSS pixels`);
}
console.log('persisted:', await p.evaluate(()=>localStorage.getItem('lem.world.resolution')));
await b.close();
