import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1000,height:600}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation&cam=wide&time=16&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, veg = w.subsystems.get('vegetation');
  const out = {tier: w.engine.tier.name, treeRange: w.engine.tier.treeRange,
               camFar: w.camera.far};
  // How far do actual tree instances reach from the site centre?
  const b2 = w.plan.bounds;
  const cx = (b2.minX + b2.maxX)/2, cz = (b2.minZ + b2.maxZ)/2;
  let maxR = 0, count = 0;
  const v3 = new (w.camera.position.constructor)();
  const grp = w.scene.getObjectByName('vegetation');
  if (grp) grp.traverse(o => {
    if (!o.isInstancedMesh) return;
    count += o.count;
    const m = new (w.camera.matrixWorld.constructor)();
    for (let i = 0; i < o.count; i += Math.max(1, Math.floor(o.count/400))) {
      o.getMatrixAt(i, m);
      v3.setFromMatrixPosition(m); o.localToWorld(v3);
      maxR = Math.max(maxR, Math.hypot(v3.x - cx, v3.z - cz));
    }
  });
  out.treeInstances = count;
  out.furthestTreeM = Math.round(maxR);
  // How far does the LAND go?
  const terrain = w.subsystems.get('terrain');
  let land = 0;
  if (terrain && terrain.heightAt) {
    for (let r = 100; r < 6000; r += 100) {
      if (isFinite(terrain.heightAt(cx + r, cz))) land = r;
    }
  }
  out.landReachesM = land;
  for (const k of ['range','drawDistance','maxDistance','RANGE','radius','extent'])
    if (veg && veg[k] !== undefined) out['veg.'+k] = veg[k];
  return out;
}), null, 1));
await b.close();
