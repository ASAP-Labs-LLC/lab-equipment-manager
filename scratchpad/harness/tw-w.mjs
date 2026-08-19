/* tw-w.mjs — WHAT IS THE TAN ACTUALLY MADE OF?
 *
 * Before repainting "the bare ground" it is worth knowing which of the seven
 * layers is carrying it, because the answer decides where a second material has
 * to be inserted. Reads the SHIPPED vertex attributes off terrain-core (not a
 * re-run of `_splat`, so nothing can drift between the two), and buckets them on
 * the vertex's own position: elevation over the waterline, slope from the baked
 * normal, distance to the earthworks, and which bench band or riser it is in.
 */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 800, height: 450}});
if (process.argv.includes('--ablate'))
  await p.addInitScript(() => { window.__lemAblateSubstrate = true; });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);

const out = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const mesh = t.meshes.find(m => m.name === 'terrain-core');
  const g = mesh.geometry;
  const pos = g.getAttribute('position'), nor = g.getAttribute('normal');
  const A = g.getAttribute('splatA'), B = g.getAttribute('splatB');
  const X = g.getAttribute('aux');
  const W = g.getAttribute('aWork');
  const T = t._terrace;
  const risers = T ? T.risers.map(r => ({z0: r.z0, z1: r.z0 + r.run})) : [];
  const bins = {};
  const add = (k, i) => {
    const b = bins[k] || (bins[k] = {n: 0, v: new Float64Array(15)});
    b.n++;
    b.v[0] += A.getX(i); b.v[1] += A.getY(i); b.v[2] += A.getZ(i); b.v[3] += A.getW(i);
    b.v[4] += B.getX(i); b.v[5] += B.getY(i); b.v[6] += B.getZ(i); b.v[7] += B.getW(i);
    b.v[8] += X.getX(i); b.v[9] += X.getY(i); b.v[10] += X.getZ(i); b.v[11] += X.getW(i);
    if (W) { b.v[12] += W.getX(i); b.v[13] += W.getY(i); b.v[14] += W.getZ(i); }
  };
  const q = new Float32Array(4);
  const N = pos.count;
  for (let i = 0; i < N; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    const aw = y - t.waterY;
    if (aw < -0.5) continue;
    const ny = nor.getY(i);
    const deg = Math.acos(Math.min(1, ny)) * 180 / Math.PI;
    t._distances(x, z, q);
    const dFoot = Math.min(q[0], t._railDist(x, z));
    const nat = t._smoothBase(x, z);
    const cut = y - nat;
    let onRiser = false;
    if (T && t._benchMask(x, z) > 0.6)
      for (const r of risers) if (z > r.z0 - 4 && z < r.z1 + 4) onRiser = true;
    if (aw <= 2.5 && ny > 0.86) add('wetBand', i);
    if (aw > 3.2 && aw < 8 && ny > 0.90 && dFoot > 60) add('dryBeach', i);
    if (aw > 16 && deg < 12 && dFoot > 90) add('plateauNat', i);
    if (aw > 10 && deg >= 12 && deg < 30 && dFoot > 90) add('dirtFlank', i);
    if (t._benchMask(x, z) > 0.9 && !onRiser && deg < 4) add('benchPad', i);
    if (onRiser && deg > 12) add('benchFace', i);
    if (cut < -2.5 && dFoot < 40) add('cutGround', i);
    if (cut > 2.5 && dFoot < 40) add('fillGround', i);
    if (dFoot < 120 && dFoot > 20 && deg < 10) add('siteOpen', i);
  }
  const names = ['grass', 'forest', 'dirt', 'stone', 'asphalt', 'mud', 'dryGrass',
                 'rockRatio', 'puddle', 'canopy', 'onSite', 'shore',
                 'cutFace', 'fillFace', 'stream'];
  const rows = {};
  for (const k of Object.keys(bins)) {
    const b = bins[k], o = {n: b.n};
    for (let c = 0; c < 15; c++) o[names[c]] = +(b.v[c] / b.n).toFixed(3);
    rows[k] = o;
  }
  return {vertices: N, hasWork: !!W, rows};
});
console.log(JSON.stringify(out, null, 1));
await b.close();
