/* whystop.mjs — why is a train not leaving?
 *
 * `traffic.mjs` says how much the railway carried. When that number is low the
 * next question is which gate refused, and guessing at it from the outside has
 * already cost this project a day. So `_tryStart` is wrapped and every refusal
 * is tallied by reason. */
import {chromium} from 'playwright';
const args={}; for(let i=2;i<process.argv.length;i++){const a=process.argv[i];
  if(!a.startsWith('--'))continue; const k=a.slice(2),n=process.argv[i+1];
  if(!n||n.startsWith('--'))args[k]=true; else {args[k]=n;i++;}}
const SEC = parseInt(args.seconds || '70', 10);
const FLEET=['multitek-ns','multitek-s','optimpp-1','optimpp-2','pac-flash-1','pac-flash-2','koehler-cp'];
const POS=[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]];
const b=await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail,trains&cam=yard&time=15&hud=0',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.evaluate(([f,pos])=>window.__lemWorld.setMachines(f.map((uid,i)=>({
  machine_uid:uid,title:uid,status:'GREEN',pos:pos[i],sub_statuses:{},
  module_running:true,module_state:'running',effective_specs:[],qc_targets:[],maintenance:[]}))),[FLEET,POS]);
await p.waitForTimeout(2500);
await p.evaluate(()=>{
  const T=window.__lemWorld.subsystems.get('trains');
  window.__why={};
  const bump=k=>window.__why[k]=(window.__why[k]||0)+1;
  const orig=T._tryStart.bind(T);
  T._tryStart=function(c){
    if(!c||c.shunt){bump('nocar');return false;}
    if(c.state!=='idle'){bump('busy:'+c.state);return false;}
    if(!c.uid){bump('nouid');return false;}
    if(c.cooldown>0){bump('cooldown');return false;}
    if(c.laden<0.98){bump('laden');return false;}
    if(T._activeCount()>=T.maxActive){bump('maxActive='+T.maxActive);return false;}
    const cyc=T._cycleFor(c.uid); if(!cyc||!cyc.r){bump('nocycle');return false;}
    if(!T._wantFor(c)){bump('nowork');return false;}
    const ok=orig(c);
    if(!ok) bump('authority');
    else bump('GO');
    return ok;
  };
  window.__snap=()=>({maxActive:T.maxActive,
    quality:window.__lemWorld.ctx.quality&&window.__lemWorld.ctx.quality.name,
    now:T.consists.filter(c=>!c.shunt&&c.uid).map(c=>({slot:c.slot,st:c.state,
      s:+c.s.toFixed(0), roadEnd:+(c.roadEnd||0).toFixed(0), lastDock:+(c.lastDock||0).toFixed(0),
      L:+(c.L||0).toFixed(0), line:c.line, wait:!!c.waiting, holds:(c.holds?c.holds.size:0)}))});
});
const t0=Date.now(); let n=0;
while((Date.now()-t0)/1000<SEC){
  await p.evaluate(uid=>window.__lemWorld.parse(uid,'L-W'),FLEET[n%FLEET.length]);
  n++; await p.waitForTimeout(400);
}
const r=await p.evaluate(()=>({why:window.__why, ...window.__snap()}));
console.log('parses', n);
console.log(JSON.stringify(r,null,1));
await b.close();
