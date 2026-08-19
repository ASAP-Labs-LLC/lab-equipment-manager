/* vcoast.mjs — prove the coast rules work before there is a coast.
 * terrain.js has not landed its island yet, so `_buildCoast` finds no sea and
 * every shore rule is inert. Raising the waterline by hand makes the same site
 * an island for one build and exercises the whole path: distance field, beach
 * and salt bands, the marram set, and the re-scatter onPlan would run. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
p.on('console', m=>{const t=m.text(); if(/vegetation\]/.test(t)) console.log(t.slice(0,220));});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=wide&time=16&hud=0',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const v=window.__lemWorld.subsystems.get('vegetation');
  const before={stems:0}; for(const e of v.trees) before.stems+=e.list.length;
  /* Flood it to twenty metres above the lowest ground: the low ring of the map
   * becomes sea and the site keeps its hill. */
  const wy = v.hMin + 20;
  v.waterY = wy; v.waterLevel = wy + 2.5; v.plantFloor = wy + 9;
  v._buildCoast();
  const C=v.coast; let sea=0, land=0, band=0;
  for(let k=0;k<C.D.length;k++){ if(C.D[k]===0) sea++; else { land++; if(C.D[k]<26) band++; } }
  v._regrow();
  let stems=0, drowned=0, onBeach=0, saltStunted=0, marram=0;
  for(const e of v.trees) for(let i=0;i<e.list.length;i++){
    stems++;
    const h=v._ground(e.xs[i],e.zs[i]);
    if(h<wy) drowned++;
    const d=v._coastDist(e.xs[i],e.zs[i]);
    if(d<26) onBeach++; else if(d<130) saltStunted++;
  }
  const shoreSet=v.clutter[v.clutter.length-1];
  let marramOffBeach=0;
  for(let i=0;i<shoreSet.count;i++){ marram++;
    if(v._coastDist(shoreSet.xs[i],shoreSet.zs[i])>130) marramOffBeach++; }
  return {waterY:+wy.toFixed(1), coastCells:{sea,land,band},
          before, after:{stems, drowned, onBeach, saltStunted},
          marram:{n:marram, offBeach:marramOffBeach},
          scatter:v._scatterStats};
}),null,1));
await b.close();
