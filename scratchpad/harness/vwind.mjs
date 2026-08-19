/* vwind.mjs — is every triangle wound the way its own normals point?
 *
 *   node vwind.mjs
 *
 * "Rocks normals are flipped so it only renders the insides." A photograph
 * cannot settle that: an inside-out lump of stone at thirty metres looks like a
 * lump of stone, only lit wrongly, and the file sets THREE.DoubleSide on
 * several foliage materials which hides the same fault everywhere it is set.
 *
 * The geometry can settle it. For every triangle: cross(b-a, c-a) is where the
 * FRONT face looks, and the mean of the three vertex normals is where the
 * surface is declared to look. Their dot product is positive on a correctly
 * wound triangle and negative on an inverted one. Reported per mesh, with the
 * material's `side` beside it, because an inverted winding under DoubleSide is
 * a shading bug and an inverted winding under FrontSide is an invisible object.
 *
 * Flat cards are excluded from the verdict: a two-sided leaf quad is meant to
 * be seen from behind and its dot is exactly what it is. Only meshes whose
 * normals genuinely vary — the tubes and the boulders — are judged.
 */
import {chromium} from 'playwright';

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html?' +
  'mods=terrain,vegetation&cam=low&time=16&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(6000);

const out = await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  const rows = [];
  const seen = new Set();
  const walk = (obj, label) => {
    const g = obj.geometry;
    if (!g || seen.has(g.uuid)) return;
    seen.add(g.uuid);
    const pos = g.getAttribute('position'), nrm = g.getAttribute('normal');
    const idx = g.getIndex();
    if (!pos || !nrm || !idx) return;
    let agree = 0, disagree = 0, flat = 0;
    /* How much the normals vary over the mesh — a card's three vertices all
     * carry the same normal and the test means nothing there. */
    let nx = 0, ny = 0, nz = 0;
    for (let i = 0; i < nrm.count; i++) { nx += nrm.getX(i); ny += nrm.getY(i); nz += nrm.getZ(i); }
    const spread = 1 - Math.hypot(nx, ny, nz) / Math.max(1, nrm.count);
    for (let t = 0; t < idx.count; t += 3) {
      const a = idx.getX(t), b2 = idx.getX(t + 1), c = idx.getX(t + 2);
      const ax = pos.getX(a), ay = pos.getY(a), az = pos.getZ(a);
      const ux = pos.getX(b2) - ax, uy = pos.getY(b2) - ay, uz = pos.getZ(b2) - az;
      const vx = pos.getX(c) - ax, vy = pos.getY(c) - ay, vz = pos.getZ(c) - az;
      const gx = uy * vz - uz * vy, gy = uz * vx - ux * vz, gz = ux * vy - uy * vx;
      const mx = (nrm.getX(a) + nrm.getX(b2) + nrm.getX(c)) / 3;
      const my = (nrm.getY(a) + nrm.getY(b2) + nrm.getY(c)) / 3;
      const mz = (nrm.getZ(a) + nrm.getZ(b2) + nrm.getZ(c)) / 3;
      const d = gx * mx + gy * my + gz * mz;
      const gl = Math.hypot(gx, gy, gz), ml = Math.hypot(mx, my, mz);
      if (gl < 1e-9 || ml < 1e-6) { flat++; continue; }
      const cos = d / (gl * ml);
      if (cos > 0.02) agree++; else if (cos < -0.02) disagree++; else flat++;
    }
    const mat = Array.isArray(obj.material) ? obj.material[0] : obj.material;
    const side = mat ? (mat.side === 2 ? 'double' : mat.side === 1 ? 'back' : 'front') : '?';
    rows.push({mesh: label, tris: idx.count / 3, agree, disagree, flat,
               normalSpread: +spread.toFixed(3), side,
               /* A closed solid is unanimous: every triangle of a boulder or a
                * tube agrees with its own normals, or none of them do. A crown
                * card mesh fans its normals outward from the crown centre on
                * purpose, so it lands somewhere near half and stays there. The
                * threshold is 90%, not 50%, precisely so a card that happens to
                * come out at 54% is not called an inversion. */
               verdict: disagree > 0.9 * (agree + disagree) ? 'INVERTED'
                      : disagree > 0.1 * (agree + disagree) ? 'bent cards'
                      : disagree ? 'mixed' : 'ok'});
  };
  (veg.trees || []).forEach((e, i) => {
    const nm = e.spec.name || e.spec.kind || ('sp' + i);
    walk(e.near, `tree.canopy ${nm}${i}`);
    if (e.trunk) walk(e.trunk, `tree.trunk ${nm}${i}`);
    walk(e.far, `tree.far ${nm}${i}`);
  });
  const names = ['bush', 'fern', 'dead', 'stump', 'log', 'rock', 'marram'];
  (veg.clutter || []).forEach((c, i) => walk(c.mesh, 'clutter.' + (names[i] || i)));
  (veg.sward || []).forEach((s, i) => walk(s.mesh, 'sward.' + i));
  (veg.groves || []).forEach((g, i) => walk(g.mesh, 'grove.' + i));
  return rows;
});

/* Only meshes with genuinely varying normals can be judged; a flat card's
 * winding is not a fact about its shading. */
const solid = out.filter(r => r.normalSpread > 0.15);
console.log('mesh                        tris  agree  disagree   side     verdict');
for (const r of solid) {
  console.log(`${r.mesh.padEnd(26)} ${String(r.tris).padStart(5)} ${String(r.agree).padStart(6)} ` +
              `${String(r.disagree).padStart(9)}   ${r.side.padEnd(7)} ${r.verdict}`);
}
/* A crown card's normals are deliberately fanned outward from the crown centre,
 * so about half of any canopy mesh's triangles present their front face away
 * from their own mean normal. That is the `bend` option doing its job on a
 * two-sided material, not an inversion — so the verdict is INVERTED (a mesh
 * wound the wrong way throughout) and not `mixed`. */
const bad = solid.filter(r => r.verdict === 'INVERTED');
const mixed = solid.filter(r => r.verdict !== 'ok' && r.verdict !== 'INVERTED');
console.log('\nsolid meshes judged:', solid.length,
            ' inverted:', bad.length, ' mixed (bent-normal cards):', mixed.length);
console.log(bad.length ? 'FAIL' : 'PASS');
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
