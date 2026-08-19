/* rr-daylight.mjs — ground truth for "masonry standing in the air".
 *
 * rr-float.mjs reads `rail._footings`: it reports on the elements rail CHOSE to
 * publish, so an element that is never pushed is silently correct. This walks
 * the triangles that are actually in the scene, so it cannot be silent about
 * anything that was drawn.
 *
 * THE TEST — and getting to it took three tries, each of which measured
 * something real and answered a different question:
 *
 *   1. "downward-facing face above the ground" flags the tunnel barrel ceiling
 *      and every bridge soffit, both correct by design. 593 of 1116 m2.
 *   2. "...with no masonry under its centroid" flags every moulding — impost
 *      band, archivolt, coping — because a vertical wall face has no plan area
 *      and a downward ray never meets it. 140 m2, of which 133 was deck soffit.
 *   3. What a wall standing in the air actually is: rasterise all masonry into
 *      a plan grid and keep the LOWEST surface in each column. If the lowest
 *      masonry over a patch of ground is clear of that ground, that patch of
 *      structure terminates in nothing. A moulding shares its wall's column and
 *      the wall's column bottoms out in its own buried base, so mouldings are
 *      invisible to it; a wing wall hanging over a cutting is not.
 *
 * Deck soffits are legitimately airborne — that is what a span is — so the
 * result is split by the earthwork kind declared under each column. The number
 * for "the wing walls float" is the `tunnel/cut` line; the number for "the
 * abutment is buried" is on the other instrument, rr-abut.mjs.
 *
 *   node rr-daylight.mjs [--layouts 2] [--tol 0.4] [--cell 0.4]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[i + 1];
}
const layouts = parseInt(a.layouts || '2', 10);
const TOL = parseFloat(a.tol || '0.4');
const CELL = parseFloat(a.cell || '0.4');

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
p.on('pageerror', e => console.log('[pageerror]', String(e).slice(0, 300)));

for (let L = 0; L < layouts; L++) {
  await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail` +
               `&cam=top&time=13&hud=0&quality=ultra&layout=${L}&seed=${L}`,
               {waitUntil: 'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
  await p.waitForTimeout(3000);
  const r = await p.evaluate(({TOL, CELL}) => {
    const w = window.__lemWorld;
    const rail = w.subsystems.get('rail');
    const terr = w.subsystems.get('terrain');
    if (!terr || !rail) return {err: 'missing subsystem'};

    /* every declared span, so a column can be attributed */
    const spans = [];
    for (const t of rail.tracks) {
      if (!t.frames) continue;
      let ws = []; try { ws = t.earthworks(); } catch {}
      for (const s of ws) spans.push({t, s});
    }
    const attrib = (x, z) => {
      let best = null, bd = Infinity;
      for (const t of rail.tracks) {
        const f = t.frames; if (!f) continue;
        for (let i = 0; i < f.count; i += 2) {
          const dx = f.pos[i * 3] - x, dz = f.pos[i * 3 + 2] - z;
          const d = dx * dx + dz * dz;
          if (d < bd) { bd = d; best = {t, i}; }
        }
      }
      if (!best) return {kind: '?', where: '?', rail: 0};
      const s = best.i * best.t.frames.step;
      let kind = 'grade';
      for (const {t, s: sp} of spans) {
        if (t === best.t && s >= sp.from && s <= sp.to) kind = sp.kind;
      }
      return {kind, where: `${best.t.name}@${s.toFixed(0)}`,
              rail: best.t.frames.pos[best.i * 3 + 1], off: Math.sqrt(bd)};
    };

    const mesh = rail.root.getObjectByName('rail.structures.masonry');
    if (!mesh) return {err: 'no masonry mesh'};
    mesh.updateMatrixWorld(true);
    const g = mesh.geometry, pos = g.attributes.position, idx = g.index;
    const M = mesh.matrixWorld.elements;
    const N = idx ? idx.count : pos.count;

    /* lowest masonry surface per plan cell */
    const low = new Map();
    const key = (i, j) => i + ':' + j;
    const v = new Float64Array(9);
    for (let k = 0; k < N; k += 3) {
      for (let c = 0; c < 3; c++) {
        const j = idx ? idx.getX(k + c) : k + c;
        const x = pos.getX(j), y = pos.getY(j), z = pos.getZ(j);
        v[c * 3] = M[0] * x + M[4] * y + M[8] * z + M[12];
        v[c * 3 + 1] = M[1] * x + M[5] * y + M[9] * z + M[13];
        v[c * 3 + 2] = M[2] * x + M[6] * y + M[10] * z + M[14];
      }
      const x1 = v[0], y1 = v[1], z1 = v[2];
      const x2 = v[3], y2 = v[4], z2 = v[5];
      const x3 = v[6], y3 = v[7], z3 = v[8];
      const det = (z2 - z3) * (x1 - x3) + (x3 - x2) * (z1 - z3);
      if (Math.abs(det) < 1e-7) continue;              // vertical: no plan area
      const i0 = Math.floor(Math.min(x1, x2, x3) / CELL);
      const i1 = Math.floor(Math.max(x1, x2, x3) / CELL);
      const j0 = Math.floor(Math.min(z1, z2, z3) / CELL);
      const j1 = Math.floor(Math.max(z1, z2, z3) / CELL);
      for (let i = i0; i <= i1; i++) {
        for (let j = j0; j <= j1; j++) {
          const px = (i + 0.5) * CELL, pz = (j + 0.5) * CELL;
          const l1 = ((z2 - z3) * (px - x3) + (x3 - x2) * (pz - z3)) / det;
          const l2 = ((z3 - z1) * (px - x3) + (x1 - x3) * (pz - z3)) / det;
          const l3 = 1 - l1 - l2;
          if (l1 < 0 || l2 < 0 || l3 < 0) continue;
          const y = l1 * y1 + l2 * y2 + l3 * y3;
          const kk = key(i, j);
          const cur = low.get(kk);
          if (cur === undefined || y < cur) low.set(kk, y);
        }
      }
    }

    const A = CELL * CELL;
    const byKind = {};
    const hits = [];
    let planArea = 0, airArea = 0, worst = 0;
    /* An OVERHANG is the honest way to tell a moulding from a hanging wall.
     * A coping that oversails its wall by 140mm is masonry in the air and is
     * meant to be; a wing wall whose outer two metres stand over a cutting is
     * masonry in the air and is not. Both are "lowest surface clear of the
     * ground". What separates them is how far you have to walk in plan from
     * that column to a column of the same structure that DOES reach the
     * ground. Multi-source BFS over the grid gives it directly. */
    const grounded = new Set();
    for (const [kk, y] of low) {
      const [i, j] = kk.split(':').map(Number);
      if (y - terr.heightAt((i + 0.5) * CELL, (j + 0.5) * CELL) <= TOL) grounded.add(kk);
    }
    const dist = new Map();
    let front = [...grounded];
    for (const k2 of front) dist.set(k2, 0);
    for (let ring = 1; front.length && ring < 40; ring++) {
      const nxt = [];
      for (const k2 of front) {
        const [i, j] = k2.split(':').map(Number);
        for (const [di, dj] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const nk = (i + di) + ':' + (j + dj);
          if (!low.has(nk) || dist.has(nk)) continue;
          dist.set(nk, ring * CELL); nxt.push(nk);
        }
      }
      front = nxt;
    }
    for (const [kk, y] of low) {
      const [i, j] = kk.split(':').map(Number);
      const x = (i + 0.5) * CELL, z = (j + 0.5) * CELL;
      planArea += A;
      const gap = y - terr.heightAt(x, z);
      if (!(gap > TOL)) continue;
      airArea += A;
      const over = dist.has(kk) ? dist.get(kk) : 99;
      const at = attrib(x, z);
      const key2 = at.kind;
      byKind[key2] = byKind[key2] || {area: 0, worst: 0, where: ''};
      byKind[key2].area += A;
      if (gap > byKind[key2].worst) { byKind[key2].worst = gap; byKind[key2].where = at.where; }
      if (gap > worst) worst = gap;
      hits.push({x, y, z, gap, kind: at.kind, where: at.where, hr: y - at.rail, over});
    }
    /* Height above the local railhead separates the parts of a portal without
     * naming them: a wing wall runs from about -1 to +3 m, the archivolt and
     * the coping oversail live at +4.5 and above. A number that does not split
     * these two is the reason this took three attempts. */
    const bands = {};
    let hang = 0, hangWorst = 0, hangAt = null;
    for (const h of hits) {
      if (h.kind === 'viaduct' || h.kind === 'bridge') continue;
      const b2 = h.over <= 0.6 ? 'oversail <=0.6m of grounded wall (a moulding)'
               : h.over <= 2 ? 'overhang 0.6-2m'
               : 'overhang >2m (a shelf in the air)';
      bands[b2] = bands[b2] || {area: 0, worst: 0};
      bands[b2].area += A;
      bands[b2].worst = Math.max(bands[b2].worst, h.gap);
      if (h.over > 0.6) {
        hang += A;
        if (h.gap > hangWorst) { hangWorst = h.gap; hangAt = [+h.x.toFixed(1), +h.y.toFixed(1), +h.z.toFixed(1), h.where]; }
      }
    }
    for (const k of Object.keys(bands)) {
      bands[k].area = +bands[k].area.toFixed(1);
      bands[k].worst = +bands[k].worst.toFixed(2);
    }
    /* cluster for reporting */
    hits.sort((p2, q) => q.gap - p2.gap);
    const cl = [];
    for (const h of hits) {
      let put = null;
      for (const c of cl) if (Math.hypot(c.x - h.x, c.z - h.z) < 2.5) { put = c; break; }
      if (put) { put.area += A; put.n++; }
      else cl.push({...h, area: A, n: 1});
    }
    cl.sort((p2, q) => q.area - p2.area);
    return {planArea: +planArea.toFixed(1), airArea: +airArea.toFixed(1),
            worst: +worst.toFixed(2), bands,
            hang: +hang.toFixed(1), hangWorst: +hangWorst.toFixed(2), hangAt,
            byKind: Object.fromEntries(Object.entries(byKind).map(([k, o]) =>
              [k, {area: +o.area.toFixed(1), worst: +o.worst.toFixed(2), at: o.where}])),
            sites: cl.slice(0, 14).map(c => ({
              area: +c.area.toFixed(1), gap: +c.gap.toFixed(2), kind: c.kind,
              hr: +c.hr.toFixed(1), over: +c.over.toFixed(1),
              at: [+c.x.toFixed(1), +c.y.toFixed(1), +c.z.toFixed(1)], near: c.where})),
            footings: (rail._footings || []).length};
  }, {TOL, CELL});
  if (r.err) { console.log('layout', L, r.err); continue; }
  console.log(`\n=== layout ${L}   (_footings published: ${r.footings})`);
  console.log(`  masonry plan area ${r.planArea} m2; lowest masonry more than ${TOL}m ` +
              `clear of the ground over ${r.airArea} m2; worst ${r.worst}m`);
  console.log(`  by declared kind under the column: ${JSON.stringify(r.byKind)}`);
  console.log(`  non-deck masonry in air, by overhang: ${JSON.stringify(r.bands)}`);
  console.log(`  >>> NON-DECK MASONRY HANGING (overhang >0.6m): ${r.hang} m2, worst ` +
              `${r.hangWorst}m clear, at ${JSON.stringify(r.hangAt)}`);
  for (const s of r.sites) {
    console.log(`     ${String(s.area).padStart(7)} m2  worst gap ${String(s.gap).padStart(6)}m  ` +
                `${s.kind.padEnd(8)} ovh ${String(s.over).padStart(4)}m ${String(s.hr).padStart(5)}m over railhead ` +
                `at ${JSON.stringify(s.at)}  ${s.near}`);
  }
}
await b.close();
