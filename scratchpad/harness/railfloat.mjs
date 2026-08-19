/* railfloat.mjs — how far above the ground does the railway actually sit?
 *
 *   node railfloat.mjs [--layouts 1] [--samples 40] [--json out.json]
 *
 * Ryan: "the amount that the train rails float above the terrain is insane."
 * This measures it the only way that settles it: for every station circuit,
 * sample the route at N points and subtract terrain.heightAt directly beneath
 * the sampled rail point. Reports railhead-above-ground, and separately the
 * ballast toe (railhead minus the section depth), which is the number that
 * says whether there is a gap you could see daylight through.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[a.slice(2)] = true; else { args[a.slice(2)] = n; i++; }
}
const SAMPLES = parseInt(args.samples || '40', 10);
const LAYOUTS = parseInt(args.layouts || '1', 10);

const BAY = 2.05;
const FLEET = ['multitek-ns', 'multitek-s', 'optimpp-1', 'optimpp-2',
               'pac-flash-1', 'pac-flash-2', 'koehler-cp'];
function layouts(n) {
  const out = [[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]];
  const all = [out];
  let seed = 12345;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let L = 1; L < n; L++) {
    const kind = L % 4;
    const pos = [];
    for (let i = 0; i < FLEET.length; i++) {
      if (kind === 0) pos.push([Math.round(rnd() * 8) * BAY, Math.round(rnd() * 8) * BAY]);
      else if (kind === 1) pos.push([i * BAY, 0]);
      else if (kind === 2) pos.push([0, i * BAY]);
      else pos.push([Math.round(rnd() * 14) * BAY, Math.round(rnd() * 14) * BAY]);
    }
    if (kind === 3) pos[1] = pos[0].slice();
    all.push(pos);
  }
  return all;
}

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 900, height: 600}});
const errors = [];
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 300)); });
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 300)));

const url = args.url ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=yard&time=16&quality=ultra';
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await page.waitForTimeout(800);

const PROBE = (SAMPLES) => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const terr = w.subsystems.get('terrain');
  const H = (x, z) => terr.heightAt(x, z);
  const rows = [];
  const perRoute = [];
  for (const s of w.plan.stations) {
    let c = null;
    try { c = rail.cycle(s.uid); } catch (e) { continue; }
    if (!c || !c.route) continue;
    const L = c.route.length;
    const mine = [];
    for (let i = 0; i < SAMPLES; i++) {
      const sd = L * (i + 0.5) / SAMPLES;
      const p = c.route.pointAtDistance(sd);
      if (!p) continue;
      const g = H(p.x, p.z);
      mine.push(+(p.y - g).toFixed(3));
      rows.push({uid: s.uid, s: +sd.toFixed(1), x: +p.x.toFixed(1), z: +p.z.toFixed(1),
                 y: +p.y.toFixed(3), g: +g.toFixed(3), d: +(p.y - g).toFixed(3)});
    }
    mine.sort((a, b) => a - b);
    perRoute.push({uid: s.uid, n: mine.length, min: mine[0],
                   med: mine[mine.length >> 1], max: mine[mine.length - 1]});
  }
  /* Also sample the laid geometry itself, every track, not just the routes a
   * train happens to run — a yard road nobody routes over is still in shot. */
  const trackRows = [];
  for (const t of (rail.tracks || [])) {
    const f = t.frames; if (!f) continue;
    const step = Math.max(1, Math.floor(f.count / 60));
    for (let i = 0; i < f.count; i += step) {
      const x = f.pos[i * 3], y = f.pos[i * 3 + 1], z = f.pos[i * 3 + 2];
      trackRows.push({track: t.name, x: +x.toFixed(1), z: +z.toFixed(1),
                      d: +(y - H(x, z)).toFixed(3)});
    }
  }
  const stat = arr => {
    if (!arr.length) return null;
    const a = arr.slice().sort((p, q) => p - q);
    const q = f => a[Math.min(a.length - 1, Math.floor(a.length * f))];
    return {n: a.length, min: +a[0].toFixed(3), p10: +q(0.1).toFixed(3),
            med: +q(0.5).toFixed(3), p90: +q(0.9).toFixed(3),
            max: +a[a.length - 1].toFixed(3),
            over2: a.filter(v => v > 2).length, over1: a.filter(v => v > 1).length,
            below0: a.filter(v => v < 0).length};
  };
  /* What the profile itself is like to ride: gradient, and vertical curvature
   * (1/R). A railway vertical curve is 2000m radius or flatter — 5e-4. */
  const prof = {};
  for (const t of (rail.tracks || [])) {
    const f = t.frames; if (!f || f.count < 5) continue;
    const st = f.step, gr = [], cv = [];
    for (let i = 1; i < f.count - 1; i++) {
      const a = f.pos[(i - 1) * 3 + 1], b = f.pos[i * 3 + 1], c = f.pos[(i + 1) * 3 + 1];
      gr.push(Math.abs(c - a) / (2 * st));
      cv.push(Math.abs(c - 2 * b + a) / (st * st));
    }
    prof[t.name] = {ruling: +(t.ruling ?? -1).toFixed(4),
                    meanFill: +(t.meanFill ?? -1).toFixed(3),
                    bankFrac: +(t.bankFraction ?? -1).toFixed(3),
                    grad: stat(gr), curv: stat(cv)};
  }
  /* Does the ground come up through the stone? The profile is floored on the
   * ground under the SLEEPER only (±1.3m); outside that the ballast batter is a
   * drape that rises to meet the ground but stops at the crib. Anywhere the
   * ground at the shoulder is above that stop, terrain cuts across the ribbon.
   * Heights are quoted from the railhead: crib is -0.297, toe -0.627. */
  const shoulder = {breach: 0, n: 0, worst: 0, crown: 0};
  for (const t of (rail.tracks || [])) {
    const f = t.frames; if (!f || !t.verge) continue;
    for (let i = 0; i < f.count; i += 3) {
      const px = f.pos[i * 3], py = f.pos[i * 3 + 1], pz = f.pos[i * 3 + 2];
      const rx = f.right[i * 3], rz = f.right[i * 3 + 2];
      for (const lat of [-2.4, -1.9, 1.9, 2.4]) {
        const g = H(px + rx * lat, pz + rz * lat);
        shoulder.n++;
        const over = g - (py - 0.317);            // above the drape's ceiling
        if (over > 0) { shoulder.breach++; shoulder.worst = Math.max(shoulder.worst, over); }
      }
      for (const lat of [-1.2, 0, 1.2]) {         // under the sleeper: must be clear
        const g = H(px + rx * lat, pz + rz * lat);
        if (g > py - 0.627) shoulder.crown++;
      }
    }
  }
  shoulder.breachPct = +(100 * shoulder.breach / Math.max(1, shoulder.n)).toFixed(2);
  shoulder.worst = +shoulder.worst.toFixed(3);
  const ds = rows.map(r => r.d);
  const ts = trackRows.map(r => r.d);
  const worst = rows.slice().sort((a, b) => b.d - a.d).slice(0, 12);
  return {routes: stat(ds), tracks: stat(ts), perRoute, worst, prof, shoulder,
          trackWorst: trackRows.slice().sort((a, b) => b.d - a.d).slice(0, 12),
          byTrack: (() => {
            const m = {};
            for (const r of trackRows) (m[r.track] ||= []).push(r.d);
            const o = {};
            for (const k in m) o[k] = stat(m[k]);
            return o;
          })()};
};

