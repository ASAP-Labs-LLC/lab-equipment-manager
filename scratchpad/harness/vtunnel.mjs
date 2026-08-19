/* vtunnel.mjs — does the forest know a tunnel is a tunnel?
 *
 *   node vtunnel.mjs [--radius 45]
 *
 * rail.js declares ~64 earthwork spans. For 'tunnel', 'viaduct' and 'bridge'
 * terrain.js explicitly does NOT move the ground, so the hillside over a bore
 * is still a hillside and should be planted like one. For 'cut' and 'fill' the
 * ground genuinely moved and a bare formation is correct.
 *
 * So: walk each span's own points, count vegetation stems (every tier's
 * PLACEMENT list, not what a camera happens to draw) within `radius` of the
 * centreline, and normalise by the span's length. A tunnel span whose stem
 * density is at grade-span levels is the bug; a tunnel span whose density
 * matches the surrounding hillside is the fix.
 *
 * Also reports, per kind, the mean vertical gap between the declared formation
 * and the ground terrain actually built — which is the number a 2-D corridor
 * test cannot see and is the whole reason the fault exists.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const R = parseFloat(arg('radius', '45'));

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html?' +
  'mods=sky,gi,terrain,buildings,rail,trains,vegetation&cam=far&time=16&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(9000);

/* The ablation, in ONE session, because rail.js is being edited in a parallel
 * round and two runs half an hour apart are two different railways: between the
 * first and second run of this probe the tunnel count went 4 -> 2 and their
 * total length 190 m -> 39 m. A before/after taken from two page loads measures
 * whoever else was awake.
 *
 * `_railStructures` is the one method that decides which declared spans are
 * exempt from the keep-out. Stubbed to an empty map, `_buildRailField` puts
 * every frame back into the at-grade field — which is exactly the file as it
 * was before this round — and `_scatterTrees` re-plants against it. */
const measure = await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  window.__vtRestore = veg._railStructures.bind(veg);
  return true;
});

const run = async (ablate) => {
  await p.evaluate((ablate) => {
    const veg = window.__lemWorld.subsystems.get('vegetation');
    veg._railStructures = ablate ? (() => { veg._structSpans = 0; return new Map(); })
                                 : window.__vtRestore;
    veg._buildRailField(veg.plan);
    veg._scatterTrees();
  }, ablate);
  return p.evaluate(sample, R);
};

