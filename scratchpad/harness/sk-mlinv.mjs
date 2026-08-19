/* sk-mlinv.mjs — inventory only. What meshes are in the far frame, what
 * material does each carry, does it take scene.fog, and how big is it.
 * Read-only: it never mutates the page beyond pinning the rig.
 *
 *   node sk-mlinv.mjs
 */
import {chromium} from 'playwright';

const MODS = 'sky,gi,terrain,vegetation,buildings,rail,trains';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}`
          + `&cam=far&time=9&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null;
const t1 = Date.now();
while (Date.now() - t1 < 30000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => { const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 10) break;
}

const out = await page.evaluate(() => {
  const w = window.__lemWorld;
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  const rows = [];
  w.scene.traverse(o => {
    if (!o.isMesh && !o.isInstancedMesh && !o.isPoints && !o.isLine) return;
    const g = o.geometry, m = o.material;
    const ms = Array.isArray(m) ? m : [m];
    const bb = g?.boundingBox || (g?.computeBoundingBox(), g?.boundingBox);
    rows.push({
      name: o.name || '(anon)', parent: o.parent?.name || '',
      kind: o.isInstancedMesh ? 'instanced' : (o.isPoints ? 'points' : 'mesh'),
      count: o.isInstancedMesh ? o.count : 1,
      visible: o.visible, renderOrder: o.renderOrder,
      verts: g?.attributes?.position?.count ?? 0,
      tris: g?.index ? g.index.count / 3 : (g?.attributes?.position?.count ?? 0) / 3,
      mat: ms.map(x => x?.type).join('|'),
      fog: ms.map(x => String(x?.fog)).join('|'),
      depthTest: ms.map(x => String(x?.depthTest)).join('|'),
      bbox: bb ? [bb.min.x, bb.min.y, bb.min.z, bb.max.x, bb.max.y, bb.max.z].map(v => Math.round(v)) : null,
    });
  });
  const eng = w.engine;
  const cu = eng?._passes?.composite?.material?.uniforms;
  return {
    rows,
    tier: w.stats().tier, tierBloom: eng?.tier?.bloom,
    compositeUniforms: cu ? Object.keys(cu) : null,
    uBloom: cu?.uBloom?.value, uFilmGrain: cu?.uFilmGrain?.value,
    uExposure: cu?.uExposure?.value, uVignette: cu?.uVignette?.value,
    fog: w.scene.fog ? {type: w.scene.fog.type ?? w.scene.fog.constructor.name,
                        density: w.scene.fog.density,
                        color: [w.scene.fog.color.r, w.scene.fog.color.g, w.scene.fog.color.b]} : null,
    camera: {pos: [cam.position.x, cam.position.y, cam.position.z].map(v => +v.toFixed(1)),
             fov: cam.fov, near: cam.near, far: cam.far},
    terrainApi: Object.getOwnPropertyNames(Object.getPrototypeOf(w.subsystems.get('terrain'))).slice(0, 200),
    mainlandR: w.subsystems.get('terrain').mainlandR,
    islandR: w.subsystems.get('terrain').islandR,
    waterY: w.subsystems.get('terrain').waterY,
  };
});
console.log(JSON.stringify({...out, errors}, null, 1));
await b.close();
