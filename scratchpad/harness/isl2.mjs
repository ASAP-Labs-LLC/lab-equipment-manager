import {chromium} from 'playwright';
const url=process.argv[2]||'http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&hud=0&quality=ultra&time=16&weather=clear&cam=wide';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
p.on('console',m=>{if(m.type()==='error')console.log('ERR',m.text());});
await p.goto(url,{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
console.log(await p.evaluate(()=>{
  const w=window.__lemWorld, t=w.subsystems.get('terrain');
  const cam=w.ctx.camera, pl=w.ctx.plan;
  // where does the coastline sit on each of 8 bearings
  const coast=[];
  for(let i=0;i<64;i++){
    const a=i*Math.PI/32;
    let lo=0,hi=4000;
    for(let k=0;k<40;k++){const m=(lo+hi)/2;
      const x=t.cx+Math.cos(a)*m,z=t.cz+Math.sin(a)*m;
      if(t.heightAt(x,z)>t.waterY) lo=m; else hi=m;}
    coast.push(+lo.toFixed(0));
  }
  return JSON.stringify({
    islandR:t.islandR, wobble:+t.coastWobble.toFixed(1), siteReach:+(t.siteReach||0).toFixed(1),
    coreSize:t.coreSize, ringSize:+t.ringSize.toFixed(0), ringSeg:t.ringSeg,
    waterY:+t.waterY.toFixed(1), cx:+t.cx.toFixed(1), cz:+t.cz.toFixed(1),
    bounds:pl.bounds, hub:{x:pl.hub.x,z:pl.hub.z}, nStations:pl.stations.length,
    cam:{fov:cam.fov, far:cam.far, pos:cam.position.toArray().map(v=>+v.toFixed(1)),
         tgt:w.rig.target.toArray().map(v=>+v.toFixed(1)), dist:+w.rig.distance.toFixed(1), pitch:+w.rig.pitch.toFixed(3)},
    coast, tris:w.ctx.renderer.info.render.triangles, draws:w.ctx.renderer.info.render.calls,
    rays:(()=>{const T=window.THREE||w.ctx.THREE;const o={};
      for(const [nm,nx,ny] of [['top',0,1],['topL',-1,1],['topR',1,1],['bot',0,-1],['midL',-1,0],['midR',1,0]]){
        const v=new T.Vector3(nx,ny,0.5).unproject(cam).sub(cam.position).normalize();
        const hit=(py)=>{ if(v.y>=-1e-6) return null; const tt=(py-cam.position.y)/v.y;
          const x=cam.position.x+v.x*tt,z=cam.position.z+v.z*tt;
          return {d:+Math.hypot(x-cam.position.x,z-cam.position.z).toFixed(0), r:+Math.hypot(x-t.cx,z-t.cz).toFixed(0)};};
        o[nm]={sea:hit(t.waterY), site:hit(7.2), dir:+(Math.asin(-v.y)*180/Math.PI).toFixed(2)};
      } return o;})(),
  },null,1);
}));
await b.close();
