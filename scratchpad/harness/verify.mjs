/* verify.mjs — the four properties the interlocking is supposed to have, each
 * asserted against the live ledger rather than against the absence of a crash.
 *
 * The soak proves trains do not touch. It cannot prove WHY, and a railway on
 * which nothing moves passes it. This checks the mechanism:
 *
 *   1. occupancy   every block under any visible consist's body is held by
 *                  that consist, on every frame, whatever it is doing
 *   2. no ghosts   every entry in the ledger belongs to a live consist and
 *                  names rail that consist is on or has been granted ahead
 *   3. release     nothing held behind a tail that is not also ahead of it
 *   4. liveness    workings keep completing on a layout built to deadlock:
 *                  two rows feeding one trunk, both saturated, plus the
 *                  returning traffic that has to cross the same throats
 *
 *   node verify.mjs [--layout rank|two-row|real] [--seconds 60]
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) args[a.slice(2)] = process.argv[i + 1];
}
const SECONDS = parseInt(args.seconds || '60', 10);
const LAYOUTS = {
  real: [[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]],
  rank: [[0,0],[2.05,0],[4.1,0],[6.15,0],[8.2,0],[10.25,0],[12.3,0]],
  /* The deadlock construction. Two full rows both feed the single trunk, so
   * every working out of either row has to take the same throat, and every
   * working coming home has to cross it in the other direction while the next
   * one is asking for it. Four benches on one road and three on the other means
   * neither road ever runs out of traffic to offer. */
  'two-row': [[0,0],[2.05,0],[4.1,0],[6.15,0],[0,2.05],[2.05,2.05],[4.1,2.05]],
};
const POS = LAYOUTS[args.layout || 'two-row'];

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text()))
  errs.push(m.text().slice(0, 300)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=15&hud=0',
             {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.evaluate(pos => {
  const F = [['multitek-ns','Multitek NS'],['multitek-s','Multitek S'],
             ['optimpp-1','OptiMPP 1'],['optimpp-2','OptiMPP 2'],
             ['pac-flash-1','PAC Flash 1'],['pac-flash-2','PAC Flash 2'],
             ['koehler-cp','Koehler CP']];
  window.__lemWorld.setMachines(F.map(([uid, title], i) => ({
    machine_uid: uid, title, status: 'GREEN', pos: pos[i], reason: 'verify',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
}, POS);
await p.waitForTimeout(3000);

await p.evaluate(() => {
  const W = window.__lemWorld;
  const V = window.__verify = {
    frames: 0, unclaimed: [], ghosts: [], stale: [],
    dispatches: 0, arrivals: 0, minSeparation: Infinity, minPair: null,
    maxWaitSeconds: 0, waitOffender: null, firstUnclaimed: null,
    lastState: {}, waitSince: {}, tracks: null,
  };
  const seen = new Set();
  const note = (bucket, s) => { const k = bucket + '|' + s;
    if (seen.has(k) || V[bucket].length > 30) return; seen.add(k); V[bucket].push(s); };

  const headArc = c => { const L = c.L || 1;
    return c.route.closed ? c.s - Math.floor(c.s / L) * L + L : c.s; };
  const bodyIds = c => {
    const out = new Set();
    if (!c.spanIdx) return out;
    const h = headArc(c), t = h - c.length;
    for (const sp of c.spanIdx) if (!(sp.b <= t || sp.a >= h)) out.add(sp.id);
    return out;
  };
  const pointAt = (c, s) => { const L = c.route.len || 1;
    const u = c.route.closed ? (((s % L) + L) % L) / L : Math.min(1, Math.max(0, s / L));
    return c.route.getPointAt ? c.route.getPointAt(u) : null; };

  const tick = () => {
    const T = W.subsystems.get('trains'), R = W.subsystems.get('rail');
    if (!T || !R) { requestAnimationFrame(tick); return; }
    V.frames++;
    const now = performance.now() / 1000;
    const held = R._held || new Map();
    const live = (T.consists || []).filter(c => c && c.group && c.group.visible &&
                                                c.route && !c.shunt);
    const bySig = new Map();
    for (const c of live) bySig.set('train' + c.slot, c);

    for (const c of live) {
      /* 1. occupancy — unconditional, whatever the state. */
      if (c.spanIdx) {
        for (const id of bodyIds(c)) {
          const who = held.get(id);
          if (who !== 'train' + c.slot) {
            note('unclaimed', `slot ${c.slot} (${c.state}) stands on ${id} held by ${who ?? 'nobody'}`);
            if (!V.firstUnclaimed) {
              const shot = x => ({slot: x.slot, uid: x.uid, state: x.state, line: x.line,
                s: +x.s.toFixed(2), v: +x.v.toFixed(2), len: +x.length.toFixed(1),
                parkS: +(x.parkS || 0).toFixed(1), L: +(x.L || 0).toFixed(1),
                headArc: +headArc(x).toFixed(2),
                docks: (x.docks || []).map(d => +d.s.toFixed(1)),
                body: [...bodyIds(x)].sort(),
                holds: [...(held)].filter(([, w]) => w === 'train' + x.slot).map(([k]) => k).sort(),
                tokens: x.tokenIds ? [...x.tokenIds].sort() : null});
              const other = live.find(z => 'train' + z.slot === who);
              V.firstUnclaimed = {tSeconds: +now.toFixed(1), block: id,
                                  me: shot(c), holder: other ? shot(other) : who};
            }
          }
        }
      }
      /* 3. release — a block held by this consist must be under it or ahead of
       * it on its own circuit. Anything else is rail it has already left. */
      const h = headArc(c), tail = h - c.length;
      const mine = [];
      for (const [id, who] of held) if (who === 'train' + c.slot) mine.push(id);
      /* Three lawful reasons to hold a block: standing on it, having been
       * granted it ahead, or holding it as part of a single-line token that is
       * not surrendered until the working is stabled. Anything else is rail the
       * train has left and not given back. */
      const token = c.tokenIds || new Set();
      for (const id of mine) {
        if (token.has(id)) continue;
        const spans = (c.spanIdx || []).filter(sp => sp.id === id);
        if (!spans.length) { note('stale', `slot ${c.slot} holds ${id}, not on its circuit and not a token`); continue; }
        const under = spans.some(sp => !(sp.b <= tail || sp.a >= h));
        const ahead = spans.some(sp => sp.a >= h - 0.01);
        if (!under && !ahead) {
          note('stale', `slot ${c.slot} still holds ${id} (behind its tail at ${tail.toFixed(1)})`);
        }
      }
      /* liveness bookkeeping */
      const prev = V.lastState[c.slot];
      if (prev !== c.state) {
        if (c.state === 'out') V.dispatches++;
        if (prev && prev !== 'idle' && c.state === 'idle') V.arrivals++;
        V.lastState[c.slot] = c.state;
      }
      const stuck = c.state !== 'idle' && c.v < 0.05 && c.dwell <= 0;
      if (stuck) {
        if (V.waitSince[c.slot] === undefined) V.waitSince[c.slot] = now;
        const w = now - V.waitSince[c.slot];
        if (w > V.maxWaitSeconds) {
          V.maxWaitSeconds = +w.toFixed(1);
          V.waitOffender = `slot ${c.slot} ${c.state} on ${c.line} at s=${c.s.toFixed(1)}`;
        }
      } else V.waitSince[c.slot] = undefined;
    }
    /* 2. no ghosts */
    for (const [id, who] of held) {
      if (!bySig.has(who)) note('ghosts', `${id} held by ${who}, which is not on the map`);
    }
    /* closest approach anywhere, in world metres, over the whole run */
    for (let i = 0; i < live.length; i++)
      for (let j = i + 1; j < live.length; j++) {
        const a = live[i], bb = live[j];
        let m = Infinity;
        for (let u = 0; u <= 6; u++) {
          const pA = pointAt(a, a.s - a.length * (u / 6)); if (!pA) break;
          for (let v = 0; v <= 6; v++) {
            const pB = pointAt(bb, bb.s - bb.length * (v / 6)); if (!pB) break;
            const d = Math.hypot(pA.x - pB.x, pA.y - pB.y, pA.z - pB.z);
            if (d < m) m = d;
          }
        }
        if (m < V.minSeparation) {
          V.minSeparation = +m.toFixed(3);
          V.minPair = `${a.slot}(${a.state})/${bb.slot}(${bb.state}) on ${a.line}/${bb.line}`;
        }
      }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
const t0 = Date.now();
let n = 0;
const marks = [];
while ((Date.now() - t0) / 1000 < SECONDS) {
  await p.evaluate(u => window.__lemWorld.parse(u, 'L-VERIFY'), FLEET[n % 7]);
  n++;
  await p.waitForTimeout(120);
  if (n % 50 === 0) {
    const s = await p.evaluate(() => ({d: window.__verify.dispatches,
                                       a: window.__verify.arrivals}));
    marks.push(`${Math.round((Date.now() - t0) / 1000)}s dispatches=${s.d} arrivals=${s.a}`);
  }
}
await p.waitForTimeout(8000);
const V = await p.evaluate(() => window.__verify);
console.log('layout:', args.layout || 'two-row');
console.log('progress:', marks.join(' | '));
console.log(JSON.stringify({
  frames: V.frames, parsesFired: n,
  dispatches: V.dispatches, arrivals: V.arrivals,
  minSeparation: V.minSeparation, minPair: V.minPair,
  maxContinuousStandSeconds: V.maxWaitSeconds, waitOffender: V.waitOffender,
  unclaimed: V.unclaimed, ghosts: V.ghosts, stale: V.stale,
  firstUnclaimed: V.firstUnclaimed,
}, null, 1));
console.log('errors:', errs.slice(0, 6));
await b.close();
