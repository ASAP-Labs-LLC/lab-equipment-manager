/* tv-pass.mjs — does a working actually PASS a standing one, in motion?
 *
 * The previous round of trains.js could not answer this and said so: "No
 * overtake, and there cannot be one — both workings are on different roads and
 * no crossover is taken." A counter moving is not a train moving, and
 * `variants` being consumed is not a train getting past anything. This watches
 * for the event itself.
 *
 * An overtake here is defined in WORLD SPACE and not in arc length, because the
 * two workings are by construction on different circuits and their arc lengths
 * are different coordinates past the divergence — which is the whole reason the
 * loop exists. So: a consist S that does not move for the whole episode, a
 * consist M that does, and the sign of (M − S) projected on S's own heading
 * going from negative to positive. The closest body-to-body approach over the
 * episode is recorded with it, against soak.mjs's 5.00 m fouling threshold,
 * because a pass that fouls is not a pass.
 *
 *   node tv-pass.mjs [--layout 0|1] [--secs 200] [--every 250]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const SECS = parseInt(arg('secs', '200'), 10);
const EVERY = parseInt(arg('every', '250'), 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
const ONE_RANK = FLEET.map((_, i) => [i * 2.05, 0]);

const LOAD = arg('load', null);
const b = LOAD ? null : await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
if (LOAD) { analyse(JSON.parse(fs.readFileSync(LOAD, 'utf8')), []); process.exit(0); }
const p = await b.newPage({viewport: {width: 900, height: 520}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);
if (LAYOUT === 1) {
  await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvpass',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(3000);
}

/* The recorder runs IN THE PAGE, at frame rate. Sampling this over the wire at
 * four hertz would step straight over the frames a pass actually happens in —
 * the whole divergence is under thirty metres of running. */
