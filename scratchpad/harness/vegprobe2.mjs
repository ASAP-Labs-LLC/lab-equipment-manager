import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2000);
const r = await p.evaluate(()=>{
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const cam = w.engine.camera;
  // walk a grid around the camera and report why trees are refused
  const reasons = {water:0, stand:0, site:0, open:0, slope:0, alt:0, ok:0};
  const step=10; let hs=[];
  for(let j=-30;j<=30;j++) for(let i=-30;i<=30;i++){
    const x=cam.position.x+i*step, z=cam.position.z+j*step;
    const h=v._ground(x,z); hs.push(h);
    if(h<v.waterLevel){reasons.water++;continue;}
    const s=v._site(x,z); if(!s){reasons.site++;continue;}
    if(v._openness(x,z)<0.2){reasons.open++;continue;}
    if(s.slope>1.0){reasons.slope++;continue;}
    reasons.ok++;
  }
  hs.sort((a,b)=>a-b);
  // distribution of tree distances from camera
  const dists=[];
  for (const e of v.trees) for (let i=0;i<e.list.length;i++){
    dists.push(Math.hypot(e.xs[i]-cam.position.x, e.zs[i]-cam.position.z));
  }
  dists.sort((a,b)=>a-b);
  const pick=f=>Math.round(dists[Math.floor(dists.length*f)]||0);
  const meshInfo = v.meshes.filter(m=>m.count>0).map(m=>({n:m.count, tri:(m.geometry.index.count/3)*m.count|0, cast:m.castShadow, mat:m.material===v.matNear?'near':m.material===v.matFar?'far':m.material===v.matBark?'bark':m.material===v.matClutter?'clut':m.material===v.matGrass?'grass':m.material===v.matRock?'rock':'prop'}));
  const byMat={};
  for(const m of meshInfo){byMat[m.mat]=byMat[m.mat]||{meshes:0,inst:0,tri:0}; byMat[m.mat].meshes++; byMat[m.mat].inst+=m.n; byMat[m.mat].tri+=m.tri;}
  return {waterLevel:v.waterLevel, hMin:+v.hMin.toFixed(1), hMax:+v.hMax.toFixed(1), relief:+v.relief.toFixed(1), flat:v.flat,
          camH:+v._ground(cam.position.x,cam.position.z).toFixed(1),
          gridH:[hs[0],hs[(hs.length/2)|0],hs[hs.length-1]].map(x=>+x.toFixed(1)),
          reasons, treeDist:{p0:pick(0),p10:pick(.1),p25:pick(.25),p50:pick(.5),p90:pick(.9)},
          byMat, activeDraws:meshInfo.length};
});
console.log(JSON.stringify(r,null,1));
await b.close();
