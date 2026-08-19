/* The user's hypothesis: "Full" is 100% of an already-reduced thing.
 * Test on a REAL retina panel (deviceScaleFactor 2), which is what a Mac has
 * and which my earlier test at dsf=1 could never have caught. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for (const dsf of [1, 2]) {
  const p=await (await b.newContext({viewport:{width:1280,height:800}, deviceScaleFactor:dsf})).newPage();
  await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather&cam=far&time=9&hud=0&quality=floor',{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  await p.waitForTimeout(4000);
  for (const mode of ['auto','full','max']) {
    const r = await p.evaluate(m=>{const e=window.__lemWorld.engine; e.setResolutionMode(m);
      const c=e.canvas; const rect=c.getBoundingClientRect();
      return {mode:m, dpr:window.devicePixelRatio, tier:e.tier.name,
              backing:[c.width,c.height], cssBox:[Math.round(rect.width),Math.round(rect.height)],
              texScale:e.tier.textureScale};}, mode);
    const devW = r.cssBox[0]*r.dpr, devH = r.cssBox[1]*r.dpr;
    const pctDevice = 100*(r.backing[0]*r.backing[1])/(devW*devH);
    console.log(`dpr=${r.dpr}  '${r.mode}'  backing ${r.backing[0]}x${r.backing[1]}  css ${r.cssBox[0]}x${r.cssBox[1]}  device ${devW}x${devH}  = ${pctDevice.toFixed(0)}% of DEVICE pixels   [textureScale ${r.texScale}]`);
  }
  await p.context().close();
}
await b.close();
