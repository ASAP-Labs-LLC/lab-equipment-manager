/* rz-stand.mjs — can a train STABLE inside a junction span?
 *
 * The one way a coupled throat could be held for ever. `LINK_BLOCK_GAP` was
 * written for this failure and describes it exactly: "a junction span under a
 * parked train is a train that `trains.js:_onRoad` can never read as home, and
 * a working that never reads as home never starts again". Growing every overlap
 * from `leadClearRun` makes junction spans LONGER, so the margin has to be
 * measured rather than assumed.
 *
 * For every stand on every loading road: the head is on the stand and the rake
 * trails back toward the entry, so the body is [s - rake, s]. Printed against
 * every junction span on that road.
 *
 *   node rz-stand.mjs [--layout 0] [--rake 84]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);
const RAKE = parseFloat(arg('rake', '84'));

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
    machine_uid: uid, title, status, pos: pos[i], reason: 'rzstand',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

console.log(JSON.stringify(await p.evaluate(RAKE => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const out = {stands: [], worstMargin: Infinity, inside: 0};
  for (const [uid, sd] of rail.sidings) {
    const t = sd.track;
    const list = rail._sections?.get(t.name) || [];
    const head = sd.sDock, tail = Math.max(0, sd.sDock - RAKE);
    const hits = [];
    let margin = Infinity;
    for (let i = 0; i < list.length; i++) {
      const s = list[i];
      if (!s.junction) continue;
      const id = s.id || `${t.name}#${i}`;
      if (s.b > tail && s.a < head) hits.push(`${id} ${s.a.toFixed(1)}..${s.b.toFixed(1)}`);
      /* how far the body ends from this span, either side */
      const d = s.a >= head ? s.a - head : (s.b <= tail ? tail - s.b : -1);
      if (d >= 0 && d < margin) margin = d;
    }
    if (hits.length) out.inside++;
    if (margin < out.worstMargin) out.worstMargin = margin;
    out.stands.push({uid, road: t.name, head: +head.toFixed(1),
                     tail: +tail.toFixed(1), marginM: +margin.toFixed(1),
                     insideJunction: hits});
  }
  out.worstMargin = +out.worstMargin.toFixed(1);
  return out;
}, RAKE), null, 1));
await b.close();
