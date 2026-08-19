import {chromium} from 'playwright';
const b=await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
for (let i=0;i<8;i++){
  await p.waitForTimeout(4000);
  console.log(JSON.stringify(await p.evaluate(()=>({
    t:Math.round(performance.now()/1000),
    tier:window.__lemWorld.ctx.quality?.name,
    maxActive:window.__lemWorld.subsystems.get('trains').maxActive,
    draws:window.__lemWorld.ctx.renderer.info.render.calls,
  }))));
}
await b.close();
