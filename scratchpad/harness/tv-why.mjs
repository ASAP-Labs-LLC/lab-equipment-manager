/* tv-why.mjs — for each train standing on the rank: which exit is its queue's,
 * and if it is not moving, WHICH of the three refusals is the one that holds it.
 *
 * Reads the file's own answers rather than re-deriving them: `_exitFor`,
 * `_berth`, `_authority`, and the road's block table in circuit arc length. A
 * queue that does not drain is one of berth, authority, or the traffic cap, and
 * guessing which has been wrong on this project before.
 *
 *   node tv-why.mjs [--layout 1] [--warm 90]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '1'), 10);
const WARM = parseInt(arg('warm', '90'), 10);

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
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);
if (LAYOUT === 1) {
  await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvwhy',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(3000);
}
await p.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__w = setInterval(() => w.parse(uids[i++ % uids.length], 'TVWHY'), 900);
});
await p.waitForTimeout(WARM * 1000);

console.log(JSON.stringify(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  clearInterval(window.__w);
  const road = [];
  const any = T.consists.find(c => c.spans && c.roadTrack);
  if (any) {
    for (const sp of any.spans) {
      if (sp.id.slice(0, sp.id.lastIndexOf('#')) !== any.roadTrack) continue;
      road.push(`${sp.id}${sp.junction ? '*' : ''} ${sp.a.toFixed(0)}..${sp.b.toFixed(0)}`);
    }
  }
  const rows = T.consists.filter(c => c.uid && c.route).map(c => {
    const e = T._exitFor(c);
    const berth = T._berth(c);
    let roadEnd = c.roadEnd, line = c.line, why = '';
    if (c.state === 'idle') {
      const home = c.cyc;
      const vc = T._cycOf(c, e);
      if (vc && vc !== home) T._rebind(c, vc);
      roadEnd = c.roadEnd; line = c.line;
      const bb = T._berth(c);
      const a = T._authority(c, Math.max(c.s + 2, c.roadEnd + 2));
      const h = T._headArc(c);
      const reach = a.limit - h;
      why = bb < c.roadEnd + 2 ? `BERTH (${bb.toFixed(0)} < ${(c.roadEnd + 2).toFixed(0)})`
          : reach < (c.roadEnd - c.s) + 2 ? `AUTHORITY (reach ${reach.toFixed(0)} < ${(c.roadEnd - c.s + 2).toFixed(0)})`
          : 'CLEAR TO GO';
      if (c.cyc !== home) T._rebind(c, home);
    }
    return {slot: c.slot, uid: c.uid, state: c.state, s: +c.s.toFixed(0),
            exit: e ? +e.s.toFixed(0) : null, via: line,
            roadEnd: +roadEnd.toFixed(0), berth: berth === Infinity ? 'inf' : +berth.toFixed(0),
            why};
  }).sort((x, y) => y.s - x.s);
  return {maxActive: T.maxActive, roads: T.roads, active: T._activeCount(),
          exits: (any?.exits || []).map(e => +e.s.toFixed(0)), road, rows};
}), null, 2));
await b.close();
