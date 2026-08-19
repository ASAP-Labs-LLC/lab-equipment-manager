/* tv-queue.mjs — does the rank actually drain, and by which exit?
 *
 * Counts departures per exit, watches the backlog at the bench DEEPEST in the
 * queue, and reports the queue structure trains.js derived from
 * `rail.cycle().variants`. A counter moving is not a train moving, so it also
 * dumps every consist's line and arc length at the end.
 *
 *   node tv-queue.mjs [--layout 0|1] [--secs 180] [--every 1200]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '1'), 10);
const SECS = parseInt(arg('secs', '180'), 10);
const EVERY = parseInt(arg('every', '1200'), 10);
/* --noloops: the ablation, so BEFORE and AFTER are the same page, the same
 * layout and the same parse rate rather than two different rounds. Nulling
 * `exits` on the cached cycle records and on every consist is exactly what
 * `_tryStart` falls back to when rail publishes no variant, so it removes the
 * consumption and nothing else. Same switch as `tv-near.mjs --noloops`. */
const NOLOOPS = process.argv.includes('--noloops');

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
const ONE_RANK = FLEET.map((_, i) => [i * 2.05, 0]);

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);
if (LAYOUT === 1) {
  await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvqueue',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(3000);
}

if (NOLOOPS) {
  console.log('# LOOPS ABLATED (--noloops)');
  await p.evaluate(() => {
    const T = window.__lemWorld.subsystems.get('trains');
    const strip = () => {
      for (const [, cyc] of T.cycles) if (cyc) cyc.exits = null;
      for (const c of T.consists) c.exits = null;
    };
    strip();
    /* and keep it stripped — a relayout or a fresh uid re-derives them */
    window.__noloopTick = setInterval(strip, 200);
  });
}

console.log(JSON.stringify(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  const c = T.consists.find(x => x.exits);
  return {
    roads: T.roads, maxActive: T.maxActive,
    exits: c ? c.exits.map(e => ({s: +e.s.toFixed(1), line: e.cyc ? e.cyc.line : '(road exit)'})) : null,
    seated: T.consists.filter(x => x.uid).map(x => ({
      slot: x.slot, uid: x.uid, s: +x.s.toFixed(1), line: x.line,
      sharedTo: x.sharedTo === Infinity ? 'inf' : +x.sharedTo.toFixed(1)})),
  };
}), null, 2));

/* ---- queue DEPTH, structurally --------------------------------------------
 *
 * rail predicted 3 -> 2 on a four-stand rank and 6 -> 3 on a seven, "by
 * geometry", and depth is a structural quantity so it is measured structurally
 * as well as watched. Depth of a stand = how many OTHER stands lie between it
 * and the earliest exit in front of it, which is the number of trains that have
 * to get out of the way before the train standing there can. With no crossover
 * the only exit is the road's own turnout and the deepest bench in the rank
 * waits for every train in front of it.
 *
 * Taken off `trains.exits` and `cycle.docks` — the file's own numbers, not a
 * re-derivation of rail's, because the claim being tested is about what
 * trains.js does with them. */
console.log('\n=== QUEUE DEPTH (stands between a bench and its earliest exit) ===');
console.log(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  const seen = new Set(), out = [];
  for (const [, cyc] of T.cycles) {
    if (!cyc || !cyc.roadTrack || seen.has(cyc.roadTrack)) continue;
    seen.add(cyc.roadTrack);
    const stands = (cyc.docks || []).map(d => d.s).sort((a, b) => a - b);
    const exits = (cyc.exits || [{s: cyc.roadEnd, cyc: null}])
      .map(e => e.s).sort((a, b) => a - b);
    const roadOnly = [cyc.roadEnd];
    const depth = (d, ex) => {
      const e = ex.find(x => x > d);
      if (e === undefined) return null;
      return stands.filter(x => x > d && x < e).length;
    };
    const on = stands.map(d => depth(d, exits));
    const off = stands.map(d => depth(d, roadOnly));
    const mean = a => a.length ? +(a.reduce((s, x) => s + x, 0) / a.length).toFixed(2) : 0;
    out.push(`${cyc.roadTrack}: ${stands.length} stands at ` +
      stands.map(s => s.toFixed(0)).join('/') + '\n' +
      `    exits:      ${(cyc.exits || []).map(e => (e.cyc ? e.cyc.line : '(road)') + '@' + e.s.toFixed(0)).join('  ') || '(road)@' + cyc.roadEnd.toFixed(0)}\n` +
      `    depth OFF:  ${off.join(' ')}   max ${Math.max(...off)}  mean ${mean(off)}\n` +
      `    depth ON:   ${on.join(' ')}   max ${Math.max(...on)}  mean ${mean(on)}`);
  }
  return out.join('\n');
}));

/* Keep every bench printing, hard. */
await p.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__tvq = setInterval(() => w.parse(uids[i++ % uids.length], 'TVQ'), 700);
  const T = w.subsystems.get('trains');
  window.__tvqSeen = {lines: {}, starts: 0, arrivals: 0, maxBacklog: 0};
  const prev = new Map();
  window.__tvqTick = setInterval(() => {
    const S = window.__tvqSeen;
    for (const c of T.consists) {
      const was = prev.get(c.slot);
      if (was !== undefined && was === 'idle' && c.state === 'out') {
        S.starts++;
        S.lines[c.line] = (S.lines[c.line] || 0) + 1;
      }
      if (was !== undefined && was === 'discharge' && c.state === 'back') S.arrivals++;
      prev.set(c.slot, c.state);
    }
    for (const [, n] of T.backlog) if (n > S.maxBacklog) S.maxBacklog = n;
  }, 60);
});

const t0 = Date.now();
while (Date.now() - t0 < SECS * 1000) {
  await p.waitForTimeout(EVERY);
  const s = await p.evaluate(() => {
    const T = window.__lemWorld.subsystems.get('trains');
    return {
      t: Math.round((performance.now()) / 1000),
      out: T.consists.filter(c => c.state !== 'idle')
        .map(c => `${c.slot}:${c.state}@${c.s.toFixed(0)}:${c.line}`).join(' '),
      backlog: [...T.backlog].map(([u, n]) => `${u.slice(0, 9)}=${n}`).join(' '),
    };
  });
  console.log(`${s.t}s  ${s.out || '(nothing out)'}\n      ${s.backlog}`);
}

console.log('\n=== TOTALS ===');
console.log(JSON.stringify(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  clearInterval(window.__tvq); clearInterval(window.__tvqTick);
  return {...window.__tvqSeen,
          finalBacklog: Object.fromEntries([...T.backlog]),
          standing: T.consists.filter(c => c.uid)
            .map(c => `${c.slot}@${c.s.toFixed(0)}:${c.state}:${c.line}`)};
}), null, 2));
if (errs.length) console.log('ERRORS', errs.slice(0, 5));
await b.close();
