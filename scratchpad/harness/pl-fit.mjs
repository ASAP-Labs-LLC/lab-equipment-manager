/* pl-fit.mjs — can a mid-rank connection fit, on every layout soak.mjs drives?
 *
 * The arithmetic the passing loop stands or falls on:
 *
 *   a train leaving stand B through a turnout at s_M must be 5 m clear of the
 *   train standing at stand A before it comes alongside it (soak.mjs's FOUL is
 *   5 m in world space, and a variant carries its own `line` so that is the
 *   test that applies). So
 *
 *      G(N)  <=  pitch - rake - eps
 *
 *   G(N) is the run from the switch tip at which the diverging road is 5 m off
 *   the through road, measured on the same lead geometry makeLead() builds.
 *
 * Reports the real pitch on every loading road on all six soak layouts and the
 * real rake lengths, so the inequality is read off the world rather than off a
 * comment. */
import {chromium} from 'playwright';

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'],
  ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'],
  ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'],
  ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
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

const G = N => {                       // clearance run for a 1:N lead, to 5 m
  const GAUGE = 1.435, FOUL = 5;
  const R = 2 * GAUGE * N * N;
  const len = Math.sqrt(2 * R * GAUGE) + 5.4;
  const phi = len / R;
  const ve = R * (1 - Math.cos(phi)), ue = R * Math.sin(phi), m = Math.tan(phi);
  const V = FOUL * Math.sqrt(1 + m * m);     // min of hypot() is V/sqrt(1+m^2)
  return {R: +R.toFixed(1), len: +len.toFixed(2), degs: +(phi * 180 / Math.PI).toFixed(2),
          ve: +ve.toFixed(3), ue: +ue.toFixed(2),
          G: +(ue + (V - ve) / m).toFixed(2)};
};
console.log('lead geometry, and the run to 5 m of clearance:');
for (const N of [8, 6, 5, 4.5, 4]) console.log(' 1:' + N, JSON.stringify(G(N)));

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,buildings,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);

console.log('\nrakes: ' + JSON.stringify(await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  return T?.consists?.map(c => +c.length.toFixed(1)) ?? 'no trains module';
})));

const SET = layouts(6);
const rows = [];
for (let L = 0; L < SET.length; L++) {
  const ok = await p.evaluate(([fleet, pos]) => {
    const list = fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'plfit',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }));
    try { window.__lemWorld.setMachines(list); return true; }
    catch (e) { return String(e); }
  }, [FLEET, SET[L]]);
  if (ok !== true) { rows.push({L, err: ok}); continue; }
  await p.waitForTimeout(2600);
  rows.push({L, roads: await p.evaluate(() => {
    const rail = window.__lemWorld.subsystems.get('rail');
    const seen = new Set(); const out = [];
    for (const [, sd] of rail.sidings) {
      if (seen.has(sd.track.name)) continue;
      seen.add(sd.track.name);
      const t = sd.track;
      const ds = (sd.row?.list || [])
        .map(st => t.nearest(st.x, sd.dockZ).s).sort((a, b) => a - b);
      out.push({road: t.name, n: ds.length,
                stands: ds.map(v => +v.toFixed(1)),
                gaps: ds.slice(1).map((v, i) => +(v - ds[i]).toFixed(1)),
                len: +t.length.toFixed(1),
                paved: t.paved ? t.paved.map(v => +v.toFixed(1)) : null,
                renderFrom: +(t.renderFrom || 0).toFixed(1),
                renderTo: +Math.min(t.renderTo, t.length).toFixed(1)});
    }
    return out;
  })});
}
console.log('\nloading roads per layout:');
console.log(JSON.stringify(rows, null, 1));
await b.close();
