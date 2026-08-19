/* tv-exits.mjs — did trains.js actually take the variants, and are the numbers
 * it derived the same numbers rail published?
 *
 * The consumption is a set of fields on the sampled cycle record — `exits`,
 * `sharedTo`, `roadEnd`, `variantOf` — and every one of them is a scaled or
 * re-measured copy of something rail owns. This prints both sides so a
 * disagreement is visible rather than inferred, and then drives the railway for
 * a while and reports which LINE each departure actually left on. A field being
 * present is not a train using it.
 *
 *   node tv-exits.mjs [--layout 0|1] [--secs 90]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const SECS = parseInt(arg('secs', '90'), 10);

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
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvexits',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(3000);
}

console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains'), rail = w.subsystems.get('rail');
  const shared = (a, b) => {
    const A = a.points, B = b.points;
    let k = -1;
    for (let i = 0; i < Math.min(A.length, B.length); i++) {
      if (Math.hypot(A[i].x - B[i].x, A[i].y - B[i].y, A[i].z - B[i].z) > 0.001) break;
      k = i;
    }
    return k < 0 ? 0 : a.acc[k];
  };
  const out = [];
  for (const [uid, cyc] of T.cycles) {
    if (!cyc) { out.push({uid, cyc: null}); continue; }
    let raw = null; try { raw = rail.cycle(uid); } catch {}
    const rawFull = raw?.variants ? raw.variants[raw.variants.length - 1] : raw;
    out.push({
      uid, line: cyc.line, len: +cyc.r.len.toFixed(1), k: +(cyc.k ?? 1).toFixed(6),
      roadTrack: cyc.roadTrack, roadEnd: +(cyc.roadEnd ?? 0).toFixed(2),
      lastDock: +(cyc.lastDock ?? 0).toFixed(1),
      dockS: +(cyc.dockS ?? 0).toFixed(1),
      exits: (cyc.exits || []).map(e => `${e.cyc ? e.cyc.line : '(road)'}@${e.s.toFixed(2)}`),
      variants: (cyc.variants || []).map(v => ({
        line: v.line, len: +v.r.len.toFixed(1),
        roadEnd: +v.roadEnd.toFixed(2),
        sharedTo_trains: +v.sharedTo.toFixed(3),
        sharedTo_rail: raw?.variants
          ? +shared((raw.variants.find(x => x.line === v.line) || {route: {points: [], acc: []}}).route,
                    rawFull.route).toFixed(3) : null,
        docks: v.docks.map(d => `${d.uid}@${d.s.toFixed(1)}`),
        variantOfIsBase: v.variantOf === cyc,
      })),
    });
  }
  return out;
}), null, 1));

/* And now drive it, and see which way the trains actually go. */
await p.evaluate(() => {
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains');
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__tx = setInterval(() => w.parse(uids[i++ % uids.length], 'TVX'), 700);
  window.__seen = {starts: {}, arrivalsHome: 0, standingLines: {}};
  const prev = new Map();
  window.__tick = setInterval(() => {
    const S = window.__seen;
    for (const c of T.consists) {
      const was = prev.get(c.slot);
      if (was === 'idle' && c.state === 'out') S.starts[c.line] = (S.starts[c.line] || 0) + 1;
      if (was && was !== 'idle' && c.state === 'idle') {
        S.arrivalsHome++;
        S.standingLines[c.line] = (S.standingLines[c.line] || 0) + 1;
      }
      prev.set(c.slot, c.state);
    }
  }, 60);
});
await p.waitForTimeout(SECS * 1000);
console.log('\n=== DEPARTURES BY LINE ===');
console.log(JSON.stringify(await p.evaluate(() => {
  clearInterval(window.__tx); clearInterval(window.__tick);
  const T = window.__lemWorld.subsystems.get('trains');
  return {...window.__seen,
    now: T.consists.filter(c => c.uid).map(c =>
      `${c.slot}:${c.state}@${c.s.toFixed(0)}:${c.line}:shared=${c.sharedTo === Infinity ? 'inf' : c.sharedTo.toFixed(1)}`)};
}), null, 1));
if (errs.length) console.log('ERRORS', errs.slice(0, 8));
await b.close();
