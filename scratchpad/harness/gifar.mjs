/* Dump the far-cascade state and, optionally, paint one of its terms straight
 * out of every standard material.  node gifar.mjs URL OUT.png [term|near|coord] */
import {chromium} from 'playwright';
const MODE = process.argv[4] || 'state';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 300)); });
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(4500);

const state = await p.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  const u = gi.uniforms;
  return {
    casters: gi._farCasters.length, farCost: gi._farCost, farTris: gi._farTris, farDirty: gi._farDirty,
    rt: !!gi._farRT, rtSize: gi._farRT?.width,
    mapBound: !!u.lemFarMap.value, farBias: u.lemFarBias.value,
    farNormalBias: u.lemFarNormalBias.value,
    nearR: u.lemNearRadius.value, nearC: u.lemNearCentre.value.toArray().map(v => +v.toFixed(1)),
    right: u.lemLightRight.value.toArray().map(v => +v.toFixed(2)),
    up: u.lemLightUp.value.toArray().map(v => +v.toFixed(2)),
    sceneEV: gi._sceneEV, exposure: gi.exposure, analytic: gi.analyticExposure,
    modeKey: gi._modeKey, mats: gi.materials.size,
    camPos: window.__lemWorld.camera.position.toArray().map(v => +v.toFixed(0)),
    comp: (() => {
      const c = window.__lemWorld.engine._passes.composite.material.uniforms;
      const o = {};
      for (const k of ['uExposure', 'uBlackPoint', 'uWhitePoint', 'uToe', 'uContrast',
                       'uSaturation', 'uVignette', 'uAOStrength', 'uFilmGrain', 'uBloom']) {
        o[k] = c[k]?.value;
      }
      o.uLift = c.uLift?.value?.toArray?.();
      return o;
    })(),
    aoStrength: u.lemAOStrength.value, giStrength: u.lemGIStrength.value,
    sunI: gi.sunIntensity, sceneIrr: gi.sceneIrradiance, fillE: gi._fillE, keyE: gi._keyE,
  };
});
console.log(JSON.stringify(state, null, 1));

if (MODE !== 'state') {
  const body = MODE === 'near'
    ? 'outgoingLight = vec3( lemNearWeight( vLemWorld ) );'
    : MODE === 'coord'
    ? `vec4 dc = lemFarMatrix * vec4( vLemWorld, 1.0 ); vec3 dp = dc.xyz / dc.w;
       outgoingLight = vec3( dp.xy, 0.0 );`
    : MODE === 'map'
    ? `vec4 dc = lemFarMatrix * vec4( vLemWorld, 1.0 ); vec3 dp = dc.xyz / dc.w;
       outgoingLight = vec3( lemUnpackDepth( texture2D( lemFarMap, dp.xy ) ) );`
    : MODE === 'depth'
    ? `vec4 dc = lemFarMatrix * vec4( vLemWorld, 1.0 ); vec3 dp = dc.xyz / dc.w;
       outgoingLight = vec3( dp.z );`
    : `outgoingLight = vec3( lemFarShadow( vLemWorld,
         normalize( cross( dFdx( vLemWorld ), dFdy( vLemWorld ) ) ) ) );`;
  console.log(JSON.stringify(await p.evaluate(async b => {
    const w = window.__lemWorld;
    let n = 0;
    const seen = new Set();
    w.scene.traverse(o => {
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) {
        if (!m || !m.isMeshStandardMaterial || seen.has(m.uuid)) continue;
        seen.add(m.uuid);
        const prev = m.onBeforeCompile;
        m.onBeforeCompile = (sh, r) => {
          prev?.call(m, sh, r);
          sh.fragmentShader = sh.fragmentShader.replace('#include <opaque_fragment>',
            `\n#ifdef LEM_FAR_SHADOW\n${b}\n#endif\n#include <opaque_fragment>`);
        };
        m.customProgramCacheKey = () => 'gifar-dbg';
        m.needsUpdate = true;
        n++;
      }
    });
    await new Promise(r => setTimeout(r, 900));
    return {patched: n};
  }, body)));
  await p.waitForTimeout(1200);
}
await p.screenshot({path: process.argv[3]});
await b.close();
