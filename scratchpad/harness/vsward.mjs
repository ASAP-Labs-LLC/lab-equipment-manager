/* vsward.mjs — the sward's own gate chain. `vdens2.mjs` for the mat.
 *
 *   node vsward.mjs [--mods ''] [--at 124,328]
 *
 * The tree scatter has had a factor-by-factor table for four rounds and the mat
 * has never had one, which is why the one vegetation instance a blind art
 * director could name in the whole frame turned out to be a sward patch and not
 * a tree. For every factor in `_scatterSward`'s `cover` product this reports the
 * mean, the share of land where it is inert (> 0.95) and the share where it is a
 * veto (< 0.05) — a rule whose mean is 0.99 is not a rule — and then prints the
 * same row at named places, so an island-wide mean cannot hide one location.
 *
 * THE PRODUCT IS THE FILE'S OWN. The first version of this probe carried a
 * hand-typed copy of the eight factors; the file's salt weight then moved and
 * the probe went on printing the old table with complete confidence. It caught
 * itself only because it also predicts the placed count, and that came out 18%
 * off. `_matCover` was lifted out of `_scatterSward` for this, exactly as
 * `_shelter` was lifted for `vdens2`. The prediction check is KEPT anyway — a
 * probe that can no longer detect its own drift is a probe that will drift.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const mods = arg('mods', '');
const at = (arg('at', '124,328;563,-47;300,-200') || '').split(';')
  .filter(Boolean).map(s => s.split(',').map(Number));

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

const out = await p.evaluate(({at}) => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const fbm = w.ctx.Tex && w.ctx.Tex.fbm;
  const isl = veg.island;
  const wy = veg.waterY;
  const clamp = (v, a, c) => v < a ? a : v > c ? c : v;

  /* Every factor, named, as the file multiplies them. */
  const chain = (x, z) => {
    const site = veg._site(x, z, 15.5 * 0.42, 0.05, 0.9, veg.plantFloor);
    if (!site) return null;
    const open = veg._openness(x, z, true);
    if (open < 0.06) return {x, z, vetoOpen: true};
    const sh = veg._shore(site);
    const stand = veg._standAt ? veg._standAt(x, z) : 0.5;
    const patch = fbm ? fbm(x * 0.0055 + 21, z * 0.0055 - 6,
                            {octaves: 3, period: 8, seed: 53}) : 0.5;
    if (!veg._matCover) return {x, z, noMatCover: true};
    const f = veg._matCover(site, open, stand, patch);
    return {x, z, ...f,
            altM: site.h - wy, coast: site.coast, wet: site.wet, stand,
            beachLow: sh.beachLow ?? 0,
            /* and what the TREE scatter would say about the same ground, which
             * is the comparison the whole question turns on: the two tiers read
             * the same `_shore` and weight it differently. */
            treeShore: (1 - sh.beach) * (1 - sh.salt * 0.62)};
  };

  const keys = ['fOpen', 'fSlope', 'fWet', 'fStand', 'fRock', 'fShore', 'fWind', 'fGully', 'fPatch', 'cover', 'treeShore'];
  const st = {}; for (const k of keys) st[k] = {n: 0, s: 0, hi: 0, lo: 0, min: 9, max: -9};
  const rows = [];
  const STEP = 6;
  let land = 0, vetoOpen = 0;
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += STEP) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += STEP) {
      const dx = x - isl.cx, dz = z - isl.cz;
      if (dx * dx + dz * dz > isl.r * isl.r) continue;
      const c = chain(x, z);
      if (!c) continue;
      if (c.vetoOpen) { vetoOpen++; continue; }
      land++;
      rows.push(c);
      for (const k of keys) { const s = st[k], v = c[k];
        s.n++; s.s += v; if (v > 0.95) s.hi++; if (v < 0.05) s.lo++;
        if (v < s.min) s.min = v; if (v > s.max) s.max = v; }
    }
  }
  const table = keys.map(k => { const s = st[k];
    return {factor: k, mean: +(s.s / s.n).toFixed(3),
            pctAbove095: +(100 * s.hi / s.n).toFixed(1),
            pctBelow005: +(100 * s.lo / s.n).toFixed(1),
            min: +s.min.toFixed(2), max: +s.max.toFixed(2)}; });

  /* THE SALT BAND, which is where the two tiers disagree. Bin the same land
   * samples by `salt` and print both tiers' shore factors side by side. */
  const saltBins = [];
  for (let i = 0; i < 5; i++) {
    const lo = i * 0.2, hi = lo + 0.2;
    const band = rows.filter(r => r.salt >= lo && r.salt < hi + (i === 4 ? 0.01 : 0));
    if (!band.length) { saltBins.push({salt: [lo, hi], n: 0}); continue; }
    const m = k => +(band.reduce((s, r) => s + r[k], 0) / band.length).toFixed(3);
    saltBins.push({salt: [lo, hi], n: band.length, swardShore: m('fShore'),
                   swardWind: m('fWind'),
                   treeShore: m('treeShore'), swardCover: m('cover'),
                   altM: m('altM'), coast: m('coast')});
  }

  /* And by height above the tide, which is the unit terrain paints the sand in
   * and the unit `_shore` does not have. */
  const altBins = [];
  for (const [lo, hi] of [[0, 2], [2, 4], [4, 6], [6, 10], [10, 16], [16, 30], [30, 70]]) {
    const band = rows.filter(r => r.altM >= lo && r.altM < hi);
    if (!band.length) { altBins.push({altM: [lo, hi], n: 0}); continue; }
    const m = k => +(band.reduce((s, r) => s + r[k], 0) / band.length).toFixed(3);
    altBins.push({altM: [lo, hi], n: band.length, coast: m('coast'),
                  beach: m('beach'), beachLow: m('beachLow'), salt: m('salt'),
                  swardShore: m('fShore'), swardWind: m('fWind'),
                  treeShore: m('treeShore'), swardCover: m('cover')});
  }

  const named = at.map(q => {
    const c = chain(q[0], q[1]);
    if (!c) return {x: q[0], z: q[1], noSite: true};
    const o = {}; for (const k of Object.keys(c)) o[k] = typeof c[k] === 'number' ? +c[k].toFixed(3) : c[k];
    return o;
  });

  /* Does the hand-typed product agree with the file? Predicted acceptances over
   * the file's own lattice against what it actually placed. */
  const SWARD_CELL = 8.4;
  let predicted = 0, cells = 0;
  const R = Math.max(isl.r, veg.landR || 0) + 40;
  const nc = Math.ceil(R / SWARD_CELL);
  for (let j = -nc; j <= nc; j += 2) {
    for (let i = -nc; i <= nc; i += 2) {
      const x = isl.cx + i * SWARD_CELL, z = isl.cz + j * SWARD_CELL;
      if (Math.hypot(x - isl.cx, z - isl.cz) > R) continue;
      cells++;
      const c = chain(x, z);
      if (c && !c.vetoOpen) predicted += c.cover;
    }
  }

  return {landSamples: land, vetoOpen, table, saltBins, altBins, named,
          check: {predictedPlacedApprox: Math.round(predicted * 4),
                  actualPlaced: veg._swardStats ? veg._swardStats.placed : null,
                  swardStats: veg._swardStats},
          treeStats: veg._scatterStats};
}, {at});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
