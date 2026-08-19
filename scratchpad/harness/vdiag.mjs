import {chromium} from 'playwright';
const url = process.argv[2];
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
p.on('console', m=>{ if(m.type()==='error') console.log('[err] '+m.text()); });
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(2500);
const r = await p.evaluate(async ()=>{
  const THREE = await import('three');
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const gi = w.subsystems.get('gi');
  const cam = w.engine.camera;
  const out = {};
  out.cam = {pos:[+cam.position.x.toFixed(1),+cam.position.y.toFixed(1),+cam.position.z.toFixed(1)],
             fov:cam.fov, far:cam.far};
  // terrain extent probe along the view direction
  const dir = new THREE.Vector3(); cam.getWorldDirection(dir);
  const ray = [];
  for (let d=50; d<=2000; d+=50) {
    const x = cam.position.x + dir.x*d, z = cam.position.z + dir.z*d;
    const h = w.ground ? w.ground(x,z) : (v.ctx.ground?v.ctx.ground(x,z):null);
    // screen y of that ground point
    const pt = new THREE.Vector3(x,h,z).project(cam);
    ray.push({d, h:+h.toFixed(1), sy: Math.round((1-pt.y)*0.5*1080)});
  }
  out.groundRay = ray;

  // far instances actually drawn: distance, base/top screen y, ground under
  const v3 = new THREE.Vector3();
  const samples = [];
  let farTot=0, nearTot=0;
  const dists=[];
  for (const e of v.trees) {
    farTot += e.far.count; nearTot += e.near.count;
    const arr = e.far.instanceMatrix.array;
    for (let i=0;i<e.far.count;i++){
      const x=arr[i*16+12], y=arr[i*16+13], z=arr[i*16+14];
      const sy = Math.abs(arr[i*16+5]); // scale y column
      const H = e.spec.refH * sy;
      const dist = Math.hypot(x-cam.position.x, z-cam.position.z);
      dists.push(dist);
      if (samples.length<4000) {
        const g = v.ctx.ground(x,z);
        const base = v3.set(x,y,z).clone().project(cam);
        const top = v3.set(x,y+H,z).clone().project(cam);
        const gp = v3.set(x,g,z).clone().project(cam);
        samples.push({d:Math.round(dist), y:+y.toFixed(1), g:+g.toFixed(1), H:+H.toFixed(1),
          by:Math.round((1-base.y)*0.5*1080), ty:Math.round((1-top.y)*0.5*1080),
          gy:Math.round((1-gp.y)*0.5*1080),
          px:Math.round((base.x*0.5+0.5)*1920)});
      }
    }
  }
  dists.sort((a,b)=>a-b);
  out.counts={far:farTot, near:nearTot};
  out.farDist={p10:Math.round(dists[(dists.length*0.1)|0]||0), p50:Math.round(dists[(dists.length*0.5)|0]||0),
               p90:Math.round(dists[(dists.length*0.9)|0]||0), min:Math.round(dists[0]||0), max:Math.round(dists[dists.length-1]||0)};
  // in-frustum ones only, sorted by screen y of base
  const vis = samples.filter(s=>s.px>0&&s.px<1920&&s.by>-200&&s.by<1400);
  vis.sort((a,b)=>a.d-b.d);
  out.visSample = [0,0.1,0.25,0.5,0.75,0.9,0.99].map(f=>vis[(vis.length*f)|0]).filter(Boolean);
  out.visN = vis.length;
  // gaps: base screen y vs ground screen y
  const gap = vis.map(s=>s.by-s.gy);
  gap.sort((a,b)=>a-b);
  out.baseVsGroundPx = {p10:gap[(gap.length*0.1)|0], p50:gap[(gap.length*0.5)|0], p90:gap[(gap.length*0.9)|0]};
  const dy = vis.map(s=>+(s.y-s.g).toFixed(2)); dy.sort((a,b)=>a-b);
  out.baseVsGroundM = {min:dy[0], p50:dy[(dy.length*0.5)|0], max:dy[dy.length-1]};

  // material state
  const mstate = m => m ? ({
    reg: !!(gi && gi.materials && gi.materials.has(m)),
    env: m.envMapIntensity, base: m.userData?.lemEnvBase,
    envU: m.userData?.lemEnvU?.value,
    defines: Object.keys(m.defines||{}),
    key: (()=>{try{return m.customProgramCacheKey()}catch(e){return 'ERR'}})(),
    prog: !!m.__prog,
  }) : null;
  out.matNear = mstate(v.matNear);
  out.matFar = mstate(v.matFar);
  out.matBark = mstate(v.matBark);
  out.scene = {envInt: w.engine.scene.environmentIntensity, hasEnv: !!w.engine.scene.environment,
               fog: w.engine.scene.fog ? {c:'#'+w.engine.scene.fog.color.getHexString(), d:w.engine.scene.fog.density} : null};
  // compiled program identity — do the two materials share a program?
  const progs = [];
  w.engine.renderer.info.programs?.forEach(pr=>progs.push({key:pr.cacheKey.slice(0,120), used:pr.usedTimes}));
  out.progs = progs.filter(x=>x.key.includes('lem-veg'));
  return out;
});
console.log(JSON.stringify(r,null,1));
await b.close();
