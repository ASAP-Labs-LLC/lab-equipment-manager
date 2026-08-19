/* vhandoff.mjs — what is actually drawn at each of vcover's camera distances.
 * vcover measures pixels; this measures instances, so a coverage cliff can be
 * told apart from a haze that has swallowed the difference. */
import {chromium} from 'playwright';
const DISTS=(process.argv[2]||'250,500,900,1600,2600').split(',').map(Number);
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=wide&time=16&hud=0',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
await p.evaluate(()=>window.__lemWorld.engine.setQualityMode('ultra'));
await p.waitForTimeout(2000);
const patch=await p.evaluate(()=>{const v=window.__lemWorld.subsystems.get('vegetation');
  const xs=[],zs=[];for(const e of v.trees||[])for(let i=0;i<e.list.length;i++){xs.push(e.xs[i]);zs.push(e.zs[i]);}
  let best=null;const R=130;
  for(let k=0;k<xs.length;k+=Math.max(1,(xs.length/400)|0)){let n=0;
    for(let j=0;j<xs.length;j+=3){const dx=xs[j]-xs[k],dz=zs[j]-zs[k];if(dx*dx+dz*dz<R*R)n++;}
    if(!best||n>best.n)best={x:xs[k],z:zs[k],n:n*3};}
  return best;});
console.log('patch',patch);
for(const d of DISTS){
  const o=await p.evaluate(({x,z,d,ref})=>{
    const w=window.__lemWorld,r=w.rig,cam=w.camera;
    r.maxDistance=Math.max(r.maxDistance||0,8000);
    if(cam.__baseFov===undefined)cam.__baseFov=cam.fov;
    r.goalTarget.set(x,w.ground?w.ground(x,z):0,z);r.target.copy(r.goalTarget);
    r.goalDistance=d;r.distance=d;r.goalYaw=-0.7;r.yaw=-0.7;r.goalPitch=0.30;r.pitch=0.30;r.idleDrift=false;
    const t0=Math.tan(cam.__baseFov*Math.PI/360)*ref/d;
    cam.fov=Math.atan(t0)*360/Math.PI;cam.updateProjectionMatrix();r.apply(1);
    const v=w.subsystems.get('vegetation');v._repartition(true);
    let near=0,far=0,grove=0;
    for(const e of v.trees||[]){near+=e.near.count;far+=e.far.count;}
    for(const g of v.groves||[])grove+=g.mesh.count;
    /* how many stems stand within 130 m of the patch, and how many of them are
       represented by something drawn */
    let inPatch=0,drawnNear=0,drawnFar=0;
    const R=130;
    for(const e of v.trees||[]){
      const M=e.near.instanceMatrix.array,F=e.far.instanceMatrix.array;
      for(let i=0;i<e.list.length;i++){const dx=e.xs[i]-x,dz=e.zs[i]-z;if(dx*dx+dz*dz<R*R)inPatch++;}
      for(let i=0;i<e.near.count;i++){const dx=M[i*16+12]-x,dz=M[i*16+14]-z;if(dx*dx+dz*dz<R*R)drawnNear++;}
      for(let i=0;i<e.far.count;i++){const dx=F[i*16+12]-x,dz=F[i*16+14]-z;if(dx*dx+dz*dz<R*R)drawnFar++;}
    }
    let gInPatch=0;
    for(const g of v.groves||[]){const M=g.mesh.instanceMatrix.array;
      for(let i=0;i<g.mesh.count;i++){const dx=M[i*16+12]-x,dz=M[i*16+14]-z;if(dx*dx+dz*dz<(R+40)*(R+40))gInPatch++;}}
    return {d,fov:+cam.fov.toFixed(2),camY:+cam.position.y.toFixed(0),
            eyeToPatch:+Math.hypot(cam.position.x-x,cam.position.z-z).toFixed(0),
            near,far,grove,inPatch,drawnNear,drawnFar,gInPatch,
            groveR:+(v.groveR||0).toFixed(0)};
  },{x:patch.x,z:patch.z,d,ref:DISTS[0]});
  await p.waitForTimeout(300);
  console.log(JSON.stringify(o));
}
await b.close();
