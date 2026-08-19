/* vegprobe.mjs — what vegetation actually has in the scene, and how far out. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(5000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, cam = w.camera;
  const out = [];
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh)) return;
    const g = o.geometry;
    const gr = g?.boundingSphere?.radius;
    let far = null, near = null, n = o.isInstancedMesh ? o.count : 0;
    if (o.isInstancedMesh && n) {
      const arr = o.instanceMatrix.array;
      let mx = 0, mn = 1e9;
      for (let i = 0; i < n; i++) {
        const x = arr[i * 16 + 12], y = arr[i * 16 + 13], z = arr[i * 16 + 14];
        const d = Math.hypot(x - cam.position.x, y - cam.position.y, z - cam.position.z);
        if (d > mx) mx = d; if (d < mn) mn = d;
      }
      far = Math.round(mx); near = Math.round(mn);
    }
    let bb = g?.boundingBox;
    if (!bb && g?.boundingSphere && isFinite(g.boundingSphere.radius)) { try { g.computeBoundingBox(); bb = g.boundingBox; } catch (e) { bb = null; } }
    out.push({
      name: o.name || '?', type: o.isInstancedMesh ? 'inst' : 'mesh',
      count: n, cast: !!o.castShadow, cdm: !!o.customDepthMaterial,
      geoR: gr ? +gr.toFixed(1) : null,
      ext: bb ? [+(bb.max.x - bb.min.x).toFixed(1), +(bb.max.y - bb.min.y).toFixed(1),
                 +(bb.max.z - bb.min.z).toFixed(1)] : null,
      instNear: near, instFar: far,
      mat: (Array.isArray(o.material) ? o.material[0] : o.material)?.type,
      alphaTest: (Array.isArray(o.material) ? o.material[0] : o.material)?.alphaTest,
      transparent: (Array.isArray(o.material) ? o.material[0] : o.material)?.transparent,
    });
  });
  return {cam: cam.position.toArray().map(v => +v.toFixed(0)), objs: out};
}), null, 1));
await b.close();
