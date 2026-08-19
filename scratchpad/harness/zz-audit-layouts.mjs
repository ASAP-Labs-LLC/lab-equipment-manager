/* zz-audit-layouts.mjs — topology audit across awkward layouts. Read-only. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const FLEET = [
  ['multitek-ns','Multitek NS','GREEN'],['multitek-s','Multitek S','YELLOW'],
  ['optimpp-1','OptiMPP 1','GREEN'],['optimpp-2','OptiMPP 2','RED'],
  ['pac-flash-1','PAC Flash 1','SERVICE'],['pac-flash-2','PAC Flash 2','DEAD-LINE'],
  ['koehler-cp','Koehler CP','UNKNOWN'],
];
const BAY = 2.05;
function layouts(n) {
  const out = [[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
  const all = [out];
  let seed = 12345;
  const rnd = () => (seed = (seed*1103515245+12345) & 0x7fffffff)/0x7fffffff;
  for (let L=1; L<n; L++) {
    const kind = L%4; const pos=[];
    for (let i=0;i<FLEET.length;i++) {
      if (kind===0) pos.push([Math.round(rnd()*8)*BAY, Math.round(rnd()*8)*BAY]);
      else if (kind===1) pos.push([i*BAY,0]);
      else if (kind===2) pos.push([0,i*BAY]);
      else pos.push([Math.round(rnd()*14)*BAY, Math.round(rnd()*14)*BAY]);
    }
    if (kind===3) pos[1]=pos[0].slice();
    all.push(pos);
  }
  return all;
}

const TOPO = () => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const out = {stations: w.plan.stations.length, tracks: (rail.tracks||[]).map(t=>({n:t.name,len:+t.length.toFixed(1)})),
               branches: (rail.branches||[]).length, sidings: rail.sidings.size,
               noSiding: [], circuits: [], conflicts: [], dead: [], report: null};
  for (const s of w.plan.stations) if (!rail.sidings.has(s.uid)) out.noSiding.push(s.uid);
  const seen = new Set();
  const trackDir = new Map();  // track -> Set of 'up'/'down' across ALL circuits
  const usedSpans = new Map(); // track -> [[lo,hi],...] over all circuits
  for (const s of w.plan.stations) {
    let c=null; try { c = rail.cycle(s.uid); } catch(e){ c = {err:String(e)}; }
    if (!c) { out.circuits.push({uid:s.uid, cycle:null}); continue; }
    if (c.err) { out.circuits.push({uid:s.uid, err:c.err}); continue; }
    const key = c.line + '|' + (c.segments||[]).map(g=>g.track+':'+g.s0.toFixed(0)).join(',');
    for (const g of (c.segments||[])) {
      if (!trackDir.has(g.track)) trackDir.set(g.track, new Set());
      trackDir.get(g.track).add(g.s1>=g.s0 ? 'up':'down');
      if (!usedSpans.has(g.track)) usedSpans.set(g.track, []);
      usedSpans.get(g.track).push([Math.min(g.s0,g.s1), Math.max(g.s0,g.s1)]);
    }
    if (seen.has(key)) continue;
    seen.add(key);
    // self-overlap within the circuit
    const per = new Map();
    for (const g of (c.segments||[])) {
      if (!per.has(g.track)) per.set(g.track, []);
      per.get(g.track).push([Math.min(g.s0,g.s1), Math.max(g.s0,g.s1)]);
    }
    const selfOv = [];
    for (const [t, sp] of per) for (let i=0;i<sp.length;i++) for (let j=i+1;j<sp.length;j++) {
      const ov = Math.min(sp[i][1],sp[j][1]) - Math.max(sp[i][0],sp[j][0]);
      if (ov>1) selfOv.push(`${t} ${ov.toFixed(1)}m`);
    }
    out.circuits.push({uid:s.uid, line:c.line, closed:c.closed, turned:c.turned,
      len:+c.route.length.toFixed(1),
      gap:+c.route.points[0].distanceTo(c.route.points[c.route.points.length-1]).toFixed(3),
      docks:(c.docks||[]).length, selfOv,
      segs:(c.segments||[]).map(g=>`${g.track} ${g.s0.toFixed(1)}->${g.s1.toFixed(1)}`)});
  }
  for (const [t,d] of trackDir) if (d.size>1) out.conflicts.push(`${t} run both ways across circuits`);
  // dead track: laid length not covered by any circuit
  for (const t of (rail.tracks||[])) {
    const sp = (usedSpans.get(t.name)||[]).slice().sort((a,b)=>a[0]-b[0]);
    let cov=0, curA=null, curB=null;
    for (const [a,b] of sp) { if (curA===null){curA=a;curB=b;} else if (a<=curB){curB=Math.max(curB,b);} else {cov+=curB-curA;curA=a;curB=b;} }
    if (curA!==null) cov += curB-curA;
    const deadLen = +(t.length - cov).toFixed(1);
    if (deadLen > 5) out.dead.push({track:t.name, len:+t.length.toFixed(1), used:+cov.toFixed(1), dead:deadLen});
  }
  try { out.report = JSON.parse(JSON.stringify(rail.oneWayReport())); } catch(e){ out.report = String(e); }
  const tr = w.subsystems.get('trains');
  out.consists = tr ? tr.consists.filter(c=>!c.shunt).length : null;
  out.turnouts = (rail._turnouts||[]).map(r=>`${r.track?.name}/${r.child?.name}@${(+r.s).toFixed(0)} ${r.pdir>0?'facing':'trailing'}`);
  return out;
};

const url = process.argv[2] || 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=rail,trains&cam=top&time=13&hud=0';
const browser = await chromium.launch({headless:true, channel:'chromium',
  args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport:{width:900,height:600}});
const errs=[]; page.on('pageerror',e=>errs.push(String(e).slice(0,200)));
page.on('console',m=>{ if(m.type()==='error'&&!/favicon/.test(m.text())) errs.push(m.text().slice(0,200)); });
await page.goto(url,{waitUntil:'load',timeout:60000});
await page.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await page.waitForTimeout(2000);

const all=[];
const SET = layouts(10);
for (let L=0;L<SET.length;L++) {
  const ok = await page.evaluate(([fleet,pos])=>{
    const list = fleet.map(([uid,title,status],i)=>({machine_uid:uid,title,status,pos:pos[i],
      reason:'audit', sub_statuses:{qc:status,pm:'GREEN',calibration:'GREEN'},
      module_running:true, module_state:'running', effective_specs:[], qc_targets:[], maintenance:[]}));
    try { window.__lemWorld.setMachines(list); return true; } catch(e){ return String(e); }
  },[FLEET,SET[L]]);
  await page.waitForTimeout(2500);
  const t = await page.evaluate(TOPO);
  all.push({layout:L, kind:L===0?'real':['scatter','rank','file','sparse+dup'][L%4], ok, ...t});
  console.log(`L${L} ${all[L].kind}: stations=${t.stations} branches=${t.branches} sidings=${t.sidings} noSiding=${t.noSiding.length} conflicts=${JSON.stringify(t.conflicts)} report=${t.report&&t.report.oneWay!==undefined?('oneWay='+t.report.oneWay+' closed='+t.report.closed+' conf='+t.report.conflicts.length+' ov='+t.report.overlaps.length+' open='+JSON.stringify(t.report.open)+' junc='+JSON.stringify(t.report.junctions)):t.report}`);
  const open = t.circuits.filter(c=>c.cycle===null||c.closed===false);
  if (open.length) console.log('   NOT-CLOSED/NO-CYCLE:', open.map(c=>c.uid+':'+(c.cycle===null?'none':'open '+c.line)).join(', '));
  const so = t.circuits.filter(c=>c.selfOv&&c.selfOv.length);
  if (so.length) console.log('   SELF-OVERLAP:', JSON.stringify(so.map(c=>[c.uid,c.selfOv])));
  console.log('   dead:', t.dead.map(d=>`${d.track} ${d.dead}/${d.len}`).join('  '));
}
await browser.close();
fs.writeFileSync('/private/tmp/claude-501/-Volumes-Labsharedrive-Ryan-C-LAB-lem/d5fe7a72-443c-4547-b733-a3e73721e820/scratchpad/layouts.json', JSON.stringify({all,errs},null,1));
console.log('errors', errs.slice(0,5));
