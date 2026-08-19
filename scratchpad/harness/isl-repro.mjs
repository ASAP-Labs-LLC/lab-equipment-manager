import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--ignore-gpu-blocklist']});
for(let run=1;run<=3;run++){
  const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
  await p.goto(URL,{waitUntil:'load',timeout:60000});
  await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
  const series=[];
  let prev=0;
  for(const t of [500,3000,8000,16000,26000]){
    await p.waitForTimeout(t-prev); prev=t;
    const v=await p.evaluate(()=>{
      const w=window.__lemWorld, T=w.subsystems.get('terrain');
      return {r:+(T&&T.islandR||0).toFixed(1),
              n:(w.plan&&w.plan.stations?w.plan.stations.length:-1)};});
    series.push(`${t/1000}s:R=${v.r}/n=${v.n}`);
  }
  console.log(`run ${run}  ` + series.join('  '));
  await p.context().close();
}
await b.close();
