/* vflow.mjs — what does terrain's NEW drainage signal actually look like from
 * inside vegetation.js's own land set?
 *
 *   node vflow.mjs [--mods terrain,vegetation] [--step 6]
 *
 * terrain round 15 retuned FLOW_LO/FLOW_HI onto measured percentiles, so
 * `biomeAt().flow` and `kind === 'stream'` fire for the first time. This file
 * has never had a rule keyed to either, so before writing one, measure:
 *
 *   · the distribution of `flow` over the same land samples vdens2 walks
 *     (its own `_site` decides what land is, so the two agree);
 *   · how much land each candidate threshold would select;
 *   · where the flow is — by coast distance, by altitude, by slope — because a
 *     riparian rule that fires only on the beach is a shore rule with extra
 *     steps, and one that fires everywhere is not riparian at all;
 *   · whether `flow` is INDEPENDENT of `site.wet`. terrain feeds flow * 0.55
 *     into moisture, so if the two are collinear a new rule keyed to flow is a
 *     second copy of a rule this file already has;
 *   · whether the high-flow cells form LINEAR RUNS (a channel) or scattered
 *     specks (noise at the erosion grid's own cell size). A stand of trees can
 *     follow a channel and cannot follow a speck.
 *   · what the existing scatter does at those cells today — if the density is
 *     already at the ceiling there, a riparian rule has nothing to add.
 *   · OUTLETS: cells where a channel meets the beach band.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const mods = arg('mods', 'terrain,vegetation');
const step = +arg('step', 6);

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

const out = await p.evaluate((STEP) => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const ter = w.subsystems.get('terrain');
  if (!veg || !ter || typeof ter.biomeAt !== 'function') return {error: 'no terrain.biomeAt'};
  const isl = veg.island;

  const pct = (a, q) => { if (!a.length) return 0; const s = a.slice().sort((u, v) => u - v);
    return +s[Math.min(s.length - 1, Math.floor(q * s.length))].toFixed(3); };

  /* One pass over the island's disc, keeping a grid so runs can be counted. */
  const N = Math.floor(2 * isl.r / STEP) + 1;
  const flowG = new Float32Array(N * N).fill(-1);
  const flows = [], kinds = {};
  const rows = [];
  let land = 0;
  for (let j = 0; j < N; j++) {
    for (let i = 0; i < N; i++) {
      const x = isl.cx - isl.r + i * STEP, z = isl.cz - isl.r + j * STEP;
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const site = veg._site(x, z, 9.0);
      if (!site) continue;
      const bi = ter.biomeAt(x, z);
      if (!bi) continue;
      land++;
      const f = Number.isFinite(bi.flow) ? bi.flow : -1;
      flowG[j * N + i] = f;
      flows.push(f);
      kinds[bi.kind] = (kinds[bi.kind] || 0) + 1;
      rows.push({x, z, f, kind: bi.kind, wet: site.wet, wetRaw: site.wetRaw,
                 coast: site.coast, alt: site.alt, altM: site.altM,
                 slope: site.slope, rock: site.rock});
    }
  }

  /* Distribution. */
  const dist = {n: land, p50: pct(flows, 0.50), p80: pct(flows, 0.80),
                p90: pct(flows, 0.90), p95: pct(flows, 0.95),
                p98: pct(flows, 0.98), p99: pct(flows, 0.99),
                max: +Math.max(...flows).toFixed(3),
                mean: +(flows.reduce((a, v) => a + v, 0) / land).toFixed(3)};

  /* How much land each threshold selects. */
  const thr = {};
  for (const t of [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.55, 0.70])
    thr['gt' + t] = +(100 * rows.filter(r => r.f > t).length / land).toFixed(2);

  /* Where is it? Bucket by coast distance and by altitude. */
  const bucket = (key, edges) => {
    const out = [];
    for (let k = 0; k < edges.length - 1; k++) {
      const sel = rows.filter(r => r[key] >= edges[k] && r[key] < edges[k + 1]);
      if (!sel.length) { out.push({lo: edges[k], hi: edges[k + 1], n: 0}); continue; }
      out.push({lo: edges[k], hi: edges[k + 1], n: sel.length,
                meanFlow: +(sel.reduce((a, r) => a + r.f, 0) / sel.length).toFixed(3),
                pctGully: +(100 * sel.filter(r => r.f > 0.20).length / sel.length).toFixed(1),
                pctStream: +(100 * sel.filter(r => r.f > 0.55).length / sel.length).toFixed(1)});
    }
    return out;
  };

  /* Is flow just moisture again? Pearson r over land, plus the mean wet inside
   * and outside the gully band — a rule that adds nothing wants to know. */
  const corr = (a, bq) => {
    const n = a.length, ma = a.reduce((s, v) => s + v, 0) / n, mb = bq.reduce((s, v) => s + v, 0) / n;
    let sab = 0, saa = 0, sbb = 0;
    for (let k = 0; k < n; k++) { const u = a[k] - ma, v = bq[k] - mb; sab += u * v; saa += u * u; sbb += v * v; }
    return +(sab / Math.sqrt(saa * sbb + 1e-9)).toFixed(3);
  };
  const gully = rows.filter(r => r.f > 0.20), dry = rows.filter(r => r.f <= 0.20);
  const mean = (a, k) => a.length ? +(a.reduce((s, r) => s + r[k], 0) / a.length).toFixed(3) : 0;

  /* Runs: for each threshold, walk the grid in x and in z and histogram the
   * length of contiguous above-threshold spans. A channel is long runs in one
   * direction; grid noise is runs of one. */
  const runs = (t) => {
    const lens = [];
    const scan = (get) => {
      for (let a = 0; a < N; a++) {
        let run = 0;
        for (let bq = 0; bq < N; bq++) {
          const v = get(a, bq);
          if (v > t) run++;
          else { if (run) lens.push(run); run = 0; }
        }
        if (run) lens.push(run);
      }
    };
    scan((a, bq) => flowG[a * N + bq]);
    scan((a, bq) => flowG[bq * N + a]);
    if (!lens.length) return {n: 0};
    lens.sort((u, v) => v - u);
    return {n: lens.length, max: lens[0], p90: lens[Math.floor(lens.length * 0.10)],
            median: lens[Math.floor(lens.length * 0.5)],
            meanCells: +(lens.reduce((s, v) => s + v, 0) / lens.length).toFixed(2),
            pctRunsOf1: +(100 * lens.filter(v => v === 1).length / lens.length).toFixed(1),
            longestMetres: +(lens[0] * STEP).toFixed(0)};
  };

  /* OUTLETS: gully cells inside the beach/salt band. */
  const SHORE_BEACH = 26, SHORE_SALT = 130;
  const outlet = rows.filter(r => r.f > 0.20 && r.coast < SHORE_SALT);
  const outletBeach = rows.filter(r => r.f > 0.20 && r.coast < SHORE_BEACH * 1.6);

  /* What does the existing scatter do at the gully cells? Re-run the density
     chain the way vdens2 does, and compare the mean final density inside the
     gully band against outside it. If they are equal the terrain signal is
     invisible to the forest. */
  const fbm = w.ctx.Tex && w.ctx.Tex.fbm;
  const noise = (x, z, s, sc) => fbm ? fbm(x * sc, z * sc, {octaves: 3, period: 8, seed: s}) : 0.5;
  const clamp = (v, a, bq) => v < a ? a : v > bq ? bq : v;
  const smoothstep = (a, bq, x) => { const t = clamp((x - a) / (bq - a || 1e-6), 0, 1); return t * t * (3 - 2 * t); };
  const density = (r) => {
    const site = veg._site(r.x, r.z, 9.0);
    if (!site) return null;
    const standN = veg._standNorm(noise(r.x, r.z, 7, 0.0042));
    const texture = 0.74 + 0.52 * noise(r.x, r.z, 23, 0.011);
    let d = clamp(veg._cover(standN, 1) * texture * veg._openness(r.x, r.z), 0, 1);
    d *= 1 - smoothstep(4.5, 9.0, site.drop || 0);
    d *= 1 - smoothstep(0.62, 1.20, site.slope);
    const crest = smoothstep(0.52, 0.98, site.alt);
    d *= 1 - crest * 0.46;
    d *= 1 - site.rock * 0.62;
    const sh = veg._shore(site);
    const rip = veg._riparian(site);
    const mouth = Math.max(rip.channel, rip.bank * 0.72) *
                  (1 - smoothstep(26 * 0.6, 130 * 0.75, site.coast));
    d *= (1 - sh.beach * (1 - 0.62 * mouth)) * (1 - sh.salt * 0.62 * (1 - 0.55 * mouth));
    const shelter = clamp(0.70 + (site.wet - 0.5) * 0.90 - crest * 0.85 -
                          sh.salt * 0.30 - site.rock * 0.25 + rip.gully * 0.26, 0, 1);
    const cover = veg._cover(standN, shelter);
    d *= cover / Math.max(1e-3, veg._cover(standN, 1));
    return {d: clamp(d, 0, 1), cover};
  };
  const sampleD = (set) => {
    const take = set.filter((_, k) => k % Math.max(1, Math.floor(set.length / 900)) === 0);
    const ds = [], cs = [];
    for (const r of take) { const q = density(r); if (q) { ds.push(q.d); cs.push(q.cover); } }
    return ds.length ? {n: ds.length,
      meanDensity: +(ds.reduce((a, v) => a + v, 0) / ds.length).toFixed(3),
      meanCover: +(cs.reduce((a, v) => a + v, 0) / cs.length).toFixed(3)} : {n: 0};
  };

  return {
    islandR: +isl.r.toFixed(0), step: STEP, land,
    flowDist: dist, thresholds: thr, kinds,
    byCoast: bucket('coast', [0, 26, 60, 130, 220, 400, 1e5]),
    byAlt: bucket('altM', [0, 5, 12, 25, 40, 60, 200]),
    bySlope: bucket('slope', [0, 0.2, 0.4, 0.62, 1.0, 9]),
    collinearity: {
      r_flow_wet: corr(rows.map(r => r.f), rows.map(r => r.wet)),
      r_flow_wetRaw: corr(rows.map(r => r.f), rows.map(r => r.wetRaw)),
      r_flow_coast: corr(rows.map(r => r.f), rows.map(r => r.coast)),
      r_flow_alt: corr(rows.map(r => r.f), rows.map(r => r.alt)),
      meanWetInGully: mean(gully, 'wet'), meanWetOutside: mean(dry, 'wet'),
      nGully: gully.length, nDry: dry.length,
    },
    runs: {'gt0.20': runs(0.20), 'gt0.40': runs(0.40), 'gt0.55': runs(0.55)},
    outlets: {inSalt: outlet.length, inBeach: outletBeach.length,
              pctOfGullyOnShore: gully.length ? +(100 * outlet.length / gully.length).toFixed(1) : 0},
    todaysForest: {
      gully: sampleD(gully), dry: sampleD(dry),
      stream: sampleD(rows.filter(r => r.f > 0.55)),
      outlet: sampleD(outletBeach),
    },
  };
}, step);

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
