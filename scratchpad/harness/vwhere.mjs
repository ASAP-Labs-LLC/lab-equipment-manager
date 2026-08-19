/* vwhere.mjs — where the camera actually is, and what the LOD tiers therefore
 * see. Every radius in vegetation.js is a distance from something, and three
 * rounds of this file have tuned one against a distance nobody measured.
 *
 * Also re-audits the outer wood against the card each grove is REALLY drawn at
 * — `visl.mjs` tests a fixed 26 m reach, which is right for a full clump and
 * wrong for the narrowed ones, so it reports a headland stand as water when the
 * painting on it is fifteen metres across.
 */
import {chromium} from 'playwright';
const arg = k => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : null; };
const cam = (process.argv[2] && !process.argv[2].startsWith('--')) ? process.argv[2] : 'wide';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}&time=16&hud=0&quality=${arg('quality') || 'ultra'}`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(5000);
const o = await p.evaluate(() => {
  const W = window.__lemWorld, v = W.subsystems.get('vegetation'), t = W.subsystems.get('terrain');
  const c = W.camera, rig = W.rig || v.ctx.rig;
  const tgt = rig?.target;
  const pc = {x: tgt ? c.position.x + (tgt.x - c.position.x) * 0.4 : c.position.x,
              z: tgt ? c.position.z + (tgt.z - c.position.z) * 0.4 : c.position.z};
  const out = {cam: {x: +c.position.x.toFixed(1), y: +c.position.y.toFixed(1), z: +c.position.z.toFixed(1)},
               target: tgt ? {x: +tgt.x.toFixed(1), z: +tgt.z.toFixed(1)} : null,
               pivotToCam: tgt ? +Math.hypot(c.position.x - tgt.x, c.position.z - tgt.z).toFixed(1) : null,
               partitionCentre: {x: +pc.x.toFixed(1), z: +pc.z.toFixed(1)},
               centreToCam: +Math.hypot(c.position.x - pc.x, c.position.z - pc.z).toFixed(1)};
  /* Eye distance of every tree that is currently in the near set. */
  const eye = [];
  for (const e of v.trees || []) {
    const M = e.near.instanceMatrix.array;
    for (let i = 0; i < e.near.count; i++) {
      eye.push(Math.hypot(M[i * 16 + 12] - c.position.x, M[i * 16 + 14] - c.position.z));
    }
  }
  eye.sort((a, z) => a - z);
  const pct = q => eye.length ? +eye[Math.min(eye.length - 1, Math.floor(q * eye.length))].toFixed(0) : null;
  out.nearEye = {n: eye.length, p10: pct(0.1), p50: pct(0.5), p90: pct(0.9), max: pct(0.999)};

  /* The outer wood against the card it is really drawn at. */
  const wy = Number.isFinite(t?.waterY) ? t.waterY : -1e6;
  const g = (x, z) => { try { const h = t.heightAt(x, z); return Number.isFinite(h) ? h : 0; } catch { return 0; } };
  let over = 0, n = 0, half = 0, hmax = 0, inB = 0;
  const blds = [];
  try { W.subsystems.get('buildings')?.sites?.forEach(s => { const q = s?.root?.position;
    if (q && Number.isFinite(s.radius)) blds.push({x: q.x, z: q.z, r: s.radius}); }); } catch {}
  for (const gv of v.groves || []) {
    for (let i = 0; i < gv.count; i++) {
      /* Horizontal scale is the length of the matrix's first column. */
      const m = gv.mats, o0 = i * 16;
      const sx = Math.hypot(m[o0], m[o0 + 1], m[o0 + 2]);
      const hw = 58 * 0.5 * sx;
      n++; half += hw; if (hw > hmax) hmax = hw;
      const x = gv.xs[i], z = gv.zs[i];
      let bad = false;
      for (let k = 0; k < 8; k++) { const a = k * 0.7854;
        if (g(x + Math.cos(a) * hw, z + Math.sin(a) * hw) < wy) { bad = true; break; } }
      if (bad) over++;
      for (const B of blds) { const dx = x - B.x, dz = z - B.z;
        if (dx * dx + dz * dz < (B.r + hw) * (B.r + hw)) { inB++; break; } }
    }
  }
  out.groves = {n, overWater: over, throughBuilding: inB,
                halfWidthMean: +(half / Math.max(1, n)).toFixed(1), halfWidthMax: +hmax.toFixed(1)};
  out.counts = {trees: (v.trees || []).reduce((a, e) => a + e.list.length, 0),
                nearDrawn: eye.length,
                farDrawn: (v.trees || []).reduce((a, e) => a + e.far.count, 0),
                groveDrawn: (v.groves || []).reduce((a, q) => a + q.mesh.count, 0)};
  return out;
});
console.log(JSON.stringify(o, null, 1));
await b.close();
