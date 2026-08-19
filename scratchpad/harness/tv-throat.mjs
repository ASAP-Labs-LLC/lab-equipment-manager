/* tv-throat.mjs — the fouling zone where a branch joins the main, against the
 * block table that is supposed to protect it.
 *
 * Two workings from two different rows were measured 5.6 m apart with neither
 * doing anything the ledger disallowed: one on `branch0#8`, flagged junction,
 * the other with its head on `main#2`, not. A converging junction always has a
 * stretch where the two legs are inside a vehicle's width of each other; the
 * block that covers it is what stops two trains being in it at once. So this
 * prints, for every branch, the arc range on `main` that is within `--foul`
 * metres of that branch, and the `main` sections that range falls in — with
 * their junction flags.
 *
 *   node tv-throat.mjs [--layout 0] [--foul 5]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const FOUL = parseFloat(arg('foul', '5'));

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
    machine_uid: uid, title, status, pos: pos[i], reason: 'tvthroat',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

console.log(JSON.stringify(await p.evaluate(FOUL => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const T = rail.tracks.filter(t => t && t.frames && t.length > 4);
  const main = T.find(t => t.name === 'main');
  if (!main) return {error: 'no main'};
  /* `Track.at` clamps to the last FRAME, not the last metre, so sampling past
   * the framed extent piles every remaining sample on one point and invents
   * closeness over hundreds of metres. Bound it. */
  const extent = t => Math.min(t.length, (t.frames.count - 1.001) * t.frames.step);
  const pts = t => {
    const a = [];
    const e = extent(t);
    for (let s = 0; s <= e; s += 1) a.push({s, p: t.at(s).position});
    return a;
  };
  const M = pts(main);
  const secs = (rail._sections.get('main') || [])
    .map((s, i) => ({i, a: +s.a.toFixed(1), b: +s.b.toFixed(1), junction: !!s.junction}));
  const out = {mainLength: +main.length.toFixed(1), mainFramed: +extent(main).toFixed(1), mainSections: secs, zones: []};
  for (const o of T) {
    if (o === main) continue;
    const O = pts(o);
    let lo = null, hi = null, worst = Infinity, worstS = 0;
    for (const m of M) {
      let d = Infinity;
      for (const q of O) {
        const dd = Math.hypot(m.p.x - q.p.x, m.p.y - q.p.y, m.p.z - q.p.z);
        if (dd < d) d = dd;
      }
      if (d < worst) { worst = d; worstS = m.s; }
      if (d < FOUL) { if (lo === null) lo = m.s; hi = m.s; }
    }
    if (lo === null) continue;
    const inSecs = secs.filter(s => s.b > lo && s.a < hi);
    out.zones.push({track: o.name, mainFrom: +lo.toFixed(1), mainTo: +hi.toFixed(1),
                    lengthM: +(hi - lo).toFixed(1),
                    closest: +worst.toFixed(2), atMainS: +worstS.toFixed(1),
                    mainSectionsInZone: inSecs.map(s =>
                      `main#${s.i}${s.junction ? '*' : ''} ${s.a}..${s.b}`)});
  }
  return out;
}, FOUL), null, 1));
await b.close();
