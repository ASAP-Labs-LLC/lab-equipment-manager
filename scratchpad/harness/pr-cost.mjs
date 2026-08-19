/* pr-cost.mjs — what do the props actually cost?
 *
 * Method is vlodcost3.mjs's, because the alternative is how the grove LOD's
 * cost was got wrong: PAIRED (a fresh baseline brackets every ablation, so the
 * drift of a long-running page cancels), REPEATED (median of the paired
 * differences), with a ZERO-COST CONTROL that must read ~0, at 4K with vsync
 * and the frame-rate limiter off. At 1080p on this laptop the frame is pinned
 * to the display refresh and every ablation, including one that hides nothing,
 * reads 0.00 ms.
 *
 * Four things are measured and the last two are the ones a budget is built on:
 *
 *   control   hide nothing. Must read ~0 AND must have a tight spread. If it
 *             does not, nothing else on the page means anything.
 *   props     hide everything props built. Expected to be under the noise
 *             floor — a RESULT, not a failure, but not a number to budget with.
 *   geometry  ONE mesh, K instances. Divides out to a cost per instance.
 *   draws     D separate meshes of one instance each. Divides out to a cost per
 *             DRAW CALL, which is the quantity that actually governs how many
 *             prop TYPES this file may add — the frame's budget is ~200 draws,
 *             and every new prop type is one of them (two if it casts).
 *
 *   node pr-cost.mjs [cam] [K] [D]
 */
import {chromium} from 'playwright';

const CAM = process.argv[2] || 'far';
const K = parseInt(process.argv[3] || '4000', 10);
const D = parseInt(process.argv[4] || '100', 10);
const REPEATS = 25;
const WARM_MS = 18000;
const WINDOW_MS = 900;
const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
  '?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather' +
  '&cam=' + CAM + '&time=9&weather=clear&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist',
         '--disable-gpu-vsync', '--disable-frame-rate-limit']});
const p = await (await b.newContext({viewport: {width: 3840, height: 2160}})).newPage();
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(WARM_MS);

const setup = await p.evaluate(([K, D]) => {
  const w = window.__lemWorld;
  const pr = w.subsystems.get('props');
  const THREE = w.ctx.THREE;
  window.__propObjs = [];
  let tris = 0, meshes = 0;
  pr.group.traverse(o => {
    if (!o.isMesh && !o.isInstancedMesh) return;
    const g = o.geometry;
    const per = (g.index ? g.index.count : g.attributes.position.count) / 3;
    tris += per * (o.isInstancedMesh ? o.count : 1);
    meshes++;
    window.__propObjs.push(o);
  });

  const src = window.__propObjs.find(o => o.isInstancedMesh);
  const a = pr.beachAnchor || {x: 0, z: 0};
  const perTri = src
    ? (src.geometry.index ? src.geometry.index.count
       : src.geometry.attributes.position.count) / 3 : 0;
  const mtx = new THREE.Matrix4(), q = new THREE.Quaternion();
  const pos = new THREE.Vector3(), scl = new THREE.Vector3(1, 1, 1);

  /* THE GEOMETRY STRESS SET — one mesh, K instances, spread over the same
   * beach so it goes through the same shader, cascade and fog. */
  window.__geoStress = null;
  if (src) {
    const m = new THREE.InstancedMesh(src.geometry, src.material, K);
    const side = Math.ceil(Math.sqrt(K));
    for (let n = 0; n < K; n++) {
      const gx = a.x + ((n % side) - side / 2) * 3.0;
      const gz = a.z + (Math.floor(n / side) - side / 2) * 3.0;
      pos.set(gx, w.ctx.ground(gx, gz), gz);
      mtx.compose(pos, q, scl); m.setMatrixAt(n, mtx);
    }
    m.instanceMatrix.needsUpdate = true;
    m.name = 'props:stress-geo';
    m.castShadow = src.castShadow; m.receiveShadow = src.receiveShadow;
    m.computeBoundingSphere(); m.visible = false;
    w.scene.add(m); window.__geoStress = m;
  }

  /* THE DRAW-CALL STRESS SET — D meshes of ONE instance, same total shape as
   * D new prop types would be. Same geometry, same material, so the ONLY thing
   * that differs from one instanced mesh of D is the number of draws. */
  window.__drawStress = [];
  if (src) {
    const side = Math.ceil(Math.sqrt(D));
    for (let n = 0; n < D; n++) {
      const m = new THREE.InstancedMesh(src.geometry, src.material, 1);
      const gx = a.x + ((n % side) - side / 2) * 3.0;
      const gz = a.z + (Math.floor(n / side) - side / 2) * 3.0;
      pos.set(gx, w.ctx.ground(gx, gz), gz);
      mtx.compose(pos, q, scl); m.setMatrixAt(0, mtx);
      m.instanceMatrix.needsUpdate = true;
      m.name = 'props:stress-draw' + n;
      m.castShadow = src.castShadow; m.receiveShadow = src.receiveShadow;
      m.computeBoundingSphere(); m.visible = false;
      w.scene.add(m); window.__drawStress.push(m);
    }
  }
  const st = w.stats();
  return {meshes, tris, perTri, K, D,
          drawCalls: st.drawCalls, triangles: st.triangles, tier: st.tier,
          sites: (pr.umbrellaSites || []).length};
}, [K, D]);

