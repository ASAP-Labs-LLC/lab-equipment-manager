import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1280,height:720}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:45000});
await p.waitForTimeout(3500);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, out = {lights: [], meshes: 0, casters: 0, receivers: 0, byModule: {}};
  w.scene.traverse(o => {
    if (o.isDirectionalLight || o.isSpotLight || o.isPointLight) {
      out.lights.push({type: o.type, castShadow: o.castShadow, intensity: +o.intensity.toFixed(3),
        mapSize: o.shadow ? [o.shadow.mapSize.width, o.shadow.mapSize.height] : null,
        cam: o.shadow && o.shadow.camera ? {l:o.shadow.camera.left, r:o.shadow.camera.right,
             t:o.shadow.camera.top, b:o.shadow.camera.bottom, n:o.shadow.camera.near, f:o.shadow.camera.far} : null,
        pos: [o.position.x|0, o.position.y|0, o.position.z|0],
        hasMap: !!(o.shadow && o.shadow.map)});
    }
    if (o.isMesh) {
      out.meshes++;
      if (o.castShadow) out.casters++;
      if (o.receiveShadow) out.receivers++;
    }
  });
  const r = w.engine.renderer;
  out.shadowMapEnabled = r.shadowMap.enabled;
  out.shadowMapType = r.shadowMap.type;
  out.shadowAutoUpdate = r.shadowMap.autoUpdate;
  out.shadowNeedsUpdate = r.shadowMap.needsUpdate;
  out.engineFlag = w.engine.shadowNeedsUpdate;
  return out;
}), null, 1));
await b.close();
