/* A/B the vegetation materials live: mutate one thing, crop the treeline, measure. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2];
const CROP = {x:950, y:395, width:170, height:130};
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2000);

const cases = {
  base: () => {},
  ab12: () => { const v=window.__lemWorld.subsystems.get('vegetation'); v.matNear.userData.lem.uVegAlphaBias.value=0.12; },
  ab22: () => { const v=window.__lemWorld.subsystems.get('vegetation'); v.matNear.userData.lem.uVegAlphaBias.value=0.22; v.matNear.userData.lem.uVegDither.value=0.24; },

  d0: () => { const v=window.__lemWorld.subsystems.get('vegetation'); v.matNear.userData.lem.uVegDither.value=0.0; },
  notrunk: () => { const v=window.__lemWorld.subsystems.get('vegetation'); for(const e of v.trees) e.trunk&&(e.trunk.visible=false); },
  noatlasnorm: () => { const v=window.__lemWorld.subsystems.get('vegetation'); v.matNear.normalMap=null; v.matNear.needsUpdate=true; },

  fg22: () => { const v=window.__lemWorld.subsystems.get('vegetation'); v.matFar.userData.lem.uVegGain.value=2.2; },
  fg30: () => { const v=window.__lemWorld.subsystems.get('vegetation'); v.matFar.userData.lem.uVegGain.value=3.0; },
  fg40: () => { const v=window.__lemWorld.subsystems.get('vegetation'); v.matFar.userData.lem.uVegGain.value=4.0; },

  w20: () => { const v=window.__lemWorld.subsystems.get('vegetation'); for(const m of v.materials) if(m.userData.lem?.uVegWrap) m.userData.lem.uVegWrap.value=1.6; },
  w30: () => { const v=window.__lemWorld.subsystems.get('vegetation'); for(const m of v.materials) if(m.userData.lem?.uVegWrap) m.userData.lem.uVegWrap.value=2.2; },
  w42: () => { const v=window.__lemWorld.subsystems.get('vegetation'); for(const m of v.materials) if(m.userData.lem?.uVegWrap) m.userData.lem.uVegWrap.value=2.8; },
  w42g13: () => { const v=window.__lemWorld.subsystems.get('vegetation'); for(const m of v.materials){ if(m.userData.lem?.uVegWrap) m.userData.lem.uVegWrap.value=2.8; }
    v.matNear.userData.lem.uVegGain.value=1.3; },

  norecv: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees){ e.near.receiveShadow=false; e.trunk&&(e.trunk.receiveShadow=false); } },
  nocast: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees){ e.near.castShadow=false; e.far.castShadow=false; e.trunk&&(e.trunk.castShadow=false); }
    window.__lemWorld.engine.shadowNeedsUpdate=true; },
  nosun: () => { window.__lemWorld.engine.scene.traverse(o=>{ if(o.isDirectionalLight) o.intensity=0; }); },
  noindirect: () => { const gi=window.__lemWorld.subsystems.get('gi');
    if(gi&&gi.uniforms&&gi.uniforms.lemProbeScale) gi.uniforms.lemProbeScale.value=0;
    else if(gi&&gi.uniforms) console.log(Object.keys(gi.uniforms).join(',')); },

  ao1: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees){ const g=e.near.geometry.getAttribute('aVegAO'); if(g){g.array.fill(1);g.needsUpdate=true;} } },
  tint1: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees){ const a=e.near.geometry.getAttribute('aVegTint'); a.array.fill(1); a.needsUpdate=true; } },
  wrap3: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    v.matNear.userData.lem.uVegWrap.value=2.2; },
  gain2: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    v.matNear.userData.lem.uVegGain.value=2.2; },
  probe: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    const g=v.trees[0].near.geometry.getAttribute('aVegAO');
    let mn=9,mx=-9,su=0; for(let i=0;i<g.array.length;i++){mn=Math.min(mn,g.array[i]);mx=Math.max(mx,g.array[i]);su+=g.array[i];}
    console.log('AO min/max/mean', mn, mx, su/g.array.length); },

  farnoshadow: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees) e.far.receiveShadow=false; },
  farnocast: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees) { e.far.castShadow=false; e.near.castShadow=false; e.trunk&&(e.trunk.castShadow=false);} window.__lemWorld.engine.shadowNeedsUpdate=true; },
  noao: () => { const e=window.__lemWorld.engine; e._passes.composite.material.uniforms.uAOStrength.value=0;
    const gi=window.__lemWorld.subsystems.get('gi'); if(gi&&gi.uniforms&&gi.uniforms.lemAOStrength) gi.uniforms.lemAOStrength.value=0; },
  fartint2: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees){ const a=e.far.geometry.getAttribute('aVegTint'); for(let i=0;i<a.array.length;i++)a.array[i]*=2.0; a.needsUpdate=true; } },

  noenv: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const m of [v.matNear,v.matFar,v.matClutter,v.matGrass]) { m.envMapIntensity=0; m.needsUpdate=true; } },
  nosss: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const m of v.materials) if (m.userData.lem?.uVegSSS) m.userData.lem.uVegSSS.value=0; },
  rough1: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const m of [v.matNear,v.matFar]) { m.roughness=1.0; m.normalMap=null; m.needsUpdate=true; } },
  nofog: () => { window.__lemWorld.engine.scene.fog = null;
    window.__lemWorld.engine.scene.traverse(o=>{ if(o.material){const ms=Array.isArray(o.material)?o.material:[o.material]; ms.forEach(m=>{m.fog=false;m.needsUpdate=true;});} }); },
  faronly: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees) { e.near.visible=false; e.trunk&&(e.trunk.visible=false); } },
  nearonly: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees) e.far.visible=false; },
  notint: () => { const v=window.__lemWorld.subsystems.get('vegetation');
    for (const e of v.trees){ for(const mesh of [e.near,e.far]){ const a=mesh.geometry.getAttribute('aVegTint'); a.array.fill(1); a.needsUpdate=true; } } },
};
const which = (process.argv[3]||'base').split(',');
for (const name of which) {
  await p.evaluate(cases[name] ? `(${cases[name].toString()})()` : '0');
  await p.waitForTimeout(700);
  const buf = await p.screenshot({clip: CROP});
  fs.writeFileSync(`/tmp/ab-${name}.png`, buf);
  console.log('wrote /tmp/ab-'+name+'.png');
}
await b.close();
