/* planshot.mjs — a plan view of the BARE TRACK, framed on the network itself.
 *
 *   node planshot.mjs --out ../shots/x.png [--layout 0] [--mods rail,trains]
 *
 * `cam=top` frames the lab, not the railway, and the railway is now half a
 * kilometre wider than the lab in both directions. This asks rail.js where its
 * track actually is and stands the camera over the middle of it, looking
 * straight down with north up — which is the drawing a signaller would be
 * handed. */
import {chromium} from 'playwright';
const args={}; for(let i=2;i<process.argv.length;i++){const a=process.argv[i];
  if(!a.startsWith('--'))continue; const k=a.slice(2),n=process.argv[i+1];
  if(!n||n.startsWith('--'))args[k]=true; else {args[k]=n;i++;}}
const FLEET=['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const BAY=2.05;
function layouts(n){
  const out=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
  const all=[out]; let seed=12345;
  const rnd=()=> (seed=(seed*1103515245+12345)&0x7fffffff)/0x7fffffff;
  for(let L=1;L<n;L++){
    const kind=L%4; const pos=[];
    for(let i=0;i<FLEET.length;i++){
      if(kind===0) pos.push([Math.round(rnd()*8)*BAY, Math.round(rnd()*8)*BAY]);
      else if(kind===1) pos.push([i*BAY,0]);
      else if(kind===2) pos.push([0,i*BAY]);
      else pos.push([Math.round(rnd()*14)*BAY, Math.round(rnd()*14)*BAY]);
    }
    if(kind===3) pos[1]=pos[0].slice();
    all.push(pos);
  }
  return all;
}
const mods = args.mods || 'rail,trains';
const L = parseInt(args.layout || '0', 10);
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal']});
const p = await b.newPage({viewport:{width:1600,height:1600}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=top&time=${args.time||16}&hud=0`,{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
if (L > 0) {
  await p.evaluate(([f,pp])=>window.__lemWorld.setMachines(f.map((uid,i)=>({
    machine_uid:uid,title:uid,status:'GREEN',pos:pp[i],sub_statuses:{},
    module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,layouts(10)[L]]);
  await p.waitForTimeout(2200);
}
const info = await p.evaluate(()=>{
  const w=window.__lemWorld, rail=w.subsystems.get('rail');
  let nx=1e9,xx=-1e9,nz=1e9,zz=-1e9;
  for(const t of rail.tracks){ const f=t.frames; if(!f) continue;
    for(let i=0;i<f.count;i++){ const x=f.pos[i*3], z=f.pos[i*3+2];
      nx=Math.min(nx,x); xx=Math.max(xx,x); nz=Math.min(nz,z); zz=Math.max(zz,z); } }
  const cx=(nx+xx)/2, cz=(nz+zz)/2;
  const span=Math.max(xx-nx, zz-nz)*1.12;
  const cam=w.ctx.camera;
  const h = span/2 / Math.tan((cam.fov*Math.PI/180)/2);
  /* Freeze the rig so it cannot ease the camera back to its own goal. */
  if (w.rig) { w.rig.enabled=false; w.rig.update = ()=>{}; }
  cam.position.set(cx, h+200, cz+0.01);
  cam.up.set(0,0,-1);
  cam.lookAt(cx, 0, cz);
  cam.far = Math.max(cam.far, h*3); cam.updateProjectionMatrix();
  return {cx:Math.round(cx), cz:Math.round(cz), span:Math.round(span), h:Math.round(h)};
});
await p.waitForTimeout(2500);
await p.evaluate(()=>{ /* re-assert after any frame the rig still owned */
  const w=window.__lemWorld; if(w.rig){ w.rig.enabled=false; }
});
await p.waitForTimeout(1200);
await p.screenshot({path: args.out || '../shots/plan.png'});
console.log(JSON.stringify(info));
await b.close();
