import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3500);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, r = w.engine.renderer;
  const out = {lights: [], receivers: [], uniformState: null};
  w.scene.traverse(o => {
    if (o.isLight) {
      const s = o.shadow;
      out.lights.push({type:o.type, name:o.name, visible:o.visible, castShadow:o.castShadow,
        intensity:+o.intensity.toFixed(3), color:o.color?.getHexString(),
        layers:o.layers.mask,
        shadow: s ? {intensity: s.intensity, bias: s.bias, normalBias: s.normalBias,
          radius: s.radius, mapSize:[s.mapSize.width,s.mapSize.height],
          hasMap: !!s.map, mapSizeActual: s.map ? [s.map.width, s.map.height] : null,
          autoUpdate: s.autoUpdate, needsUpdate: s.needsUpdate,
          camNear: s.camera.near, camFar: s.camera.far,
          camLRTB: [s.camera.left,s.camera.right,s.camera.top,s.camera.bottom],
          matrixEl: Array.from(s.matrix.elements).map(v=>+v.toFixed(3)).slice(0,4)} : null});
    }
  });
  // three's own light state as the renderer saw it last frame
  const rs = r.properties;
  out.renderLists = null;
  // Peek at three's currentRenderState via a hack: re-render and inspect uniforms of a program
  const gl = r.getContext();
  let prog = null;
  for (const pr of r.info.programs) {
    const src = gl.getShaderSource(pr.fragmentShader) || '';
    if (/lemIndirect/.test(src) && /directionalShadowMap/.test(src)) { prog = pr; break; }
  }
  if (prog) {
    const P = prog.program;
    const n = gl.getProgramParameter(P, gl.ACTIVE_UNIFORMS);
    const vals = {};
    for (let i = 0; i < n; i++) {
      const info = gl.getActiveUniform(P, i);
      if (!/receiveShadow|directionalLightShadows|directionalLights|ambientLightColor|lemGIStrength|lemIblDiffuse|envMapIntensity|directionalShadowMap/.test(info.name)) continue;
      const loc = gl.getUniformLocation(P, info.name);
      if (!loc) continue;
      try { const v = gl.getUniform(P, loc); vals[info.name] = ArrayBuffer.isView(v) ? Array.from(v).map(x=>+(+x).toFixed(4)) : v; }
      catch(e) { vals[info.name] = 'ERR'; }
    }
    out.uniformState = vals;
    out.progIsCurrent = gl.getParameter(gl.CURRENT_PROGRAM) === P;
  }
  let n = 0;
  w.scene.traverse(o => { if (o.isMesh && n < 12) { out.receivers.push({name:o.name||o.parent?.name||'?', rs:o.receiveShadow, cs:o.castShadow, mat:o.material?.type, layers:o.layers.mask}); n++; } });
  out.cameraLayers = w.engine.camera.layers.mask;
  return out;
}), null, 1));
await b.close();
