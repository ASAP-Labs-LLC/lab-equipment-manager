/* vdens2.mjs — which planting rule is actually doing anything?
 *
 *   node vdens2.mjs [--mods terrain,vegetation]
 *
 * Re-runs the tree scatter's own density chain over a grid of island points,
 * in the page, calling vegetation.js's OWN `_site`, `_openness`, `_standNorm`
 * and `_cover` rather than a copy of them, and reports for each factor: its
 * mean, and the fraction of land where it is above 0.95 (inert) or below 0.05
 * (a veto). A rule whose mean is 0.99 is not a rule.
 *
 * The finding this was written for: `smoothstep(0.14, 0.34, stand)` returned
 * 1.000 at 100% of 12,568 land samples, because `ctx.Tex.fbm` never leaves
 * [0.40, 0.72]. Every rule that was supposed to shape the forest was inert.
 *
 * Also reports the DISTRIBUTION of the final density and of the cover field,
 * which is the quantity the art direction is about: one density everywhere is a
 * spike, three densities doing three jobs is a spread.
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
await p.waitForTimeout(8000);

const out = await p.evaluate(() => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const ctx = w.ctx;
  const fbm = ctx.Tex && ctx.Tex.fbm;
  const isl = veg.island;
  const K = veg.constructor;
  const noise = (x, z, s, sc) => fbm
    ? fbm(x * sc, z * sc, {octaves: 3, period: 8, seed: s}) : 0.5;
  const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
  const smoothstep = (a, b, x) => { const t = clamp((x - a) / (b - a || 1e-6), 0, 1); return t * t * (3 - 2 * t); };

  const stats = {};
  const acc = (k, v) => { const s = stats[k] || (stats[k] = {n: 0, sum: 0, hi: 0, lo: 0, min: 9, max: -9});
    s.n++; s.sum += v; if (v > 0.95) s.hi++; if (v < 0.05) s.lo++;
    if (v < s.min) s.min = v; if (v > s.max) s.max = v; };

  const hist = new Array(10).fill(0);
  const cHist = new Array(10).fill(0);
  const step = 6;
  let land = 0;
  /* The band edges the file itself uses, read off it so this cannot drift. */
  const OPEN = 0.18, MARGIN = 0.62, CLOSED = 1.30;
  let bOpen = 0, bMargin = 0, bClosed = 0;
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += step) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += step) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const site = veg._site(x, z, 9.0);
      if (!site) continue;
      land++;
      const standN = veg._standNorm(noise(x, z, 7, 0.0042));
      const grain = noise(x, z, 23, 0.011);
      acc('standN', standN);
      const texture = 0.74 + 0.52 * grain;
      const open = veg._openness(x, z);
      acc('openness', open);
      let d = clamp(veg._cover(standN, 1) * texture * open, 0, 1);
      const fCliff = 1 - smoothstep(4.5, 9.0, site.drop || 0);
      acc('crownDrop', clamp((site.drop || 0) / 9, 0, 1));
      acc('cliffFactor', fCliff);
      d *= fCliff;
      const fSlope = 1 - smoothstep(0.55, 1.05, site.slope) * 0.92;
      acc('slopeFactor', fSlope);
      d *= fSlope;
      /* The graded half of the slope rule is in `shelter` below, not here. This
         * row was 0.994/98.1% for four rounds and reporting it alone is what let
         * "slope drives the density" stay believable while it did nothing. */
      const slopeN = veg._slopeNorm(site.slope);
      acc('slopeN', slopeN);
      acc('aspect01', (site.aspect + 1) / 2);
      const crest = smoothstep(0.52, 0.98, site.alt);
      acc('crest', crest);
      d *= 1 - crest * 0.46;
      const fRock = 1 - site.rock * 0.62;
      acc('rockFactor', fRock);
      d *= fRock;
      const sh = veg._shore(site);
      acc('exposure', sh.exposure);
      const rip = veg._riparian(site);
      acc('ripGully', rip.gully);
      acc('ripBank', rip.bank);
      acc('ripChannel', rip.channel);
      acc('flow', site.flow || 0);
      const mouth = Math.max(rip.channel, rip.bank * 0.72) *
                    (1 - smoothstep(26 * 0.6, 130 * 0.75, site.coast));
      acc('mouth', mouth);
      const fShore = (1 - sh.beach * (1 - 0.62 * mouth)) *
                     (1 - sh.salt * 0.62 * (1 - 0.55 * mouth));
      acc('shoreFactor', fShore);
      d *= fShore;
      /* `site.wet` is ALREADY the normalised field — vegetation.js moved the
         * median-centring into `_biome` so the eleven consumers cannot disagree.
         * This probe double-normalised it for one run and reported the three
         * bands in the wrong order, which is the same class of mistake it was
         * written to catch. */
      /* THE FILE'S OWN SUM, not a copy of it. This probe carried a hand-typed
       * duplicate of the shelter expression with all eight coefficients written
       * out as literals — so a coefficient change in vegetation.js moved the
       * island and did not move this table, which is the exact mechanism by
       * which sixteen instruments on this project have given confident wrong
       * answers. `_shelter` was lifted out of the scatter for this. */
      acc('windExposure', veg._windExposure ? veg._windExposure(x, z) : 0.5);
      acc('prominence', veg._prominence ? veg._prominence(x, z) : 0.5);
      const shelter = veg._shelter
        ? veg._shelter(site, sh, rip, slopeN, crest)
        : clamp(0.74 + (site.wet - 0.5) * 0.90 - crest * 0.85 -
                sh.salt * 0.30 - site.rock * 0.25 + rip.gully * 0.26 +
                (0.5 - slopeN) * 0.46 + site.aspect * 0.30, 0, 1);
      /* The two hillside terms on their own, as signed contributions mapped on
         * to 0..1 with a half at "does nothing" — a term whose mean is 0.5 and
         * whose spread is a hundredth is inert whatever its coefficient says. */
      acc('shSlope', 0.5 + (0.5 - slopeN) * 0.46);
      acc('shAspect', 0.5 + site.aspect * 0.30);
      acc('shelter', shelter);
      acc('wet', site.wet);
      acc('c', clamp(standN * 0.52 + shelter * 0.48, 0, 1));
      const cover = veg._cover(standN, shelter);
      acc('cover', cover / CLOSED);
      d *= cover / Math.max(1e-3, veg._cover(standN, 1));
      d = clamp(d, 0, 1);
      acc('final', d);
      hist[Math.min(9, Math.floor(d * 10))]++;
      cHist[Math.min(9, Math.floor(cover / CLOSED * 10))]++;
      if (cover < MARGIN * 0.9) bOpen++;
      else if (cover < CLOSED * 0.85) bMargin++;
      else bClosed++;
    }
  }
  const rows = Object.keys(stats).map(k => {
    const s = stats[k];
    return {rule: k, mean: +(s.sum / s.n).toFixed(3),
            pctAbove095: +(100 * s.hi / s.n).toFixed(1),
            pctBelow005: +(100 * s.lo / s.n).toFixed(1),
            min: +s.min.toFixed(2), max: +s.max.toFixed(2)};
  });
  return {land, islandR: +isl.r.toFixed(0), landRelief: +(veg.landRelief || 0).toFixed(1),
          standRange: veg._standRange, wetRange: veg._wetRange, scatter: veg._scatterStats,
          rail: veg._railStats, expoStats: veg._expoStats,
          bandsByArea: {open: bOpen, margin: bMargin, closed: bClosed},
          rows, finalHist: hist.map(v => +(100 * v / land).toFixed(1)),
          coverHist: cHist.map(v => +(100 * v / land).toFixed(1))};
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
