/* tk-cast.mjs — WHY is there no shadow under a tank? Reports, per buildings
 * mesh: castShadow now, lemCastBase, cascade layers, the distance from the near
 * shadow box centre in the LIGHT's own plane against the reach `_nearCull`
 * uses, and the near-box weight the shader computes at the site centre (which
 * is what decides whether the coarse cascades are allowed to contribute).
 *
 *   node tk-cast.mjs [--cam wide] [--time 9]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'wide', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
          + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(9000);
console.log(JSON.stringify(await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), B = w.subsystems.get('buildings');
  const r1 = v => +v.toFixed(1);
  const fit = gi._shadowFit;
  const rx = gi.uniforms.lemLightRight.value, ru = gi.uniforms.lemLightUp.value;
  const nc = gi.uniforms.lemNearCentre.value, nr = gi.uniforms.lemNearRadius.value;
  const boxW = (p, centre, radius) => {
    const dx = p.x - centre.x, dy = p.y - centre.y, dz = p.z - centre.z;
    const q = Math.max(Math.abs(dx * rx.x + dy * rx.y + dz * rx.z),
                       Math.abs(dx * ru.x + dy * ru.y + dz * ru.z));
    const t = Math.min(1, Math.max(0, (q - radius * 0.80) / (radius * 0.97 - radius * 0.80)));
    return 1 - t * t * (3 - 2 * t);
  };
  const out = {
    nearFit: fit ? {r: r1(fit.radius), c: [fit.centre.x, fit.centre.y, fit.centre.z].map(r1)} : null,
    nearUniform: {r: r1(nr), c: [nc.x, nc.y, nc.z].map(r1)},
    csm: gi._csm.map(c => ({layer: c.layer, casters: c.casters.length,
      ready: c.ready, box: c.box ? {r: r1(c.box.w || c.box.radius || 0)} : null})),
    cullable: gi._cullable.length,
    sunCast: gi.sun.castShadow,
    sunShadowRadius: gi.sun.shadow && gi.sun.shadow.camera
      ? r1(gi.sun.shadow.camera.right) : null,
    sites: [], veg: [],
  };
  for (const [uid, s] of B.sites) {
    const p = s.root.position;
    const row = {uid, arch: s.materials.arch, nearWeight: +boxW(p, nc, nr).toFixed(3), meshes: []};
    if (fit) {
      const dx = p.x - fit.centre.x, dy = p.y - fit.centre.y, dz = p.z - fit.centre.z;
      row.qRight = r1(Math.abs(dx * rx.x + dy * rx.y + dz * rx.z));
      row.qUp = r1(Math.abs(dx * ru.x + dy * ru.y + dz * ru.z));
    }
    for (const m of s.meshes) {
      if (!/:(steel|concrete|brick|rust)$/.test(m.name)) continue;
      row.meshes.push({n: m.name.split(':').pop(), cast: m.castShadow,
        base: m.userData.lemCastBase, size: r1(m.userData.lemCast?.size || 0),
        reach: fit ? r1(fit.radius + (m.userData.lemCast?.size || 0) + 6) : null,
        inCullable: gi._cullable.includes(m),
        L: [6, 7].filter(x => m.layers.isEnabled(x))});
    }
    out.sites.push(row);
  }
  w.scene.traverse(o => {
    if (!o.isInstancedMesh) return;
    if (out.veg.length > 6) return;
    if (!/tree|veg|canopy|trunk|shrub/i.test(o.name || '')) return;
    out.veg.push({name: o.name, cast: o.castShadow, base: o.userData.lemCastBase,
      L: [6, 7].filter(x => o.layers.isEnabled(x))});
  });
  return out;
}), null, 1));
if (errs.length) console.log('errors', errs);
await b.close();
