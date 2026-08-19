import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation');
  const cx=v._lastGrass.x, cz=v._lastGrass.z;
  let cells=0, cellsOpen=0, cand=0, outR=0, noSite=0, steep=0, dieRej=0, ok=0;
  const C=Math.ceil(125/8);
  const ci=Math.round(cx/8), cj=Math.round(cz/8);
  for(let j=-C;j<=C;j++)for(let i=-C;i<=C;i++){
    const gx=(ci+i)*8, gz=(cj+j)*8;
    if(Math.hypot(gx-cx,gz-cz)>125+8) continue;
    cells++;
    const open=v._openness(gx,gz,true);
    if(open<0.1) continue;
    cellsOpen++;
    for(let k=0;k<26;k++){
      cand++;
      const x=gx+Math.random()*8, z=gz+Math.random()*8;
      const d=Math.hypot(x-cx,z-cz);
      if(d>125){outR++;continue;}
      const s=v._site(x,z);
      if(!s){noSite++;continue;}
      if(s.slope>0.85){steep++;continue;}
      ok++;
    }
  }
  return {cx,cz,cells,cellsOpen,cand,outR,noSite,steep,ok,relief:v.relief,flat:v.flat,water:v.waterLevel,grass:v.grass.count};
})));
await b.close();
