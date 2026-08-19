/* _vabl16.mjs — round sixteen, ablated in ONE page.
 *
 *   node _vabl16.mjs [--mods terrain,vegetation]
 *
 * The dev server relayouts between processes: round fifteen watched the island
 * go from r 597 to r 646 and the land sample count from 13,006 to 14,799
 * between two consecutive probe runs, and spent half an hour believing a
 * coefficient of variation from one process was a control for one from another.
 * So the before and the after here are one page, one instant, one layout, and
 * the only thing that changes between them is which of this round's three
 * inputs the file is allowed to see.
 *
 * WHAT IS NEUTRALISED, and it is inputs rather than code:
 *
 *   _aspectNorm -> 0     the neutral value it returns on flat ground, so every
 *                        aspect rule does nothing. This is a fair "before" for
 *                        the shelter term and for the aspect half of the conifer
 *                        roll; it is NOT a reproduction of the old bug, which
 *                        was `max(0, radians) * 0.30` and actively wrong rather
 *                        than merely absent. The unablated old behaviour is in
 *                        the cross-run baseline quoted in the notes.
 *   _slopeNorm  -> 0.5   the neutral half, so the slope shelter term is zero and
 *                        the conifer roll's slope term is zero.
 *   _ageNorm    -> 0.5   one stand age everywhere: maturity 0.97, size exponent
 *                        2.40, i.e. exactly the spatially-white size the round
 *                        was called for. This IS a faithful before for the ICC.
 *
 * WHAT IS NOT REPRODUCED and is stated rather than hidden: the slope density
 * ramp's own constants (0.62..1.20 -> 0.55..1.05), the shelter base (0.70 ->
 * 0.74), the pine/spruce split constant and the two centred noise picks are code
 * changes, not input changes, and the ablation cannot switch them off. Rows that
 * depend on them carry the cross-run baseline instead.
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

  const stat = (a) => {
    if (!a.length) return {n: 0};
    const m = a.reduce((s, v) => s + v, 0) / a.length;
    const sd = Math.sqrt(a.reduce((s, v) => s + (v - m) * (v - m), 0) / a.length);
    const so = a.slice().sort((u, v) => u - v);
    const at = f => +so[Math.min(so.length - 1, Math.floor(a.length * f))].toFixed(2);
    return {n: a.length, mean: +m.toFixed(3), sd: +sd.toFixed(3),
            cv: +(sd / (m || 1e-9)).toFixed(3), p10: at(0.10), p50: at(0.5), p90: at(0.9)};
  };

  /* The land mask and the per-sample fields, taken ONCE — they do not depend on
   * the ablation and re-walking them per pass would be the slowest part. */
  const STEP = 8;
  const L = {x: [], z: [], slope: [], aspect: [], coast: [], nat: [], ang: []};
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += STEP) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += STEP) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const h = veg._ground(x, z);
      if (h <= veg.waterY) continue;
      const s = veg._biome(x, z, h);
      L.x.push(x); L.z.push(z);
      L.slope.push(s.slope);
      /* Read through the REAL `_aspectNorm`, before anything is stubbed, so the
       * bins are the same physical hillsides in both passes. */
      L.aspect.push(s.aspect);
      L.coast.push(s.coast);
      L.nat.push(s.coast >= 90 && veg._openness(x, z) > 0.8 ? 1 : 0);
      L.ang.push(Math.atan2(dz, dx));
    }
  }

  /* The lattice, indexed, so a stem is binned by the ground it stands on and
   * NOT by a fresh `_biome` call. The first version of this harness called
   * `_biome` per stem inside `measure()` — with `_aspectNorm` stubbed to zero,
   * every stem in the ablated pass came back with aspect 0 and the whole island
   * landed in one bin against denominators computed with the real function.
   * `0/0/1185/0`. An instrument that cannot see the field it has switched off is
   * the seventeenth confident wrong answer on this project. */
  const GK = (x, z) => ((Math.floor(x / STEP) & 0xffff) << 16) | (Math.floor(z / STEP) & 0xffff);
  const GRID = new Map();
  for (let k = 0; k < L.x.length; k++) GRID.set(GK(L.x[k], L.z[k]), k);

  const SB = [0.10, 0.25, 0.42], AB = [-0.35, 0.0, 0.35];
  const binOf = (v, e) => { let i = 0; while (i < e.length && v >= e[i]) i++; return i; };
  const SECT = 16;
  const bands = [[0, 40], [40, 90], [90, 150], [150, 260], [260, 1e5]];
  const cellA = STEP * STEP;
  const areaS = new Float64Array(4), areaA = new Float64Array(4);
  const areaG = new Float64Array(SECT * bands.length);
  for (let k = 0; k < L.x.length; k++) {
    const bi = bands.findIndex(q => L.coast[k] >= q[0] && L.coast[k] < q[1]);
    if (bi >= 0) {
      const se = Math.floor((L.ang[k] + Math.PI) / (2 * Math.PI) * SECT) % SECT;
      areaG[bi * SECT + se] += cellA;
    }
    if (!L.nat[k]) continue;
    areaS[binOf(L.slope[k], SB)] += cellA;
    areaA[binOf(L.aspect[k], AB)] += cellA;
  }

  /* One measurement pass over whatever `veg.trees` currently holds. */
  const measure = () => {
    const stems = [];
    for (let e = 0; e < veg.trees.length; e++) {
      const t = veg.trees[e];
      const si = Math.floor(e / VARIANTS), vi = e % VARIANTS;
      const refH = t.spec ? t.spec.refH : 20;
      const n = t.count != null ? t.count : t.xs.length;
      for (let i = 0; i < n; i++) {
        const x = t.xs[i], z = t.zs[i];
        if (!Number.isFinite(x)) continue;
        const m = t.mats, o = i * 16;
        const sy = Math.hypot(m[o + 4], m[o + 5], m[o + 6]);
        stems.push({x, z, si, vi, hM: refH * sy, coast: veg._coastDist(x, z),
                    ang: Math.atan2(z - isl.cz, x - isl.cx)});
      }
    }
    const cntS = new Float64Array(4), cntA = new Float64Array(4);
    const cntG = new Float64Array(SECT * bands.length);
    for (const s of stems) {
      const bi = bands.findIndex(q => s.coast >= q[0] && s.coast < q[1]);
      if (bi >= 0) {
        const se = Math.floor((s.ang + Math.PI) / (2 * Math.PI) * SECT) % SECT;
        cntG[bi * SECT + se]++;
      }
      const k = GRID.get(GK(s.x, s.z));
      if (k === undefined || !L.nat[k]) continue;
      cntS[binOf(L.slope[k], SB)]++;
      cntA[binOf(L.aspect[k], AB)]++;
    }
    const perHa = (c, a) => [...c].map((v, i) => +(v / Math.max(1e-9, a[i] / 10000)).toFixed(0));
    const compass = bands.map((q, bi) => {
      const per = [];
      for (let s = 0; s < SECT; s++) {
        const a = areaG[bi * SECT + s];
        if (a < 2000) continue;
        per.push(cntG[bi * SECT + s] / (a / 10000));
      }
      const st = stat(per);
      return {band: q[0] + '-' + (q[1] > 1e4 ? 'inf' : q[1]),
              perHa: st.mean, cv: st.cv,
              min: per.length ? +Math.min(...per).toFixed(0) : 0,
              max: per.length ? +Math.max(...per).toFixed(0) : 0};
    });
    /* Intraclass correlation of log height over 40 m cells. */
    const g = new Map();
    for (const s of stems) {
      const k = ((Math.floor(s.x / 40) & 0xffff) << 16) | (Math.floor(s.z / 40) & 0xffff);
      (g.get(k) || g.set(k, []).get(k)).push(Math.log(Math.max(0.2, s.hM)));
    }
    let gm = 0, gn = 0;
    for (const v of g.values()) for (const q of v) { gm += q; gn++; }
    gm /= (gn || 1);
    let bet = 0, win = 0;
    for (const v of g.values()) {
      if (v.length < 4) continue;
      const m = v.reduce((a, q) => a + q, 0) / v.length;
      bet += v.length * (m - gm) * (m - gm);
      for (const q of v) win += (q - m) * (q - m);
    }
    const spc = new Array(5).fill(0);
    for (const s of stems) spc[s.si]++;
    const inland = stems.filter(s => s.coast >= 200);
    const spcI = new Array(5).fill(0);
    for (const s of inland) spcI[s.si]++;
    const bkt = new Map();
    for (const s of stems) bkt.set(s.si * 8 + s.vi, (bkt.get(s.si * 8 + s.vi) || 0) + 1);
    const top = Math.max(...bkt.values());
    let H = 0;
    for (const v of bkt.values()) { const q = v / stems.length; H -= q * Math.log(q); }
    return {
      stems: stems.length,
      bands: veg._scatterStats ? {open: veg._scatterStats.open, margin: veg._scatterStats.margin,
                                  closed: veg._scatterStats.closed} : null,
      stemsPerHaBySlope: perHa(cntS, areaS),
      stemsPerHaByAspect: perHa(cntA, areaA),
      compass,
      heightM: stat(stems.map(s => s.hM)),
      icc40: +(bet / ((bet + win) || 1e-9)).toFixed(3),
      speciesPct: spc.map(v => +(100 * v / stems.length).toFixed(1)),
      speciesPctInland: spcI.map(v => +(100 * v / (inland.length || 1)).toFixed(1)),
      topBucketPct: +(100 * top / stems.length).toFixed(1),
      evenness: +(H / Math.log(Math.max(2, bkt.size))).toFixed(3),
    };
  };

  /* ---- pass 1: the three inputs neutralised ------------------------------ */
  const realAspect = veg._aspectNorm, realSlope = veg._slopeNorm, realAge = veg._ageNorm;
  veg._aspectNorm = () => 0;
  veg._slopeNorm = () => 0.5;
  veg._ageNorm = () => 0.5;
  veg._scatterTrees();
  const before = measure();

  /* ---- pass 2: the file as it ships -------------------------------------- */
  veg._aspectNorm = realAspect;
  veg._slopeNorm = realSlope;
  veg._ageNorm = realAge;
  veg._scatterTrees();
  const after = measure();

  return {islandR: +isl.r.toFixed(0), landSamples: L.x.length,
          aspectUnit: veg._aspectUnit || null, before, after};
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
