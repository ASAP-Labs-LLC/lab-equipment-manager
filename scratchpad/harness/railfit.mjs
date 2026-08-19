/* railfit.mjs — how much of the railway is standing on ground terrain graded.
 *
 * terrain.js grades a straight corridor from each station to the hub and a box
 * under the terminal. rail.js is free to route however it likes. Where the two
 * disagree the track is left on natural ground and rail builds a wall to reach
 * it, which is what the tan embankments on layout 7 are. This walks every
 * vertex of rail's own geometry and reports how far it is from the design
 * plane the ground under it was graded to. */
import {chromium} from 'playwright';
const args={};for(let i=2;i<process.argv.length;i++){const a=process.argv[i];if(!a.startsWith('--'))continue;const k=a.slice(2),n=process.argv[i+1];if(!n||n.startsWith('--'))args[k]=true;else{args[k]=n;i++;}}
const FLEET=[['multitek-ns','Multitek NS','GREEN'],['multitek-s','Multitek S','YELLOW'],['optimpp-1','OptiMPP 1','GREEN'],['optimpp-2','OptiMPP 2','RED'],['pac-flash-1','PAC Flash 1','SERVICE'],['pac-flash-2','PAC Flash 2','DEAD-LINE'],['koehler-cp','Koehler CP','UNKNOWN']];
function layouts(n){const BAY=2.05;const all=[[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]]];let seed=12345;const rnd=()=>(seed=(seed*1103515245+12345)&0x7fffffff)/0x7fffffff;
for(let L=1;L<n;L++){const kind=L%4,pos=[];for(let i=0;i<FLEET.length;i++){if(kind===0)pos.push([Math.round(rnd()*8)*BAY,Math.round(rnd()*8)*BAY]);else if(kind===1)pos.push([i*BAY,0]);else if(kind===2)pos.push([0,i*BAY]);else pos.push([Math.round(rnd()*14)*BAY,Math.round(rnd()*14)*BAY]);}if(kind===3)pos[1]=pos[0].slice();all.push(pos);}return all;}
const L=parseInt(args.layout||'7',10);
const b=await chromium.launch({args:['--use-angle=metal']});const p=await b.newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail&time=16&hud=0',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.evaluate(([f,pos])=>window.__lemWorld.setMachines(f.map(([uid,title,status],i)=>({machine_uid:uid,title,status,pos:pos[i],reason:'x',sub_statuses:{qc:status,pm:'GREEN',calibration:'GREEN'},module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,layouts(L+1)[L]]);
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld,t=w.subsystems.get('terrain'),r=w.subsystems.get('rail');
  const out={hubPad:null,samples:0,offGraded:0,worst:0,worstAt:null,railBox:null};
  const hub=w.plan.hub;
  out.hubPad={x:hub.x,z:hub.z};
  const box={minX:1e9,maxX:-1e9,minZ:1e9,maxZ:-1e9};
  const grp=r&&(r.group||r.root);
  if(!grp) return {error:'no rail group'};
  grp.updateMatrixWorld(true);
  const pts=[];
  grp.traverse(o=>{
    const g=o.geometry; if(!g||!g.attributes||!g.attributes.position) return;
    const a=g.attributes.position; const step=Math.max(1,Math.floor(a.count/220));
    for(let i=0;i<a.count;i+=step){
      const x0=a.getX(i),y0=a.getY(i),z0=a.getZ(i);
      const m=o.matrixWorld.elements;
      const x=m[0]*x0+m[4]*y0+m[8]*z0+m[12];
      const y=m[1]*x0+m[5]*y0+m[9]*z0+m[13];
      const z=m[2]*x0+m[6]*y0+m[10]*z0+m[14];
      pts.push([x,y,z]);
    }
  });
  for(const [x,y,z] of pts){
    box.minX=Math.min(box.minX,x);box.maxX=Math.max(box.maxX,x);
    box.minZ=Math.min(box.minZ,z);box.maxZ=Math.max(box.maxZ,z);
    const f=t._distances(x,z,null);
    out.samples++;
    if(f>0){ out.offGraded++;
      if(f>out.worst){out.worst=f;out.worstAt=[Math.round(x),Math.round(z),Math.round(y)];}}
  }
  out.railBox={x:Math.round(box.maxX-box.minX),z:Math.round(box.maxZ-box.minZ)};
  out.worst=Math.round(out.worst);
  out.pctOff=Math.round(100*out.offGraded/Math.max(1,out.samples));
  return out;
})));
await b.close();
