/* tq-macro.mjs — IS THE RUT CHANNEL DOING ANYTHING ON THE TERRACE?
 *
 * `rut = macro.b * vAux.z * macroIn` is the only wheel-rut term in the ground
 * material, and tq-yard.mjs measured vAux.z as a CONSTANT 1.000 over every
 * terrace vertex (min = p50 = max). So the whole rut signal on the plateau is
 * the macro map's blue channel. This reads that channel where the terrace
 * actually is, at the resolution the shader reads it, before anything is
 * written against it.
 *
 *   node tq-macro.mjs
 */
import {chromium} from 'playwright';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 800, height: 450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);

const out = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const img = t.macroTex && t.macroTex.image;
  if (!img) return {error: 'no macroTex'};
  const S = img.width;
  const cv = document.createElement('canvas');
  cv.width = cv.height = S;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(img, 0, 0);
  const D = g.getImageData(0, 0, S, S).data;
  const half = t.coreSize / 2;
  const at = (x, z) => {
    const u = (x - (t.cx - half)) / t.coreSize, v = (z - (t.cz - half)) / t.coreSize;
    if (u < 0 || u > 1 || v < 0 || v > 1) return null;
    const X = Math.min(S - 1, Math.floor(u * S)), Y = Math.min(S - 1, Math.floor(v * S));
    const o = (Y * S + X) * 4;
    return [D[o] / 255, D[o + 1] / 255, D[o + 2] / 255];
  };

  const mesh = t.meshes.find(m => m.name === 'terrain-core');
  const pos = mesh.geometry.getAttribute('position');
  const nor = mesh.geometry.getAttribute('normal');
  const q = new Float32Array(4);
  const smoothstep = (a, b, x) => {
    const u = Math.max(0, Math.min(1, (x - a) / (b - a)));
    return u * u * (3 - 2 * u);
  };
  const bins = {};
  const add = (k, key, v) => {
    const c = bins[k] || (bins[k] = {});
    (c[key] || (c[key] = [])).push(v);
  };
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    if (y <= t.waterY) continue;
    const m = at(x, z);
    if (!m) continue;
    const bm = t._benchMask(x, z);
    t._distances(x, z, q);
    const gravel = Math.max(smoothstep(1.8, -1.5, q[2]), smoothstep(2, -3, q[3]) * 0.35) * 0.95;
    const asphalt = smoothstep(5, -5, q[1]) * 0.95 * (1 - smoothstep(4, -5, q[2]));
    const hard = Math.max(gravel, asphalt);
    const deg = Math.acos(Math.min(1, nor.getY(i))) * 180 / Math.PI;
    let k;
    if (bm >= 0.9) k = hard > 0.45 ? 'terraceHard' : (deg < 4 ? 'terraceOpenFlat' : 'terraceOpenSloped');
    else if (q[0] > 200) k = 'openCountry';
    else k = 'nearWorks';
    add(k, 'macroR', m[0]); add(k, 'macroG', m[1]); add(k, 'macroB', m[2]);
    /* the shader's own rut expression, with vAux.z and macroIn at their
     * measured values on this domain */
    add(k, 'rutTerm', m[2] * 1.0);
  }
  const pct = (v, q) => v[Math.min(v.length - 1, Math.floor(v.length * q))];
  const r3 = x => +(+x).toFixed(3);
  const res = {};
  for (const k of Object.keys(bins)) {
    const o = {n: bins[k].macroB.length};
    for (const key of Object.keys(bins[k])) {
      const v = bins[k][key].slice().sort((a, b) => a - b);
      o[key] = {min: r3(v[0]), p50: r3(pct(v, 0.5)), p90: r3(pct(v, 0.9)),
                p99: r3(pct(v, 0.99)), max: r3(v[v.length - 1]),
                mean: r3(v.reduce((s, x) => s + x, 0) / v.length),
                pctOver: {'0.10': r3(v.filter(x => x > 0.10).length / v.length),
                          '0.25': r3(v.filter(x => x > 0.25).length / v.length),
                          '0.50': r3(v.filter(x => x > 0.50).length / v.length)}};
    }
    res[k] = o;
  }
  return {macroSize: S, coreSize: t.coreSize, bins: res};
});

console.log(JSON.stringify(out, null, 1));
await b.close();
