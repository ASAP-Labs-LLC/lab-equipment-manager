/* Does killing the env-map's diffuse contribution (what gi.js believes it is
 * already doing) bring the shadows back? */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1600,height:900}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
await p.screenshot({path: process.argv[3]});
console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld;
  let n = 0, hit = 0;
  const seen = new Set();
  w.scene.traverse(o => {
    if (!o.isMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m || !m.isMeshStandardMaterial || seen.has(m.uuid)) continue;
      seen.add(m.uuid); n++;
      const prev = m.onBeforeCompile;
      m.onBeforeCompile = (sh, r) => {
        prev?.call(m, sh, r);
        const before = sh.fragmentShader;
        sh.fragmentShader = sh.fragmentShader.replace(
          '#include <lights_fragment_maps>',
          '#include <lights_fragment_maps>\n\tiblIrradiance *= lemIblDiffuse;');
        if (sh.fragmentShader !== before) hit++;
        // also report whether the ORIGINAL gi.js target string was ever present
        sh.__lemHadRawTarget = /iblIrradiance \+= getIBLIrradiance\( geometryNormal \);/.test(before);
        window.__rawTargetPresent = (window.__rawTargetPresent || 0) + (sh.__lemHadRawTarget ? 1 : 0);
      };
      m.customProgramCacheKey = () => 'ibltest';
      m.needsUpdate = true;
    }
  });
  await new Promise(res => setTimeout(res, 900));
  return {materials: n, patchHit: hit, rawTargetPresentInSource: window.__rawTargetPresent || 0};
})));
await p.waitForTimeout(1500);
await p.screenshot({path: process.argv[4]});
await b.close();
