import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(1500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation'), cam=w.engine.camera;
  const fbm=v.ctx.Tex.fbm;
  const noise=(x,z,s,sc)=>fbm(x*sc,z*sc,{octaves:3,period:8,seed:s});
  const sm=(a,bb,x)=>{const t=Math.max(0,Math.min(1,(x-a)/(bb-a)));return t*t*(3-2*t);};
  const stats={n:0,standSum:0,standMin:9,standMax:-9,dSum:0,zero:0,bins:[0,0,0,0,0]};
  const acc={water:0,site:0,open:0,slope:0,alt:0,spec:0,ok:0,stand0:0};
  for(let j=-25;j<=25;j++)for(let i=-25;i<=25;i++){
    const x=cam.position.x+i*8, z=cam.position.z+j*8;
    const stand=noise(x,z,7,0.0018), grain=noise(x,z,23,0.011);
    stats.n++; stats.standSum+=stand; stats.standMin=Math.min(stats.standMin,stand); stats.standMax=Math.max(stats.standMax,stand);
    let d=sm(0.14,0.34,stand)*(0.72+0.55*grain);
    if(d<=0.02){acc.stand0++; stats.zero++; continue;}
    const site=v._site(x,z); if(!site){acc.water++;continue;}
    d*=v._openness(x,z); if(d<=0.02){acc.open++;continue;}
    d*=1-sm(0.45,0.95,site.slope);
    if(v.relief>25) d*=1-sm(0.70,0.94,site.alt);
    if(d<=0.02){acc.slope++;continue;}
    stats.dSum+=d; acc.ok++;
    stats.bins[Math.min(4,Math.floor(d*5))]++;
  }
  return {standMean:+(stats.standSum/stats.n).toFixed(3), standMin:+stats.standMin.toFixed(3), standMax:+stats.standMax.toFixed(3),
          cells:stats.n, acc, meanD:+(stats.dSum/Math.max(1,acc.ok)).toFixed(3), dBins:stats.bins,
          placed:v.trees.reduce((a,e)=>a+e.list.length,0), cap:v._treeBudget};
}),null,1));
await b.close();
