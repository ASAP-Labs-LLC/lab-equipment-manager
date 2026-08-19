/* Measure the ENGINE'S OWN draw intervals by hooking renderFrame — a parallel
 * rAF keeps ticking whether the engine drew or not, so it cannot see a cap. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:false, channel:'chromium',
  args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:800}, deviceScaleFactor:2})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=low&time=9&hud=0&quality=ultra',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.evaluate(()=>window.__lemWorld.engine.setResolutionMode('x2'));
await p.waitForTimeout(8000);
for (const cap of ['off','60','30']) {
  const r = await p.evaluate(c=>new Promise(res=>{
    const e=window.__lemWorld.engine;
    e.setFrameCap(c);
    const gaps=[]; let last=performance.now();
    const orig=e.renderFrame.bind(e);
    e.renderFrame=()=>{ const n=performance.now(); gaps.push(n-last); last=n; return orig(); };
    setTimeout(()=>{
      e.renderFrame=orig;
      const s=gaps.slice(5).sort((a,b)=>a-b);
      const q=x=>+s[Math.min(s.length-1,Math.floor(s.length*x))].toFixed(1);
      const m=s.reduce((a,v)=>a+v,0)/s.length;
      const sd=Math.sqrt(s.reduce((a,v)=>a+(v-m)**2,0)/s.length);
      // how many draws land on 1 vs 2 refresh intervals — the beating signature
      const one=s.filter(v=>v<12.5).length, two=s.filter(v=>v>=12.5&&v<21).length;
      res({draws:s.length, p50:q(.5), p95:q(.95), max:+s[s.length-1].toFixed(1),
           jitterCv:+(sd/m).toFixed(3), drawFps:Math.round(1000/m),
           pctOneInterval:+(100*one/s.length).toFixed(1),
           pctTwoIntervals:+(100*two/s.length).toFixed(1)});
    }, 8000);
  }), cap);
  console.log(`cap=${cap.padEnd(3)}`, JSON.stringify(r));
}
await b.close();
