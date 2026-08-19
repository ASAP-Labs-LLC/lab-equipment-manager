/* vfringe.mjs — is the tree line a beaded fringe of constant width?
 *
 *   node vfringe.mjs [--mods terrain,vegetation]
 *
 * The blind critique, twice, unprompted: "B's trees are a beaded fringe of
 * constant width following the coastline — same size, same value, same
 * silhouette, one instance repeated"; "a continuous band hugging the perimeter
 * that thins toward the centre. That is a distance-from-coastline mask, not a
 * biome."
 *
 * Four claims, and every one of them is a number that can be read off the
 * PLACED instance lists rather than argued about from a screenshot:
 *
 *   1. CONSTANT WIDTH. If the band is a distance mask, then stem density is a
 *      function of `coastDist` alone: pick any coast band and the density is
 *      the same all the way round the island. Measured as the coefficient of
 *      variation of density around the compass WITHIN one coast band. A mask
 *      gives a small CV; a biome gives a large one.
 *   2. SAME SIZE. Histogram the composed matrix scale, in the salt band and
 *      inland. The salt shrink multiplies by 0.46, which does not merely make
 *      the fringe smaller — it COMPRESSES the spread toward one value.
 *   3. SAME SILHOUETTE. Which crown variant and which species stand in the
 *      fringe. `vi = 2` is forced on ~80% of the salt band by one line.
 *   4. NEVER MASS. Nearest-neighbour spacing against crown radius: crowns that
 *      touch fuse into a canopy, crowns that do not stay legible blobs.
 *
 * Everything comes off `veg.trees[*].mats` (the real matrices, after every
 * scale rule) and `veg.trees[*].xs/zs`, so it is what is drawn, not what a
 * rule said.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const mods = arg('mods', 'terrain,vegetation');

const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}` +
  '&cam=wide&time=16&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(9000);

const out = await p.evaluate(() => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const isl = veg.island;
  const VARIANTS = 3;

  /* Every placed stem, with what it actually became. */
  const stems = [];
  for (let e = 0; e < veg.trees.length; e++) {
    const t = veg.trees[e];
    const si = Math.floor(e / VARIANTS), vi = e % VARIANTS;
    const n = t.count != null ? t.count : t.xs.length;
    for (let i = 0; i < n; i++) {
      const x = t.xs[i], z = t.zs[i];
      if (!Number.isFinite(x)) continue;
      /* Column lengths of the composed matrix are the world scale. */
      const m = t.mats, o = i * 16;
      const sx = Math.hypot(m[o], m[o + 1], m[o + 2]);
      const sy = Math.hypot(m[o + 4], m[o + 5], m[o + 6]);
      stems.push({x, z, si, vi, sx, sy, rad: t.rad ? t.rad[i] : 0,
                  coast: veg._coastDist(x, z),
                  ang: Math.atan2(z - isl.cz, x - isl.cx)});
    }
  }

  const stat = (a) => {
    if (!a.length) return {n: 0};
    const m = a.reduce((s, v) => s + v, 0) / a.length;
    const sd = Math.sqrt(a.reduce((s, v) => s + (v - m) * (v - m), 0) / a.length);
    const so = a.slice().sort((u, v) => u - v);
    return {n: a.length, mean: +m.toFixed(3), sd: +sd.toFixed(3),
            cv: +(sd / (m || 1e-9)).toFixed(3),
            p10: +so[Math.floor(a.length * 0.1)].toFixed(2),
            p50: +so[Math.floor(a.length * 0.5)].toFixed(2),
            p90: +so[Math.floor(a.length * 0.9)].toFixed(2)};
  };

  /* --- 1. constant width: density around the compass, per coast band ------ */
  const SECT = 16;
  const bands = [[0, 40], [40, 90], [90, 150], [150, 260], [260, 1e5]];
  /* Land area per (sector, band), so density is stems per hectare of the land
   * that actually exists there — an island is not a disc and dividing by an
   * annulus wedge would report a bay as a thin forest. */
  const areaG = new Float64Array(SECT * bands.length);
  const STEP = 8;
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += STEP) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += STEP) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      if (veg._ground(x, z) <= veg.waterY) continue;
      const c = veg._coastDist(x, z);
      let bi = bands.findIndex(q => c >= q[0] && c < q[1]);
      if (bi < 0) continue;
      let s = Math.floor((Math.atan2(dz, dx) + Math.PI) / (2 * Math.PI) * SECT) % SECT;
      areaG[bi * SECT + s] += STEP * STEP;
    }
  }
  const cntG = new Float64Array(SECT * bands.length);
  for (const s of stems) {
    let bi = bands.findIndex(q => s.coast >= q[0] && s.coast < q[1]);
    if (bi < 0) continue;
    let se = Math.floor((s.ang + Math.PI) / (2 * Math.PI) * SECT) % SECT;
    cntG[bi * SECT + se]++;
  }
  const widthRows = bands.map((q, bi) => {
    const per = [];
    for (let s = 0; s < SECT; s++) {
      const a = areaG[bi * SECT + s];
      if (a < 2000) continue;           /* too little land in this wedge to mean anything */
      per.push(cntG[bi * SECT + s] / (a / 10000));
    }
    const st = stat(per);
    return {band: q[0] + '-' + (q[1] > 1e4 ? 'inf' : q[1]) + 'm',
            sectors: per.length, stemsPerHa: st.mean, sd: st.sd,
            cvAroundCompass: st.cv,
            min: per.length ? +Math.min(...per).toFixed(0) : 0,
            max: per.length ? +Math.max(...per).toFixed(0) : 0};
  });

  /* --- 2/3. size, species and silhouette, salt band vs inland ------------- */
  const salt = stems.filter(s => s.coast < 90);
  const inland = stems.filter(s => s.coast >= 200);
  const mixOf = (set, key, k) => {
    const c = new Array(k).fill(0);
    for (const s of set) c[s[key]]++;
    return c.map(v => +(100 * v / (set.length || 1)).toFixed(1));
  };
  /* Shannon evenness over the 15 species-variant buckets: 1.0 is every
     silhouette equally likely, 0 is one instance repeated. */
  const evenness = (set) => {
    const c = new Map();
    for (const s of set) { const k = s.si * 8 + s.vi; c.set(k, (c.get(k) || 0) + 1); }
    let H = 0; const n = set.length || 1;
    for (const v of c.values()) { const q = v / n; H -= q * Math.log(q); }
    return {buckets: c.size, evenness: +(H / Math.log(Math.max(2, c.size))).toFixed(3),
            H: +H.toFixed(3)};
  };

  /* --- 4. do the crowns touch? ------------------------------------------- */
  const touch = (set) => {
    if (set.length < 50) return {n: set.length};
    const C = 24, key = (i, j) => ((i & 0xffff) << 16) | (j & 0xffff);
    const g = new Map();
    for (const s of set) {
      const k = key(Math.floor(s.x / C), Math.floor(s.z / C));
      (g.get(k) || g.set(k, []).get(k)).push(s);
    }
    const take = set.filter((_, i) => i % Math.max(1, Math.floor(set.length / 1500)) === 0);
    const nd = [], ratio = [];
    for (const s of take) {
      const ci = Math.floor(s.x / C), cj = Math.floor(s.z / C);
      let best = 1e9, bestR = 0;
      for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) {
        const l = g.get(key(ci + i, cj + j)); if (!l) continue;
        for (const o of l) {
          if (o === s) continue;
          const d = Math.hypot(o.x - s.x, o.z - s.z);
          if (d < best) { best = d; bestR = o.rad; }
        }
      }
      if (best < 1e8) { nd.push(best); ratio.push((s.rad + bestR) / Math.max(0.1, best)); }
    }
    const st = stat(nd);
    return {n: set.length, nearestNeighbourM: st,
            crownsTouchPct: +(100 * ratio.filter(v => v >= 1).length / ratio.length).toFixed(1),
            meanOverlapRatio: +(ratio.reduce((a, v) => a + v, 0) / ratio.length).toFixed(2)};
  };

  return {
    islandR: +isl.r.toFixed(0), stems: stems.length,
    scatter: veg._scatterStats,
    constantWidth: widthRows,
    size: {
      salt: stat(salt.map(s => s.sy)),
      inland: stat(inland.map(s => s.sy)),
      all: stat(stems.map(s => s.sy)),
    },
    silhouette: {
      saltSpeciesPct: mixOf(salt, 'si', 6), inlandSpeciesPct: mixOf(inland, 'si', 6),
      saltVariantPct: mixOf(salt, 'vi', 3), inlandVariantPct: mixOf(inland, 'vi', 3),
      saltEvenness: evenness(salt), inlandEvenness: evenness(inland),
    },
    massing: {salt: touch(salt), inland: touch(inland)},
  };
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
