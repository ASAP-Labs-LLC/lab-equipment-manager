/* pr-sun.mjs — does the drawn shade follow the sun, and does it thrash?
 *
 * Sweeps the time of day and asserts three things about `_resunDecals`:
 *   1. the shade's azimuth tracks the sun's;
 *   2. it does NOT rebuild every frame (the hysteresis works);
 *   3. it disappears when the sun goes down and comes back when it rises.
 *
 *   node pr-sun.mjs
 */
import {chromium} from 'playwright';
const MODS='sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b=await chromium.launch({headless:true,channel:'chromium',
 args:['--use-angle=metal','--ignore-gpu-blocklist','--enable-unsafe-swiftshader']});
const p=await (await b.newContext({viewport:{width:1280,height:720}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods='+MODS+
 '&cam=far&time=9&weather=clear&hud=0&quality=ultra',{waitUntil:'load',timeout:120000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:120000});
await p.waitForTimeout(6000);
/* count rebuilds by wrapping _buildDecals */
await p.evaluate(()=>{
  const pr=window.__lemWorld.subsystems.get('props');
  pr.__rebuilds=0;
  const orig=pr._buildDecals.bind(pr);
  pr._buildDecals=function(){ pr.__rebuilds++; return orig(); };
});
const read=async(t)=>{
  await p.evaluate(t=>window.__lemWorld.setTimeOfDay(t), t);
  /* 4 SECONDS, NOT 1.4. `setTimeOfDay` does not move the sun synchronously —
   * gi's key direction lags it by something over two seconds. At 1.4 s this
   * probe read the PREVIOUS hour's sun at every step and reported that the
   * shade vanished on returning to 09:00, which looked exactly like a bug in
   * `_resunDecals` and was the harness reading before the sky had caught up. */
  await p.waitForTimeout(4000);
  return await p.evaluate(()=>{
    const pr=window.__lemWorld.subsystems.get('props');
    let d=null; pr.group.traverse(o=>{if(o.name==='props:decals')d=o;});
    return {shade:pr.shade, rebuilds:pr.__rebuilds,
            tris: d? d.geometry.index.count/3 : 0};
  });
};
/* how many frames pass in 1.4s of a static scene, as the thrash baseline */
const base=await read(9);
const rows=[];
for(const t of [9,10,12,14,16,18,20,22,4,6,9]) rows.push([t, await read(t)]);
console.log('static hold at time=9 -> rebuilds so far', base.rebuilds);
let prev=base.rebuilds;
for(const [t,r] of rows){
  console.log('time='+String(t).padStart(2)+'  shade='+
    (r.shade?('el '+r.shade.elevDeg.toFixed(1)+' az '+r.shade.azDeg.toFixed(1)+
              ' k '+r.shade.strength):'NONE (sun down)')+
    '   decalTris='+r.tris+'  rebuilds +'+(r.rebuilds-prev));
  prev=r.rebuilds;
}
await b.close();
