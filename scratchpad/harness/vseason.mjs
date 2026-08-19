import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(process.argv[2],{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(()=>{
  const w=window.__lemWorld, v=w.subsystems.get('vegetation');
  return {weather: JSON.parse(JSON.stringify(w.weather)),
          season: v.shared.uVegSeason.value, wet: v.shared.uVegWet.value,
          snow: v.shared.uVegSnow.value, wind: v.shared.uVegWind.value,
          plantFloor: v.plantFloor, waterLevel: v.waterLevel,
          trees: v.trees.reduce((a,e)=>a+e.list.length,0),
          near: v.trees.reduce((a,e)=>a+e.near.count,0),
          far: v.trees.reduce((a,e)=>a+e.far.count,0),
          grass: v.grass.count,
          vegDraws: v.meshes.filter(m=>m.count>0).length,
          vegTris: Math.round(v.meshes.reduce((a,m)=>a+(m.geometry.index?m.geometry.index.count/3:0)*m.count,0)),
          stats: w.stats()};
})));
await b.close();
