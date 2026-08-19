import {chromium} from 'playwright';
const hide=process.argv[2]||'';
const url='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&quality=ultra&time=16&weather=clear';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(1000);
await p.evaluate((hide)=>{
  const w=window.__lemWorld,t=w.subsystems.get('terrain');
  const th=3.9; let hit=t.islandR;
  for(let i=0;i<600;i++){const rr=t.islandR*0.4+i*6;const x=t.cx+Math.cos(th)*rr,z=t.cz+Math.sin(th)*rr;if(t.heightAt(x,z)<=t.waterY){hit=rr;break;}}
  const tx=t.cx+Math.cos(th)*hit,tz=t.cz+Math.sin(th)*hit;
  w.rig.goalTarget.set(tx,t.waterY+6,tz); w.rig.target.set(tx,t.waterY+6,tz);
  Object.assign(w.rig,{goalYaw:th,goalPitch:0.22,goalDistance:360}); w.rig.idleDrift=false; w.rig.apply(1);
  if(hide==='fog') w.ctx.scene.fog=null;
  else if(hide==='mag'){const oc=t.meshes.find(m=>m.name==='terrain-ocean');oc.material=new oc.material.constructor({color:0xff00ff,roughness:1,metalness:0});oc.material.fog=false;}
  else if(hide) for(const part of hide.split('+')) for(const m of t.meshes) if(m.name.includes(part)) m.visible=false;
},hide);
await p.waitForTimeout(900);
await p.screenshot({path:process.argv[3]});
await b.close();
