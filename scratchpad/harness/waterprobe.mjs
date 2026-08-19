import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1200,height:700}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation&cam=wide&time=16&hud=0&quality=ultra',{waitUntil:'load'});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:60000});
await p.waitForTimeout(4000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, terrain = w.subsystems.get('terrain');
  const THREE = Object.getPrototypeOf(w.camera).constructor;
  const out = {reportedWaterY: terrain && terrain.waterY};

  // Find the water as DRAWN: any mesh whose name or material hints at water.
  const waters = [];
  w.scene.traverse(o => {
    const n = ((o.name||'') + ' ' + (o.material && o.material.name || '')).toLowerCase();
    if (o.isMesh && /water|river|lake|sea/.test(n)) {
      o.geometry.computeBoundingBox();
      const bb = o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld);
      waters.push({name: o.name || o.material?.name, minY:+bb.min.y.toFixed(2),
                   maxY:+bb.max.y.toFixed(2),
                   spanX: Math.round(bb.max.x-bb.min.x), spanZ: Math.round(bb.max.z-bb.min.z)});
    }
  });
  out.waterMeshes = waters;
  const surfaceY = waters.length ? Math.max(...waters.map(x=>x.maxY)) : null;
  out.drawnWaterSurfaceY = surfaceY;

  // Now count trees against the DRAWN surface, sampling everything present.
  let total=0, below=0, lowest=1e9;
  const M = new (w.camera.matrixWorld.constructor)();
  const V = new (w.camera.position.constructor)();
  const grp = w.scene.getObjectByName('vegetation');
  if (grp) grp.traverse(o => {
    if (!o.isInstancedMesh) return;
    const step = Math.max(1, Math.floor(o.count/500));
    for (let i=0;i<o.count;i+=step) {
      total++; o.getMatrixAt(i,M); V.setFromMatrixPosition(M); o.localToWorld(V);
      lowest = Math.min(lowest, V.y);
      if (surfaceY !== null && V.y < surfaceY) below++;
    }
  });
  out.treesSampled = total; out.treesBelowDrawnWater = below;
  out.lowestTreeY = +lowest.toFixed(2);
  out.groundAtCentre = terrain && terrain.heightAt
    ? +terrain.heightAt(0,0).toFixed(2) : null;
  return out;
}), null, 1));
await b.close();