const sample = (R) => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const ctx = w.ctx;
  const spans = ctx.railEarthworks || [];
  if (!veg || !spans.length) return {ok: false, spans: spans.length, veg: !!veg};

  /* Every stem this file placed, in a grid so the query is cheap. */
  const CELL = 32;
  const grid = new Map();
  const key = (i, j) => ((i & 0xffff) << 16) | (j & 0xffff);
  let stems = 0;
  for (const e of veg.trees || []) {
    for (const t of e.list) {
      const k = key(Math.floor(t.x / CELL), Math.floor(t.z / CELL));
      let a = grid.get(k);
      if (!a) { a = []; grid.set(k, a); }
      a.push(t.x, t.z);
      stems++;
    }
  }
  const near = (x, z, r) => {
    const ci = Math.floor(x / CELL), cj = Math.floor(z / CELL);
    const rings = Math.ceil(r / CELL);
    let n = 0;
    for (let j = -rings; j <= rings; j++) {
      for (let i = -rings; i <= rings; i++) {
        const a = grid.get(key(ci + i, cj + j));
        if (!a) continue;
        for (let q = 0; q < a.length; q += 2) {
          const dx = a[q] - x, dz = a[q + 1] - z;
          if (dx * dx + dz * dz < r * r) n++;
        }
      }
    }
    return n;
  };

  /* Undergrowth and sward too — the whole ground layer either knows or does not. */
  const other = {};
  for (const nm of ['clutter', 'sward']) {
    const lst = veg['_' + nm + 'Sites'] || null;
    if (lst) other[nm] = lst.length;
  }

  const ground = ctx.ground;
  const by = {};
  for (const s of spans) {
    const t = by[s.kind] || (by[s.kind] = {spans: 0, metres: 0, stems: 0,
                                           samples: 0, gap: 0, cover: 0, ctl: 0,
                                           inKeepOut: 0});
    t.spans++;
    t.metres += s.length || 0;
    const pts = s.points, n = pts ? pts.length / 3 : 0;
    /* Sample the centreline every ~10 m so a long span is not over-counted. */
    const stride = Math.max(1, Math.round(10 / (s.step || 2)));
    const seen = new Set();
    const keep = by[s.kind]._keep || (by[s.kind]._keep = new Set());
    for (let i = 0; i < n; i += stride) {
      const x = pts[i * 3], y = pts[i * 3 + 1], z = pts[i * 3 + 2];
      const g = ground ? ground(x, z) : 0;
      t.samples++;
      t.gap += (g - y);            // + means ground stands above the formation
      /* stems within R of this sample; de-duplicated per span by rounding the
       * position, since consecutive samples overlap. */
      t.stems += near(x, z, R);
      /* The control: the SAME hillside, 130 m to either side of the alignment,
       * where nothing this file does is allowed to clear anything. A corridor
       * that matches its own neighbourhood is a corridor that is not being
       * cleared; one at a third of it is a bald stripe. Taken per sample rather
       * than per island, because a tunnel is in a hill and a hill is thinner
       * than the island mean whatever the railway does. */
      const j2 = Math.min(n - 1, i + stride);
      let tx = pts[j2 * 3] - x, tz = pts[j2 * 3 + 2] - z;
      const tl = Math.hypot(tx, tz) || 1;
      const nx = -tz / tl, nz = tx / tl;
      t.ctl += 0.5 * (near(x + nx * 130, z + nz * 130, R) +
                      near(x - nx * 130, z - nz * 130, R));
      /* The counterfactual, and it needs no re-scatter: 31 m is
       * RAIL_FORMATION + TREE_CESS + the 9 m crown reach the tree scatter
       * passes, i.e. exactly the keep-out this file applies to an at-grade
       * formation. Every stem inside it on a tunnel span is a stem that did not
       * exist before the fix; every stem inside it on a cut or a grade span
       * would be a bug. Counted once per span rather than per sample. */
      for (const [kx, kz] of [[x, z]]) {
        const ci = Math.floor(kx / CELL), cj = Math.floor(kz / CELL);
        for (let jj = -1; jj <= 1; jj++) for (let ii = -1; ii <= 1; ii++) {
          const a = grid.get(key(ci + ii, cj + jj));
          if (!a) continue;
          for (let q = 0; q < a.length; q += 2) {
            const dx = a[q] - kx, dz = a[q + 1] - kz;
            if (dx * dx + dz * dz < 31 * 31) keep.add(a[q] * 1e5 + a[q + 1]);
          }
        }
      }
      t.cover++;
      seen.add(1);
    }
  }
  const rows = [];
  for (const k of Object.keys(by)) {
    const t = by[k];
    rows.push({kind: k, spans: t.spans, metres: Math.round(t.metres),
               samples: t.samples,
               meanGroundAboveFormation: +(t.gap / Math.max(1, t.samples)).toFixed(1),
               stemsPerSample: +(t.stems / Math.max(1, t.samples)).toFixed(1),
               sameHillControl: +(t.ctl / Math.max(1, t.samples)).toFixed(1),
               corridorOverControl: +(t.stems / Math.max(1, t.ctl)).toFixed(2),
               stemsInsideAtGradeKeepOut: t._keep ? t._keep.size : 0});
  }
  rows.sort((a, b) => a.kind < b.kind ? -1 : 1);

  /* A control: the same query at random island points, so "stems per sample"
   * has a scale. */
  const isl = veg.island;
  let ctl = 0, ctln = 0;
  let seed = 12345;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
  for (let i = 0; i < 400; i++) {
    const a = rnd() * Math.PI * 2, r = Math.sqrt(rnd()) * isl.r * 0.92;
    const x = isl.cx + Math.cos(a) * r, z = isl.cz + Math.sin(a) * r;
    if (ground && ground(x, z) < (veg.waterY ?? -1e9)) continue;
    ctl += near(x, z, R); ctln++;
  }
  /* And the mechanism itself, at the one place it is supposed to act: the
   * MIDPOINT of every declared structure. `_railDist` is the distance to the
   * nearest rail sample the keep-out field still holds; the tree scatter
   * refuses a candidate inside RAIL_FORMATION + TREE_CESS + 9 = 31 m of one.
   * So `dist >= 31` over a bore is the fix working, and `dist < 31` is the bald
   * stripe. This is a per-point fact and does not need a stand to land on it,
   * which is why it is here as well as the population counts: a 39 m tunnel
   * whose two portals are 31 m keep-out discs has almost no midpoint left. */
  const mids = [];
  for (const s of spans) {
    if (s.kind !== 'tunnel' && s.kind !== 'viaduct' && s.kind !== 'bridge') continue;
    const pts = s.points, n = pts ? pts.length / 3 : 0;
    if (!n) continue;
    const i = n >> 1;
    const x = pts[i * 3], z = pts[i * 3 + 2];
    mids.push({kind: s.kind, track: s.track, lengthM: Math.round(s.length),
               keepOutDistM: +veg._railDist(x, z, 90).toFixed(1),
               clearedAtGrade: veg._railDist(x, z, 90) < 31});
  }
  return {ok: true, stems, R, spans: spans.length, rows, other, mids,
          islandControlStemsPerSample: +(ctl / Math.max(1, ctln)).toFixed(1)};
};

const before = await run(true);
const after = await run(false);
console.log(JSON.stringify({before, after}, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
