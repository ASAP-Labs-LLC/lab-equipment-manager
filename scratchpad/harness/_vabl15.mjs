/* _vabl15.mjs — the round-15 planting rules, ablated in one session.
 *
 *   node _vabl15.mjs --cam far --out /tmp/x
 *
 * Two frames from ONE page load and ONE instant, differing only in whether the
 * exposure field and the drainage bands are answering. Written because the
 * scene totals have moved by 850,000 triangles inside a single round of this
 * file for other people's reasons (rail, terrain and gi are all live), so a
 * frame from disk taken forty minutes ago is not a control for anything.
 *
 * The ablation is a stub on the two readers rather than a flag in the shipped
 * file: `_exposure` returns the neutral half it returns when there is no field,
 * and `_riparian` returns the three zeros it returned for every round before
 * terrain's drainage retune. Both are exactly the values the code takes when
 * the inputs are absent, so the "before" frame is the file as it behaved when
 * `flow` was a hard zero and the coast was one scalar. Then the stubs come off
 * and it re-scatters, in the same page, against the same terrain.
 */
import {chromium} from 'playwright';
import fs from 'fs';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const cam = arg('cam', 'far');
const out = arg('out', '/tmp/vabl15');
const time = arg('time', '9');

const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}` +
  `&time=${time}&quality=ultra&hud=0`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto(URL, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(14000);

/* The fringe measurement from `vfringe.mjs`, re-taken IN THIS PAGE. The dev
 * server relayouts the world between runs — the island went from r 597 to
 * r 646 between two consecutive probe runs of this round — so a coefficient of
 * variation from one process is not a control for one from another. */
const grab = async (tag) => {
  await p.waitForTimeout(2500);
  await p.screenshot({path: `${out}-${tag}.png`});
  return p.evaluate(() => {
    const w = window.__lemWorld;
    const veg = w.subsystems.get('vegetation');
    const isl = veg.island, VARIANTS = 3;
    const stems = [];
    for (let e = 0; e < veg.trees.length; e++) {
      const t = veg.trees[e];
      const si = Math.floor(e / VARIANTS), vi = e % VARIANTS;
      const n = t.count != null ? t.count : t.xs.length;
      for (let i = 0; i < n; i++) {
        const x = t.xs[i], z = t.zs[i];
        if (!Number.isFinite(x)) continue;
        const m = t.mats, o = i * 16;
        stems.push({x, z, si, vi,
                    sy: Math.hypot(m[o + 4], m[o + 5], m[o + 6]),
                    coast: veg._coastDist(x, z),
                    ang: Math.atan2(z - isl.cz, x - isl.cx)});
      }
    }
    const SECT = 16, bands = [[0, 40], [40, 90], [90, 150], [150, 260]];
    const areaG = new Float64Array(SECT * bands.length), STEP = 8;
    for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += STEP) {
      for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += STEP) {
        const dx = x - isl.cx, dz = z - isl.cz;
        if (dx * dx + dz * dz > isl.r * isl.r) continue;
        if (veg._ground(x, z) <= veg.waterY) continue;
        const c = veg._coastDist(x, z);
        const bi = bands.findIndex(q => c >= q[0] && c < q[1]);
        if (bi < 0) continue;
        areaG[bi * SECT + (Math.floor((Math.atan2(dz, dx) + Math.PI) / (2 * Math.PI) * SECT) % SECT)] += STEP * STEP;
      }
    }
    const cntG = new Float64Array(SECT * bands.length);
    for (const s of stems) {
      const bi = bands.findIndex(q => s.coast >= q[0] && s.coast < q[1]);
      if (bi < 0) continue;
      cntG[bi * SECT + (Math.floor((s.ang + Math.PI) / (2 * Math.PI) * SECT) % SECT)]++;
    }
    const width = bands.map((q, bi) => {
      const per = [];
      for (let s = 0; s < SECT; s++) {
        const a = areaG[bi * SECT + s];
        if (a < 2000) continue;
        per.push(cntG[bi * SECT + s] / (a / 10000));
      }
      const mn = per.reduce((a, v) => a + v, 0) / (per.length || 1);
      const sd = Math.sqrt(per.reduce((a, v) => a + (v - mn) * (v - mn), 0) / (per.length || 1));
      return {band: q[0] + '-' + q[1], perHa: +mn.toFixed(1), cv: +(sd / (mn || 1e-9)).toFixed(3),
              min: +Math.min(...per).toFixed(0), max: +Math.max(...per).toFixed(0)};
    });
    const salt = stems.filter(s => s.coast < 90);
    const sz = (a) => { if (!a.length) return {}; const so = a.slice().sort((u, v) => u - v);
      const mn = a.reduce((x, v) => x + v, 0) / a.length;
      return {n: a.length, mean: +mn.toFixed(3),
              sd: +Math.sqrt(a.reduce((x, v) => x + (v - mn) ** 2, 0) / a.length).toFixed(3),
              p50: +so[Math.floor(a.length * 0.5)].toFixed(2),
              p90: +so[Math.floor(a.length * 0.9)].toFixed(2)}; };
    const vmix = (set) => { const c = [0, 0, 0]; for (const s of set) c[s.vi]++;
      return c.map(v => +(100 * v / (set.length || 1)).toFixed(1)); };
    const even = (set) => { const c = new Map();
      for (const s of set) { const k = s.si * 8 + s.vi; c.set(k, (c.get(k) || 0) + 1); }
      let H = 0; const n = set.length || 1;
      for (const v of c.values()) { const q = v / n; H -= q * Math.log(q); }
      return +(H / Math.log(Math.max(2, c.size))).toFixed(3); };
    return {scatter: veg._scatterStats,
            tris: w.ctx.renderer?.info?.render?.triangles,
            stems: stems.length, width,
            saltSize: sz(salt.map(s => s.sy)),
            inlandSize: sz(stems.filter(s => s.coast >= 200).map(s => s.sy)),
            saltVariantPct: vmix(salt), saltEvenness: even(salt)};
  });
};

/* OFF first, so the "after" is the state the page is left in. */
const off = await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  veg.__expo = veg._exposure; veg.__rip = veg._riparian;
  veg._exposure = () => 0.5;
  veg._riparian = () => ({gully: 0, bank: 0, channel: 0});
  veg._regrow();
  return true;
});
const before = await grab('before');

await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  veg._exposure = veg.__expo; veg._riparian = veg.__rip;
  veg._regrow();
});
const after = await grab('after');

console.log(JSON.stringify({cam, off, before, after, errs: errs.slice(0, 4)}, null, 1));
await b.close();
