import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
const errs=[];p.on('pageerror',e=>errs.push(String(e).slice(0,120)));
await p.goto(URL,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(14000);
console.log(await p.evaluate(()=>{
  const w=window.__lemWorld, rail=w.subsystems.get('rail');
  const ew = rail && typeof rail.earthworks==='function' ? rail.earthworks() : null;
  const ctx = w.ctx && w.ctx.railEarthworks;
  if(!ew) return {published:false, onCtx:!!ctx};
  const by={}; let len=0;
  for(const e of ew){by[e.kind]=(by[e.kind]||0)+1; len+=e.length||0;}
  const deep=ew.filter(e=>Math.abs(e.maxDepth||0)>9 && e.kind==='cut');
  return {published:true, onCtx:!!ctx, spans:ew.length, byKind:by,
          totalLengthM:Math.round(len),
          deepestCut:Math.max(...ew.filter(e=>e.kind==='cut').map(e=>Math.abs(e.maxDepth||0))).toFixed(1),
          cutsDeeperThan9m:deep.length,
          sample:ew[0]?{kind:ew[0].kind,length:Math.round(ew[0].length),half:ew[0].half,batter:ew[0].batter,pts:(ew[0].points||[]).length/3}:null};
}));
if(errs.length) console.log('errors:',errs.slice(0,2));
await b.close();
