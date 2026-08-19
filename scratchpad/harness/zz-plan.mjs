/* zz-plan.mjs — a true plan view: camera straight down, framed on the whole
 * permanent way. Read-only; touches only the local rig. */
import {chromium} from 'playwright';
const args={}; for(let i=2;i<process.argv.length;i++){const a=process.argv[i];if(!a.startsWith('--'))continue;const n=process.argv[i+1];if(!n||n.startsWith('--'))args[a.slice(2)]=true;else{args[a.slice(2)]=n;i++;}}
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1600,height:1200}, deviceScaleFactor:1});
await p.goto(args.url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2500);
if (args.tx !== undefined) await p.evaluate(([x,z])=>{window.__tx=x;window.__tz=z;}, [parseFloat(args.tx), parseFloat(args.tz)]);
await p.evaluate((d)=>{
  const w = window.__lemWorld, rail = w.subsystems.get('rail');
  let minX=Infinity,maxX=-Infinity,minZ=Infinity,maxZ=-Infinity;
  for (const t of rail.tracks||[]) { const f=t.frames; if(!f) continue;
    for(let i=0;i<f.count;i++){const x=f.pos[i*3],z=f.pos[i*3+2];
      minX=Math.min(minX,x);maxX=Math.max(maxX,x);minZ=Math.min(minZ,z);maxZ=Math.max(maxZ,z);} }
  const cx=(minX+maxX)/2, cz=(minZ+maxZ)/2;
  const span=Math.max(maxX-minX,maxZ-minZ);
  const q = new URLSearchParams(location.search);
  Object.assign(w.rig,{goalYaw:0, goalPitch:1.5620, goalDistance: d?parseFloat(d):span*1.15});
  w.rig.goalTarget.set(window.__tx ?? cx, 0, window.__tz ?? cz);
  w.rig.apply(1); w.rig.idleDrift=false;
  window.__frame={minX,maxX,minZ,maxZ,cx,cz,span};
}, args.dist||null);
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>window.__frame)));
await p.screenshot({path:args.out});
await b.close();
