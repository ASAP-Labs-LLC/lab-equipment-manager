import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(3000);
const r = await p.evaluate(()=>{
  const w = window.__lemWorld;
  const R = w.engine.renderer;
  const keys = [];
  R.info.programs.forEach(pr=>keys.push(pr.cacheKey ? pr.cacheKey.slice(0,120) : '?'));
  const v = w.subsystems.get('vegetation');
  // pull the real compiled source off the material's program
  const out = {keys, mats:[]};
  const gl = R.getContext();
  for (const [name, m] of [['near',v.matNear],['far',v.matFar],['bark',v.matBark],['grass',v.matGrass]]) {
    const rec = {name, hasPatch: !!m.onBeforeCompile};
    try {
      const prog = R.properties.get(m).currentProgram;
      rec.found = !!prog;
      if (prog) {
        const sh = gl.getAttachedShaders(prog.program);
        for (const s of sh) {
          const src = gl.getShaderSource(s);
          const isFrag = gl.getShaderParameter(s, gl.SHADER_TYPE) === gl.FRAGMENT_SHADER;
          if (isFrag) {
            rec.fragLen = src.length;
            rec.sss = src.includes('vegPass');
            rec.tint = src.includes('vVegTint * vVegAO');
            rec.alpha = src.includes('vegLod');
            rec.fade = src.includes('vegVis');
            rec.opaqueFragmentPresent = src.includes('gl_FragColor = vec4( outgoingLight');
            fs.writeFileSync;
            rec.tail = src.slice(src.length-1400);
          } else {
            rec.vertWind = src.includes('vegSway');
            rec.vertNW = src.includes('vVegNW = normalize');
            rec.vertDist = src.includes('vVegDist = length');
          }
        }
      }
    } catch(e){ rec.err = String(e); }
    out.mats.push(rec);
  }
  return out;
});
fs.writeFileSync('/Users/rynatical/LAB-lem/scratchpad/harness/shaderprobe.json', JSON.stringify(r,null,1));
console.log(JSON.stringify(r.mats.map(m=>({...m, tail:undefined})),null,1));
await b.close();
