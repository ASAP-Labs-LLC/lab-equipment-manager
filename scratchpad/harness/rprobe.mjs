import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:900,height:520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(2000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, T = w.subsystems.get('terrain'), rail = w.subsystems.get('rail');
  const plan = w.plan;
  const out = {islandR: T.islandR, waterY: T.waterY, ringSize: T.ringSize,
               cx: T.cx, cz: T.cz, coastWobble: T.coastWobble,
               bounds: plan.bounds, hub: {x: plan.hub.x, z: plan.hub.z},
               stations: plan.stations.map(s=>({uid:s.uid, x:+s.x.toFixed(0), z:+s.z.toFixed(0)})),
               tracks: rail.tracks.map(t=>({name:t.name, len:+t.length.toFixed(0)}))};
  const h = (x,z)=>T.heightAt(x,z);
  // height grid over the whole island, 40m
  const R = Math.ceil((T.islandR+60)/40)*40;
  const grid = [];
  for (let z=T.cz-R; z<=T.cz+R; z+=40) {
    const row=[];
    for (let x=T.cx-R; x<=T.cx+R; x+=40) row.push(Math.round(h(x,z)));
    grid.push(row);
  }
  out.gridOrigin = {x:T.cx-R, z:T.cz-R, step:40};
  out.grid = grid;
  // how much of the ring corridor is under water?
  return out;
}), null, 0));
await b.close();
