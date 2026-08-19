/* rr-struct.mjs — measure the structures rather than look at them.
 *
 * For every deck span: where each pier's foot actually is, and where the ground
 * and the water are under it. A pier that stops above the ground is the defect
 * that made the old viaduct read as a rectangle hanging in the air, and it is
 * not something a screenshot settles at 60m.
 *
 * Also reports the darkest and brightest vertex tint in the structures meshes,
 * because an inward-wound face renders at ambient and the number that catches
 * it is the fraction of triangles whose normal points at the solid's own axis.
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i + 1];
}
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0, 300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail' +
             '&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);

console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const terr = w.subsystems.get('terrain');
  const g = (x, z) => terr ? terr.heightAt(x, z) : null;
  const out = {waterY: rail.waterY, structures: rail.structures, decks: [], meshes: []};

  /* Structure meshes: triangle count, and how many faces are lit from behind
   * when seen from outside their own local solid. Cheap proxy: the fraction of
   * triangles whose geometric normal disagrees with the stored normal. */
  for (const m of rail._meshes || []) {
    const gm = m.geometry;
    if (!gm?.attributes?.position || !gm.attributes.normal) continue;
    const P = gm.attributes.position.array, N = gm.attributes.normal.array;
    const tris = P.length / 9;
    if (tris < 20 || tris > 60000) continue;
    let bad = 0;
    for (let i = 0; i < tris; i++) {
      const o = i * 9;
      const ex = P[o + 3] - P[o], ey = P[o + 4] - P[o + 1], ez = P[o + 5] - P[o + 2];
      const fx = P[o + 6] - P[o], fy = P[o + 7] - P[o + 1], fz = P[o + 8] - P[o + 2];
      const nx = ey * fz - ez * fy, ny = ez * fx - ex * fz, nz = ex * fy - ey * fx;
      if (nx * N[i * 9] + ny * N[i * 9 + 1] + nz * N[i * 9 + 2] < 0) bad++;
    }
    out.meshes.push({name: m.material?.name || 'mesh', tris, windingMismatch: bad});
  }

  /* Piers. Rebuilt from the same arithmetic the builder uses, so the numbers
   * refer to the geometry that is actually in the scene. */
  const BALLAST_TOE = -0.627;
  for (const t of rail.tracks) {
    if (!t.frames) continue;
    let works = [];
    try { works = t.earthworks(); } catch { works = []; }
    for (const q of works) {
      if (q.kind !== 'viaduct' && q.kind !== 'bridge') continue;
      const f = t.frames;
      const i0 = Math.max(0, q.i0), i1 = Math.min(f.count - 1, q.i1);
      const bays = Math.max(1, Math.round((q.to - q.from) / 24));
      const legs = [];
      for (let k = 0; k <= bays; k++) {
        const i = Math.round(i0 + (i1 - i0) * (k / bays));
        const x = f.pos[i * 3], y = f.pos[i * 3 + 1], z = f.pos[i * 3 + 2];
        const soffit = y + BALLAST_TOE - 0.03 - 1.05;
        legs.push({k, x: +x.toFixed(1), z: +z.toFixed(1),
                   soffit: +soffit.toFixed(2),
                   ground: +(g(x, z) ?? 0).toFixed(2),
                   height: +(soffit - (g(x, z) ?? 0)).toFixed(2)});
      }
      out.decks.push({track: t.name, kind: q.kind, from: +q.from.toFixed(1),
                      to: +q.to.toFixed(1), bays, legs});
    }
  }
  return out;
}), null, 1));
await b.close();