const all = [];
const L = layouts(LAYOUTS);
for (let i = 0; i < LAYOUTS; i++) {
  if (i > 0) {
    await page.evaluate(([fleet, pos]) => {
      const list = fleet.map((uid, k) => ({
        machine_uid: uid, title: uid, status: 'GREEN', pos: pos[k],
        reason: 'railfloat',
        sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
        module_running: true, module_state: 'running',
        effective_specs: [], qc_targets: [], maintenance: [],
      }));
      window.__lemWorld.setMachines(list);
    }, [FLEET, L[i]]).catch(e => console.log('relayout failed', String(e)));
    await page.waitForTimeout(2600);
  }
  const r = await page.evaluate(PROBE, SAMPLES);
  all.push({layout: i, ...r});
  console.log(`layout ${i}  routes ${JSON.stringify(r.routes)}`);
  console.log(`          tracks ${JSON.stringify(r.tracks)}`);
  console.log(`          shoulder ${JSON.stringify(r.shoulder)}`);
}
if (args.json) fs.writeFileSync(args.json, JSON.stringify({all, errors}, null, 1));
console.log('worst route samples:', JSON.stringify(all[0].worst));
console.log('per track (layout0):', JSON.stringify(all[0].byTrack, null, 1));
for (const k in all[0].prof) { const p = all[0].prof[k]; console.log('PROF', k.padEnd(15), 'ruling', p.ruling, 'meanFill', p.meanFill, 'bankFrac', p.bankFrac, 'grad med/p90/max', p.grad.med, p.grad.p90, p.grad.max, 'curv med/p90/max', p.curv.med, p.curv.p90, p.curv.max); }
if (errors.length) console.log('CONSOLE ERRORS', errors.slice(0, 5));
await browser.close();
