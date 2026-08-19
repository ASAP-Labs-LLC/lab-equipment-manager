/* The gi round measured engine.shadowNeedsUpdate being raised 251x/s, all from
 * trains.js._step — which forces three's shadow map to redraw EVERY frame.
 * That is a per-frame spike and a prime suspect for uneven pacing. Verify. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1440,height:900}})).newPage();
await p.addInitScript(()=>{ window.__setCount = 0; });
await p.goto('http://127.0.0.1:5612/floor',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>!!window.__lemWorld,null,{timeout:45000});
await p.waitForTimeout(15000);
const r = await p.evaluate(()=>new Promise(res=>{
  const e = window.__lemWorld.engine;
  let hits = 0;
  const key = '_shadowNeedsUpdate__probe';
  let v = e.shadowNeedsUpdate;
  Object.defineProperty(e, 'shadowNeedsUpdate', {
    configurable: true,
    get(){ return v; },
    set(nv){ if (nv && !v) hits++; v = nv; },
  });
  const t0 = performance.now();
  setTimeout(()=>{
    const secs = (performance.now()-t0)/1000;
    delete e.shadowNeedsUpdate; e.shadowNeedsUpdate = v;
    res({raisesPerSec: +(hits/secs).toFixed(1), seconds:+secs.toFixed(1)});
  }, 6000);
}));
console.log(JSON.stringify(r));
await b.close();
