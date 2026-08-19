/* tenrol.mjs — why is (or isn't) a vehicle in the coarse cascades? Walks gi's
 * own `_enrol` conditions for one tank car and prints each gate. */
import {chromium} from 'playwright';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=16&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p = await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,200)));
await p.goto(url,{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(7000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, gi=w.subsystems.get('gi'), tr=w.subsystems.get('trains');
  const out=[];
  let n=0;
  tr.root.traverse(o=>{
    if(!(o.isMesh||o.isInstancedMesh) || n>=4) return;
    if(o.material?.isMeshBasicMaterial) return;
    n++;
    const m = gi._casterMetrics(o);
    out.push({
      inst: !!o.isInstancedMesh,
      cast: o.castShadow, base: o.userData.lemCastBase, keep: o.userData.lemKeepShadow,
      transparent: o.material?.transparent, depthWrite: o.material?.depthWrite,
      metrics: m, depthFor: !!gi._depthFor(o),
      layers: gi._csm.map(c=>o.layers.isEnabled(c.layer)),
    });
  });
  return {csm: gi._csm.map(c=>({i:c.i, n:c.casters.length, ready: gi.uniforms['lemCsmReady'+c.i]?.value})), sample: out,
          trainsInCsm: gi._csm.map(c=>c.casters.filter(o=>{let p=o;while(p){if(p===tr.root)return true;p=p.parent;}return false;}).length)};
}),null,1));
await b.close();
