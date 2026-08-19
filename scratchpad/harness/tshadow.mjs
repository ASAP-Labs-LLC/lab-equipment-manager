/* tshadow.mjs — who actually gets drawn into the shadow map?
 *
 * Six rounds of critics have said the rolling stock casts nothing while the
 * trackside furniture two metres away casts correctly. `castShadow` is set in
 * trains.js on every body and both instanced pools, so the flag is not the
 * question. This wraps the renderer's shadow pass and tallies the objects that
 * are actually submitted to it, so the answer comes from the draw call rather
 * than from reading the source.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}

const MODS = args.mods || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=${args.cam || 'yard'}` +
  `&time=${args.time || 16}&weather=clear&hud=0&quality=${args.quality || 'ultra'}`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await (await browser.newContext({viewport: {width: 1280, height: 720}})).newPage();
page.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load'});
await page.waitForFunction(() => window.__lemWorld?.subsystems?.size > 0, null, {timeout: 60000});
await page.waitForTimeout(4000);

const out = await page.evaluate(async (seconds) => {
  const w = window.__lemWorld;
  const r = w.engine.renderer;
  const tally = {};          // name -> {shadow, beauty}
  const label = o => {
    let n = o.name || o.type;
    let p = o, chain = [];
    while (p) { chain.push(p.name || p.type); p = p.parent; }
    return n + ' @ ' + chain.slice(-4).join('/');
  };

  let inShadow = false;
  const sm = r.shadowMap;
  const origRender = sm.render;
  sm.render = function (...a) {
    inShadow = true;
    try { return origRender.apply(this, a); } finally { inShadow = false; }
  };
  const origRBD = r.renderBufferDirect.bind(r);
  r.renderBufferDirect = function (cam, scene, geo, mat, obj, group) {
    const k = label(obj);
    (tally[k] || (tally[k] = {shadow: 0, beauty: 0}))[inShadow ? 'shadow' : 'beauty']++;
    return origRBD(cam, scene, geo, mat, obj, group);
  };

  /* Force the map to be redrawn a few times so the tally is not empty. */
  let updates = 0;
  const t0 = performance.now();
  let n = 0;
  while (performance.now() - t0 < seconds * 1000) {
    if (n++ % 4 === 0) { w.engine.shadowNeedsUpdate = true; updates++; }
    await new Promise(r2 => requestAnimationFrame(r2));
  }

  /* And a static description of the trains' own meshes. */
  const trains = w.subsystems.get('trains');
  const meshes = [];
  trains?.root?.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh)) return;
    meshes.push({
      name: label(o), cast: !!o.castShadow, recv: !!o.receiveShadow,
      visible: o.visible, parentVisible: (() => { let p = o; while (p) { if (!p.visible) return false; p = p.parent; } return true; })(),
      mask: o.layers.mask, frustumCulled: o.frustumCulled,
      count: o.isInstancedMesh ? o.count : 1,
      castBase: o.userData.lemCastBase,
      customDepth: !!o.customDepthMaterial,
      mat: o.material?.type, transparent: !!o.material?.transparent,
      depthWrite: o.material?.depthWrite,
      radius: o.geometry?.boundingSphere?.radius ?? null,
    });
  });

  const gi = w.subsystems.get('gi');
  const info = {
    updates,
    camMask: w.engine.camera.layers.mask,
    sunCast: gi?.sun?.castShadow,
    shadowType: r.shadowMap.type, enabled: r.shadowMap.enabled,
    tier: w.engine.tier.name,
    csm: (gi?._csm || []).map(c => ({i: c.i, layer: c.layer, casters: c.casters.length, dirty: c.dirty})),
    fit: gi?._shadowFit ? {r: gi._shadowFit.radius, c: gi._shadowFit.centre.toArray().map(n => +n.toFixed(1))} : null,
  };
  return {info, meshes, tally};
}, parseFloat(args.seconds || '3'));

const shadowed = Object.entries(out.tally).filter(([, v]) => v.shadow > 0);
console.log('== info', JSON.stringify(out.info, null, 1));
console.log('== train meshes');
for (const m of out.meshes) console.log(JSON.stringify(m));
console.log('== objects entering the shadow pass:', shadowed.length);
for (const [k, v] of shadowed.sort((a, b) => b[1].shadow - a[1].shadow)) {
  console.log(String(v.shadow).padStart(6), k.slice(0, 110));
}
console.log('== drawn in beauty but NEVER in shadow');
for (const [k, v] of Object.entries(out.tally)) {
  if (v.shadow === 0) console.log(String(v.beauty).padStart(6), k.slice(0, 110));
}
await browser.close();
