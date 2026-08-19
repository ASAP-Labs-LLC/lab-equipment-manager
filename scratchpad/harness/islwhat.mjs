import {chromium} from 'playwright';
import * as fs from 'node:fs';
const url='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&quality=ultra&time=16&weather=clear';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(1200);
console.log(await p.evaluate(()=>{
  const w=window.__lemWorld,t=w.subsystems.get('terrain');
  const th=3.9; let hit=t.islandR;
  for(let i=0;i<600;i++){const rr=t.islandR*0.4+i*6;const x=t.cx+Math.cos(th)*rr,z=t.cz+Math.sin(th)*rr;if(t.heightAt(x,z)<=t.waterY){hit=rr;break;}}
  const tx=t.cx+Math.cos(th)*hit,tz=t.cz+Math.sin(th)*hit;
  w.rig.goalTarget.set(tx,t.waterY+6,tz); w.rig.target.set(tx,t.waterY+6,tz);
  Object.assign(w.rig,{goalYaw:th,goalPitch:0.22,goalDistance:360}); w.rig.idleDrift=false; w.rig.apply(1);
  const THREE=w.THREE||window.THREE;
  const rc=new (w.ctx.raycasterCtor|| Object)();
  // build a raycaster from three via an existing object
  const cam=w.ctx.camera;
  const res=[];
  const pts=[[0.88,0.80],[0.80,0.88],[0.95,0.72],[0.55,0.62],[0.30,0.80],[0.70,0.50]];
  const Ray = w.ctx.THREE ? w.ctx.THREE.Raycaster : null;
  return JSON.stringify({needThree: !Ray, camPos: cam.position.toArray().map(v=>Math.round(v)), waterY: t.waterY,
    oceanBS: t.meshes.find(m=>m.name==='terrain-ocean').geometry.boundingSphere.radius,
    ringName: t.meshes.map(m=>m.name)});
}));
await b.close();
