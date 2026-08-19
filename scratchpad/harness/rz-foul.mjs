/* rz-foul.mjs — tv-throat.mjs, but reporting CONTIGUOUS fouling runs.
 *
 * tv-throat records `lo` = first main-s inside --foul and `hi` = LAST one, and
 * prints `hi - lo` as "the zone". It never checks the samples between are also
 * inside. A branch that fouls the ring for 40 m at its WEST junction and 40 m
 * at its EAST junction therefore prints as one 875 m zone with everything in
 * between implied. This prints every maximal run separately, with the branch
 * arc length opposite each, so a fouling stretch can be matched to a block.
 *
 *   node rz-foul.mjs [--layout 0] [--foul 5] [--gap 6]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const FOUL = parseFloat(arg('foul', '5'));
const GAP = parseFloat(arg('gap', '6'));

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
    machine_uid: uid, title, status, pos: pos[i], reason: 'rzfoul',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

console.log(JSON.stringify(await p.evaluate(([FOUL, GAP]) => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const T = rail.tracks.filter(t => t && t.frames && t.length > 4);
  const main = T.find(t => t.name === 'main');
  if (!main) return {error: 'no main'};
  const extent = t => Math.min(t.length, (t.frames.count - 1.001) * t.frames.step);
  const pts = t => {
    const a = []; const e = extent(t);
    for (let s = 0; s <= e; s += 1) a.push({s, p: t.at(s).position});
    return a;
  };
  const M = pts(main);
  const secs = (rail._sections.get('main') || [])
    .map((s, i) => ({i, a: +s.a.toFixed(1), b: +s.b.toFixed(1), junction: !!s.junction}));
  const out = {mainLength: +main.length.toFixed(1),
               mainFramed: +extent(main).toFixed(1),
               mainSections: secs.map(s => `main#${s.i}${s.junction ? '*' : ''} ${s.a}..${s.b}`),
               tracks: []};
  for (const o of T) {
    if (o === main) continue;
    const O = pts(o);
    /* per main sample: nearest distance and which arc of the other track */
    const near = M.map(m => {
      let d = Infinity, os = 0;
      for (const q of O) {
        const dd = Math.hypot(m.p.x - q.p.x, m.p.y - q.p.y, m.p.z - q.p.z);
        if (dd < d) { d = dd; os = q.s; }
      }
      return {s: m.s, d, os};
    });
    const runs = [];
    let cur = null;
    for (const n of near) {
      if (n.d < FOUL) {
        if (cur && n.s - cur.hi <= GAP) { cur.hi = n.s; }
        else { if (cur) runs.push(cur); cur = {lo: n.s, hi: n.s, worst: Infinity, atS: 0, oLo: Infinity, oHi: -Infinity}; }
        if (n.d < cur.worst) { cur.worst = n.d; cur.atS = n.s; }
        cur.oLo = Math.min(cur.oLo, n.os); cur.oHi = Math.max(cur.oHi, n.os);
      }
    }
    if (cur) runs.push(cur);
    let gWorst = Infinity, gAt = 0;
    for (const n of near) if (n.d < gWorst) { gWorst = n.d; gAt = n.s; }
    if (!runs.length) { out.tracks.push({track: o.name, len: +o.length.toFixed(1), clear: true, closest: +gWorst.toFixed(2)}); continue; }
    out.tracks.push({
      track: o.name, len: +o.length.toFixed(1),
      closest: +gWorst.toFixed(2), atMainS: +gAt.toFixed(1),
      totalFoulM: +runs.reduce((a, r) => a + (r.hi - r.lo), 0).toFixed(1),
      spanFirstToLast: +(runs[runs.length - 1].hi - runs[0].lo).toFixed(1),
      runs: runs.map(r => ({
        main: `${r.lo.toFixed(0)}..${r.hi.toFixed(0)}`, lenM: +(r.hi - r.lo).toFixed(1),
        closest: +r.worst.toFixed(2), atMainS: +r.atS.toFixed(1),
        other: `${r.oLo.toFixed(0)}..${r.oHi.toFixed(0)}`,
        sections: secs.filter(s => s.b > r.lo && s.a < r.hi)
          .map(s => `main#${s.i}${s.junction ? '*' : ''} ${s.a}..${s.b}`),
      })),
    });
  }
  return out;
}, [FOUL, GAP]), null, 1));
await b.close();
