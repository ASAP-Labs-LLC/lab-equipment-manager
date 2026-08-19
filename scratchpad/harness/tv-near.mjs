/* tv-near.mjs — catch the soak's collision in the act, with enough state to say
 * WHY, and to say whether the metal was really there.
 *
 * soak.mjs reports two slots and a distance. That is enough to fail a build and
 * not enough to fix one. This watches the same pairs at 50 ms and records, the
 * moment any pair comes inside `--warn`:
 *
 *   soakM — the distance soak.mjs itself would compute, using ITS `at()`:
 *           `getPointAt(clamp(s/len))`, three points per body.
 *   trueM — the distance the metal is actually at, using the route's own
 *           wrapping (`s mod len`), every four metres of both bodies.
 *
 * The two differ for exactly one reason and it is worth knowing about: a working
 * coming home on a CLOSED circuit runs past `len` — `homeS` is one lap plus the
 * road — and soak's clamp maps every one of those arc lengths on to the last
 * point of the array. Two workings on two roads then both appear parked on their
 * own closure points whatever they are really doing.
 *
 * Also records each consist's held blocks and tokens, so "they were never in the
 * same block" and "they were and the ledger let them" can be told apart.
 *
 *   node tv-near.mjs [--layout 0] [--secs 200] [--warn 8] [--rate 350]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const SECS = parseInt(arg('secs', '200'), 10);
const WARN = parseFloat(arg('warn', '8'));
const RATE = parseInt(arg('rate', '350'), 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
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
const POS = layouts(LAYOUT + 1)[LAYOUT];

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);
await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
  fleet.map(([uid, title, status], i) => ({
    machine_uid: uid, title, status, pos: pos[i], reason: 'tvnear',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

/* A/B: strip the passing loops back out at runtime, so the same page, the same
 * layout and the same parse rate can be run with and without them. `exits` is
 * the only thing the consumption hangs off — with it null, `_exitFor` returns
 * null, `_cycOf` returns null, and no working ever leaves by a crossover. */
if (process.argv.includes('--noloops')) {
  console.log('# loops disabled');
  await p.evaluate(() => {
    const T = window.__lemWorld.subsystems.get('trains');
    for (const [, cyc] of T.cycles) if (cyc) cyc.exits = null;
    for (const c of T.consists) c.exits = null;
  });
}
/* And the traffic cap on its own, so "the crossover is unsafe" and "this
 * railway's junction throat is unsafe above N workings" can be told apart. */
const CAP = parseInt(arg('cap', '0'), 10);
if (CAP) {
  console.log('# maxActive pinned to ' + CAP);
  await p.evaluate(n => {
    const T = window.__lemWorld.subsystems.get('trains');
    T._setActive = () => { T.maxActive = n; };
    T.maxActive = n;
  }, CAP);
}

await p.evaluate(([WARN, RATE]) => {
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains');
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__n = setInterval(() => w.parse(uids[i++ % uids.length], 'TVNEAR'), RATE);
  window.__hits = [];
  const lenOf = r => r.totalLength || r.len || 0;
  /* soak.mjs's own sampler: clamped, never wrapped */
  const soakAt = (c, s) => {
    const r = c.route, len = lenOf(r);
    if (!len) return null;
    return r.getPointAt(Math.min(1, Math.max(0, s / len)));
  };
  /* the metal: the route's own wrap */
  const trueAt = (c, s) => {
    const r = c.route, len = lenOf(r);
    if (!len) return null;
    let u = s / len;
    if (r.closed) u -= Math.floor(u);
    return r.getPointAt(Math.min(1, Math.max(0, u)));
  };
  const minD = (A, B, f, step) => {
    let d = Infinity, bu = 0, bv = 0;
    for (let u = 0; u <= A.length; u += step) {
      const pa = f(A, A.s - u);
      if (!pa) continue;
      for (let v = 0; v <= B.length; v += step) {
        const pb = f(B, B.s - v);
        if (!pb) continue;
        const dd = Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z);
        if (dd < d) { d = dd; bu = u; bv = v; }
      }
    }
    window.__lastUV = [bu, bv];
    return d;
  };
  /* which block of which track a consist's own table says it is on at arc s */
  const spanAt = (c, s) => {
    if (!c.spanIdx) return null;
    const L = c.L || 1;
    const h = c.route.closed ? s - Math.floor(s / L) * L + L : s;
    for (const sp of c.spanIdx) if (h >= sp.a && h <= sp.b) return sp.id + (sp.junction ? '*' : '');
    return null;
  };
  /* and soak's exact three-point body */
  const soakBody = (A, B) => {
    let d = Infinity;
    for (const fa of [0, 0.5, 1]) {
      if (A.s - A.length * fa < 0) continue;
      const pa = soakAt(A, A.s - A.length * fa);
      if (!pa) continue;
      for (const fb of [0, 0.5, 1]) {
        if (B.s - B.length * fb < 0) continue;
        const pb = soakAt(B, B.s - B.length * fb);
        if (!pb) continue;
        d = Math.min(d, Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z));
      }
    }
    return d;
  };
  const snap = c => ({
    slot: c.slot, uid: c.uid, state: c.state, s: +c.s.toFixed(1),
    L: +(c.L || 0).toFixed(1), v: +(c.v || 0).toFixed(2), line: c.line,
    terminal: +(c.terminal || 0).toFixed(1), homeS: +(c.homeS || 0).toFixed(1),
    holds: [...(c.holds || [])].sort(), tokens: [...(c.tokenIds || [])].sort(),
  });
  window.__watch = setInterval(() => {
    if (window.__hits.length > 5) return;
    const live = T.consists.filter(c => c && c.group.visible && c.route);
    for (let x = 0; x < live.length; x++) {
      for (let y = x + 1; y < live.length; y++) {
        const A = live[x], B = live[y];
        if (A.state === 'idle' && B.state === 'idle') continue;
        const sd = soakBody(A, B);
        const td = minD(A, B, trueAt, 4);
        const [ua, vb] = window.__lastUV;
        if (Math.min(sd, td) < WARN) {
          const pa = trueAt(A, A.s - ua), pb = trueAt(B, B.s - vb);
          window.__hits.push({
            t: Math.round(performance.now()),
            soakM: +sd.toFixed(2), trueM: +td.toFixed(2),
            aAt: {off: ua, block: spanAt(A, A.s - ua),
                  pt: [+pa.x.toFixed(1), +pa.y.toFixed(1), +pa.z.toFixed(1)]},
            bAt: {off: vb, block: spanAt(B, B.s - vb),
                  pt: [+pb.x.toFixed(1), +pb.y.toFixed(1), +pb.z.toFixed(1)]},
            a: snap(A), b: snap(B),
            shared: [...(A.holds || [])].filter(id => (B.holds || new Set()).has(id)),
          });
        }
      }
    }
  }, 50);
}, [WARN, RATE]);

const t0 = Date.now();
while (Date.now() - t0 < SECS * 1000) {
  await p.waitForTimeout(4000);
  if (await p.evaluate(() => window.__hits.length > 5)) break;
}
console.log(JSON.stringify(await p.evaluate(() => {
  clearInterval(window.__n); clearInterval(window.__watch);
  return window.__hits;
}), null, 1));
await b.close();
