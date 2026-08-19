/* csmpaint.mjs — paint one cascade term straight out of every standard
 * material, so what the selector is actually returning can be looked at.
 *   node csmpaint.mjs URL OUT.png [term|near|box0|c0|c1|coord0] */
import {chromium} from 'playwright';
const MODE = process.argv[4] || 'term';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 400)));
p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 400)); });
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(5000);

const body = {
  term: 'outgoingLight = vec3( lemFarShadow( vLemWorld, lemWorldNormal ) );',
  near: 'outgoingLight = vec3( lemNearWeight( vLemWorld ) );',
  box0: 'outgoingLight = vec3( lemBoxWeight( vLemWorld, lemCsmBox0.xyz, lemCsmBox0.w ) );',
  c0: 'outgoingLight = vec3( lemCascade( vLemWorld, lemWorldNormal, lemCsmMap0, lemCsmMat0, lemCsmParam0 ) );',
  c1: 'outgoingLight = vec3( lemCascade( vLemWorld, lemWorldNormal, lemCsmMap1, lemCsmMat1, lemCsmParam1 ) );',
  coord0: `vec4 dc = lemCsmMat0 * vec4( vLemWorld, 1.0 ); vec3 dp = dc.xyz / dc.w;
           outgoingLight = vec3( dp.xy, 0.0 );`,
  coord1: `vec4 dc = lemCsmMat1 * vec4( vLemWorld, 1.0 ); vec3 dp = dc.xyz / dc.w;
           outgoingLight = vec3( dp.xy, 0.0 );`,
  sel: `float wN = lemNearWeight( vLemWorld );
        float w0 = lemBoxWeight( vLemWorld, lemCsmBox0.xyz, lemCsmBox0.w );
        outgoingLight = vec3( wN, ( 1.0 - wN ) * w0, ( 1.0 - wN ) * ( 1.0 - w0 ) );`,
  map1: `vec4 dc = lemCsmMat1 * vec4( vLemWorld, 1.0 ); vec3 dp = dc.xyz / dc.w;
         outgoingLight = vec3( lemUnpackDepth( texture2D( lemCsmMap1, dp.xy ) ) );`,
}[MODE];

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
      m.customProgramCacheKey = () => 'csm-dbg';
      m.needsUpdate = true;
      n++;
    }
  });
  await new Promise(r => setTimeout(r, 900));
  return {patched: n};
}, body)));
await p.waitForTimeout(1500);
await p.screenshot({path: process.argv[3]});
await b.close();
