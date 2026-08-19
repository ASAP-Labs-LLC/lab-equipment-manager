import {chromium} from 'playwright';
import fs from 'node:fs';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
const r = await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation'), rr=w.engine.renderer;
  const gl=rr.getContext();
  const grab=(m)=>{
    const prog=rr.properties.get(m).currentProgram; if(!prog) return null;
    const sh=gl.getAttachedShaders(prog.program)||[];
    const src=sh.map(s=>gl.getShaderSource(s));
    const fs=src.find(s=>/gl_FragColor|pc_fragColor/.test(s))||'';
    const vs=src.find(s=>s!==fs)||'';
    return {fs,vs};
  };
  const out={};
  for (const k of ['matNear','matFar','matBark','matRock']){
    const g=grab(v[k]); if(!g){out[k]=null;continue;}
    out[k]={
      markers:{vegSway:/vegSway/.test(g.vs), vVegNW:/vVegNW/.test(g.vs), vVegDist:/vVegDist/.test(g.vs),
               vegThr:/vegThr/.test(g.fs), vegPass:/vegPass/.test(g.fs), vVegTint:/vVegTint \* vVegAO/.test(g.fs),
               vegVis:/vegVis/.test(g.fs), normOverride:/normal = normalize\( *vNormal *\)|normal = normalize\(vNormal\)/.test(g.fs),
               roughWet:/uVegWet \* 0\.85/.test(g.fs),
               giIndirect:/lemIndirect/.test(g.fs), giIbl:/lemIblDiffuse/.test(g.fs)},
      fsLen:g.fs.length};
    if(k==='matNear') out.__dump=g.fs;
  }
  return out;
});
fs.writeFileSync('/tmp/near.frag', r.__dump||''); delete r.__dump;
console.log(JSON.stringify(r,null,1));
await b.close();
