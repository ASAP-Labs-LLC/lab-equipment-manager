/* The real /floor page in the real Flask app — not dev/solo.html.
 * Everything judged tonight was the dev harness; this is the deliverable. */
import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1920,height:1080}})).newPage();
const errs=[], reqfail=[];
p.on('pageerror',e=>errs.push(String(e).slice(0,200)));
p.on('console',m=>{if(m.type()==='error') errs.push('console: '+m.text().slice(0,200));});
p.on('response',r=>{if(r.status()>=400 && !/favicon/.test(r.url())) reqfail.push(r.status()+' '+r.url().split('/').pop());});
await p.goto('http://127.0.0.1:5612/floor',{waitUntil:'load',timeout:60000});
await p.waitForTimeout(3000);
// wait for the world if it boots
let ready=false;
try{ await p.waitForFunction(()=>window.__worldReady===true||!!window.__lemWorld,null,{timeout:45000}); ready=true; }catch{}
await p.waitForTimeout(12000);
const st=await p.evaluate(()=>{
  const w=window.__lemWorld;
  return {hasWorld:!!w, tier:w&&w.engine&&w.engine.tier?w.engine.tier.name:null,
          stats:w&&w.stats?w.stats():null,
          bridge:!!window.__floorBridge,
          subsystems:w?[...w.subsystems.keys()]:[], failed:w?w.failed:null};
});
await p.screenshot({path:'/tmp/floor-real.png'});
console.log(JSON.stringify({ready, ...st, errors:errs.slice(0,5), httpFailures:reqfail.slice(0,5)},null,1));
await b.close();
