import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3500);
const res = await p.evaluate(() => {
  const w = window.__lemWorld, r = w.engine.renderer, gl = r.getContext();
  const out = {programs: [], lightState: null, dumps: []};
  for (const prog of r.info.programs) {
    let fs_ = '';
    try { fs_ = gl.getShaderSource(prog.fragmentShader) || ''; } catch (e) { fs_ = 'ERR ' + e; }
    out.programs.push({
      name: prog.name, usedTimes: prog.usedTimes,
      cacheKey: (prog.cacheKey || '').slice(0, 160),
      hasUSE_SHADOWMAP: /#define USE_SHADOWMAP/.test(fs_),
      numDirShadows: (fs_.match(/#define NUM_DIR_LIGHT_SHADOWS (\d+)/) || [])[1] ?? null,
      numDirLights: (fs_.match(/#define NUM_DIR_LIGHTS (\d+)/) || [])[1] ?? null,
      hasGetShadow: /getShadow\s*\(/.test(fs_),
      shadowCalls: (fs_.match(/getShadow\s*\(/g) || []).length,
      hasShadowmapPars: /shadow_pars_fragment|directionalShadowMap|sampler2DShadow|shadowIntensity/.test(fs_),
      hasReceiveShadowUniform: /uniform bool receiveShadow/.test(fs_),
      lemGI: /lemIndirect/.test(fs_),
      len: fs_.length,
    });
  }
  // find the terrain-ish and a building material program, dump the lights block
  const wanted = [];
  w.scene.traverse(o => {
    if (!o.isMesh || !o.material || !o.material.isMeshStandardMaterial) return;
    if (wanted.length < 400) wanted.push(o);
  });
  const seen = new Set();
  for (const o of wanted) {
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (seen.has(m.uuid)) continue;
      seen.add(m.uuid);
      out.dumps.push({name: o.name || o.parent?.name || '?', mat: m.name || m.type,
        receiveShadowObj: o.receiveShadow, castShadowObj: o.castShadow,
        matUuid: m.uuid.slice(0,8), defines: Object.keys(m.defines || {}),
        noGI: !!m.userData?.noGI, hasOBC: !!m.onBeforeCompile,
        progCacheKey: (m.customProgramCacheKey ? m.customProgramCacheKey() : null)});
      if (out.dumps.length > 40) break;
    }
  }
  return out;
});
console.log(JSON.stringify(res, null, 1).slice(0, 12000));
// Now dump one full fragment shader that contains lemIndirect
const src = await p.evaluate(() => {
  const r = window.__lemWorld.engine.renderer, gl = r.getContext();
  let best = null;
  for (const prog of r.info.programs) {
    const s = gl.getShaderSource(prog.fragmentShader) || '';
    if (/lemIndirect/.test(s) && (!best || s.length > best.length)) best = s;
  }
  return best;
});
if (src) fs.writeFileSync(process.argv[3] || '/tmp/frag.glsl', src);
await b.close();
