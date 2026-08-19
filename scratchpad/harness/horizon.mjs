/* horizon.mjs — does the map end anywhere the camera can see?
 * Parks the camera high over the site looking outward on a chosen bearing, so
 * the rim of the world (if there is one) is in frame rather than over it. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const args={};for(let i=2;i<process.argv.length;i++){const a=process.argv[i];if(!a.startsWith('--'))continue;const k=a.slice(2),n=process.argv[i+1];if(!n||n.startsWith('--'))args[k]=true;else{args[k]=n;i++;}}
const H=parseFloat(args.height||'260'), B=parseFloat(args.bearing||'40'), T=args.time||'16';
const b=await chromium.launch({args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage({viewport:{width:1600,height:900}});
const errs=[];p.on('pageerror',e=>errs.push(String(e).slice(0,160)));
p.on('console',m=>{if(m.type()==='error')errs.push(m.text().slice(0,160));});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${args.mods||'sky,gi,terrain,vegetation,weather'}&time=${T}&hud=0`,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(3500);
await p.evaluate(([h,bear])=>{
  const w=window.__lemWorld,t=w.subsystems.get('terrain');
  w.rig.suspended=true; w.rig.update=()=>{};
  const a=bear*Math.PI/180, cx=t.cx, cz=t.cz;
  const g=t.heightAt(cx,cz);
  w.camera.far=Math.max(w.camera.far,40000); w.camera.updateProjectionMatrix();
  w.camera.position.set(cx,g+h,cz);
  w.camera.lookAt(cx+Math.cos(a)*4000, g+h-360, cz+Math.sin(a)*4000);
  w.camera.updateMatrixWorld();
},[H,B]);
await p.waitForTimeout(1500);
fs.writeFileSync(args.out||'/Users/rynatical/LAB-lem/scratchpad/shots/horizon.png', await p.screenshot());
if(errs.length)console.log('errors:',errs.slice(0,4));
await b.close();
