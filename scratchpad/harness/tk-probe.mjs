/* tk-probe.mjs — is the tank farm lit, is it enrolled, and are its cylinders
 * smooth-shaded? Adapted from gi-land.mjs (which asks the same of the terrain).
 *
 *   node tk-probe.mjs [cam] [time]
 */
import {chromium} from 'playwright';
const cam = process.argv[2] || 'far';
const time = process.argv[3] || '9';
const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=${cam}&time=${time}&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await (await b.newContext({viewport: {width: 1600, height: 900}})).newPage();
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto(URL, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(9000);
const out = await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const B = w.subsystems.get('buildings');
  const r3 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(3) : v;
  const res = {buildStable: window.__buildStable !== false, sites: [], sun: null,
               cascades: null, steelMeshes: [], normals: [], cascadeCounts: {}};
  /* the sun */
  const sun = gi && gi.sun;
  if (sun) {
    const d = sun.position.clone().normalize();
    res.sun = {x: r3(d.x), y: r3(d.y), z: r3(d.z),
               elevDeg: r3(Math.asin(d.y) * 180 / Math.PI),
               intensity: r3(sun.intensity), colour: sun.color.getHexString(),
               castShadow: sun.castShadow};
  }
  const amb = [];
  w.scene.traverse(o => { if (o.isLight && !o.isDirectionalLight)
    amb.push({type: o.type, intensity: r3(o.intensity)}); });
  res.ambient = amb;
  res.envIntensity = r3(w.scene.environmentIntensity ?? 1);
  res.hasEnv = !!w.scene.environment;

  if (gi && gi._csm) res.cascades = gi._csm.map(c => ({
    i: c.i, layer: c.layer, size: c.spec.size, cap: c.spec.cap,
    casters: c.casters.length,
    maxCasters: (c.spec && 0) || null}));

  /* which sites are tank farms */
  for (const [uid, s] of (B ? B.sites : new Map())) {
    res.sites.push({uid, arch: s.materials && s.materials.arch,
                    x: r3(s.root.position.x), y: r3(s.root.position.y),
                    z: r3(s.root.position.z)});
  }
  /* every buildings mesh: flags + layer membership */
  const LAY = [6, 7];
  w.scene.traverse(o => {
    if (!o.isMesh) return;
    const nm = o.name || '';
    if (!/:(steel|rust|concrete)$/.test(nm)) return;
    const g = o.geometry;
    if (!g.boundingSphere) g.computeBoundingSphere();
    if (!g.boundingBox) g.computeBoundingBox();
    const bb = g.boundingBox;
    res.steelMeshes.push({name: nm, cast: o.castShadow, recv: o.receiveShadow,
      lemCastBase: o.userData.lemCastBase, noShadow: !!o.userData.noShadow,
      r: r3(g.boundingSphere.radius), rise: r3(bb.max.y - bb.min.y),
      layers: LAY.filter(L => o.layers.isEnabled(L)),
      tris: g.index ? g.index.count / 3 : g.attributes.position.count / 3,
      mat: {rough: r3(o.material.roughness), metal: r3(o.material.metalness),
            colour: o.material.color.getHexString(),
            flat: !!o.material.flatShading,
            envI: r3(o.material.envMapIntensity),
            hasRoughMap: !!o.material.roughnessMap,
            hasNormalMap: !!o.material.normalMap}});
  });
  for (const L of LAY) {
    let n = 0; const samp = [];
    w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.layers.isEnabled(L)) {
      n++; if (samp.length < 40) samp.push(o.name || '(unnamed)'); } });
    res.cascadeCounts[L] = {count: n, sample: samp};
  }
  return res;
});
out.pageErrors = errs;
console.log(JSON.stringify(out, null, 1));
await b.close();
