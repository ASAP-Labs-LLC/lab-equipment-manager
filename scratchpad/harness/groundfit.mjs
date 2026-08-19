/* How far is anything from the ground it is supposed to be standing on? */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1000,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=yard&time=15&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, terrain = w.subsystems.get('terrain');
  const rail = w.subsystems.get('rail');
  const out = {waterY: terrain && terrain.waterY};
  const H = (x,z) => terrain && terrain.heightAt ? terrain.heightAt(x,z) : null;

  // 1. Rails: sample each station's route and compare rail height to ground.
  const gaps = [];
  for (const st of w.plan.stations) {
    const r = rail && rail.route ? rail.route(st.uid) : null;
    if (!r) continue;
    const len = r.totalLength || r.len || (r.getLength && r.getLength());
    if (!len || !r.getPointAt) continue;
    for (let i = 0; i <= 40; i++) {
      const q = r.getPointAt(i/40);
      const g = H(q.x, q.z);
      if (g !== null && isFinite(g)) gaps.push(+(q.y - g).toFixed(2));
    }
  }
  gaps.sort((a,b)=>a-b);
  out.railAboveGround = gaps.length ? {
    min: gaps[0], median: gaps[Math.floor(gaps.length/2)],
    max: gaps[gaps.length-1], samples: gaps.length,
    over2m: gaps.filter(g => g > 2).length,
    over5m: gaps.filter(g => g > 5).length} : null;

  // 2. Trees below the waterline.
  let inWater = 0, treeTotal = 0;
  const grp = w.scene.getObjectByName('vegetation');
  const M = new (w.camera.matrixWorld.constructor)();
  const V = new (w.camera.position.constructor)();
  if (grp && terrain) grp.traverse(o => {
    if (!o.isInstancedMesh) return;
    const step = Math.max(1, Math.floor(o.count/300));
    for (let i = 0; i < o.count; i += step) {
      treeTotal++;
      o.getMatrixAt(i, M); V.setFromMatrixPosition(M); o.localToWorld(V);
      if (V.y < (terrain.waterY ?? -1e9)) inWater++;
    }
  });
  out.treesSampled = treeTotal;
  out.treesBelowWater = inWater;
  return out;
}), null, 1));
await b.close();
