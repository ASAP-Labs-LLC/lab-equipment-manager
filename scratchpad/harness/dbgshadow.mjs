/* Paint the shadow term (or the shadow coord) straight out of a receiving
 * material, so we can see whether getShadow is returning anything. */
import {chromium} from 'playwright';
const MODE = process.argv[4] || 'term';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,400)));
p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text().slice(0,400)); });
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
const info = await p.evaluate(async (mode) => {
  const w = window.__lemWorld;
  const out = {patched: 0};
  const body = mode === 'coord'
    ? `vec3 dbgc = vDirectionalShadowCoord[0].xyz / vDirectionalShadowCoord[0].w;
       outgoingLight = vec3(dbgc.xy, 0.0);`
    : mode === 'inf'
    ? `vec3 dbgc = vDirectionalShadowCoord[0].xyz / vDirectionalShadowCoord[0].w;
       bool inF = dbgc.x >= 0.0 && dbgc.x <= 1.0 && dbgc.y >= 0.0 && dbgc.y <= 1.0 && dbgc.z <= 1.0;
       outgoingLight = inF ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);`
    : `float dbgS = getShadow( directionalShadowMap[0], directionalLightShadows[0].shadowMapSize,
                              1.0, directionalLightShadows[0].shadowBias,
                              directionalLightShadows[0].shadowRadius, vDirectionalShadowCoord[0] );
       outgoingLight = vec3(dbgS);`;
  const seen = new Set();
  w.scene.traverse(o => {
    if (!o.isMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m || !m.isMeshStandardMaterial || seen.has(m.uuid)) continue;
      seen.add(m.uuid);
      const prev = m.onBeforeCompile;
      m.onBeforeCompile = (sh, r) => {
        prev?.call(m, sh, r);
        sh.fragmentShader = sh.fragmentShader.replace(
          '#include <opaque_fragment>', `\n#ifdef USE_SHADOWMAP\n${body}\n#endif\n#include <opaque_fragment>`);
      };
      m.customProgramCacheKey = () => 'dbg-' + mode;
      m.needsUpdate = true;
      out.patched++;
    }
  });
  await new Promise(res => setTimeout(res, 800));
  return out;
}, MODE);
console.log(JSON.stringify(info));
await p.waitForTimeout(1500);
await p.screenshot({path: process.argv[3]});
await b.close();
