/* oneway.mjs — is the network a one-way circuit, on every layout?
 *
 * The topology gate. `railcheck.mjs` asks whether every station is reachable
 * and whether the alignments are laid to a radius track could be built at;
 * this asks the question Ryan actually asked, which neither of those can see:
 *
 *   - no track carries traffic in BOTH directions in one working;
 *   - no track is covered twice by one working even in the same direction;
 *   - the circuit closes, exactly, so it can be run round for ever;
 *   - the joins still meet (`jointReport`) — a topology that closes because a
 *     connection was quietly dropped is not a fix;
 *   - and the network is still there: branches, sidings, cycles, the trunk.
 */
import {chromium} from 'playwright';

const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const BAY = 2.05;
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

const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal']});
const p = await b.newPage({viewport:{width:1000,height:700}});
const errs=[];
p.on('pageerror', e=>errs.push(String(e).slice(0,180)));
p.on('console', m=>{ if(m.type()==='error' && !/favicon/.test(m.text())) errs.push(m.text().slice(0,180)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail&cam=top&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});

let fails = 0;
for (const [L, pos] of layouts(10).entries()) {
  await p.evaluate(([f,pp])=>window.__lemWorld.setMachines(f.map((uid,i)=>({
    machine_uid:uid,title:uid,status:'GREEN',pos:pp[i],sub_statuses:{},
    module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,pos]);
  await p.waitForTimeout(1600);
  const r = await p.evaluate(()=>{
    const w=window.__lemWorld, rail=w.subsystems.get('rail'), plan=w.plan;
    const ow = rail.oneWayReport();
    const jr = rail.jointReport();
    let routed=0, cycles=0, turned=0;
    for (const st of plan.stations) {
      if (rail.route(st.uid)) routed++;
      const c = rail.cycle(st.uid);
      if (c) { cycles++; if (c.turned) turned++; }
    }
    let laid=0;
    for (const t of rail.tracks) if (t.frames) laid += Math.min(t.renderTo,t.length)-(t.renderFrom||0);
    /* Longest chord in every circuit: a route that steps is a train crossing
     * open ground. */
    let chord=0;
    const seen=new Set();
    for (const st of plan.stations) {
      const c = rail.cycle(st.uid);
      if (!c || seen.has(c.line)) continue; seen.add(c.line);
      const P=c.route.points;
      for(let i=1;i<P.length;i++) chord=Math.max(chord, P[i].distanceTo(P[i-1]));
    }
    return {ow, joins: jr.joins, gapMm:+jr.worstGapMm.toFixed(3),
            levelMm:+jr.worstLevelMm.toFixed(3), angle:+jr.worstAngle.toFixed(3),
            routed, n: plan.stations.length, cycles, turned,
            branches: rail.branches.length, sidings: rail.sidings.size,
            tracks: rail.tracks.length, km:+(laid/1000).toFixed(2),
            chord:+chord.toFixed(1),
            loop: !!rail.loop, spur: !!rail._spur};
  });
  const ok = r.ow.oneWay && r.ow.closed && r.routed === r.n &&
             r.cycles === r.n && r.turned === r.n && r.chord < 30 &&
             r.gapMm < 5 && r.joins > 0;
  if (!ok) fails++;
  console.log(`L${L} ${ok?'ok ':'FAIL'} oneWay=${r.ow.oneWay} closed=${r.ow.closed} ` +
    `circuits=${r.ow.circuits} | ${r.routed}/${r.n} routed, ${r.cycles} cycles, ${r.turned} turn | ` +
    `br ${r.branches} sid ${r.sidings} trk ${r.tracks} loop ${r.loop?'y':'n'} spur ${r.spur?'y':'n'} | ` +
    `${r.km}km chord ${r.chord}m | joins ${r.joins} gap ${r.gapMm}mm lvl ${r.levelMm}mm ang ${r.angle}°`);
  for (const c of r.ow.conflicts) console.log('    CONFLICT', c);
  for (const c of r.ow.overlaps) console.log('    OVERLAP ', c);
  for (const c of r.ow.open) console.log('    OPEN    ', c);
}
if (errs.length) { console.log('console errors:'); for(const e of errs.slice(0,8)) console.log('  ', e); }
console.log(fails ? `FAIL (${fails} layouts)` : 'PASS');
await b.close();
process.exit(fails || errs.length ? 1 : 0);
