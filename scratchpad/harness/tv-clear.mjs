/* tv-clear.mjs — how far back from a junction is the CLEARANCE POINT?
 *
 * trains.js stands a refused working `CLEAR` = 6.5 m short of the block it was
 * refused. At a plain block joint on a straight road that is a buffer-beam
 * margin and nothing more. At a CONVERGING JUNCTION it is the clearance point,
 * and it has to be far enough back that a train standing at the signal is not
 * foul of the other leg — which is a property of the geometry, not a constant.
 *
 * So: for every junction section on every track, walk BACK along that track from
 * where the section begins and find the first point that is at least `--foul`
 * metres from every other track's centre line. The distance walked is the
 * clearance this railway actually needs there.
 *
 *   node tv-clear.mjs [--layout 0] [--foul 6]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const FOUL = parseFloat(arg('foul', '6'));

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
    machine_uid: uid, title, status, pos: pos[i], reason: 'tvclear',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

const out = await p.evaluate(FOUL => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const tracks = rail.tracks.filter(t => t && t.length > 4 &&
                                    t.frames);
  const cache = new Map();
  const pts = t => {
    if (!cache.has(t)) {
      const a = [];
      for (let s = 0; s <= t.length; s += 2) a.push(t.at(s).position);
      cache.set(t, a);
    }
    return cache.get(t);
  };
  const nearestOther = (t, q) => {
    let best = Infinity, who = null;
    for (const o of tracks) {
      if (o === t) continue;
      for (const r of pts(o)) {
        const d = Math.hypot(q.x - r.x, q.y - r.y, q.z - r.z);
        if (d < best) { best = d; who = o.name; }
      }
    }
    return {d: best, who};
  };
  const res = [];
  for (const t of tracks) {
    const secs = rail._sections?.get(t.name) || [];
    for (let i = 0; i < secs.length; i++) {
      if (!secs[i].junction) continue;
      const a = secs[i].a;
      /* a train refused THIS section stands with its head short of `a` */
      let need = 0, at = null;
      for (let back = 0; back <= 90; back += 1) {
        const s = a - back;
        if (s < 0) break;
        const q = t.at(s).position;
        const n = nearestOther(t, q);
        if (n.d >= FOUL) { need = back; at = n; break; }
        need = back; at = n;
      }
      res.push({track: t.name, sec: i, a: +a.toFixed(1),
                clearanceNeeded: need, nearest: at ? +at.d.toFixed(2) : null,
                other: at ? at.who : null});
    }
  }
  return res;
}, FOUL);

let worst = 0, worstRow = null;
for (const r of out) {
  if (r.clearanceNeeded > worst) { worst = r.clearanceNeeded; worstRow = r; }
  if (r.clearanceNeeded > 6.5) {
    console.log(`${r.track}#${r.sec} junction at ${r.a} m: needs ${r.clearanceNeeded} m ` +
                `(at that point ${r.nearest} m from ${r.other})   *** more than CLEAR=6.5 ***`);
  }
}
console.log(`\njunction sections examined: ${out.length}`);
console.log(`worst clearance needed: ${worst} m` +
            (worstRow ? `  (${worstRow.track}#${worstRow.sec}, ${worstRow.other})` : ''));
await b.close();
