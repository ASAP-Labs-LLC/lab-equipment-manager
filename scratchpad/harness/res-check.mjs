/* What resolution does each tier actually render at, against the CSS size? */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for (const q of ['ultra','high','medium','low','floor']) {
  const p=await (await b.newContext({viewport:{width:1920,height:1080}, deviceScaleFactor:1})).newPage();
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=far&time=9&hud=0&quality=${q}`,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  await p.waitForTimeout(4000);
  const r = await p.evaluate(()=>{const e=window.__lemWorld.engine, c=e.canvas;
    return {tier:e.tier.name, scale:e.tier.scale, dpr:window.devicePixelRatio,
            css:[e.cssWidth,e.cssHeight], backing:[c.width,c.height]};});
  const pxRatio = (r.backing[0]*r.backing[1])/(r.css[0]*r.css[1]);
  console.log(`${r.tier.padEnd(7)} scale ${r.scale.toFixed(2)}  css ${r.css[0]}x${r.css[1]}  backing ${r.backing[0]}x${r.backing[1]}  = ${(pxRatio*100).toFixed(0)}% of CSS pixels`);
  await p.context().close();
}
await b.close();
