/* What the round's new work costs, timed on the live page rather than inferred
 * from a wall clock three other builders are moving. */
import {chromium} from 'playwright';
const URL='http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=far&time=16&hud=0&quality=ultra';
const b=await chromium.launch({headless:true,channel:'chromium',args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto(URL,{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const veg=window.__lemWorld.subsystems.get('vegetation');
  const med=(f,n=7)=>{const a=[];for(let i=0;i<n;i++){const t=performance.now();f();a.push(performance.now()-t);}a.sort((x,y)=>x-y);return +a[n>>1].toFixed(2);};
  return {buildMs:Math.round(veg._buildMs),
          probeFieldsMs:med(()=>veg._probeFields(veg.plan)),
          railFieldMs:med(()=>veg._buildRailField(veg.plan)),
          scatterTreesMs:med(()=>veg._scatterTrees(),3),
          seatOffsetsMs:med(()=>veg._seatOffsets()),
          reseatMs:med(()=>veg._reseat('timing')),
          stems:veg._scatterStats.placed};
}),null,1));
await b.close();
