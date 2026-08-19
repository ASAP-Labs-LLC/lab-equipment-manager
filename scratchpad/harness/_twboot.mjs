/* terrain's own build cost, ablated. Five loads of mods=terrain each way, taking
 * the median, because a cold module-graph parse is not free either. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
async function run(ablate) {
  const t=[];
  for (let i=0;i<5;i++){
    const ctx = await b.newContext({viewport:{width:960,height:540}});
    const p = await ctx.newPage();
    if (ablate) await p.addInitScript(()=>{window.__lemAblateSubstrate=true;});
    const t0=Date.now();
    await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=far&time=9&weather=clear&hud=0&quality=ultra',
      {waitUntil:'load',timeout:60000});
    await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
    t.push(Date.now()-t0);
    await ctx.close();
  }
  t.sort((x,y)=>x-y);
  return {all:t, median:t[2]};
}
const A = await run(true), L = await run(false);
console.log(JSON.stringify({ablated:A, live:L, deltaMs:L.median-A.median}));
await b.close();
