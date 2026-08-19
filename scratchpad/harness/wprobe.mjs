import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0,300)));
p.on('console', m => { if (m.type()==='error'||m.type()==='warning') console.log('CONSOLE', m.type(), m.text().slice(0,300)); });
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3500);
const res = await p.evaluate(() => {
  const w = window.__lemWorld;
  const terr = w.subsystems.get('terrain');
  const mesh = terr && terr.water;
  const sh = terr && terr._waterShader;
  const frag = sh ? sh.fragmentShader : '';
  const vert = sh ? sh.vertexShader : '';
  const r = w.engine.renderer, gl = r.getContext();
  let progFrag = '';
  for (const prog of r.info.programs) {
    if ((prog.cacheKey||'').includes('terrain-water')) {
      try { progFrag = gl.getShaderSource(prog.fragmentShader) || ''; } catch(e){}
    }
  }
  return {
    hasMesh: !!mesh,
    visible: mesh ? mesh.visible : null,
    inScene: mesh ? !!mesh.parent : null,
    matType: mesh ? mesh.material.type : null,
    envAmt: terr?._waterUniforms?.uEnvAmt?.value,
    sceneEnv: !!w.scene.environment,
    envIntensity: mesh ? mesh.material.envMapIntensity : null,
    obcRan: !!sh,
    spliceMap: /A shore is where the bed comes up/.test(frag),
    spliceRough: /gWaterRough, 0.02/.test(frag),
    spliceNormal: /viewMatrix \* vec4\(gWaterN/.test(frag),
    spliceOpaque: /What a grazing ray actually hits/.test(frag),
    vertSplice: /vWaterW = /.test(vert),
    leftoverMap: /#include <map_fragment>/.test(frag),
    leftoverNormalMaps: /#include <normal_fragment_maps>/.test(frag),
    leftoverRough: /#include <roughnessmap_fragment>/.test(frag),
    progHasShore: /A shore is where the bed/.test(progFrag),
    progLen: progFrag.length,
    progHasEnv: /USE_ENVMAP/.test(progFrag),
    progHasIBL: /getIBLRadiance/.test(progFrag),
    progHasLemGI: /lemIndirect/.test(progFrag),
    waterY: terr?.waterY, waterLevel: terr?.waterLevel,
    camPos: w.camera.position.toArray().map(v=>Math.round(v)),
  };
});
console.log(JSON.stringify(res, null, 1));
if (process.argv[3]) {
  const src = await p.evaluate(() => {
    const r = window.__lemWorld.engine.renderer, gl = r.getContext();
    for (const prog of r.info.programs)
      if ((prog.cacheKey||'').includes('terrain-water')) return gl.getShaderSource(prog.fragmentShader)||'';
    return '';
  });
  fs.writeFileSync(process.argv[3], src);
  console.log('wrote', process.argv[3], src.length);
}
await b.close();
