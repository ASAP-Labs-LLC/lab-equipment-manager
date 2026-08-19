/* vfactor.mjs — which planting rule is actually emptying the island?
 *
 * Re-runs the tree scatter's own acceptance chain over a uniform sample of the
 * island, using vegetation.js's own `_site`, `_openness`, `_shore` and biome —
 * so the numbers are the rules as written and not a reimplementation of them.
 * Prints the mean of every multiplier and the survival after each stage, which
 * is the one form in which a rule that rejects nine candidates in ten is
 * visible at all. Three rounds of this file argued about density from a
 * screenshot; a screenshot cannot tell you which line did it.
 */
import {chromium} from 'playwright';
const arg = k => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : null; };
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?cam=wide&time=16&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(4000);
const o = await p.evaluate(N => {
  const W = window.__lemWorld, v = W.subsystems.get('vegetation'), t = W.subsystems.get('terrain');
  const isl = v.island;
  const ss = (a, c, x) => { const q = Math.max(0, Math.min(1, (x - a) / (c - a))); return q * q * (3 - 2 * q); };
  const acc = {}, add = (k, x) => { (acc[k] = acc[k] || {n: 0, s: 0}); acc[k].n++; acc[k].s += x; };
  const st = {sample: 0, offDisc: 0, site: 0, land: 0, kept: 0};
  const kinds = {};
  const rnd = () => Math.random();
  for (let i = 0; i < N; i++) {
    const a = Math.random() * 6.2832, r = Math.sqrt(Math.random()) * isl.r;
    const x = isl.cx + Math.cos(a) * r, z = isl.cz + Math.sin(a) * r;
    st.sample++;
    const site = v._site(x, z, 6.5);
    if (!site) { st.site++; continue; }
    st.land++;
    let k = '?'; try { k = t.biomeAt(x, z).kind; } catch {}
    kinds[k] = (kinds[k] || 0) + 1;
    const bm = t.biomeAt(x, z);
    const stand = W.Tex ? 0 : 0;
    const sh = v._shore(site);
    const fSlope = 1 - ss(0.45, 0.95, site.slope);
    const fAlt = v.relief > 25 ? 1 - ss(0.70, 0.94, site.alt) : 1;
    const fRock = 1 - site.rock * 0.85;
    const fShore = (1 - sh.beach) * (1 - sh.salt * 0.62);
    const fOpen = v._openness(x, z);
    add('slope', fSlope); add('treeline', fAlt); add('rock', fRock);
    add('shore', fShore); add('openness', fOpen);
    add('alt_unit', site.alt); add('alt_metres', bm.altitude);
    add('slope_raw', site.slope); add('wet', site.wet);
    const d = fSlope * fAlt * fRock * fShore * fOpen;
    add('product', d);
    if (d > 0.02) st.kept += d;
  }
  const out = {island: isl, relief: v.relief, hMin: v.hMin, hMax: v.hMax,
               waterY: v.waterY, altUnit: v._altUnit, stages: st, kinds, means: {}};
  for (const k in acc) out.means[k] = +(acc[k].s / acc[k].n).toFixed(4);
  out.landFrac = +(st.land / st.sample).toFixed(3);
  out.expectedPerHa = +(1e4 * (st.kept / st.sample) * (st.land / st.sample ? 1 : 1) /
                        (Math.PI * isl.r * isl.r / st.sample) * 0 + 0);
  return out;
}, parseInt(arg('n') || '6000', 10));
console.log(JSON.stringify(o, null, 1));
await b.close();
