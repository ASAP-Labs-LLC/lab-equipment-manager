/* vspit.mjs — the two named places, measured where the critic is looking.
 *
 *   node vspit.mjs [--mods terrain,vegetation]
 *
 * Job 1. A stem is standing on bare sand on the south-east spit. The island-wide
 * tables cannot see one stem, so this finds it by its own description — LOW
 * ground, ISOLATED — off the placed matrices, and then re-runs the scatter's own
 * gate chain AT THAT POINT using vegetation.js's own methods so the number that
 * let it through is named rather than guessed.
 *
 * Job 2. `vslope.mjs` bins the whole island. The critic is looking at ONE PLACE.
 * This finds the eastern seaward crest geometrically — walk inland from the
 * waterline on each east-facing bearing, stop at the first ridge — and reports
 * which island-wide wind-exposure quartile those points land in, plus the stems
 * standing on them.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
/* ALL mods by default, not terrain+vegetation. `_openness` reads buildings and
 * props; with them absent the scatter is a different scatter, and the frame the
 * critic is judging has them. An instrument that measures a world the operator
 * never sees is this project's commonest liar. */
const mods = arg('mods', '');
const box = arg('box', '60,300,140,380').split(',').map(Number);

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

const out = await p.evaluate(({box}) => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const ter = w.subsystems.get('terrain');
  const ctx = w.ctx;
  const fbm = ctx.Tex && ctx.Tex.fbm;
  const noise = (x, z, s, sc) => fbm
    ? fbm(x * sc, z * sc, {octaves: 3, period: 8, seed: s}) : 0.5;
  const clamp = (v, a, c) => v < a ? a : v > c ? c : v;
  const smoothstep = (a, c, x) => { const t = clamp((x - a) / (c - a || 1e-6), 0, 1); return t * t * (3 - 2 * t); };
  const isl = veg.island;
  const wy = veg.waterY;
  const ground = (x, z) => veg._ground(x, z);

  /* ---------- every placed stem, with its ground facts ------------------- */
  const stems = [];
  for (const e of (veg.trees || [])) {
    for (let i = 0; i < e.list.length; i++) {
      const x = e.xs[i], z = e.zs[i];
      const y = e.mats[i * 16 + 13];
      /* the matrix's own y scale times the species reference height: what the
       * eye actually sees, not the die. */
      const sy = Math.hypot(e.mats[i * 16 + 4], e.mats[i * 16 + 5], e.mats[i * 16 + 6]);
      stems.push({x, z, y, h: sy * (e.spec.refH || 18), spec: e.spec.name || '?',
                  vi: e.S && e.S.vi});
    }
  }
  /* nearest-neighbour, on a 20 m hash so this is not 46k^2 */
  const CELL = 24;
  const grid = new Map();
  const key = (i, j) => i * 100000 + j;
  for (let k = 0; k < stems.length; k++) {
    const i = Math.floor(stems[k].x / CELL), j = Math.floor(stems[k].z / CELL);
    const kk = key(i, j);
    let a = grid.get(kk); if (!a) grid.set(kk, a = []);
    a.push(k);
  }
  const nnDist = (k, maxR = 3) => {
    const s = stems[k];
    const ci = Math.floor(s.x / CELL), cj = Math.floor(s.z / CELL);
    let best = 1e9;
    for (let di = -maxR; di <= maxR; di++) for (let dj = -maxR; dj <= maxR; dj++) {
      const a = grid.get(key(ci + di, cj + dj)); if (!a) continue;
      for (const o of a) { if (o === k) continue;
        const dx = stems[o].x - s.x, dz = stems[o].z - s.z;
        const d = Math.hypot(dx, dz); if (d < best) best = d; }
    }
    return best;
  };

  /* ---------- every OTHER vegetation instance, by tier ------------------ */
  const tiers = {tree: stems.map(s => [s.x, s.z, s.h]), grove: [], clutter: [], sward: [], grass: []};
  for (const g of (veg.groves || [])) for (let i = 0; i < g.count; i++) tiers.grove.push([g.xs[i], g.zs[i], 26]);
  for (const c of (veg.clutter || [])) for (let i = 0; i < c.count; i++) tiers.clutter.push([c.xs[i], c.zs[i], 1.1]);
  for (const s of (veg.sward || [])) for (let i = 0; i < s.count; i++) tiers.sward.push([s.xs[i], s.zs[i], 7.8]);
  if (veg.grass) for (let i = 0; i < veg.grass.count; i++)
    tiers.grass.push([veg.grass.mats[i * 16 + 12], veg.grass.mats[i * 16 + 14], 0.5]);

  /* ---------- the named box the props round gave ------------------------ */
  const inBox = {};
  for (const k of Object.keys(tiers)) {
    const hit = tiers[k].filter(q => q[0] >= box[0] && q[0] <= box[2] &&
                                     q[1] >= box[1] && q[1] <= box[3]);
    inBox[k] = {n: hit.length, sample: hit.slice(0, 20).map(q =>
      [+q[0].toFixed(1), +q[1].toFixed(1), +q[2].toFixed(1),
       +(ground(q[0], q[1]) - wy).toFixed(2), +veg._coastDist(q[0], q[1]).toFixed(1)])};
  }

  /* ---------- Job 1: low + isolated ------------------------------------- */
  /* terrain's own strand arithmetic, quoted in REQUESTS.md from terrain.js:
   *   strand  = smoothstep(10, 0, h - waterY); wetSand = smoothstep(0.79, 0.965, strand)
   * saturated below 1.12 m, gone by 2.95 m, damp fringe out to 4.87 m. */
  const lowIso = [];
  for (let k = 0; k < stems.length; k++) {
    const s = stems[k];
    const altM = s.y - wy;
    if (altM > 6.0) continue;
    const nn = nnDist(k);
    if (nn < 22) continue;
    lowIso.push({x: +s.x.toFixed(1), z: +s.z.toFixed(1), altM: +altM.toFixed(2),
                 nn: +nn.toFixed(1), h: +s.h.toFixed(1), spec: s.spec,
                 coast: +veg._coastDist(s.x, s.z).toFixed(1)});
  }
  lowIso.sort((a, c) => c.nn - a.nn);

  /* how many stems stand on sand at all, isolated or not */
  let onWash = 0, onDamp = 0, below6 = 0;
  for (const s of stems) {
    const a = s.y - wy;
    if (a < 2.95) onWash++;
    if (a < 4.87) onDamp++;
    if (a < 6.0) below6++;
  }

  /* the gate chain at a named point, using the file's own methods */
  const chainAt = (x, z) => {
    const site = veg._site(x, z, 9.0);
    if (!site) return {x, z, rejected: '_site'};
    const standN = veg._standNorm(noise(x, z, 7, 0.0042));
    const grain = noise(x, z, 23, 0.011);
    const texture = 0.74 + 0.52 * grain;
    const open = veg._openness(x, z);
    const d0 = clamp(veg._cover(standN, 1) * texture * open, 0, 1);
    const sh = veg._shore(site);
    const rip = veg._riparian(site);
    const crest = smoothstep(0.52, 0.98, site.alt);
    const mouth = Math.max(rip.channel, rip.bank * 0.72) *
                  (1 - smoothstep(26 * 0.6, 130 * 0.75, site.coast));
    const fBeach = 1 - sh.beach * (1 - 0.62 * mouth);
    const fSalt = 1 - sh.salt * 0.62 * (1 - 0.55 * mouth);
    const slopeN = veg._slopeNorm(site.slope);
    const shelter = veg._shelter(site, sh, rip, slopeN, crest, sh.wind);
    const cover = veg._cover(standN, shelter);
    const d = clamp(d0 * (1 - smoothstep(4.5, 9.0, site.drop || 0)) *
                    (1 - smoothstep(0.55, 1.05, site.slope) * 0.92) *
                    (1 - crest * 0.46) * (1 - site.rock * 0.62) *
                    fBeach * fSalt * (cover / Math.max(1e-3, veg._cover(standN, 1))), 0, 1);
    return {x: +x.toFixed(1), z: +z.toFixed(1),
            groundY: +site.h.toFixed(2), altM: +(site.h - wy).toFixed(2),
            altN: +site.alt.toFixed(3), slope: +site.slope.toFixed(3),
            coastDist: +site.coast.toFixed(1),
            beach: +sh.beach.toFixed(3), salt: +sh.salt.toFixed(3),
            exposure: +sh.exposure.toFixed(3), wind: +sh.wind.toFixed(3),
            prom: +veg._prominence(x, z).toFixed(3),
            mouth: +mouth.toFixed(3), fBeach: +fBeach.toFixed(3), fSalt: +fSalt.toFixed(3),
            standN: +standN.toFixed(3), open: +open.toFixed(3),
            shelter: +shelter.toFixed(3), cover: +cover.toFixed(3),
            dBound: +d0.toFixed(3), dFinal: +d.toFixed(3),
            wet: +site.wet.toFixed(3), rock: +(site.rock||0).toFixed(2),
            flow: +(site.flow||0).toFixed(3)};
  };

  /* ---------- how beach/coastDist behaves along a transect --------------- */
  /* Walk a ray from the island centre out through the SE spit and print, per
   * metre band, the elevation above the tide against `sh.beach`. If the two
   * disagree the beach veto is measuring the wrong thing. */
  const transect = (bearingDeg, from, to, step) => {
    const a = bearingDeg * Math.PI / 180;
    const rows = [];
    for (let r = from; r <= to; r += step) {
      const x = isl.cx + Math.cos(a) * r, z = isl.cz + Math.sin(a) * r;
      const h = ground(x, z);
      const cd = veg._coastDist(x, z);
      const sh = veg._shore({coast: cd, x, z});
      rows.push({r, altM: +(h - wy).toFixed(2), coastD: +cd.toFixed(1),
                 beach: +sh.beach.toFixed(3), salt: +sh.salt.toFixed(3)});
    }
    return rows;
  };

  /* ---------- the disagreement, island-wide ----------------------------- */
  /* Over land samples: cross-tabulate "terrain paints this sand" (altM < 2.95)
   * against "vegetation calls this beach" (sh.beach > 0.5). */
  let n11 = 0, n10 = 0, n01 = 0, n00 = 0;
  const sandNotBeach = [];
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += 6) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += 6) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const h = ground(x, z);
      if (h <= wy) continue;
      const altM = h - wy;
      const cd = veg._coastDist(x, z);
      const sh = veg._shore({coast: cd, x, z});
      const sand = altM < 2.95, beach = sh.beach > 0.5;
      if (sand && beach) n11++;
      else if (sand && !beach) { n10++; if (sandNotBeach.length < 400) sandNotBeach.push([+x.toFixed(0), +z.toFixed(0), +altM.toFixed(2), +cd.toFixed(1)]); }
      else if (!sand && beach) n01++;
      else n00++;
    }
  }

  /* ---------- Job 2: the eastern seaward crest -------------------------- */
  /* Walk inland from the waterline on each east-facing bearing; the crest is
   * the first point at which the ground stops climbing. */
  const crestPts = [];
  for (let deg = -50; deg <= 50; deg += 2) {
    const a = deg * Math.PI / 180;
    const cx = Math.cos(a), cz = Math.sin(a);
    /* find the waterline outward */
    let rw = -1;
    for (let r = isl.r + 60; r > 20; r -= 3) {
      if (ground(isl.cx + cx * r, isl.cz + cz * r) > wy) { rw = r; break; }
    }
    if (rw < 0) continue;
    /* inland until the height stops rising */
    let best = -1e9, bestR = rw, prev = -1e9, fell = 0;
    for (let r = rw; r > rw - 320 && r > 20; r -= 4) {
      const h = ground(isl.cx + cx * r, isl.cz + cz * r);
      if (h > best) { best = h; bestR = r; fell = 0; }
      else if (h < prev - 0.4) { fell++; if (fell >= 3) break; }
      prev = h;
    }
    const x = isl.cx + cx * bestR, z = isl.cz + cz * bestR;
    if (ground(x, z) <= wy) continue;
    crestPts.push({deg, x, z, altM: best - wy,
                   coast: veg._coastDist(x, z),
                   seawardRun: rw - bestR});
  }

  /* island-wide wind quartile edges, on land, so a named place can be placed
   * in the SAME table vslope prints. */
  const windAll = [], expAll = [], promAll = [];
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += 6) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += 6) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      if (ground(x, z) <= wy) continue;
      windAll.push(veg._windExposure(x, z));
      expAll.push(veg._exposure(x, z));
      promAll.push(veg._prominence(x, z));
    }
  }
  const qEdges = (a) => { const s = a.slice().sort((u, v) => u - v);
    return [s[Math.floor(s.length * 0.25)], s[Math.floor(s.length * 0.5)], s[Math.floor(s.length * 0.75)]]; };
  const wq = qEdges(windAll);
  const quartOf = (v, e) => v < e[0] ? 1 : v < e[1] ? 2 : v < e[2] ? 3 : 4;

  const crestRows = crestPts.map(c => ({
    deg: c.deg, x: +c.x.toFixed(0), z: +c.z.toFixed(0),
    altM: +c.altM.toFixed(1), coast: +c.coast.toFixed(0),
    wind: +veg._windExposure(c.x, c.z).toFixed(3),
    wq: quartOf(veg._windExposure(c.x, c.z), wq),
    exp: +veg._exposure(c.x, c.z).toFixed(3),
    prom: +veg._prominence(c.x, c.z).toFixed(3),
  }));
  const wqHist = [0, 0, 0, 0];
  for (const r of crestRows) wqHist[r.wq - 1]++;

  /* and the stems standing on that crest: density and height, against the
   * island's own means. */
  const near = [];
  for (let k = 0; k < stems.length; k++) {
    const s = stems[k];
    for (const c of crestPts) {
      const dx = s.x - c.x, dz = s.z - c.z;
      if (dx * dx + dz * dz < 35 * 35) { near.push(s); break; }
    }
  }
  const mean = a => a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0;
  const areaCrest = crestPts.length * Math.PI * 35 * 35 * 0.42; /* overlapping, rough */

  return {
    island: {cx: +isl.cx.toFixed(1), cz: +isl.cz.toFixed(1), r: +isl.r.toFixed(1)},
    waterY: +wy.toFixed(2), landRelief: +(veg.landRelief || 0).toFixed(1),
    coastCell: veg.coast ? +veg.coast.cell.toFixed(1) : null,
    stems: stems.length,
    tierCounts: Object.fromEntries(Object.keys(tiers).map(k => [k, tiers[k].length])),
    inBox: {box, ...inBox},
    onWashSand: onWash, onDampSand: onDamp, below6m: below6,
    lowIsolated: lowIso.slice(0, 25),
    lowIsolatedN: lowIso.length,
    chains: lowIso.slice(0, 6).map(s => chainAt(s.x, s.z)),
    sandVsBeach: {bothSandAndBeach: n11, sandButNotBeach: n10,
                  beachButNotSand: n01, neither: n00},
    sandNotBeachSample: sandNotBeach.slice(0, 12),
    transectSE: transect(45, 20, isl.r + 40, 8),
    transectE: transect(0, 20, isl.r + 40, 8),
    windQuartileEdges: wq.map(v => +v.toFixed(3)),
    crestPts: crestRows,
    crestWindQuartileHist: wqHist,
    crestMeanWind: +mean(crestRows.map(r => r.wind)).toFixed(3),
    islandMeanWind: +mean(windAll).toFixed(3),
    crestStems: near.length,
    crestStemHeightMean: +mean(near.map(s => s.h)).toFixed(2),
    islandStemHeightMean: +mean(stems.map(s => s.h)).toFixed(2),
    crestStemsPerHa: +(near.length / (areaCrest / 10000)).toFixed(1),
    expoStats: veg._expoStats,
    scatter: veg._scatterStats,
  };
}, {box});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
