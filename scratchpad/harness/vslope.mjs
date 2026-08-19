/* vslope.mjs — three questions asked BEFORE a rule is written against them.
 *
 *   node vslope.mjs [--mods terrain,vegetation]
 *
 * The blind art direction, round sixteen: "ONE CANOPY BILLBOARD AT ONE SCALE,
 * REPEATED. Two or three canopy types at three scale tiers, with density driven
 * by slope and aspect." Three claims, and this file's own history says every one
 * of them has to be MEASURED before a constant is chosen — four rules have
 * shipped inert in this file for want of exactly that question, and `vdens2`
 * already reports `slopeFactor` mean 0.994 / 98.1% above 0.95, i.e. the slope
 * rule that exists does nothing.
 *
 *   1. THE FIELDS. `site.slope` and `site.aspect` as terrain actually reports
 *      them on land: distribution, and — the question `vexp.mjs` had to ask
 *      about exposure — whether they carry any information that `coastDist`,
 *      `wet`, `alt` and `flow` do not already carry. A driver that is 0.9
 *      correlated with the distance mask is the distance mask under a new name.
 *      Reported both as a global correlation and as the spread WITHIN a coast
 *      band, because the second is what decides whether the fringe can vary.
 *
 *   2. THE SPECIES ROLL, counted by branch. The wood measures 82-87% conifer
 *      inland and aspen is 0.2% of it — one fifth of the atlas is unused. This
 *      re-runs the roll's own arithmetic over land samples and reports which
 *      branch each stem came from and which refusal killed the rest, so the
 *      cause is a column rather than a guess.
 *
 *   3. THE SCALE, off the PLACED matrices. Not the spread — the spread is
 *      already 0.65 CV — but whether it is SPATIALLY STRUCTURED. Size drawn
 *      per-stem from a die is white noise, and white noise at a hundred metres
 *      averages to one texture whatever its variance is; a stand of mature
 *      timber beside a patch of regrowth is what "three scale tiers" means.
 *      Measured as an intraclass correlation: the fraction of the variance of
 *      log(height) that lies BETWEEN 40 m cells rather than within them.
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
  const clamp = (v, a, c) => v < a ? a : v > c ? c : v;

  const stat = (a) => {
    if (!a.length) return {n: 0};
    const m = a.reduce((s, v) => s + v, 0) / a.length;
    const sd = Math.sqrt(a.reduce((s, v) => s + (v - m) * (v - m), 0) / a.length);
    const so = a.slice().sort((u, v) => u - v);
    const at = f => +so[Math.min(so.length - 1, Math.floor(a.length * f))].toFixed(3);
    return {n: a.length, mean: +m.toFixed(3), sd: +sd.toFixed(3),
            p05: at(0.05), p25: at(0.25), p50: at(0.5), p75: at(0.75), p95: at(0.95),
            min: +so[0].toFixed(3), max: +so[so.length - 1].toFixed(3)};
  };
  const corr = (a, c) => {
    const n = Math.min(a.length, c.length);
    if (n < 8) return 0;
    let ma = 0, mc = 0;
    for (let i = 0; i < n; i++) { ma += a[i]; mc += c[i]; }
    ma /= n; mc /= n;
    let sa = 0, sc = 0, sac = 0;
    for (let i = 0; i < n; i++) {
      const u = a[i] - ma, v = c[i] - mc;
      sa += u * u; sc += v * v; sac += u * v;
    }
    return +(sac / Math.sqrt((sa * sc) || 1e-9)).toFixed(3);
  };

  /* ---- 1. the fields, over the same land samples vdens2 walks ------------- */
  const F = {slope: [], aspect: [], aspAbs: [], alt: [], wet: [], flow: [],
             coast: [], expo: [], wind: [], prom: [], curv: []};
  const byBand = {};        /* coast band -> {slope: [], aspect: []} */
  const step = 8;
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += step) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += step) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const h = veg._ground(x, z);
      if (h <= veg.waterY) continue;
      const s = veg._biome(x, z, h);
      if (!s) continue;
      s.x = x; s.z = z;
      F.slope.push(s.slope); F.aspect.push(s.aspect); F.aspAbs.push(Math.abs(s.aspect));
      F.alt.push(s.alt); F.wet.push(s.wet); F.flow.push(s.flow);
      F.coast.push(s.coast); F.expo.push(veg._exposure(x, z));
      F.wind.push(veg._windExposure ? veg._windExposure(x, z) : 0.5);
      F.prom.push(veg._prominence ? veg._prominence(x, z) : 0.5);
      /* Convexity of the ground under a 26 m span — a shoulder sheds and a
       * hollow collects, and unlike `slope` it is signed. Free here; it is the
       * same four samples `_biome`'s own fallback takes. */
      const lap = (veg._ground(x + 26, z) + veg._ground(x - 26, z) +
                   veg._ground(x, z + 26) + veg._ground(x, z - 26)) / 4 - h;
      F.curv.push(lap);
      const bi = s.coast < 40 ? 0 : s.coast < 90 ? 1 : s.coast < 150 ? 2 :
                 s.coast < 260 ? 3 : 4;
      const q = byBand[bi] || (byBand[bi] = {slope: [], aspect: [], expo: []});
      q.slope.push(s.slope); q.aspect.push(s.aspect); q.expo.push(veg._exposure(x, z));
    }
  }

  const fields = {};
  for (const k of Object.keys(F)) fields[k] = stat(F[k]);
  const against = ['coast', 'wet', 'alt', 'flow', 'expo', 'prom', 'wind'];
  const collinear = {};
  for (const k of ['slope', 'aspect', 'aspAbs', 'curv', 'prom', 'wind', 'expo']) {
    collinear[k] = {};
    for (const a of against) collinear[k][a] = corr(F[k], F[a]);
  }
  /* The question that killed the naive exposure term: does it still vary once
   * the coast distance is held? An sd near zero inside a band means the field
   * cannot break a ring however it is wired. */
  const withinBand = {};
  for (const bi of Object.keys(byBand)) {
    withinBand[['0-40', '40-90', '90-150', '150-260', '260+'][bi]] = {
      slopeSd: stat(byBand[bi].slope).sd, aspectSd: stat(byBand[bi].aspect).sd,
      expoSd: stat(byBand[bi].expo).sd, n: byBand[bi].slope.length};
  }

  /* ---- 2. the species roll, by branch ------------------------------------ */
  const fbm = w.ctx?.Tex?.fbm;
  const noise = (x, z, s, sc) => fbm
    ? fbm(x * sc, z * sc, {octaves: 3, period: 8, seed: s}) : 0.5;
  let seed = 0x5EED1;
  const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const branch = {conifer: 0, wetEdge: 0, oakRoll: 0, mixRoll: 0};
  const chosen = new Array(5).fill(0);
  const refused = {weight: 0, altitude: 0, slope: 0, wet: 0};
  const kept = new Array(5).fill(0);
  const mixSamples = [];
  const coniferP = [];
  /* SPECIES is module-private; the built entries carry the same objects. */
  const SPEC = [];
  for (const t of veg.trees) if (t.spec && !SPEC.includes(t.spec)) SPEC.push(t.spec);
  SPEC.sort((a, c) => ['spruce', 'pine', 'birch', 'oak', 'aspen'].indexOf(a.id) -
                      ['spruce', 'pine', 'birch', 'oak', 'aspen'].indexOf(c.id));
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += 10) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += 10) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const site = veg._site(x, z, 9.0);
      if (!site) continue;
      const sh = veg._shore(site);
      const rip = veg._riparian(site);
      const mixRaw = noise(x, z, 61, 0.0032);
      mixSamples.push(mixRaw);
      /* THE FILE'S OWN ROLL, not a copy of it. `_species` was extracted for
       * exactly this: the first version of this probe reimplemented the
       * arithmetic and would have gone on reporting the old mix after the rule
       * changed, which is how an instrument gives a confident wrong answer. */
      const mixN = veg._mixNorm ? veg._mixNorm(mixRaw) : mixRaw;
      const slopeN = veg._slopeNorm ? veg._slopeNorm(site.slope) : site.slope;
      if (veg._species) {
        const si = veg._species(site, sh, rip, mixN, slopeN, rnd);
        /* The probability itself, read off the file rather than recomputed,
         * because a saturated probability and a well-spread one have the same
         * mean and produce two different islands. */
        if (Number.isFinite(veg._lastConiferP)) coniferP.push(veg._lastConiferP);
        if (si < 0) { refused.weight++; continue; }
        chosen[si]++; kept[si]++;
      }
      void branch; void SPEC; void clamp;
    }
  }
  const keptTot = kept.reduce((a, q) => a + q, 0) || 1;

  /* ---- 3. the scale, off the placed matrices ----------------------------- */
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
      stems.push({x, z, si, vi, sy, hM: refH * sy, coast: veg._coastDist(x, z)});
    }
  }
  /* Intraclass correlation of log height over 40 m cells: 0 is a die, 1 is a
   * mosaic of even-aged stands. Whether SIZE is doing topographic work at all. */
  const icc = (cell) => {
    const g = new Map();
    for (const s of stems) {
      const k = ((Math.floor(s.x / cell) & 0xffff) << 16) | (Math.floor(s.z / cell) & 0xffff);
      (g.get(k) || g.set(k, []).get(k)).push(Math.log(Math.max(0.2, s.hM)));
    }
    let gm = 0, gn = 0;
    for (const v of g.values()) for (const q of v) { gm += q; gn++; }
    gm /= (gn || 1);
    let between = 0, within = 0, cells = 0;
    for (const v of g.values()) {
      if (v.length < 4) continue;
      cells++;
      const m = v.reduce((a, q) => a + q, 0) / v.length;
      between += v.length * (m - gm) * (m - gm);
      for (const q of v) within += (q - m) * (q - m);
    }
    return {cellM: cell, cells, iccBetween: +(between / ((between + within) || 1e-9)).toFixed(3)};
  };
  /* What an art director actually sees at 900 m is not the stem histogram, it
   * is the ROOF: the mean height of each stand and how much that varies from
   * stand to stand, in metres. ICC is the same quantity as a ratio and has been
   * reported for two rounds without anyone being able to picture it. */
  const roof = (cell) => {
    const g = new Map();
    for (const s of stems) {
      const k = ((Math.floor(s.x / cell) & 0xffff) << 16) | (Math.floor(s.z / cell) & 0xffff);
      (g.get(k) || g.set(k, []).get(k)).push(s.hM);
    }
    const means = [];
    for (const v of g.values()) {
      if (v.length < 4) continue;
      means.push(v.reduce((a, q) => a + q, 0) / v.length);
    }
    return {cellM: cell, stands: means.length, meanHeightM: stat(means)};
  };
  /* And whether the top of the distribution is a real tail or a CLAMP. `sc` is
   * clamped at a constant, and a clamp that binds makes every emergent exactly
   * the same height — which is "one crown size everywhere" manufactured at the
   * one end of the range where it would be most visible. Counted as the share
   * of stems within 1% of the tallest stem OF THEIR OWN SPECIES, since the
   * conversion from scale to metres is per species. */
  const perSpecMax = new Map();
  for (const s of stems) perSpecMax.set(s.si, Math.max(perSpecMax.get(s.si) || 0, s.sy));
  let atCap = 0;
  for (const s of stems) if (s.sy >= perSpecMax.get(s.si) * 0.99) atCap++;

  /* --- 3b. DOES density actually vary with slope and aspect? --------------
   *
   * The question the round is judged on, and the one a factor's mean cannot
   * answer: a term with a healthy spread can still be multiplied by something
   * that flattens it. So this is measured the same way `vfringe` measures the
   * coastal ring — stems per hectare of the land that actually exists in each
   * bin, off the placed matrices and the land mask, with no rule consulted.
   * Land area is walked on the same lattice as the fields above.
   */
  /* NATURAL GROUND ONLY, and the first version of this table did not say so and
   * was wrong because of it. The flattest ground on this island is the lab's own
   * apron and the beach — `_openness` is 0 on the pads and the beach carries a
   * total veto — so an unfiltered slope table reports the gentlest quartile as
   * the emptiest and reads as "trees prefer cliffs". Openness above 0.8 and
   * outside the salt band is the ground the planting rules actually govern. */
  const natural = (x, z, coast) => coast >= 90 && veg._openness(x, z) > 0.8;
  const SB = [0.10, 0.25, 0.42], AB = [-0.35, 0.0, 0.35];
  const binOf = (v, e) => { let i = 0; while (i < e.length && v >= e[i]) i++; return i; };
  const areaS = new Float64Array(4), areaA = new Float64Array(4);
  const cntS = new Float64Array(4), cntA = new Float64Array(4);
  const cell = step * step;
  {
    let k = 0;
    for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += step) {
      for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += step) {
        const dx = x - isl.cx, dz = z - isl.cz;
        if (dx * dx + dz * dz > isl.r * isl.r) continue;
        if (veg._ground(x, z) <= veg.waterY) continue;
        const kk = k++;
        if (!natural(x, z, F.coast[kk])) continue;
        areaS[binOf(F.slope[kk], SB)] += cell;
        areaA[binOf(F.aspect[kk], AB)] += cell;
      }
    }
  }
  for (const s of stems) {
    if (!natural(s.x, s.z, s.coast)) continue;
    const b = veg._biome(s.x, s.z, veg._ground(s.x, s.z));
    if (!b) continue;
    cntS[binOf(b.slope, SB)]++;
    cntA[binOf(b.aspect, AB)]++;
  }
  const perHa = (c, a) => [...c].map((v, i) => +(v / Math.max(1e-9, a[i] / 10000)).toFixed(1));

  /* --- 3c. EXPOSURE, which is the number round seventeen turns on -----------
   *
   * The blind verdict: "THE DENSEST, HEAVIEST MASS IN THE FRAME SITS ON THE
   * EXPOSED SEAWARD CREST." A wind-exposed crest should carry the THINNEST and
   * SHORTEST growth on the island. So: stems per hectare of the land that
   * exists, and the mean height of what stands on it, by quartile of the
   * island's OWN exposure distribution — quartiles measured here rather than
   * assumed, because a hard-coded edge over an unmeasured field is this file's
   * single most expensive recurring bug.
   *
   * Two windows, because exposure is only defined where the sea is inside
   * EXPOSE_R: `all` is every natural land cell, `band` is the 40-200 m ring
   * where the field actually has spread (expoSd 0.24 at 40-90 m against 0.00
   * past 260 m). A ratio below 1.0 on `expoPerHa` is the wanted direction.
   *
   * Also the MEAN OF EVERY OTHER DRIVER per exposure quartile. If exposure is
   * correctly signed and something else is overwhelming it, that thing is a
   * column in this table with a gradient down it.
   */
  const quart = (arr) => {
    const s = arr.slice().sort((a, c) => a - c);
    const q = f => s[Math.min(s.length - 1, Math.floor(s.length * f))];
    return [q(0.25), q(0.50), q(0.75)];
  };
  const EB = quart(F.expo), WB = quart(F.wind);
  /* `lat` is the field the LATTICE is binned by and `at` reads the same field
   * off a stem's position. Two arguments rather than one because a stem is not
   * on the lattice, and the first version of this table binned the stems with
   * one field and the area with another — an ablation that stubs a field and
   * then bins the result with a function that was also stubbed is on this
   * project's list of instruments that lied. */
  const expoTable = (lo, hi, lat, at, edges) => {
    const area = new Float64Array(4), cnt = new Float64Array(4);
    const hsum = new Float64Array(4), hn = new Float64Array(4);
    const drv = {wet: [], alt: [], slopeN: [], aspect: [], salt: [], rock: [],
                 gully: [], shelter: [], cover: [], coast: [], prom: [], wind: []};
    const dacc = {}; for (const k of Object.keys(drv)) dacc[k] = new Float64Array(4);
    const dn = new Float64Array(4);
    let k = 0;
    for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += step) {
      for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += step) {
        const dx = x - isl.cx, dz = z - isl.cz;
        if (dx * dx + dz * dz > isl.r * isl.r) continue;
        if (veg._ground(x, z) <= veg.waterY) continue;
        const kk = k++;
        const co = F.coast[kk];
        if (co < lo || co > hi) continue;
        if (veg._openness(x, z) <= 0.8) continue;
        const bi = binOf(lat[kk], edges);
        area[bi] += cell;
        /* The drivers, off the file's own readers — no arithmetic copied. */
        const site = veg._site(x, z, 9.0);
        if (!site) continue;
        const sh = veg._shore(site), rip = veg._riparian(site);
        const sN = veg._slopeNorm(site.slope);
        const cr = smoothstep(0.52, 0.98, site.alt);
        dacc.wet[bi] += site.wet; dacc.alt[bi] += site.alt;
        dacc.slopeN[bi] += sN; dacc.aspect[bi] += site.aspect;
        dacc.salt[bi] += sh.salt; dacc.rock[bi] += site.rock;
        dacc.gully[bi] += rip.gully; dacc.coast[bi] += site.coast;
        dacc.prom[bi] += veg._prominence ? veg._prominence(x, z) : 0.5;
        dacc.wind[bi] += veg._windExposure ? veg._windExposure(x, z) : 0.5;
        /* `shelter` and `cover` are the file's, via the one entry point that
         * exists for them, so this table cannot drift from the rule. */
        const shel = veg._shelter ? veg._shelter(site, sh, rip, sN, cr) : NaN;
        dacc.shelter[bi] += shel;
        dacc.cover[bi] += veg._cover(veg._standNorm(0.46), shel);
        dn[bi]++;
      }
    }
    for (const s of stems) {
      const co = veg._coastDist(s.x, s.z);
      if (co < lo || co > hi) continue;
      if (veg._openness(s.x, s.z) <= 0.8) continue;
      const bi = binOf(at(s.x, s.z), edges);
      cnt[bi]++; hsum[bi] += s.hM; hn[bi]++;
    }
    const per = [...cnt].map((v, i) => +(v / Math.max(1e-9, area[i] / 10000)).toFixed(1));
    const hm = [...hsum].map((v, i) => +(v / Math.max(1, hn[i])).toFixed(2));
    const drivers = {};
    for (const key of Object.keys(dacc)) {
      drivers[key] = [...dacc[key]].map((v, i) => +(v / Math.max(1, dn[i])).toFixed(3));
    }
    return {stemsPerHa: per, meanHeightM: hm, n: [...cnt],
            ratioQ4Q1: +(per[3] / Math.max(1e-6, per[0])).toFixed(3),
            heightRatioQ4Q1: +(hm[3] / Math.max(1e-6, hm[0])).toFixed(3),
            drivers};
  };
  const smoothstep = (a, c, x) => { const t = clamp((x - a) / (c - a || 1e-6), 0, 1); return t * t * (3 - 2 * t); };

  /* How many silhouette classes carry the wood: the share of the largest
     species-variant bucket, and how many buckets it takes to reach 80%. */
  const bucket = new Map();
  for (const s of stems) {
    const k = s.si * 8 + s.vi;
    bucket.set(k, (bucket.get(k) || 0) + 1);
  }
  const sorted = [...bucket.entries()].sort((a, c) => c[1] - a[1]);
  let cum = 0, need80 = 0;
  for (const [, v] of sorted) { cum += v; need80++; if (cum >= stems.length * 0.8) break; }

  /* Height tiers as an art director would count them: understorey, canopy,
     emergent, by absolute metres rather than by scale factor. */
  const hs = stems.map(s => s.hM);
  const tier = (lo, hi) => +(100 * hs.filter(v => v >= lo && v < hi).length / (hs.length || 1)).toFixed(1);

  /* ---- THE NAMED PLACE, and it is the whole lesson of round eighteen -------
   *
   * Every table above is binned over the WHOLE island, and the critic is
   * looking at ONE PLACE. Round seventeen's exposure quartiles came out in the
   * wanted direction over the island and the eastern seaward crest went on
   * reading heavy, and there was no instrument that could tell which of "the
   * field cannot see that place" and "the field sees it and something else
   * overrides it" was true. This is that instrument.
   *
   * The crest is found GEOMETRICALLY rather than typed in as a box: walk inland
   * from the waterline on each east-facing bearing and stop at the first ridge,
   * then keep only the bearings whose ridge is still within 145 m of the water,
   * because a ridge 200 m inland is a different hill and averaging it in is how
   * the island-wide table hid this in the first place. */
  const namedPlace = (() => {
    const wy = veg.waterY;
    const g = (x, z) => veg._ground(x, z);
    const ridge = [];
    for (let deg = -30; deg <= 40; deg += 2) {
      const a = deg * Math.PI / 180, cx = Math.cos(a), cz = Math.sin(a);
      let rw = -1;
      for (let r = isl.r + 60; r > 20; r -= 3)
        if (g(isl.cx + cx * r, isl.cz + cz * r) > wy) { rw = r; break; }
      if (rw < 0) continue;
      let best = -1e9, bestR = rw, prev = -1e9, fell = 0;
      for (let r = rw; r > rw - 320 && r > 20; r -= 4) {
        const h = g(isl.cx + cx * r, isl.cz + cz * r);
        if (h > best) { best = h; bestR = r; fell = 0; }
        else if (h < prev - 0.4) { fell++; if (fell >= 3) break; }
        prev = h;
      }
      const x = isl.cx + cx * bestR, z = isl.cz + cz * bestR;
      if (veg._coastDist(x, z) > 145) continue;
      ridge.push({x, z, altM: best - wy});
    }
    if (!ridge.length) return {found: 0};
    const qOf = (v, e) => v < e[0] ? 1 : v < e[1] ? 2 : v < e[2] ? 3 : 4;
    const hist = [0, 0, 0, 0];
    let ws = 0;
    for (const c of ridge) { const v = veg._windExposure(c.x, c.z);
      ws += v; hist[qOf(v, WB) - 1]++; }
    const on = stems.filter(s => ridge.some(c =>
      (s.x - c.x) ** 2 + (s.z - c.z) ** 2 < 40 * 40));
    const altLo = Math.min(...ridge.map(c => c.altM));
    const altHi = Math.max(...ridge.map(c => c.altM));
    /* A control matched on ALTITUDE, in the sheltered quartile. Without it a
       height difference on a ridge is a statement about how high the ridge is. */
    const ctl = stems.filter(s => {
      const a = g(s.x, s.z) - wy;
      return a >= altLo && a <= altHi && veg._windExposure(s.x, s.z) < WB[0];
    });
    const m = (a, f) => a.length ? +(a.reduce((t, q) => t + f(q), 0) / a.length).toFixed(3) : null;
    return {
      found: ridge.length, altRangeM: [+altLo.toFixed(1), +altHi.toFixed(1)],
      windMean: +(ws / ridge.length).toFixed(3),
      windQuartileHist: hist,
      verdict: hist[3] >= ridge.length * 0.5
        ? 'the field SEES this place (majority Q4) — a height or density that does '
          + 'not move here is being overridden downstream, not mis-measured'
        : 'the field CANNOT see this place — no weighting on it will ever strip it',
      onCrest: {n: on.length, heightM: m(on, s => s.hM), scale: m(on, s => s.sy)},
      shelteredControl: {n: ctl.length, heightM: m(ctl, s => s.hM), scale: m(ctl, s => s.sy)},
      heightRatio: on.length && ctl.length
        ? +(m(on, s => s.hM) / m(ctl, s => s.hM)).toFixed(3) : null,
    };
  })();

  return {
    islandR: +isl.r.toFixed(0), landSamples: F.slope.length, stems: stems.length,
    easternSeawardCrest: namedPlace,
    strand: veg._strandStats || null,
    fields, collinear, withinBand,
    aspectUnit: veg._aspectUnit || null,
    ranges: {slope: veg._slopeRange, mix: veg._mixRange, age: veg._ageRange,
             stand: veg._standRange, wet: veg._wetRange},
    mix: stat(mixSamples),
    speciesRoll: {
      order: SPEC.map(s => s.id), branch, refused,
      coniferP: stat(coniferP),
      chosenPct: chosen.map(v => +(100 * v / (chosen.reduce((a, q) => a + q, 0) || 1)).toFixed(1)),
      keptPct: kept.map(v => +(100 * v / keptTot).toFixed(1)),
    },
    topographicWork: {
      slopeBins: '<0.10 / 0.10-0.25 / 0.25-0.42 / >0.42',
      stemsPerHaBySlope: perHa(cntS, areaS),
      aspectBins: 'sun-facing <-0.35 / -0.35..0 / 0..0.35 / shaded >0.35',
      stemsPerHaByAspect: perHa(cntA, areaA),
    },
    exposure: {
      note: 'Q1 sheltered .. Q4 exposed. ratioQ4Q1 < 1 is the wanted direction.',
      promRange: veg._promStats || null,
      /* SEA FETCH, the field that already existed. Its Q4 is the island's
         LOWEST ground — a spit detector — which is why it never spoke for the
         crest the critique is looking at. */
      seaFetch: {
        quartileEdges: EB.map(v => +v.toFixed(3)),
        all: expoTable(0, 1e9, F.expo, (x, z) => veg._exposure(x, z), EB),
        band40to200: expoTable(40, 200, F.expo, (x, z) => veg._exposure(x, z), EB),
      },
      /* WIND EXPOSURE: sea fetch plus local prominence. This is the field the
         density and height rules are written on, so this is the table the
         round is judged by. */
      windExposure: {
        quartileEdges: WB.map(v => +v.toFixed(3)),
        all: expoTable(0, 1e9, F.wind, (x, z) => veg._windExposure(x, z), WB),
        band40to200: expoTable(40, 200, F.wind, (x, z) => veg._windExposure(x, z), WB),
      },
    },
    scale: {
      heightM: stat(hs),
      tiersPct: {'<6m': tier(0, 6), '6-12m': tier(6, 12), '12-20m': tier(12, 20),
                 '20-30m': tier(20, 30), '30m+': tier(30, 1e4)},
      icc40: icc(40), icc80: icc(80),
      roof40: roof(40), roof80: roof(80),
      scaleAtCapPct: +(100 * atCap / (stems.length || 1)).toFixed(2),
      /* And the same question asked of the file rather than inferred from the
         matrices: `scaleAtCapPct` above is a PROXY (within 1% of the tallest
         stem of its species) and it cannot separate a stem that hit the clamp
         from one that merely rolled a high depth die. `_scaleStats` is counted
         at the clamp itself. The proxy said 0.09% where the counter says
         otherwise; trust the counter. */
      scaleClamp: veg._scaleStats || null,
      topBucketPct: +(100 * (sorted[0] ? sorted[0][1] : 0) / (stems.length || 1)).toFixed(1),
      bucketsFor80Pct: need80, buckets: sorted.length,
    },
  };
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
