/* gitier.mjs — the rig's state at each rung, and on the way back up.
 * State, not pixels: this is the check that `gi: false` really does build
 * nothing, that `lighting` really does move the cadences, and that the shadow
 * flags suppressed at the floor tier come back when the ladder climbs — which
 * is a permanent, silent failure if it does not. */
import {chromium} from 'playwright';
const MODS='sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url=`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}&cam=yard&time=16&weather=clear&hud=0&quality=ultra`;
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage();
const errs=[]; p.on('pageerror',e=>errs.push(String(e).slice(0,140)));
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(5000);
const snap = async name => {
  await p.evaluate(n=>window.__lemWorld.engine.setQualityMode(n), name);
  await p.waitForTimeout(3500);
  return p.evaluate(()=>{
    const w=window.__lemWorld, gi=w.subsystems.get('gi'), eng=w.engine;
    let casters=0, mats=0, flatDef=0;
    w.scene.traverse(o=>{ if(o.castShadow && !o.isLight) casters++; });
    for (const m of gi.materials) { mats++; if (m.defines?.LEM_GI_FLAT!==undefined) flatDef++; }
    return {tier:eng.tier.name, lighting:eng.tier.lighting, budget:gi._budget,
      flat:gi._flat, probes:gi.grid?gi.grid.count:0, cascades:gi._csm.length,
      pool:gi._pool.length, env:!!w.scene.environment, sunCasts:!!gi.sun?.castShadow,
      shadowMap:eng.renderer.shadowMap.enabled, casters, mats, flatDef,
      giStrength:+gi.uniforms.lemGIStrength.value.toFixed(3),
      ao:+gi.uniforms.lemAOStrength.value.toFixed(3),
      emissive:gi.uniforms.lemEmissiveGain.value,
      draws:eng.renderer.info.render.calls, tris:eng.renderer.info.render.triangles};
  });
};
const out=[];
for (const t of ['ultra','high','medium','low','floor','ultra']) out.push(await snap(t));
console.log(JSON.stringify({errs,out},null,1));
await b.close();
