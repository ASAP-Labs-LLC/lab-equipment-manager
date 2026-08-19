import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
const src = await p.evaluate(()=>{
  const w=window.__lemWorld, R=w.engine.renderer, gl=R.getContext();
  const v=w.subsystems.get('vegetation');
  const prog = R.properties.get(v.matGrass).currentProgram;
  for (const s of gl.getAttachedShaders(prog.program)) {
    if (gl.getShaderParameter(s, gl.FRAGMENT_SHADER===undefined?0:gl.SHADER_TYPE)===gl.FRAGMENT_SHADER) return gl.getShaderSource(s);
  }
  return 'none';
});
const i = src.indexOf('vegLod');
fs.writeFileSync('/tmp/frag.glsl', src);
console.log(src.slice(Math.max(0,i-1500), i+1600));
await b.close();
