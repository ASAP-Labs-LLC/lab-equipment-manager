import {chromium} from 'playwright';
const url='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&quality=ultra&time=16&weather=clear';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(1000);
console.log(await p.evaluate(async ()=>{
  const w=window.__lemWorld,t=w.subsystems.get('terrain');
  const th=3.9; let hit=t.islandR;
  for(let i=0;i<600;i++){const rr=t.islandR*0.4+i*6;const x=t.cx+Math.cos(th)*rr,z=t.cz+Math.sin(th)*rr;if(t.heightAt(x,z)<=t.waterY){hit=rr;break;}}
  const tx=t.cx+Math.cos(th)*hit,tz=t.cz+Math.sin(th)*hit;
  w.rig.goalTarget.set(tx,t.waterY+6,tz); w.rig.target.set(tx,t.waterY+6,tz);
  Object.assign(w.rig,{goalYaw:th,goalPitch:0.22,goalDistance:360}); w.rig.idleDrift=false; w.rig.apply(1);
  const THREE=await import('/static/world/vendor/three.module.js').catch(()=>null)
            || await import('three').catch(()=>null);
  if(!THREE) return 'no three';
  const rc=new THREE.Raycaster();
  const out=[];
  for(const [nx,ny] of [[0.72,-0.55],[0.55,-0.35],[0.0,-0.4],[-0.5,-0.6],[0.9,-0.8],[0.3,-0.15]]){
    rc.setFromCamera(new THREE.Vector2(nx,ny), w.ctx.camera);
    rc.far = 30000;
    const hits=rc.intersectObjects(t.meshes,false);
    out.push([nx,ny,hits.map(h=>[h.object.name, Math.round(h.distance)]).slice(0,3)]);
  }
  return JSON.stringify(out);
}));
await b.close();
