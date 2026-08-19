import {chromium} from 'playwright';
const url='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&quality=ultra&time=16&weather=clear';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(1000);
const info = await p.evaluate(()=>{
  const w=window.__lemWorld,t=w.subsystems.get('terrain');
  const th=3.9; let hit=t.islandR;
  for(let i=0;i<600;i++){const rr=t.islandR*0.4+i*6;const x=t.cx+Math.cos(th)*rr,z=t.cz+Math.sin(th)*rr;if(t.heightAt(x,z)<=t.waterY){hit=rr;break;}}
  const tx=t.cx+Math.cos(th)*hit,tz=t.cz+Math.sin(th)*hit;
  w.rig.goalTarget.set(tx,t.waterY+6,tz); w.rig.target.set(tx,t.waterY+6,tz);
  Object.assign(w.rig,{goalYaw:th,goalPitch:0.22,goalDistance:360}); w.rig.idleDrift=false; w.rig.apply(1);
  const oc=t.meshes.find(m=>m.name==='terrain-ocean');
  const THREE=Object.getPrototypeOf(oc.material).constructor;
  const flat=new (Object.getPrototypeOf(oc.material.constructor)) ;
  // build a raw material via the mesh's own constructor family
  const m=new oc.material.constructor({color:0xff00ff});
  m.fog=false; m.transparent=false; m.depthWrite=true; m.depthTest=true;
  m.onBeforeCompile=()=>{};
  oc.material=m;
  const g=oc.geometry, pos=g.attributes.position, dep=g.attributes.aDepth;
  const idx=g.index;
  // stats: how many triangles per radius band
  const bands={};
  for(let i=0;i<idx.count;i+=3){
    const v=idx.getX(i);
    const r=Math.hypot(pos.getX(v)-t.cx, pos.getZ(v)-t.cz);
    const key=Math.floor(r/500)*500; bands[key]=(bands[key]||0)+1;
  }
  return JSON.stringify({tris:idx.count/3, bands});
});
await p.waitForTimeout(900);
await p.screenshot({path:'/Users/rynatical/LAB-lem/scratchpad/shots/isl-ocean-magenta.png'});
console.log(info);
await b.close();
