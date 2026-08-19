/* vstrand.mjs — the two named places, measured. Job 1 and Job 2 in one session.
 *
 *   node vstrand.mjs [--mods ''] [--json]
 *
 * JOB 1. `_shore().beach` is a function of ONE variable — metres of PLAN
 * distance from the waterline — and a beach is not a constant-width strip. This
 * measures the island's own strand: how high the ground stands above the tide
 * inside the band the file already calls beach, and how much ground is low,
 * level and near the sea but OUTSIDE it. Then it counts what is planted there,
 * per tier, so the answer is an area and a population rather than a screenshot.
 *
 * JOB 2. `vslope.mjs` bins the whole island and the critic is looking at ONE
 * PLACE. This finds the eastern seaward crest geometrically — walk inland from
 * the waterline on each east-facing bearing, stop at the first ridge, keep only
 * the bearings whose ridge is actually seaward — and reports its wind-exposure
 * quartile against the island-wide edges, then DECOMPOSES the height of the
 * stems standing on it into the matrix scale and the species reference height.
 * A height in metres that does not move can hide a scale that did.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const mods = arg('mods', '');

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
  const ter = w.subsystems.get('terrain');
  const isl = veg.island;
  const wy = veg.waterY;
  const ground = (x, z) => veg._ground(x, z);
  const clamp = (v, a, c) => v < a ? a : v > c ? c : v;
  const pct = (a, f) => { if (!a.length) return null;
    const s = a.slice().sort((u, v) => u - v);
    return +s[Math.min(s.length - 1, Math.floor(s.length * f))].toFixed(2); };
  const mean = a => a.length ? +(a.reduce((s, v) => s + v, 0) / a.length).toFixed(3) : null;

  /* ---------- the land lattice, once ------------------------------------ */
  const L = [];
  const STEP = 5;
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += STEP) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += STEP) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const h = ground(x, z);
      if (h <= wy) continue;
      const d = veg._coastDist(x, z);
      if (d > 1e4) continue;
      L.push({x, z, altM: h - wy, coast: d});
    }
  }
  /* terrain's own slope, on the sample: cheap central difference off _ground,
   * the same one `_biome`'s fallback uses. Only needed on the coastal band. */
  const slopeAt = (x, z) => {
    const d = 4;
    return Math.hypot((ground(x + d, z) - ground(x - d, z)) / (2 * d),
                      (ground(x, z + d) - ground(x, z - d)) / (2 * d));
  };

  /* ---------- JOB 1: how high does this island's beach get? -------------- */
  /* The band the file already calls beach without argument: d < SHORE_BEACH.
   * Whatever altitude THAT ground occupies is what "beach" means here, measured
   * rather than copied from another module's shader. */
  const SHORE_BEACH = 26;
  const inner = L.filter(q => q.coast < SHORE_BEACH).map(q => q.altM);
  const strandProfile = [];
  for (let d0 = 0; d0 < 160; d0 += 10) {
    const band = L.filter(q => q.coast >= d0 && q.coast < d0 + 10);
    strandProfile.push({d: d0, n: band.length,
                        p10: pct(band.map(q => q.altM), 0.10),
                        p50: pct(band.map(q => q.altM), 0.50),
                        p90: pct(band.map(q => q.altM), 0.90)});
  }
  /* And the converse: for each elevation band, how far inland does it reach? */
  const reachByAlt = [];
  for (const a of [1, 2, 3, 4, 6, 8, 12]) {
    const s = L.filter(q => q.altM < a).map(q => q.coast);
    reachByAlt.push({belowM: a, n: s.length, p50: pct(s, 0.5), p90: pct(s, 0.9),
                     max: pct(s, 0.999)});
  }

  /* the disputed ground: low, level, near the sea, and OUTSIDE the current
   * beach mask. This is what is being planted on sand. */
  const strandTopP90 = pct(inner, 0.90);
  const disputed = [];
  for (const q of L) {
    if (q.coast < SHORE_BEACH) continue;
    if (q.coast > 130) continue;
    if (q.altM > (strandTopP90 ?? 6)) continue;
    const sl = slopeAt(q.x, q.z);
    if (sl > 0.20) continue;
    disputed.push({...q, slope: sl});
  }
  /* where is it? bucket by compass octant about the island centre. */
  const oct = ['E', 'SE', 'S', 'SW', 'W', 'NW', 'N', 'NE'];
  const octCount = {}; for (const o of oct) octCount[o] = 0;
  for (const q of disputed) {
    const a = Math.atan2(q.z - isl.cz, q.x - isl.cx);
    octCount[oct[((Math.round(a / (Math.PI / 4)) % 8) + 8) % 8]]++;
  }

  /* ---------- what is planted on it ------------------------------------- */
  const tiers = {tree: [], clutter: [], sward: []};
  for (const e of (veg.trees || [])) for (let i = 0; i < e.list.length; i++) {
    const sy = Math.hypot(e.mats[i * 16 + 4], e.mats[i * 16 + 5], e.mats[i * 16 + 6]);
    tiers.tree.push({x: e.xs[i], z: e.zs[i], sc: sy, refH: e.spec.refH || 18,
                     h: sy * (e.spec.refH || 18), spec: e.spec.name || e.spec.id || '?'});
  }
  for (const c of (veg.clutter || [])) for (let i = 0; i < c.count; i++)
    tiers.clutter.push({x: c.xs[i], z: c.zs[i]});
  for (const s of (veg.sward || [])) for (let i = 0; i < s.count; i++)
    tiers.sward.push({x: s.xs[i], z: s.zs[i]});

  const onDisputed = {};
  for (const k of Object.keys(tiers)) {
    let n = 0;
    for (const q of tiers[k]) {
      const h = ground(q.x, q.z);
      const altM = h - wy, cd = veg._coastDist(q.x, q.z);
      if (cd >= SHORE_BEACH && cd <= 130 && altM <= (strandTopP90 ?? 6) &&
          slopeAt(q.x, q.z) <= 0.20) n++;
    }
    onDisputed[k] = n;
  }

  /* the SE spit specifically: the blob props filed. */
  const spit = {x0: 40, z0: 270, x1: 200, z1: 400};
  const inSpit = (q) => q.x >= spit.x0 && q.x <= spit.x1 && q.z >= spit.z0 && q.z <= spit.z1;
  const spitCounts = Object.fromEntries(Object.keys(tiers)
    .map(k => [k, tiers[k].filter(inSpit).length]));
  const spitLand = L.filter(inSpit);
  const spitSward = tiers.sward.filter(inSpit).map(q => {
    const cd = veg._coastDist(q.x, q.z);
    const sh = veg._shore({coast: cd, x: q.x, z: q.z});
    return {x: +q.x.toFixed(0), z: +q.z.toFixed(0),
            altM: +(ground(q.x, q.z) - wy).toFixed(2), coast: +cd.toFixed(1),
            beach: +sh.beach.toFixed(3), salt: +sh.salt.toFixed(3)};
  });

  /* ---------- JOB 2: the eastern seaward crest --------------------------- */
  const ridge = [];
  for (let deg = -30; deg <= 40; deg += 2) {
    const a = deg * Math.PI / 180, cx = Math.cos(a), cz = Math.sin(a);
    let rw = -1;
    for (let r = isl.r + 60; r > 20; r -= 3)
      if (ground(isl.cx + cx * r, isl.cz + cz * r) > wy) { rw = r; break; }
    if (rw < 0) continue;
    let best = -1e9, bestR = rw, prev = -1e9, fell = 0;
    for (let r = rw; r > rw - 320 && r > 20; r -= 4) {
      const h = ground(isl.cx + cx * r, isl.cz + cz * r);
      if (h > best) { best = h; bestR = r; fell = 0; }
      else if (h < prev - 0.4) { fell++; if (fell >= 3) break; }
      prev = h;
    }
    const x = isl.cx + cx * bestR, z = isl.cz + cz * bestR;
    const cd = veg._coastDist(x, z);
    /* SEAWARD only: a ridge 200 m inland is a different hill and including it
     * is how an island-wide table hid this in the first place. */
    if (cd > 145) continue;
    ridge.push({deg, x, z, altM: best - wy, coast: cd});
  }

  const windAll = [], altAll = [];
  for (const q of L) { windAll.push(veg._windExposure(q.x, q.z)); altAll.push(q.altM); }
  const qe = (() => { const s = windAll.slice().sort((u, v) => u - v);
    return [s[(s.length * 0.25) | 0], s[(s.length * 0.5) | 0], s[(s.length * 0.75) | 0]]; })();
  const qOf = v => v < qe[0] ? 1 : v < qe[1] ? 2 : v < qe[2] ? 3 : 4;

  const ridgeRows = ridge.map(c => ({deg: c.deg, x: +c.x.toFixed(0), z: +c.z.toFixed(0),
    altM: +c.altM.toFixed(1), coast: +c.coast.toFixed(0),
    wind: +veg._windExposure(c.x, c.z).toFixed(3), wq: qOf(veg._windExposure(c.x, c.z))}));
  const ridgeHist = [0, 0, 0, 0]; for (const r of ridgeRows) ridgeHist[r.wq - 1]++;

  /* stems on the ridge, and a matched SHELTERED control: same altitude band,
   * wind quartile 1. Without the control a height difference is a statement
   * about the ridge's altitude, not about its wind. */
  const near = (q, pts, r) => pts.some(c => (q.x - c.x) ** 2 + (q.z - c.z) ** 2 < r * r);
  const ridgeStems = tiers.tree.filter(q => near(q, ridge, 40));
  const altLo = Math.min(...ridge.map(c => c.altM)), altHi = Math.max(...ridge.map(c => c.altM));
  const control = tiers.tree.filter(q => {
    const altM = ground(q.x, q.z) - wy;
    if (altM < altLo || altM > altHi) return false;
    return veg._windExposure(q.x, q.z) < qe[0];
  });
  const desc = (a) => ({n: a.length,
    hMean: mean(a.map(q => q.h)), hP90: pct(a.map(q => q.h), 0.9),
    scMean: mean(a.map(q => q.sc)), refHMean: mean(a.map(q => q.refH)),
    windMean: mean(a.map(q => veg._windExposure(q.x, q.z))),
    altMean: mean(a.map(q => ground(q.x, q.z) - wy)),
    spec: a.reduce((m, q) => (m[q.spec] = (m[q.spec] || 0) + 1, m), {})});

  /* density: stems per hectare inside a 40 m disc about each ridge point,
   * measured per point so the overlap does not have to be guessed. */
  const perPoint = ridge.map(c => tiers.tree.filter(q =>
    (q.x - c.x) ** 2 + (q.z - c.z) ** 2 < 40 * 40).length / (Math.PI * 40 * 40 / 10000));
  const ctlPts = [];
  for (const q of L) if (veg._windExposure(q.x, q.z) < qe[0] && q.altM >= altLo && q.altM <= altHi) ctlPts.push(q);
  const ctlSample = ctlPts.filter((_, i) => i % Math.max(1, (ctlPts.length / 40) | 0) === 0);
  const ctlDens = ctlSample.map(c => tiers.tree.filter(q =>
    (q.x - c.x) ** 2 + (q.z - c.z) ** 2 < 40 * 40).length / (Math.PI * 40 * 40 / 10000));

  /* THE EAST FLANK, band by band inland from its own waterline. The ridge probe
   * above answers "is the crest in Q4"; this answers "then what is the heavy
   * dark thing the critic is looking at", because a crest that has been stripped
   * and a flank below it that has not both sit in the same silhouette. */
  const eastBands = [];
  /* TWO sectors, and the difference between them is the whole of why an
   * island-wide table hid this. The wide one is "the right flank of the frame";
   * the narrow one is the bearings on which a seaward crest actually exists.
   * Averaging the second into the first is how a stripped ridge and an unstripped
   * shoulder come out as one plausible number. */
  const eastOf = q => {
    const a = Math.atan2(q.z - isl.cz, q.x - isl.cx);
    return a > -Math.PI / 3 && a < Math.PI / 3;
  };
  const crestBearing = q => {
    const a = Math.atan2(q.z - isl.cz, q.x - isl.cx) * 180 / Math.PI;
    return a >= -16 && a <= 32;
  };
  const treeCoast = tiers.tree.map(q => ({...q, coast: veg._coastDist(q.x, q.z),
    altM: ground(q.x, q.z) - wy, wind: veg._windExposure(q.x, q.z), east: eastOf(q),
    cb: crestBearing(q)}));
  const landCoast = L.map(q => ({...q, east: eastOf(q), cb: crestBearing(q),
    wind: veg._windExposure(q.x, q.z)}));
  const crestBands = [];
  for (const [lo, hi] of [[0, 30], [30, 60], [60, 90], [90, 130], [130, 200], [200, 400]]) {
    const T2 = treeCoast.filter(q => q.cb && q.coast >= lo && q.coast < hi);
    const A2 = landCoast.filter(q => q.cb && q.coast >= lo && q.coast < hi);
    const ha2 = A2.length * STEP * STEP / 10000;
    crestBands.push({coast: [lo, hi], ha: +ha2.toFixed(2), stems: T2.length,
      stemsPerHa: ha2 > 0 ? +(T2.length / ha2).toFixed(1) : null,
      hMean: mean(T2.map(q => q.h)), scMean: mean(T2.map(q => q.sc)),
      windMean: mean(A2.map(q => q.wind)), altMean: mean(A2.map(q => q.altM)),
      openMean: mean(A2.map(q => veg._openness(q.x, q.z))),
      crownM2PerHa: ha2 > 0 ? +(T2.reduce((s, q) => s + Math.PI * (q.h * 0.30) ** 2, 0) / ha2).toFixed(0) : null});
  }
  for (const [lo, hi] of [[0, 30], [30, 60], [60, 90], [90, 130], [130, 200], [200, 400]]) {
    const T = treeCoast.filter(q => q.east && q.coast >= lo && q.coast < hi);
    const A = landCoast.filter(q => q.east && q.coast >= lo && q.coast < hi);
    const ha = A.length * STEP * STEP / 10000;
    eastBands.push({coast: [lo, hi], ha: +ha.toFixed(2), stems: T.length,
      stemsPerHa: ha > 0 ? +(T.length / ha).toFixed(1) : null,
      hMean: mean(T.map(q => q.h)), hP90: pct(T.map(q => q.h), 0.9),
      scMean: mean(T.map(q => q.sc)),
      windMean: mean(A.map(q => q.wind)), altMean: mean(A.map(q => q.altM)),
      /* canopy area per hectare — the quantity "heaviest mass" is about. A
       * crown's footprint goes as the square of its scale, so half the height
       * at half the spacing is a QUARTER of the cover, and stems/ha alone
       * cannot say that. */
      crownM2PerHa: ha > 0 ? +(T.reduce((s, q) => s + Math.PI * (q.h * 0.30) ** 2, 0) / ha).toFixed(0) : null,
      /* `_shelter` decomposed into its eight signed contributions, averaged over
       * the band's LAND rather than over its stems — a term averaged over the
       * stems it helped place is measured on its own survivors. A term whose
       * mean is +0.20 where the mass is is the override; a term that is flat is
       * not the answer however loudly it is named in the comments. */
      shelter: (() => {
        const s = {base: 0, wet: 0, crest: 0, salt: 0, rock: 0, gully: 0,
                   slope: 0, aspect: 0, wind: 0, total: 0, cover: 0, n: 0};
        for (const q of A) {
          const site = veg._site(q.x, q.z, 9.0);
          if (!site) continue;
          const sh = veg._shore(site), rip = veg._riparian(site);
          const slopeN = veg._slopeNorm(site.slope);
          const crest = (() => { const t = clamp((site.alt - 0.52) / 0.46, 0, 1);
            return t * t * (3 - 2 * t); })();
          s.base += 0.675;
          s.wet += (site.wet - 0.5) * 0.90;
          s.crest += -crest * 0.55;
          s.salt += -sh.salt * 0.30;
          s.rock += -site.rock * 0.25;
          s.gully += rip.gully * 0.26;
          s.slope += (0.5 - slopeN) * 0.46;
          s.aspect += site.aspect * 0.30;
          s.wind += -(sh.wind - 0.5) * 0.88;
          s.total += veg._shelter(site, sh, rip, slopeN, crest, sh.wind);
          s.cover += veg._cover(veg._standNorm(0.5), veg._shelter(site, sh, rip, slopeN, crest, sh.wind));
          s.n++;
        }
        if (!s.n) return null;
        const o = {}; for (const k of Object.keys(s)) if (k !== 'n') o[k] = +(s[k] / s.n).toFixed(3);
        return o;
      })()});
  }

  return {
    island: {cx: +isl.cx.toFixed(1), cz: +isl.cz.toFixed(1), r: +isl.r.toFixed(1)},
    eastBands, crestBands,
    waterY: +wy.toFixed(2), landSamples: L.length, sampleStepM: STEP,
    job1: {
      innerBandAltM: {n: inner.length, p50: pct(inner, 0.5), p90: pct(inner, 0.9),
                      p99: pct(inner, 0.99), max: pct(inner, 0.999)},
      strandTopP90,
      strandProfile, reachByAlt,
      disputedCells: disputed.length,
      disputedPctOfLand: +(100 * disputed.length / L.length).toFixed(1),
      disputedByOctant: octCount,
      plantedOnDisputed: onDisputed,
      tierTotals: Object.fromEntries(Object.keys(tiers).map(k => [k, tiers[k].length])),
      spit: {box: spit, land: spitLand.length, counts: spitCounts,
             swardSample: spitSward.slice(0, 20),
             swardAltP50: pct(spitSward.map(q => q.altM), 0.5),
             swardCoastP50: pct(spitSward.map(q => q.coast), 0.5),
             swardBeachMean: mean(spitSward.map(q => q.beach))},
    },
    job2: {
      windQuartileEdges: qe.map(v => +v.toFixed(3)),
      ridgePoints: ridgeRows, ridgeWindQuartileHist: ridgeHist,
      ridgeAltRange: [+altLo.toFixed(1), +altHi.toFixed(1)],
      ridge: desc(ridgeStems), shelteredControl: desc(control),
      ridgeStemsPerHa: mean(perPoint), controlStemsPerHa: mean(ctlDens),
    },
    scatter: veg._scatterStats, sward: veg._swardStats,
  };
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
