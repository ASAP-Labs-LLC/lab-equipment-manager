import {chromium} from 'playwright';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=wide&time=16&hud=0',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
for(const t of ['ultra','floor']){
  await p.evaluate(q=>window.__lemWorld.engine.setQualityMode(q),t);
  await p.waitForTimeout(2500);
  console.log(t, JSON.stringify(await p.evaluate(()=>{
    const v=window.__lemWorld.subsystems.get('vegetation');
    const q=Math.max(0,Math.min(1,v.quality))*v._treeBudget;
    let tot=0,in_=0;
    for(const e of v.trees||[]) for(let i=0;i<e.list.length;i++){tot++; if(e.rank[i]<=q) in_++;}
    return {tier:v.tier, quality:v.quality, range:v.range, treeBudget:v._treeBudget, q:+q.toFixed(3),
            total:tot, ranked:in_, pct:+(100*in_/tot).toFixed(1), groundCover:v.groundCover};
  })));
}
await b.close();
