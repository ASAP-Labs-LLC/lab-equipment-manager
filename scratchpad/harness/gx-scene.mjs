/* gx-scene.mjs — name/bbox census of the industrial plant, and where the sun
 * says its shadows must land. Read-only. */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${a.cam || 'far'}&time=${a.time || '9'}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(9000);

console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const T = w.THREE || window.THREE;
  const rows = [];
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
    let own = [], n = o;
    while (n) { if (n.name) own.push(n.name); n = n.parent; }
    const g = o.geometry;
    let bb = null;
    try {
      if (!g.boundingBox) g.computeBoundingBox();
      bb = g.boundingBox;
    } catch (e) { void e; }
    o.updateWorldMatrix(true, false);
    const e2 = o.matrixWorld.elements;
    const wp = [e2[12], e2[13], e2[14]];
    rows.push({
      chain: own.slice(0, 4).join('<'),
      inst: o.isInstancedMesh ? o.count : 1,
      cast: o.castShadow, base: o.userData?.lemCastBase,
      l6: o.layers.isEnabled(6), l7: o.layers.isEnabled(7),
      size: +(o.userData?.lemCast?.size || 0).toFixed(1),
      rise: +(o.userData?.lemCast?.rise || 0).toFixed(1),
      ext: bb ? [+(bb.max.x - bb.min.x).toFixed(1), +(bb.max.y - bb.min.y).toFixed(1),
                 +(bb.max.z - bb.min.z).toFixed(1)] : null,
      wp: wp.map(v => +v.toFixed(1)),
      mat: (Array.isArray(o.material) ? o.material[0] : o.material)?.name || '',
      vis: o.visible,
    });
  });
  return {
    n: rows.length,
    sunDir: gi.sunDirection.toArray().map(v => +v.toFixed(4)),
    elevDeg: +(Math.asin(gi.sunDirection.y) * 180 / Math.PI).toFixed(2),
    camPos: w.camera.position.toArray().map(v => +v.toFixed(1)),
    plan: (w.plan?.stations || []).map(s => ({uid: s.uid, x: s.x, z: s.z})).slice(0, 12),
    hub: w.plan?.hub ? {x: w.plan.hub.x, z: w.plan.hub.z} : null,
    rows,
  };
}), null, 0));
await b.close();
