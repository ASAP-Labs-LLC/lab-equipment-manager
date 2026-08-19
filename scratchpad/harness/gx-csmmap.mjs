/* gx-csmmap.mjs — LOOK AT THE COARSE CASCADE MAPS, and re-run the shader's own
 * lookup on the CPU at named ground points around the plant.
 *
 * Everything before this inferred the map's contents from the frame or from an
 * ablation that never took. This reads back the two RGBA-packed depth targets
 * gi.js draws itself (`gi._csm[i].rt`), writes each as a PNG, and then, for a
 * grid of ground points across the tank farm, evaluates exactly what
 * `lemFarShadow` evaluates: lemNearWeight, lemBoxWeight for cascade 0, and the
 * four-tap comparison against each map. Nothing is inferred.
 *
 *   node gx-csmmap.mjs [--cam far] [--time 9] [--out /tmp/gx]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const OUT = a.out || '/tmp/gx';
fs.mkdirSync(OUT, {recursive: true});
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(10000);
await p.screenshot({path: path.join(OUT, `beauty-${cam}-${time}.png`)});

const res = await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const rn = w.engine.renderer;
  const u = gi.uniforms;
  const d = gi.sunDirection;
  const right = u.lemLightRight.value, up = u.lemLightUp.value;
  const dot3 = (ax, ay, az, v) => ax * v.x + ay * v.y + az * v.z;
  const smoothstep = (e0, e1, x) => {
    const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
    return t * t * (3 - 2 * t);
  };
  const boxWeight = (px, py, pz, c, radius) => {
    const dx = px - c.x, dy = py - c.y, dz = pz - c.z;
    const q = Math.max(Math.abs(dot3(dx, dy, dz, right)), Math.abs(dot3(dx, dy, dz, up)));
    return 1 - smoothstep(radius * 0.80, radius * 0.97, q);
  };

  /* --- read both cascade targets back, whole --- */
  const maps = [];
  for (const c of gi._csm) {
    const N = c.rt.width;
    const buf = new Uint8Array(N * N * 4);
    let err = null;
    try { rn.readRenderTargetPixels(c.rt, 0, 0, N, N, buf); }
    catch (e) { err = String(e).slice(0, 140); }
    maps.push({c, N, buf, err});
  }
  const unpack = (buf, N, ix, iy) => {
    ix = Math.min(N - 1, Math.max(0, ix)); iy = Math.min(N - 1, Math.max(0, iy));
    const o = (iy * N + ix) * 4;
    return buf[o] / 255 + buf[o + 1] / 65025 + buf[o + 2] / 16581375 + buf[o + 3] / 4228250625;
  };
  /* four taps exactly as lemCascade does them, in texel offsets */
  const TAPS = [[-0.8, 0.4], [0.4, 0.8], [0.8, -0.4], [-0.4, -0.8]];
  const cascade = (m, wx, wy, wz, nx, ny, nz) => {
    const mat = gi.uniforms[`lemCsmMat${m.c.i}`].value.elements;
    const par = gi.uniforms[`lemCsmParam${m.c.i}`].value;
    const X = wx + nx * par.w, Y = wy + ny * par.w, Z = wz + nz * par.w;
    const cx = mat[0] * X + mat[4] * Y + mat[8] * Z + mat[12];
    const cy = mat[1] * X + mat[5] * Y + mat[9] * Z + mat[13];
    const cz = mat[2] * X + mat[6] * Y + mat[10] * Z + mat[14];
    const cw = mat[3] * X + mat[7] * Y + mat[11] * Z + mat[15];
    const px = cx / cw, py = cy / cw, pz = cz / cw;
    if (px < 0 || px > 1 || py < 0 || py > 1 || pz > 1 || pz < 0)
      return {s: 1, out: true, u: px, v: py, z: pz};
    const dd = pz - par.z;
    let s = 0, best = 1;
    for (const [tx, ty] of TAPS) {
      const uu = px + tx * par.x, vv = py + ty * par.y;
      const dep = unpack(m.buf, m.N, Math.floor(uu * m.N), Math.floor(vv * m.N));
      best = Math.min(best, dep);
      if (dd <= dep) s += 1;
    }
    return {s: s * 0.25, out: false, u: px, v: py, z: pz, map: best, d: dd};
  };

  /* --- the plant's ground points. Sample a grid over each site pad and also
   *     the point where each site's tallest mass must throw its shadow. --- */
  const sites = [];
  w.scene.traverse(o => { if (/^site:/.test(o.name || '')) sites.push(o); });
  const pts = [];
  for (const s of sites) {
    s.updateWorldMatrix(true, false);
    const e = s.matrixWorld.elements;
    const ox = e[12], oz = e[14];
    /* the shadow of a 20 m mass falls this far along -sun on the ground */
    const L = 20 / Math.max(0.05, d.y);
    for (const [lx, lz, tag] of [
      [0, 0, 'centre'],
      [-d.x * L, -d.z * L, 'shadowline'],
      [-d.x * L * 1.6, -d.z * L * 1.6, 'shadowline-far'],
      [d.x * L, d.z * L, 'sunward'],
      [60, 60, 'open'],
    ]) pts.push({site: s.name, tag, x: ox + lx, y: 5.2, z: oz + lz});
  }
  const near = {c: gi.uniforms.lemNearCentre.value, r: gi.uniforms.lemNearRadius.value};
  const box0 = gi.uniforms.lemCsmBox0.value;
  const rows = pts.map(q => {
    const nw = boxWeight(q.x, q.y, q.z, near.c, near.r);
    const b0 = boxWeight(q.x, q.y, q.z, {x: box0.x, y: box0.y, z: box0.z}, box0.w);
    const r1 = maps[1] ? cascade(maps[1], q.x, q.y, q.z, 0, 1, 0) : null;
    const r0 = maps[0] ? cascade(maps[0], q.x, q.y, q.z, 0, 1, 0) : null;
    let s = 1;
    if (r1 && gi.uniforms.lemCsmReady1.value > 0.5) s = r1.s;
    if (r0 && gi.uniforms.lemCsmReady0.value > 0.5) s = s + (r0.s - s) * b0;
    const far = s + (1 - s) * nw;
    return {site: q.site, tag: q.tag, x: +q.x.toFixed(1), z: +q.z.toFixed(1),
            nearW: +nw.toFixed(3), b0: +b0.toFixed(3),
            c0: r0 ? {s: r0.s, out: r0.out, u: +r0.u.toFixed(4), v: +r0.v.toFixed(4),
                      z: +r0.z.toFixed(5), map: r0.map === undefined ? null : +r0.map.toFixed(5)} : null,
            c1: r1 ? {s: r1.s, out: r1.out, u: +r1.u.toFixed(4), v: +r1.v.toFixed(4),
                      z: +r1.z.toFixed(5), map: r1.map === undefined ? null : +r1.map.toFixed(5)} : null,
            lemFarShadow: +far.toFixed(4)};
  });

  /* --- write each map out as a picture, min-filtered down to 1024 --- */
  const pngs = [];
  for (const m of maps) {
    if (m.err) { pngs.push({i: m.c.i, err: m.err}); continue; }
    const N = m.N, M = 1024, s = N / M;
    const cvs = document.createElement('canvas'); cvs.width = cvs.height = M;
    const g = cvs.getContext('2d');
    const img = g.createImageData(M, M);
    let nz = 0, lo = 1;
    for (let y = 0; y < M; y++) for (let x = 0; x < M; x++) {
      let v = 1;
      for (let j = 0; j < s; j++) for (let i2 = 0; i2 < s; i2++) {
        const sy = N - 1 - Math.min(N - 1, (y * s + j) | 0);
        const sx = Math.min(N - 1, (x * s + i2) | 0);
        v = Math.min(v, unpack(m.buf, N, sx, sy));
      }
      if (v < 0.999) nz++;
      lo = Math.min(lo, v);
      const o = (y * M + x) * 4;
      /* stretch the used depth range so a 3 % window is visible at all */
      const c8 = Math.max(0, Math.min(255, Math.round(v * 255)));
      img.data[o] = img.data[o + 1] = img.data[o + 2] = c8;
      img.data[o + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    pngs.push({i: m.c.i, occupancy: +(nz / (M * M)).toFixed(4), lo: +lo.toFixed(5),
               png: cvs.toDataURL('image/png')});
  }

  return {
    sun: d.toArray().map(v => +v.toFixed(4)),
    elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2),
    nearReach: gi._nearReach, nearRadius: near.r,
    nearCentre: near.c.toArray().map(v => +v.toFixed(1)),
    box0: box0.toArray().map(v => +v.toFixed(1)),
    ready: [gi.uniforms.lemCsmReady0.value, gi.uniforms.lemCsmReady1.value],
    param0: gi.uniforms.lemCsmParam0.value.toArray(),
    param1: gi.uniforms.lemCsmParam1.value.toArray(),
    cascades: gi._csm.map(c => ({i: c.i, radius: c.radius, casters: c.casters.length,
                                 tris: c.tris, cost: c.cost, runs: c.runs})),
    rows, pngs,
  };
});
const pngs = res.pngs; delete res.pngs;
console.log(JSON.stringify(res, null, 1));
for (const q of pngs) {
  if (q.err) { console.log('cascade', q.i, 'READ ERROR', q.err); continue; }
  const f = path.join(OUT, `csm${q.i}-${cam}-${time}.png`);
  fs.writeFileSync(f, Buffer.from(q.png.split(',')[1], 'base64'));
  console.log('cascade', q.i, 'occupancy', q.occupancy, 'lo', q.lo, '->', f);
}
await b.close();
