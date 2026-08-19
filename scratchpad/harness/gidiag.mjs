import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('console', m => { if (m.type() === 'warning' || m.type() === 'error') console.log('[page]', m.text()); });
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(3500);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, s = w.scene, gi = w.subsystems.get('gi');
  const r = w.renderer || window.__lemRenderer;
  const out = {mats: [], programs: []};
  const seen = new Set();
  s.traverse(o => {
    if (!o.material) return;
    const ms = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of ms) {
      if (seen.has(m.uuid)) continue; seen.add(m.uuid);
      const props = r ? r.properties.get(m) : null;
      const prog = props?.currentProgram;
      out.mats.push({
        name: m.name || o.name || m.type,
        type: m.type,
        std: !!m.isMeshStandardMaterial,
        inGI: gi ? gi.materials.has(m) : null,
        defines: Object.keys(m.defines || {}),
        key: (() => { try { return m.customProgramCacheKey ? m.customProgramCacheKey() : ''; } catch (e) { return 'ERR'; } })(),
        envMapIntensity: m.envMapIntensity,
        colour: m.color ? '#' + m.color.getHexString() : null,
        alphaTest: m.alphaTest,
        progId: prog ? prog.id : null,
        hasGI: prog ? /lemIndirect/.test(prog.fragmentShader || '') : null,
        hasIblPatch: prog ? /lemIblDiffuse/.test(prog.fragmentShader || '') : null,
        hasAO: prog ? /lemAOMap/.test(prog.fragmentShader || '') : null,
        hasVeg: prog ? /uVegSSS/.test(prog.fragmentShader || '') : null,
        layerFar: o.layers.isEnabled(6),
        castShadow: o.castShadow,
        bsr: o.geometry?.boundingSphere?.radius,
      });
    }
  });
  if (gi) {
    out.gi = {
      giScale: gi.giScale, fillE: gi._fillE, keyE: gi._keyE,
      sky: gi.uniforms.lemSkyIrradiance.value.toArray().map(v=>+v.toFixed(4)),
      ground: gi.uniforms.lemGroundIrradiance.value.toArray().map(v=>+v.toFixed(4)),
      zenith: gi.zenith.toArray().map(v=>+v.toFixed(3)),
      horizon: gi.horizon.toArray().map(v=>+v.toFixed(3)),
      groundAlbedo: gi.groundAlbedo.toArray().map(v=>+v.toFixed(3)),
      iblDiffuse: gi.uniforms.lemIblDiffuse.value,
      aoStrength: gi.uniforms.lemAOStrength.value,
      aoFloor: gi.uniforms.lemAOFloor.value,
      aoMap: !!gi.uniforms.lemAOMap.value,
      farCasters: gi._farCasters.length,
      farNames: gi._farCasters.slice(0, 40).map(o => (o.name || o.parent?.name || o.type) + '/' + (o.userData.lemFarSize|0)),
      exposure: gi.exposure,
      envIntensity: gi._envFactor(),
    };
    // probe irradiance at a few normals, at the camera
    const c = w.camera; const T = window.THREE_NS;
    out.irr = {};
    const at = [c.position.x, c.position.y, c.position.z];
    for (const [k, n] of Object.entries({up:[0,1,0], down:[0,-1,0], north:[0,0,-1], south:[0,0,1]})) {
      const o2 = gi.irradianceAt(at[0], at[1], at[2], {x:n[0],y:n[1],z:n[2]});
      out.irr[k] = [o2.r, o2.g, o2.b].map(v=>+v.toFixed(4));
    }
    void T;
  }
  return out;
}), null, 1));
await b.close();
