import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
p.on('console', m=>{const t=m.text(); if(/vegetation\]/.test(t)) console.log(t.slice(0,200));});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=wide&time=16&hud=0',{waitUntil:'load',timeout:90000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:90000});
await p.waitForTimeout(4000);
await p.evaluate(()=>window.__lemWorld.engine.setQualityMode('ultra'));
await p.waitForTimeout(2500);
console.log(JSON.stringify(await p.evaluate(()=>{
  const W=window.__lemWorld, v=W.subsystems.get('vegetation'), t=W.subsystems.get('terrain');
  const ln=v._lastNear, cam=W.camera.position;
  let stems=0, within=[0,0,0,0], rank99=0;
  const hist={};
  for(const e of v.trees) for(let i=0;i<e.list.length;i++){
    stems++;
    const d=Math.hypot(e.xs[i]-ln.x, e.zs[i]-ln.z);
    if(e.rank[i]>1) rank99++;
    if(d<225) within[0]++; if(d<150) within[1]++; if(d<400) within[2]++; if(d<620) within[3]++;
    const k=Math.floor(d/100)*100; hist[k]=(hist[k]||0)+1;
  }
  return {stems, rank99, within, lastNear:{x:+ln.x.toFixed(0),z:+ln.z.toFixed(0)}, cam:{x:+cam.x.toFixed(0),y:+cam.y.toFixed(0),z:+cam.z.toFixed(0)},
    hist, quality:v.quality, treeBudget:v._treeBudget, range:v.range,
    hMin:+v.hMin.toFixed(1), hMax:+v.hMax.toFixed(1), waterY:+v.waterY.toFixed(1), plantFloor:+v.plantFloor.toFixed(1),
    island:v.island, altUnit:v._altUnit, coast: v.coast?{n:v.coast.n}:null,
    coastAtCentre: +v._coastDist(v.island.cx, v.island.cz).toFixed(0),
    terrainIsland: t?.island||t?.islandRadius||null};
}),null,1));
await b.close();
