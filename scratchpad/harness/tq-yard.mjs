/* tq-yard.mjs — WHAT DOES THE PLATEAU'S INTERIOR ACTUALLY HAVE TO WORK WITH?
 *
 * The round-32 charge: "the plateau itself is still one low-frequency wash …
 * no gravel, no compaction difference between trafficked and untrafficked
 * ground, no wheel-rut darkening, no puddling in the low spots, no colour shift
 * where the fill was placed versus where the native ground was left."
 *
 * Five candidate drivers exist in the source already. This prints their
 * DISTRIBUTION over the domain a rule would be written against, so no threshold
 * in this round joins the six recorded inert rules. Domain is the bench terrace
 * as `_benchMask` defines it, split into the hardstanding (which is deliberately
 * not to be repainted) and the OPEN ground the charge is about.
 *
 *   node tq-yard.mjs
 */
import {chromium} from 'playwright';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 800, height: 450}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=far&time=9&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);

const out = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const mesh = t.meshes.find(m => m.name === 'terrain-core');
  const g = mesh.geometry;
  const pos = g.getAttribute('position');
  const nor = g.getAttribute('normal');
  const A = g.getAttribute('splatA'), B = g.getAttribute('splatB');
  const X = g.getAttribute('aux'), W = g.getAttribute('aWork');
  const q = new Float32Array(4);

  const smoothstep = (a, b, x) => {
    const u = Math.max(0, Math.min(1, (x - a) / (b - a)));
    return u * u * (3 - 2 * u);
  };

  const cols = {};
  const push = (bin, key, v) => {
    const c = cols[bin] || (cols[bin] = {});
    (c[key] || (c[key] = [])).push(v);
  };

  let nAll = 0;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    if (y <= t.waterY) continue;
    const bm = t._benchMask(x, z);
    if (bm < 0.9) continue;
    nAll++;
    t._distances(x, z, q);
    const dFoot = q[0], dPad = q[1], dBal = q[2], dRoad = q[3];
    const gravel = Math.max(smoothstep(1.8, -1.5, dBal), smoothstep(2, -3, dRoad) * 0.35) * 0.95;
    const asphalt = smoothstep(5, -5, dPad) * 0.95 * (1 - smoothstep(4, -5, dBal));
    const hard = Math.max(gravel, asphalt);
    const nat = t._smoothBase(x, z);
    const moved = y - nat;
    const ny = nor.getY(i);
    const deg = Math.acos(Math.min(1, ny)) * 180 / Math.PI;
    const flow = t._flowAt(x, z);
    const moist = t._moistAt(x, z);

    const bin = hard > 0.45 ? 'siteHard' : (deg < 4 ? 'siteOpenFlat' : 'siteOpenSloped');
    for (const [k, v] of [
      ['moved', moved], ['deg', deg], ['dRoad', dRoad], ['dBal', dBal],
      ['dPad', dPad], ['dFoot', dFoot], ['flow', flow], ['moist', moist],
      ['hollowAuxX', X.getX(i)], ['siteAuxZ', X.getZ(i)],
      ['workCut', W.getX(i)], ['workFill', W.getY(i)], ['workTraffic', W.getZ(i)],
      ['wGrass', A.getX(i)], ['wForest', A.getY(i)], ['wDirt', A.getZ(i)],
      ['wStone', A.getW(i)], ['wAsph', B.getX(i)], ['wMud', B.getY(i)],
      ['wStraw', B.getZ(i)], ['rockRatio', B.getW(i)],
      ['hard', hard],
      /* the candidate NEW drivers, computed here before they are written into
       * the file, so every threshold is chosen off the measured spread */
      ['dTraffic', Math.min(dRoad, dBal, dPad)],
      ['absMoved', Math.abs(moved)],
      ['isFill', moved > 0 ? 1 : 0],
      /* CANDIDATE MASKS — the numbers that decide whether a rule is inert */
      ['mPadWork', (1 - smoothstep(0.010, 0.038, 1 - ny))
                   * smoothstep(1.2, 5.0, Math.abs(moved)) * (1 - hard)],
      ['mTraffic', (1 - smoothstep(2.3, 21.0, Math.min(dRoad, dBal, dPad))) * (1 - hard)],
      ['mYardWet', smoothstep(0.05, 0.25, flow) * (1 - hard)],
    ]) push(bin, k, v);
  }

  const pct = (v, q) => v.length ? v[Math.min(v.length - 1, Math.floor(v.length * q))] : NaN;
  const r3 = x => Number.isFinite(x) ? +(+x).toFixed(3) : null;
  const res = {};
  for (const bin of Object.keys(cols)) {
    const o = {n: cols[bin].moved.length};
    for (const k of Object.keys(cols[bin])) {
      const v = cols[bin][k].slice().sort((a, b) => a - b);
      o[k] = {min: r3(v[0]), p05: r3(pct(v, 0.05)), p25: r3(pct(v, 0.25)),
              p50: r3(pct(v, 0.50)), p75: r3(pct(v, 0.75)),
              p90: r3(pct(v, 0.90)), p95: r3(pct(v, 0.95)),
              max: r3(v[v.length - 1]),
              mean: r3(v.reduce((s, x) => s + x, 0) / v.length),
              /* THE anti-inert check: a mask that claims almost none or almost
               * all of its own domain is the seventh recorded inert rule. */
              cov: {'>0.1': r3(v.filter(x => x > 0.1).length / v.length),
                    '>0.5': r3(v.filter(x => x > 0.5).length / v.length),
                    '>0.9': r3(v.filter(x => x > 0.9).length / v.length)}};
    }
    res[bin] = o;
  }
  /* the share question the charge is really about */
  const open = (cols.siteOpenFlat && cols.siteOpenFlat.moved.length) || 0;
  const sloped = (cols.siteOpenSloped && cols.siteOpenSloped.moved.length) || 0;
  const hardN = (cols.siteHard && cols.siteHard.moved.length) || 0;
  return {
    terraceVerts: nAll,
    share: {siteHard: +(hardN / nAll).toFixed(3),
            siteOpenFlat: +(open / nAll).toFixed(3),
            siteOpenSloped: +(sloped / nAll).toFixed(3)},
    bins: res,
    erosStats: t.erosStats ? t.erosStats : null,
  };
});

console.log(JSON.stringify(out, null, 1));
await b.close();
