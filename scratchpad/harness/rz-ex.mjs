/* rz-pair.mjs — the static form of soak's collision counter.
 *
 * soak.mjs runs trains and reports two slots that came within 5 m. That is a
 * SEARCH: it finds a fouling pair only if traffic happens to put two workings
 * there. The block table can be interrogated directly instead. For every pair
 * of blocks on DIFFERENT tracks, the minimum distance between the two pieces of
 * metal is a fixed property of the built railway; if that distance is under the
 * fouling threshold and nothing couples the two blocks, then two trains holding
 * one each are legally separated and physically touching, and the only question
 * is whether traffic ever puts them there.
 *
 * Same-track pairs are excluded: adjacent blocks on one road always meet at
 * their joint, and along-track separation is trains.js's lookahead, not the
 * table's job.
 *
 *   node rz-pair.mjs [--layout 0] [--foul 5] [--all]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const FOUL = parseFloat(arg('foul', '5'));
const ALL = process.argv.includes('--all');
const NOCOUPLE = process.argv.includes('--nocouple');

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
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);
await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
  fleet.map(([uid, title, status], i) => ({
    machine_uid: uid, title, status, pos: pos[i], reason: 'rzex',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

console.log(JSON.stringify(await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  return {exceptions: rail.exceptions, passingLoops: rail.passingLoops,
          throats: (rail._throats||[]).map(t => t.id + ' <- ' + t.child.join(',')),
          throatReport: rail.throatReport(),
          ringR: rail.ringR, runs: [...(rail._runBlocks||new Map()).keys()], runOf: (rail._runOf||new Map()).size};
}), null, 1));
await b.close();
