import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation'), r=w.engine.renderer;
  const names={matNear:'near',matFar:'far',matBark:'bark',matProp:'prop',matRock:'rock',matClutter:'clut',matGrass:'grass'};
  const out={};
  for (const k in names){
    const m=v[k]; if(!m) continue;
    const props=r.properties.get(m);
    const prog=props.currentProgram;
    out[names[k]]={progId:prog?prog.id:null, key:prog?prog.cacheKey:null,
      hasSSS: prog? /vegPass/.test(prog.fragmentShader||''):null,
      hasAlpha: prog? /vegThr|vegLod/.test(prog.fragmentShader||''):null,
      sss: m.userData.lem?.uVegSSS?.value, env:m.envMapIntensity,
      uniSSS: props.uniforms?.uVegSSS?.value};
  }
  out.__programs = r.info.programs.length;
  return out;
}),null,1));
await b.close();