await p.evaluate(() => {
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains');
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__pp = setInterval(() => w.parse(uids[i++ % uids.length], 'TVPASS'), 800);

  const at = (c, s) => {
    const r = c.route;
    if (!r || !r.getPointAt) return null;
    return r.getPointAt(s / r.len);
  };
  const rec = [];
  window.__passRec = rec;
  const tick = () => {
    const row = {t: +(performance.now() / 1000).toFixed(2), c: []};
    for (const c of T.consists) {
      if (!c || !c.uid || !c.route || !c.group?.visible) continue;
      const h = at(c, c.s), m = at(c, c.s - c.length / 2), t2 = at(c, c.s - c.length);
      if (!h || !t2) continue;
      row.c.push({slot: c.slot, state: c.state, s: +c.s.toFixed(2),
                  v: +c.v.toFixed(3), line: c.line, len: +c.length.toFixed(1),
                  road: c.roadTrack || null,
                  p: [+h.x.toFixed(2), +h.y.toFixed(2), +h.z.toFixed(2)],
                  m: [+m.x.toFixed(2), +m.y.toFixed(2), +m.z.toFixed(2)],
                  q: [+t2.x.toFixed(2), +t2.y.toFixed(2), +t2.z.toFixed(2)]});
    }
    rec.push(row);
    if (rec.length > 40000) rec.shift();
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const t0 = Date.now();
while (Date.now() - t0 < SECS * 1000) {
  await p.waitForTimeout(EVERY * 12);
  const s = await p.evaluate(() => {
    const T = window.__lemWorld.subsystems.get('trains');
    return T.consists.filter(c => c.uid)
      .map(c => `${c.slot}:${c.state}@${c.s.toFixed(0)}:${c.line}`).join('  ');
  });
  process.stdout.write('.' );
  if (process.env.TVPASS_VERBOSE) console.log(' ' + s);
}
console.log('');

const rec = await p.evaluate(() => {
  clearInterval(window.__pp);
  return window.__passRec;
});
await b.close();
fs.writeFileSync('/tmp/tv-pass.json', JSON.stringify(rec));
analyse(rec, errs);

/* ---- the analysis ----------------------------------------------------------
 *
 * A pass needs a stander and a mover, and three things have to be true of the
 * pair or the event is not the one being claimed:
 *
 *   SAME ROAD. Two workings off different loading roads cross each other's
 *     bearing all day at ninety metres and it is not an overtake. The first
 *     version of this instrument reported two of those as passes, which is the
 *     nineteenth confident wrong answer on this project and very nearly the
 *     twentieth.
 *   THE STANDER STOOD, over the APPROACH — not over the whole run of frames
 *     since the pair last changed sides, which on a working railway is minutes
 *     and includes the stander creeping up the road. The window is the frames
 *     either side of the crossing in which the two are within 150 m.
 *   AND IT ACTUALLY CROSSED, head to tail: the mover's midpoint goes from
 *     behind the stander's midpoint to in front of it, measured along the
 *     stander's own heading. */
function analyse(rec, errs) {
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const bodyDist = (A, B) => {
  let d = Infinity;
  for (const a of [A.p, A.m, A.q]) for (const b of [B.p, B.m, B.q]) {
    const dd = dist(a, b); if (dd < d) d = dd;
  }
  return d;
};
const NEAR = 150;

const bySlot = new Map();
for (let f = 0; f < rec.length; f++) {
  for (const c of rec[f].c) {
    if (!bySlot.has(c.slot)) bySlot.set(c.slot, []);
    bySlot.get(c.slot).push({f, ...c});
  }
}

const events = [];
const slots = [...bySlot.keys()];
for (const sMover of slots) {
  for (const sStand of slots) {
    if (sMover === sStand) continue;
    const M = new Map(bySlot.get(sMover).map(r => [r.f, r]));
    const S = new Map(bySlot.get(sStand).map(r => [r.f, r]));
    let prevSign = 0;
    for (let f = 1; f < rec.length; f++) {
      const m = M.get(f), st = S.get(f);
      if (!m || !st) { prevSign = 0; continue; }
      if (!m.road || m.road !== st.road) { prevSign = 0; continue; }
      const fwd = sub(st.p, st.q);
      const L = Math.hypot(fwd[0], fwd[1], fwd[2]) || 1;
      const u = [fwd[0] / L, fwd[1] / L, fwd[2] / L];
      const sign = dot(sub(m.m, st.m), u) > 0 ? 1 : -1;
      if (prevSign === -1 && sign === 1) {
        /* the approach and the departure, bounded by proximity */
        let a = f, z = f;
        while (a > 0 && M.get(a - 1) && S.get(a - 1) &&
               bodyDist(M.get(a - 1), S.get(a - 1)) < NEAR) a--;
        while (z + 1 < rec.length && M.get(z + 1) && S.get(z + 1) &&
               bodyDist(M.get(z + 1), S.get(z + 1)) < NEAR) z++;
        let moved = 0, closest = Infinity, closestF = f, movedM = 0, working = false;
        for (let g = a; g <= z; g++) {
          const sg = S.get(g), mg = M.get(g);
          /* "Standing" is stabled stock, not a frozen one. A train at a bench
           * shuffles up the road as the queue drains — `_stepIdle`, at CREEP,
           * 3.4 m/s — and it is still the thing being passed. What it must not
           * be is a working: `idle` is the state, and it is the state soak's own
           * sampler had to stop excusing for the same reason. */
          if (sg.state !== 'idle' || Math.abs(sg.v) > 3.5) { working = true; break; }
          moved = Math.max(moved, dist(sg.p, S.get(a).p));
          movedM = Math.max(movedM, dist(mg.p, M.get(a).p));
          const d = bodyDist(mg, sg);
          if (d < closest) { closest = d; closestF = g; }
        }
        if (!working && movedM > 20 && z - a >= 5) {
          events.push({mover: sMover, stander: sStand, fromF: a, toF: z,
                       crossF: f, standerMoved: +moved.toFixed(2),
                       closest: +closest.toFixed(2), closestF,
                       t0: rec[a].t, t1: rec[z].t});
        }
      }
      prevSign = sign;
    }
  }
}

events.sort((a, b) => a.closest - b.closest);
const shown = [];
for (const e of events) {
  if (shown.some(x => x.mover === e.mover && x.stander === e.stander &&
                      Math.abs(x.crossF - e.crossF) < 200)) continue;
  shown.push(e);
  if (shown.length > 5) break;
}
console.log(`\n=== ${events.length} overtakes of a standing consist on the SAME loading road ` +
            `(${shown.length} distinct shown) ===`);
for (const e of shown) {
  const M = new Map(bySlot.get(e.mover).map(r => [r.f, r]));
  const S = new Map(bySlot.get(e.stander).map(r => [r.f, r]));
  console.log(`\n--- slot ${e.mover} passes STANDING slot ${e.stander} on ` +
              `${M.get(e.crossF).road} (t ${e.t0}s → ${e.t1}s; the stander was ` +
              `idle throughout and crept ${e.standerMoved} m up the road)`);
  console.log(`    closest body-to-body ${e.closest} m at t=${rec[e.closestF].t}s — ` +
              `${e.closest < 5 ? '*** FOULS, soak threshold is 5.00 ***'
                               : 'clear of soak.mjs\'s 5.00 m fouling threshold'}`);
  const step = Math.max(1, Math.round((e.toF - e.fromF) / 13));
  console.log('       t   mover                                         stander                                   gap');
  for (let f = e.fromF; f <= e.toF; f += step) {
    const m = M.get(f), s = S.get(f);
    if (!m || !s) continue;
    console.log(`  ${String(rec[f].t).padStart(7)}  ` +
      `slot${m.slot} ${m.state.padEnd(9)} s=${String(m.s).padStart(8)} v=${String(m.v).padStart(6)} ${m.line.padEnd(12)}  ` +
      `slot${s.slot} ${s.state.padEnd(9)} s=${String(s.s).padStart(8)} v=${String(s.v).padStart(6)} ${s.line.padEnd(11)}  ` +
      `${bodyDist(m, s).toFixed(2)} m` + (f <= e.crossF && f + step > e.crossF ? '   <- crosses here' : ''));
  }
}
if (!events.length) console.log('none — nothing passed anything standing on its own road.');
if (errs.length) console.log('ERRORS', errs.slice(0, 5));
console.log('\nraw per-frame record: /tmp/tv-pass.json (' + rec.length + ' frames)');
}
