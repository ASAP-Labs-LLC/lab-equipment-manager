/* joints.mjs — measure every join on the railway, on every layout.
 *
 *   node joints.mjs [--layouts 10]
 *
 * The audit against Factorio's rail planner said the generator's turnouts
 * "join two railheads that do not meet". This is the number. For each junction
 * it reports the distance from the switch tip — the point on the through road's
 * centreline where the turnout starts — to where the diverging road's own
 * alignment actually begins, plus the vertical part of that on its own and the
 * angle between the two tangents.
 *
 * It also reports what the network came out as, because a join that closes
 * because the connection was silently dropped is not a fix.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}
const LAYOUTS = parseInt(args.layouts || '10', 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'],
  ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'],
  ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'],
  ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];

/* Identical to soak.mjs's, deliberately: the gate and this measurement have to
 * be looking at the same railways or one of them is describing a fiction. */
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

const url = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
            '?mods=terrain,buildings,rail&cam=yard&time=15&hud=0';
const browser = await chromium.launch({headless: true, channel: 'chromium',
                                       args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text()))
  errs.push(m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});

const SET = layouts(LAYOUTS);
let worst = 0, worstWhere = null, worstAngle = 0, worstLevel = 0;
for (let L = 0; L < SET.length; L++) {
  const r = await page.evaluate(([fleet, pos]) => {
    const list = fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'joints',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }));
    window.__lemWorld.setMachines(list);
    const rail = window.__lemWorld.subsystems.get('rail');
    const rep = rail.jointReport();
    const stations = window.__lemWorld.plan.stations;
    let routed = 0, cycles = 0, turned = 0;
    for (const s of stations) {
      if (rail.route(s.uid)) routed++;
      const c = rail.cycle(s.uid);
      if (c) { cycles++; if (c.turned) turned++; }
    }
    const bad = rep.detail.filter(d => d.angle > 1.2 || d.gapMm > 1);
    return {bad, joins: rep.joins, worstGapMm: rep.worstGapMm,
            worstAngle: rep.worstAngle, worstLevelMm: rep.worstLevelMm,
            worst: rep.worst, branches: rail.branches.length,
            sidings: rail.sidings.size, tracks: rail.tracks.length,
            balloon: !!rail.balloon, loop: !!rail.loop, spur: !!rail._spur,
            stations: stations.length, routed, cycles, turned};
  }, [FLEET, SET[L]]);
  if (r.worstGapMm > worst) { worst = r.worstGapMm; worstWhere = {L, ...r.worst}; }
  worstAngle = Math.max(worstAngle, r.worstAngle);
  worstLevel = Math.max(worstLevel, r.worstLevelMm);
  console.log(`L${L}: ${r.joins} joins · worst gap ${r.worstGapMm.toFixed(3)}mm ` +
    `· level ${r.worstLevelMm.toFixed(3)}mm · angle ${r.worstAngle.toFixed(3)}° ` +
    `| branches ${r.branches} sidings ${r.sidings} tracks ${r.tracks} ` +
    `balloon ${r.balloon ? 'y' : 'n'} loop ${r.loop ? 'y' : 'n'} spur ${r.spur ? 'y' : 'n'} ` +
    `| ${r.routed}/${r.stations} routed, ${r.cycles} cycles, ${r.turned} turn`);
  for (const b of r.bad) console.log('    ! ' + JSON.stringify(b));
}
console.log('\nWORST JOIN ACROSS ALL LAYOUTS: ' + worst.toFixed(3) + ' mm');
console.log('  level ' + worstLevel.toFixed(3) + ' mm · tangent ' +
            worstAngle.toFixed(3) + '°');
if (worstWhere) console.log('  ' + JSON.stringify(worstWhere));
if (errs.length) { console.log('CONSOLE ERRORS:'); for (const e of errs.slice(0, 8)) console.log('  ' + e); }
await browser.close();
process.exit(errs.length ? 1 : 0);
