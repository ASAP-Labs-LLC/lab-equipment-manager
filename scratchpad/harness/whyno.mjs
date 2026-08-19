/* whyno.mjs — why a connection was refused.
 *
 * The generator now refuses rather than emit a join that does not close, which
 * makes "there is no branch here" a legitimate answer and therefore a thing
 * that has to be checkable. This re-runs the guards on the live layout and says
 * which one bit.
 */
import {chromium} from 'playwright';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
            '?mods=terrain,buildings,rail&cam=yard&time=15&hud=0';
const FLEET = [
  ['multitek-ns', 'Multitek NS'], ['multitek-s', 'Multitek S'],
  ['optimpp-1', 'OptiMPP 1'], ['optimpp-2', 'OptiMPP 2'],
  ['pac-flash-1', 'PAC Flash 1'], ['pac-flash-2', 'PAC Flash 2'],
  ['koehler-cp', 'Koehler CP'],
];
const POS = JSON.parse(process.argv[2] ||
  '[[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]]');

const browser = await chromium.launch({headless: true, channel: 'chromium',
                                       args: ['--use-angle=metal']});
const page = await browser.newPage({viewport: {width: 800, height: 600}});
page.on('console', m => console.log('[page]', m.text().slice(0, 240)));
page.on('pageerror', e => console.log('[err]', String(e).slice(0, 240)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
const out = await page.evaluate(([fleet, pos]) => {
  const list = fleet.map(([uid, title], i) => ({
    machine_uid: uid, title, status: 'GREEN', pos: pos[i],
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }));
  window.__lemWorld.setMachines(list);
  const w = window.__lemWorld, rail = w.subsystems.get('rail'), plan = w.plan;
  const hub = plan.hub, ZY = hub.z + 26;
  const rows = new Map();
  for (const s of plan.stations) {
    const k = Math.round(s.z / 8);
    if (!rows.has(k)) rows.set(k, []);
    rows.get(k).push(s);
  }
  const order = [...rows.values()]
    .map(l => ({z: l.reduce((a, b) => a + b.z, 0) / l.length, list: l}))
    .sort((a, b) => Math.abs(a.z - hub.z) - Math.abs(b.z - hub.z));
  let minX = Infinity, maxX = -Infinity;
  for (const s of plan.stations) { minX = Math.min(minX, s.x); maxX = Math.max(maxX, s.x); }
  const WX = Math.min(minX - 270, hub.x - 290);
  return {
    hub: {x: hub.x, z: hub.z}, ZY, WX, minX, maxX,
    trunk: rail.trunk ? {len: rail.trunk.length} : null,
    rows: order.map(r => ({z: r.z, n: r.list.length,
                           runZ: r.z - 26 - 8.4, gap: (r.z - 26 - 8.4) - ZY,
                           headX: Math.max(...r.list.map(s => s.x)),
                           x0: Math.min(...r.list.map(s => s.x)),
                           x1: Math.max(...r.list.map(s => s.x))})),
    branches: rail.branches.map(b => ({name: b.track.name, len: b.track.length,
                                       renderTo: b.track.renderTo,
                                       tight: b.track.tight,
                                       minR: b.track.minRadiusUsed})),
    tracks: rail.tracks.map(t => ({name: t.name, len: t.length,
                                   from: t.renderFrom, to: t.renderTo,
                                   tight: t.tight, minR: t.minRadiusUsed})),
    sidings: rail.sidings.size,
    balloon: !!rail.balloon, loop: !!rail.loop,
  };
}, [FLEET, POS]);
console.log(JSON.stringify(out, null, 1));
await browser.close();
