/* zz-spacing.mjs — standage between consecutive junctions on the one-way ring,
 * measured against train length. Read-only. */
import {chromium} from 'playwright';
const FLEET=[['multitek-ns','Multitek NS','GREEN'],['multitek-s','Multitek S','YELLOW'],
 ['optimpp-1','OptiMPP 1','GREEN'],['optimpp-2','OptiMPP 2','RED'],
 ['pac-flash-1','PAC Flash 1','SERVICE'],['pac-flash-2','PAC Flash 2','DEAD-LINE'],
 ['koehler-cp','Koehler CP','UNKNOWN']];
const BAY=2.05;
const LAYOUTS={
 real:[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]],
 rank:Array.from({length:7},(_,i)=>[i*BAY,0]),
 file:Array.from({length:7},(_,i)=>[0,i*BAY]),
};
const b=await chromium.launch({headless:true,channel:'chromium',
 args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:800,height:500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=rail,trains&time=13&hud=0',{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2000);
for (const [name,pos] of Object.entries(LAYOUTS)) {
  await p.evaluate(([f,ps])=>window.__lemWorld.setMachines(f.map(([uid,title,status],i)=>({
    machine_uid:uid,title,status,pos:ps[i],reason:'audit',
    sub_statuses:{qc:status,pm:'GREEN',calibration:'GREEN'},module_running:true,
    module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,pos]);
  await p.waitForTimeout(2500);
  const r=await p.evaluate(()=>{
    const w=window.__lemWorld,rail=w.subsystems.get('rail'),tr=w.subsystems.get('trains');
    const js=[];
    for(const br of rail.branches||[]) { js.push({n:br.track.name,W:+br.tS.toFixed(1),E:+br.teS.toFixed(1)}); }
    const other=(rail._turnouts||[]).filter(t=>t.track===rail.trunk&&!rail.branchOf.get(t.child))
      .map(t=>({n:t.child?.name,s:+(+t.s).toFixed(1),hand:t.pdir>0?'facing':'trailing'}));
    const lens=(tr?.consists||[]).filter(c=>!c.shunt).map(c=>+(c.length||0).toFixed(1));
    const mainLen=rail.trunk?+rail.trunk.length.toFixed(1):null;
    // loading-road capacity: dock spacing
    const caps=[];
    const seen=new Set();
    for(const s of w.plan.stations){ const c=rail.cycle(s.uid); if(!c||seen.has(c.line))continue; seen.add(c.line);
      caps.push({line:c.line, docks:(c.docks||[]).map(d=>+d.s.toFixed(0)), roadLen:+((c.segments[0].s1)).toFixed(0), circuit:+c.route.length.toFixed(0)}); }
    return {js,other,lens,mainLen,caps,rack:rail.rackS!==undefined?+rail.rackS.toFixed(1):null};
  });
  const W=r.js.map(j=>j.W).sort((a,b)=>a-b), E=r.js.map(j=>j.E).sort((a,b)=>a-b);
  const gaps=a=>a.slice(1).map((v,i)=>+(v-a[i]).toFixed(1));
  console.log(`\n== ${name}  branches=${r.js.length} main=${r.mainLen}m rackS=${r.rack} trainLen=${r.lens[0]}..${Math.max(...r.lens)}`);
  console.log('  west (trailing) junctions on main:', W, ' gaps:', gaps(W));
  console.log('  east (facing)   junctions on main:', E, ' gaps:', gaps(E));
  console.log('  other trunk connections:', JSON.stringify(r.other));
  for(const c of r.caps) console.log(`  ${c.line}: circuit ${c.circuit}m, loading road ${c.roadLen}m, docks ${JSON.stringify(c.docks)}`);
}
await b.close();
