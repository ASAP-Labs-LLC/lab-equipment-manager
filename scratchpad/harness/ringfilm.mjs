/* ringfilm.mjs — the topology, in motion, from above.
 *
 * `film.mjs --cam yard` shows the yard working and is the right gate for
 * whether stock rolls; it cannot show whether the NETWORK works, because the
 * network is a kilometre across and the yard camera sees one corner of it. This
 * stands the camera over the whole railway, runs the traffic, and writes a
 * contact sheet of the plan — which is the drawing the topology gets judged
 * from. */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
const args={}; for(let i=2;i<process.argv.length;i++){const a=process.argv[i];
  if(!a.startsWith('--'))continue; const k=a.slice(2),n=process.argv[i+1];
  if(!n||n.startsWith('--'))args[k]=true; else {args[k]=n;i++;}}
const OUT=path.resolve(args.out||'../shots/ringfilm');
const FRAMES=parseInt(args.frames||'9',10), EVERY=parseInt(args.every||'2200',10);
fs.mkdirSync(OUT,{recursive:true});
const FLEET=['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const POS=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage({viewport:{width:1100,height:1100}});
p.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,200)));
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${args.mods||'rail,trains'}&cam=top&time=${args.time||16}&hud=0`,{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.evaluate(([f,pos])=>window.__lemWorld.setMachines(f.map((uid,i)=>({machine_uid:uid,title:uid,status:'GREEN',pos:pos[i],
  sub_statuses:{},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,POS]);
await p.waitForTimeout(2500);
await p.evaluate(()=>{
  const w=window.__lemWorld, rail=w.subsystems.get('rail');
  let nx=1e9,xx=-1e9,nz=1e9,zz=-1e9;
  for(const t of rail.tracks){const f=t.frames; if(!f)continue;
    for(let i=0;i<f.count;i++){const x=f.pos[i*3],z=f.pos[i*3+2];
      nx=Math.min(nx,x);xx=Math.max(xx,x);nz=Math.min(nz,z);zz=Math.max(zz,z);}}
  const cx=(nx+xx)/2, cz=(nz+zz)/2, span=Math.max(xx-nx,zz-nz)*1.1;
  const cam=w.ctx.camera, h=span/2/Math.tan((cam.fov*Math.PI/180)/2);
  /* The rig eases the camera back to its own goal every frame, so it has to
   * be silenced and not merely disabled. */
  if(w.rig){ w.rig.enabled=false; w.rig.update=()=>{}; }
  cam.position.set(cx,h+200,cz+0.01); cam.up.set(0,0,-1); cam.lookAt(cx,0,cz);
  cam.far=Math.max(cam.far,h*3); cam.updateProjectionMatrix();
});
const rows=[];
for(let i=0;i<FRAMES;i++){
  for(let k=0;k<6;k++){ await p.evaluate(u=>window.__lemWorld.parse(u,'L-R'),FLEET[(i*6+k)%7]); await p.waitForTimeout(60); }
  await p.waitForTimeout(EVERY);
  await p.evaluate(()=>{ const w=window.__lemWorld; if(w.rig){w.rig.enabled=false; w.rig.update=()=>{};} });
  const f=path.join(OUT,`f-${String(i).padStart(2,'0')}.png`);
  await p.screenshot({path:f});
  const st=await p.evaluate(()=>{
    const T=window.__lemWorld.subsystems.get('trains');
    return T.consists.filter(c=>!c.shunt&&c.uid&&c.state!=='idle')
      .map(c=>`${c.slot}:${c.state}@${c.s.toFixed(0)}/${c.L.toFixed(0)}`).join(' ');
  });
  rows.push(st); console.log(`f-${i}`, st||'(all standing)');
}
fs.writeFileSync(OUT+'/track.json', JSON.stringify(rows,null,1));
await b.close();
