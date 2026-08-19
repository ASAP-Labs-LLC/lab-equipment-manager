/* Is ctx.quality really `floor` when subsystems build against it? */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.addInitScript(() => {
  window.__qtrace = [];
  const t0 = performance.now();
  const iv = setInterval(() => {
    const w = window.__lemWorld;
    if (!w) return;
    window.__qtrace.push({t: Math.round(performance.now()-t0),
      ctxQuality: w.ctx && w.ctx.quality ? w.ctx.quality.name : null,
      engineTier: w.engine && w.engine.tier ? w.engine.tier.name : null});
    if (performance.now()-t0 > 14000) clearInterval(iv);
  }, 400);
});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=far&time=9&hud=0&auto=1',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(15000);
const tr = await p.evaluate(()=>window.__qtrace);
const seen = []; let last=null;
for (const r of tr) { const k=r.ctxQuality+'/'+r.engineTier; if(k!==last){seen.push(r); last=k;} }
console.log('ctx.quality vs engine.tier over time (auto ladder on):');
for (const r of seen) console.log(`  t=${String(r.t).padStart(6)}ms   ctx.quality=${String(r.ctxQuality).padEnd(8)} engine.tier=${r.engineTier}`);
await b.close();
