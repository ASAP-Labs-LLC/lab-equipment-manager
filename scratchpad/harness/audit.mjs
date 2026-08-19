/* audit.mjs — close the soak's junction blind spot and hunt for fouling.
 *
 * Differences from soak.mjs:
 *   - samples world position from r.P/r.C directly, so it works on routes that
 *     reverseRoute() produced (those carry NO getPointAt / totalLength).
 *   - checks the WHOLE consist, not just the head, at 4m intervals.
 *   - includes idle and shunt consists — a parked train is still metal.
 *   - records who held which block at the moment of the foul.
 *   - relayouts while trains are running, on purpose.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}
const PARSES = parseInt(args.parses || '200', 10);
const LAYOUTS = parseInt(args.layouts || '6', 10);
const HOT = !!args.hot;   // relayout while trains are out

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'],
  ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'],
  ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'],
  ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
function layouts(n) {
  const BAY = 2.05;
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

const SAMPLER = () => {
  const w = window.__lemWorld;
  const faults = [];
  const seen = new Set();
  const note = (kind, detail, extra) => {
    const key = kind + '|' + detail;
    if (seen.has(key)) return;
    seen.add(key);
    faults.push({kind, detail, extra, at: Math.round(performance.now())});
  };
  window.__auditStats = {frames: 0, pairs: 0, unsampleable: 0, missingMethods: 0,
                         minSep: Infinity, minSepWho: null, waitFrames: 0,
                         maxWaitRun: 0, stallFrames: 0, holdsSnapshot: null,
                         idleOverlap: 0, shuntPairs: 0};

  /* Arc length -> world point, straight off the arrays. Independent of whatever
   * methods the route object happens to carry, which is the bug that switched
   * the harness's cross-line check off. */
  const ptAt = (r, s, o) => {
    const C = r.C, n = r.n;
    if (!C || !n) return null;
    let t;
    if (r.closed) { const L = C[n - 1] || 1; t = s - Math.floor(s / L) * L; }
    else t = Math.min(C[n - 1], Math.max(0, s));
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (C[m] <= t) lo = m; else hi = m; }
    const seg = C[hi] - C[lo] || 1, k = (t - C[lo]) / seg;
    const a = lo * 3, b = hi * 3, P = r.P;
    o.x = P[a] + (P[b] - P[a]) * k;
    o.y = P[a + 1] + (P[b + 1] - P[a + 1]) * k;
    o.z = P[a + 2] + (P[b + 2] - P[a + 2]) * k;
    return o;
  };

  const body = (c) => {                 // sample points down the whole consist
    const r = c.route;
    if (!r || !r.P) return null;
    const pts = [];
    const L = c.length || 24;
    for (let d = 0; d <= L; d += 4) {
      const o = {x: 0, y: 0, z: 0};
      if (!ptAt(r, c.s - d, o)) return null;
      pts.push(o);
    }
    return pts;
  };

  const wait = new Map();
  const tick = () => {
    const st = window.__auditStats;
    st.frames++;
    const T = w.subsystems && w.subsystems.get('trains');
    if (!T || !Array.isArray(T.consists)) { requestAnimationFrame(tick); return; }
    const live = T.consists.filter(c => c && c.group && c.group.visible && c.route);
    /* Does the route still expose what soak.mjs probes for? */
    for (const c of live) {
      if (typeof c.route.getPointAt !== 'function' || !c.route.totalLength) {
        st.missingMethods++;
      }
    }
    const bodies = new Map();
    for (const c of live) {
      const b = body(c);
      if (!b) { st.unsampleable++; continue; }
      bodies.set(c, b);
    }
    for (let i = 0; i < live.length; i++) {
      for (let j = i + 1; j < live.length; j++) {
        const a = live[i], b = live[j];
        const ba = bodies.get(a), bb = bodies.get(b);
        if (!ba || !bb) continue;
        st.pairs++;
        let d2 = Infinity, best = null;
        for (const pa of ba) for (const pb of bb) {
          const dx = pa.x - pb.x, dy = pa.y - pb.y, dz = pa.z - pb.z;
          const q = dx * dx + dy * dy + dz * dz;
          if (q < d2) { d2 = q; best = [pa, pb]; }
        }
        const d = Math.sqrt(d2);
        const bothIdle = a.state === 'idle' && b.state === 'idle';
        const anyShunt = a.shunt || b.shunt;
        if (d < st.minSep) {
          st.minSep = d;
          st.minSepWho = `${a.slot}(${a.state},${a.line})/${b.slot}(${b.state},${b.line}) d=${d.toFixed(2)}`;
        }
        /* 4.2m centre-to-centre is closer than two parallel roads ever are here
         * (rail.js runs parallel roads at 8.4m) — under that, the two rakes are
         * on the same stone. */
        if (d < 4.2) {
          if (bothIdle) st.idleOverlap++;
          if (anyShunt) st.shuntPairs++;
          const holds = c => c.holds ? [...c.holds].join(',') : '-';
          note('foul',
            `slots ${a.slot}/${b.slot} ${d.toFixed(2)}m apart — ` +
            `${a.state}@${a.s.toFixed(1)} line=${a.line} holds=[${holds(a)}] cIn=${a.cIn} vs ` +
            `${b.state}@${b.s.toFixed(1)} line=${b.line} holds=[${holds(b)}] cIn=${b.cIn}`,
            {x: best[0].x.toFixed(1), z: best[0].z.toFixed(1),
             aShunt: !!a.shunt, bShunt: !!b.shunt, bothIdle});
        }
      }
    }
    /* Deadlock / starvation: a working that has been waiting on the junction
     * for a long time, or a backlog that never drains. */
    for (const c of T.consists) {
      if (c.shunt) continue;
      if (c.waiting) {
        st.waitFrames++;
        const n = (wait.get(c.slot) || 0) + 1;
        wait.set(c.slot, n);
        if (n > st.maxWaitRun) st.maxWaitRun = n;
        if (n === 900) note('stall', `slot ${c.slot} waiting on 'common' for ~15s at s=${c.s.toFixed(1)}`);
      } else wait.set(c.slot, 0);
    }
    /* Snapshot the block table so a foul can be read against it. */
    if (T.blocks) {
      const m = [];
      for (const [k, v] of T.blocks) m.push(k + '->' + v.slot);
      st.holdsSnapshot = m.join(' ');
    }
    window.__auditFaults = faults;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0`;
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);
await page.evaluate(SAMPLER);

const SET = layouts(LAYOUTS);
const PER = Math.max(1, Math.round(PARSES / LAYOUTS));
for (let L = 0; L < SET.length; L++) {
  const pos = SET[L];
  await page.evaluate(([fleet, p]) => {
    const list = fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: p[i], reason: 'audit',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }));
    window.__lemWorld.setMachines(list);
  }, [FLEET, pos]);
  if (!HOT) await page.waitForTimeout(2500);
  else await page.waitForTimeout(400);   // relayout lands on top of running trains
  for (let p = 0; p < PER; p++) {
    await page.evaluate(uid => window.__lemWorld.parse(uid, 'L-AUDIT'),
                        FLEET[p % FLEET.length][0]);
    await page.waitForTimeout(120);
  }
  await page.waitForTimeout(HOT ? 800 : 4000);
  const f = await page.evaluate(() => window.__auditFaults || []);
  const s = await page.evaluate(() => window.__auditStats || {});
  process.stdout.write(`layout ${L}: ${f.length} faults, minSep=${(s.minSep||0).toFixed?.(2)} ` +
    `pairs=${s.pairs} unsampleable=${s.unsampleable} missingMethods=${s.missingMethods}\n`);
}
const faults = await page.evaluate(() => window.__auditFaults || []);
const stats = await page.evaluate(() => window.__auditStats || {});
await browser.close();
console.log('\n=== AUDIT ===');
console.log(JSON.stringify(stats, null, 1));
for (const f of faults.slice(0, 25)) console.log(`  ${f.kind}: ${f.detail}\n      ${JSON.stringify(f.extra)}`);
console.log('faults:', faults.length, 'pageerrors:', errs.length);
if (args.json) fs.writeFileSync(args.json, JSON.stringify({faults, stats, errs}, null, 2));
