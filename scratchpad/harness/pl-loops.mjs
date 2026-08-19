/* pl-loops.mjs — what the passing loops actually are, live, on every layout.
 * Reports the crossovers built and refused, the variants each cycle publishes,
 * the joint quality of the new turnouts, and the one-way report. */
import {chromium} from 'playwright';

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
function layouts(n) {
  const BAY = 2.05;
  const all = [[[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]]];
  let seed = 12345;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let L = 1; L < n; L++) {
    const kind = L % 4; const pos = [];
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

const PROBE = () => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const seen = new Set(); const roads = [];
  for (const [uid, sd] of rail.sidings) {
    if (seen.has(sd.track.name)) continue;
    seen.add(sd.track.name);
    let cyc = null; try { cyc = rail.cycle(uid); } catch (e) { cyc = {err: String(e)}; }
    roads.push({
      road: sd.track.name,
      paved: (sd.track.paved || []).map(s => s.map(v => +v.toFixed(1))),
      links: (sd.track.links || []).map(k => ({
        name: k.track.name, roadS: +k.roadS.toFixed(1), lineS: +k.lineS.toFixed(1),
        len: +k.track.length.toFixed(1),
        from: +k.track.renderFrom.toFixed(1),
        to: +Math.min(k.track.renderTo, k.track.length).toFixed(1),
        minR: k.track.minRadiusUsed === Infinity ? 'straight' : +k.track.minRadiusUsed.toFixed(1),
      })),
      variants: (cyc?.variants || []).map(v => ({
        line: v.line, len: +v.route.length.toFixed(1),
        docks: v.docks.map(d => +d.s.toFixed(1)),
        terminal: +v.terminal.toFixed(1), closed: v.closed,
      })),
    });
  }
  const j = rail.jointReport();
  const bad = j.detail.filter(d => d.gapMm > 5 || d.plan > 3)
                      .map(d => `${d.turnout} gap ${d.gapMm.toFixed(1)}mm plan ${d.plan.toFixed(2)}deg`);
  return {
    roads, dead: rail.deadTracks,
    loops: rail.passingLoops,
    oneWay: rail.oneWayReport(),
    joints: {joins: j.joins, worstGapMm: +j.worstGapMm.toFixed(2),
             worstAngle: +j.worstAngle.toFixed(3), bad},
    tracks: rail.tracks.map(t => t.name),
  };
};

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error') errs.push('[console] ' + m.text().slice(0, 200)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);

const SET = layouts(6);
for (let L = 0; L < SET.length; L++) {
  if (L > 0) {
    await p.evaluate(([fleet, pos]) => {
      window.__lemWorld.setMachines(fleet.map(([uid, title, status], i) => ({
        machine_uid: uid, title, status, pos: pos[i], reason: 'plloops',
        sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
        module_running: true, module_state: 'running',
        effective_specs: [], qc_targets: [], maintenance: [],
      })));
    }, [FLEET, SET[L]]);
    await p.waitForTimeout(2600);
  }
  const r = await p.evaluate(PROBE);
  console.log(`\n===== layout ${L} =====`);
  console.log(JSON.stringify(r, null, 1));
}
if (errs.length) console.log('\nPAGE ERRORS:\n' + errs.slice(0, 10).join('\n'));
else console.log('\nno page errors');
await b.close();
