/* gicsm.mjs — dump the cascade state out of a running solo page. */
import {chromium} from 'playwright';

const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';
const secs = parseFloat(process.argv[3] || '5');

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errs.push(m.text().slice(0, 300)); });
page.on('pageerror', e => errs.push('pageerror ' + String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(secs * 1000);

const out = await page.evaluate(() => {
  const w = window.__lemWorld;
  const gi = w.subsystems.get('gi');
  if (!gi) return {no: 'gi'};
  const u = gi.uniforms;
  const csm = (gi._csm || []).map(c => ({
    i: c.i, size: c.rt?.width, layer: c.layer, casters: c.casters.length,
    ready: c.ready, dirty: c.dirty, runs: c.runs, cost: c.cost, tris: c.tris,
    radius: c.radius,
    sample: c.casters.slice(0, 8).map(o => ({
      n: o.name || o.type, inst: o.count | 0,
      size: +(o.userData.lemCast?.size || 0).toFixed(1),
      rise: +(o.userData.lemCast?.rise || 0).toFixed(2),
      onLayer: o.layers.isEnabled(c.layer),
    })),
  }));
  // Layer census over the whole scene
  const census = {l6: 0, l7: 0, meshes: 0, casters: 0, cullable: (gi._cullable || []).length};
  const rejected = {noWant: 0, noMetrics: 0, slab: 0, big: 0, noDepth: 0, tooSmall: 0, ok: 0};
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
    census.meshes++;
    if (o.castShadow) census.casters++;
    if (o.layers.isEnabled(6)) census.l6++;
    if (o.layers.isEnabled(7)) census.l7++;
    const wants = o.userData?.lemCastBase ?? o.castShadow;
    if (!wants || o.userData?.noShadow) { rejected.noWant++; return; }
    const m = o.userData?.lemCast;
    if (!m) { rejected.noMetrics++; return; }
    if (m.slab) { rejected.slab++; return; }
    if (m.size > 400) { rejected.big++; return; }
    if (!gi._depthFor(o)) { rejected.noDepth++; return; }
    if (m.rise < 2.0 || m.size < 1.6) { rejected.tooSmall++; return; }
    rejected.ok++;
  });
  // material defines
  let far = 0, csm2 = 0, mats = 0;
  for (const m of gi.materials) {
    mats++;
    if (m.defines?.LEM_FAR_SHADOW !== undefined) far++;
    if (m.defines?.LEM_CSM2 !== undefined) csm2++;
  }
  return {
    tier: gi.tier?.name, modeKey: gi._modeKey,
    sun: {dir: gi.sunDirection?.toArray().map(v => +v.toFixed(3)),
          intensity: +(gi.sunIntensity || 0).toFixed(3),
          castShadow: gi.sun?.castShadow,
          mapSize: gi.sun?.shadow?.mapSize?.x,
          ortho: [gi.sun?.shadow?.camera?.left, gi.sun?.shadow?.camera?.right],
          autoUpdate: w.engine.renderer.shadowMap.autoUpdate,
          needsUpdate: w.engine.renderer.shadowMap.needsUpdate,
          enabled: w.engine.renderer.shadowMap.enabled,
          type: w.engine.renderer.shadowMap.type},
    nearReach: gi._nearReach, shadowFit: {r: gi._shadowFit?.radius,
      c: gi._shadowFit?.centre?.toArray().map(v => +v.toFixed(1))},
    uni: {nearRadius: u.lemNearRadius.value,
          ready0: u.lemCsmReady0.value, ready1: u.lemCsmReady1.value,
          box0: u.lemCsmBox0.value.toArray(),
          param0: u.lemCsmParam0.value.toArray(),
          param1: u.lemCsmParam1.value.toArray()},
    csm, census, rejected, mats, far, csm2,
    rig: {distance: w.rig.distance, pitch: w.rig.pitch, camY: +w.camera.position.y.toFixed(1)},
    draws: w.engine.drawCalls,
  };
});
console.log(JSON.stringify(out, null, 2));
if (errs.length) console.log('CONSOLE:', JSON.stringify(errs.slice(0, 12), null, 1));
await browser.close();
