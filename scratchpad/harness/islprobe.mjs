import {chromium} from 'playwright';
const url='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&quality=ultra&time=16&weather=clear';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:800,height:450}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
console.log(await p.evaluate(()=>{
  const w=window.__lemWorld, t=w.subsystems.get('terrain');
  const sc=w.ctx.scene;
  const o=t.meshes.find(m=>m.name==='terrain-ocean');
  const fog=sc.fog?{type:sc.fog.type||sc.fog.constructor.name,color:sc.fog.color.getHexString(),density:sc.fog.density,near:sc.fog.near,far:sc.fog.far}:null;
  const g=o.geometry;
  const pos=g.attributes.position, dep=g.attributes.aDepth;
  // sample depth at a few radii
  const out=[];
  for (const rTest of [500,1500,2500,5000,12000,29000]) {
    let best=null,bd=1e9;
    for(let i=0;i<pos.count;i++){
      const r=Math.hypot(pos.getX(i)-t.cx,pos.getZ(i)-t.cz);
      if(Math.abs(r-rTest)<bd){bd=Math.abs(r-rTest);best=i;}
    }
    out.push([rTest, +dep.getX(best).toFixed(1)]);
  }
  return JSON.stringify({fog, oceanVisible:o.visible, mat:{transparent:o.material.transparent,depthTest:o.material.depthTest,depthWrite:o.material.depthWrite,fog:o.material.fog}, camFar:w.ctx.camera.far, depths:out, envSet: !!sc.environment});
}));
await b.close();