console.log('cam=' + CAM + '  tier=' + setup.tier);
console.log('props: ' + setup.meshes + ' mesh(es), ' + setup.sites + ' umbrellas, ' +
  setup.perTri + ' tris each, ' + setup.tris + ' triangles total');
console.log('frame: ' + setup.drawCalls + ' draws, ' +
  setup.triangles.toLocaleString() + ' triangles');
console.log('stress: geometry ' + K + ' inst / ' +
  (K * setup.perTri).toLocaleString() + ' tris in ONE draw;  draws ' + D +
  ' meshes of one instance\n');

async function ms(pv, gv, dv) {
  await p.evaluate(([pv, gv, dv]) => {
    for (const o of window.__propObjs) o.visible = pv;
    if (window.__geoStress) window.__geoStress.visible = gv;
    for (const o of window.__drawStress) o.visible = dv;
  }, [pv, gv, dv]);
  await p.waitForTimeout(320);
  return await p.evaluate(win => new Promise(res => {
    const f = []; let last = performance.now(); const stop = last + win;
    const t = n => {
      f.push(n - last); last = n;
      if (n < stop) requestAnimationFrame(t);
      else { f.sort((a, b) => a - b); res(f[f.length >> 1]); }
    };
    requestAnimationFrame(t);
  }), WINDOW_MS);
}
const med = a => { a = [...a].sort((x, y) => x - y); return a[a.length >> 1]; };
/* The honest uncertainty on a machine three other rounds are also rendering on:
 * the inter-quartile range of the PAIRED DIFFERENCES. A median saving smaller
 * than half the IQR is not a measurement, it is a coin. */
const iqr = a => { a = [...a].sort((x, y) => x - y);
  return a[Math.floor(a.length * 0.75)] - a[Math.floor(a.length * 0.25)]; };

/* AN INSTRUMENT THAT CANNOT SEE THE FIELD IT SWITCHED OFF CANNOT MEASURE
 * SWITCHING IT OFF (REQUESTS.md, lesson 5). Before believing any saving, prove
 * each ablated state actually changes what the renderer submits. A stress set
 * that is frustum-culled reads as free and is measuring nothing at all. */
const states = [['baseline', 1, 0, 0], ['props off', 0, 0, 0],
                ['geo on', 1, 1, 0], ['draws on', 1, 0, 1]];
console.log('state verification — what the renderer actually submits:');
for (const [name, a, c, d] of states) {
  await ms(!!a, !!c, !!d);
  const s = await p.evaluate(() => {
    const r = window.__lemWorld.engine.renderer.info.render;
    return {calls: r.calls, tris: r.triangles};
  });
  console.log('  ' + name.padEnd(12) + ' draws ' + String(s.calls).padStart(6) +
    '   triangles ' + s.tris.toLocaleString());
}
console.log('');

/* [label, key, baseline(props,geo,draw), ablated(props,geo,draw)] */
const cases = [
  ['control  (hide nothing)',                      'control', [1, 0, 0], [1, 0, 0]],
  ['props    (the ' + setup.sites + ' that ship)',  'props',   [1, 0, 0], [0, 0, 0]],
  ['geometry (' + K + ' inst, 1 draw)',             'geo',     [1, 1, 0], [1, 0, 0]],
  ['draws    (' + D + ' meshes, ' + D + ' draws)',  'draw',    [1, 0, 1], [1, 0, 0]],
];
console.log('paired ablation, ' + REPEATS + ' repeats, 4K, vsync off, ' +
  WARM_MS / 1000 + 's warm-up\n');
const R = {};
for (const [label, key, bl, ab] of cases) {
  const d = [];
  for (let i = 0; i < REPEATS; i++) {
    const a = await ms(!!bl[0], !!bl[1], !!bl[2]);
    const c = await ms(!!ab[0], !!ab[1], !!ab[2]);
    d.push(a - c);
  }
  const s = med(d), base = await ms(!!bl[0], !!bl[1], !!bl[2]);
  R[key] = {saving: s, base, iqr: iqr(d), spread: [Math.min(...d), Math.max(...d)]};
  console.log(label.padEnd(32) + ' saves ' + s.toFixed(3) + ' +/- ' +
    (iqr(d) / 2).toFixed(3) + ' ms  (' +
    (100 * s / base).toFixed(2) + '% of a ' + base.toFixed(2) + ' ms frame)' +
    (key === 'control' ? '   <-- CONTROL, must read ~0' : ''));
}

const noise = Math.max(Math.abs(R.control.saving), R.control.iqr / 2);
console.log('\nnoise floor            ' + noise.toFixed(3) +
  ' ms  (|control| and half its IQR, whichever is larger)');
console.log('per umbrella           ' + (R.geo.saving / K * 1000).toFixed(3) +
  ' us   -> ' + (R.geo.saving / K * setup.sites * 1000).toFixed(1) +
  ' us for the ' + setup.sites + ' that ship');
console.log('per DRAW CALL          ' + (R.draw.saving / D * 1000).toFixed(1) +
  ' us   -> a prop type that is one instanced mesh costs this much before it ' +
  'draws a single triangle');
console.log('shipped set, measured  ' + R.props.saving.toFixed(3) + ' ms' +
  (Math.abs(R.props.saving) <= noise
    ? '  — AT OR UNDER THE NOISE FLOOR. The honest statement is "< ' +
      Math.max(noise, 0.01).toFixed(2) + ' ms", never "0.00 ms".' : ''));
await b.close();
