/* pl-pass.mjs — the passing move, measured on the railway that was built.
 *
 * Three things, all from rail.js's own published arrays and none of them from
 * my arithmetic:
 *
 *  1. BYTE-IDENTITY. trains.js's `_berth` subtracts arc lengths between two
 *     workings on one road, which is meaningless unless every variant quotes the
 *     shared railway in one coordinate. So: compare the variant's route points
 *     against the full circuit's, point by point, over the stretch they share.
 *
 *  2. THE PASSING MOVE. Run a body of length L down the variant's route from the
 *     stand it releases, and measure the true minimum distance to a body of
 *     length L parked at the NEXT stand on the full circuit. soak.mjs calls
 *     anything under 5 m a collision; it samples three points per consist, this
 *     samples every two metres of both.
 *
 *  3. AND THE SAME MOVE WITHOUT THE LOOP, so the number has something to be
 *     better than: the same train leaving by the road's own exit turnout, which
 *     is what it has to do today.
 *
 *   node pl-pass.mjs [--rake 84] [--layout 0|1]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const RAKE = parseFloat(arg('rake', '84'));
const LAYOUT = parseInt(arg('layout', '0'), 10);

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
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);
if (LAYOUT === 1) {
  await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'plpass',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(2800);
}

const out = await p.evaluate(RAKE => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const res = [];
  const seen = new Set();
  const P = (route, s) => route.pointAtDistance(
    Math.max(0, Math.min(route.length, s)));
  for (const [uid, sd] of rail.sidings) {
    if (seen.has(sd.track.name)) continue;
    let cyc = null; try { cyc = rail.cycle(uid); } catch { continue; }
    if (!cyc?.variants || cyc.variants.length < 2) continue;
    seen.add(sd.track.name);
    const full = cyc.variants[cyc.variants.length - 1];
    /* every bench on this road, so each variant can be judged from the stand it
     * actually releases */
    const cycles = new Map();
    for (const st of sd.row.list) {
      try { cycles.set(st.uid, rail.cycle(st.uid)); } catch {}
    }
    for (let vi = 0; vi < cyc.variants.length - 1; vi++) {
      /* the stand this variant releases: the last dock on it */
      const relDock = cyc.variants[vi].docks[cyc.variants[vi].docks.length - 1];
      const relUid = relDock.uid;
      const relCyc = cycles.get(relUid);
      const v = relCyc?.variants?.find(x => x.line === cyc.variants[vi].line);
      if (!v) continue;
      const fullRel = relCyc.variants[relCyc.variants.length - 1];
      /* the next stand along, on the full circuit */
      const idx = fullRel.docks.findIndex(d => d.uid === relUid);
      if (idx < 0 || idx + 1 >= fullRel.docks.length) continue;
      const aheadS = fullRel.docks[idx + 1].s;

      /* 1. byte-identity over the shared road */
      let worstPt = 0, shared = 0;
      const A = v.route.points, B = fullRel.route.points;
      for (let i = 0; i < Math.min(A.length, B.length); i++) {
        const d = A[i].distanceTo(B[i]);
        if (d > 0.001) break;
        worstPt = Math.max(worstPt, d);
        shared = i;
      }
      const sharedArc = fullRel.route.acc[shared];

      /* 2/3. the passing move, on the loop and off it */
      const run = route => {
        let worst = Infinity, at = 0;
        for (let h = v.dockS; h < Math.min(route.length, v.dockS + 320); h += 0.5) {
          let d = Infinity;
          for (let o = 0; o <= RAKE; o += 2) {
            const q = P(route, h - o);
            for (let e = 0; e <= RAKE; e += 2) {
              const r = P(fullRel.route, aheadS - e);
              const dd = Math.hypot(q.x - r.x, q.y - r.y, q.z - r.z);
              if (dd < d) d = dd;
            }
          }
          if (d < worst) { worst = d; at = h; }
        }
        return {min: +worst.toFixed(2), at: +at.toFixed(1)};
      };
      res.push({
        road: sd.track.name, variant: v.line, releases: relUid,
        dockS: +v.dockS.toFixed(1), nextStandS: +aheadS.toFixed(1),
        pitch: +(aheadS - v.dockS).toFixed(1),
        identicalTo: +sharedArc.toFixed(1), worstPointDiffMm: +(worstPt * 1000).toFixed(3),
        leavingByTheLoop: run(v.route),
        leavingByTheRoad: run(fullRel.route),
      });
    }
  }
  return res;
}, RAKE);

console.log(`rake ${RAKE} m, layout ${LAYOUT}\n`);
for (const r of out) {
  console.log(`${r.road}  ${r.variant}  releases ${r.releases}`);
  console.log(`  stand ${r.dockS} m, next stand ${r.nextStandS} m, pitch ${r.pitch} m`);
  console.log(`  route identical to the full circuit for ${r.identicalTo} m ` +
              `(worst point difference ${r.worstPointDiffMm} mm)`);
  console.log(`  min body-to-body to the train standing at the next bench:`);
  console.log(`     leaving by the loop  ${r.leavingByTheLoop.min} m  (at s=${r.leavingByTheLoop.at})`);
  console.log(`     leaving by the road  ${r.leavingByTheRoad.min} m  (at s=${r.leavingByTheRoad.at})`);
  console.log(`  soak.mjs fouls under 5.00 m\n`);
}
if (!out.length) console.log('no road on this layout publishes a variant');
await b.close();
