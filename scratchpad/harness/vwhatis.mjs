/* vwhatis.mjs — raycast the camera through given screen pixels and name every
 * object the ray meets, nearest first. The only honest way to find out what a
 * pale slab in a canopy actually is. */
import {chromium} from 'playwright';
const url = process.argv[2];
const pts = JSON.parse(process.argv[3]);   // [[px,py], ...] in a 1920x1080 frame
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
await p.goto(url,{waitUntil:'load',timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true,null,{timeout:45000});
await p.waitForTimeout(5000);
const out = await p.evaluate(({pts}) => {
  const w = window.__lemWorld, v = w.subsystems.get('vegetation');
  const THREE = v.ctx.THREE;
  const rc = new THREE.Raycaster();
  rc.far = 4000;
  const res = [];
  for (const [px, py] of pts) {
    const nd = new THREE.Vector2(px / 1920 * 2 - 1, -(py / 1080 * 2 - 1));
    rc.setFromCamera(nd, w.camera);
    const hits = rc.intersectObjects(w.scene.children, true).slice(0, 6);
    res.push({px, py, hits: hits.map(h => ({
      d: Math.round(h.distance),
      name: h.object.name || h.object.type,
      mat: h.object.material && (h.object.material.name || h.object.material.type),
      inst: h.instanceId === undefined ? null : h.instanceId,
    }))});
  }
  return res;
}, {pts});
console.log(JSON.stringify(out, null, 1));
await b.close();
