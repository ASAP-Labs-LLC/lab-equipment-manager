import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.evaluate(()=>{ window.__peak={d:0,t:0}; const w=window.__lemWorld;
  const R=w.engine.renderer; const loop=()=>{ const i=R.info.render;
    if(i.calls>window.__peak.d) window.__peak.d=i.calls;
    if(i.triangles>window.__peak.t) window.__peak.t=i.triangles;
    requestAnimationFrame(loop); }; loop();
  // force a shadow rebuild every second
  setInterval(()=>{ w.engine.shadowNeedsUpdate = true; }, 200);
});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(()=>window.__peak)));
await b.close();
