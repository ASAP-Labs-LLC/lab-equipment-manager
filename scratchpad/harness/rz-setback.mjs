/* rz-setback.mjs — sweep `rail.loadSetback` in ONE page load.
 *
 * The rake at the first stand hangs over the entry turnout because the loading
 * road's switch tip stands 102 m outside the outermost bench and the rake plus
 * the entry overlap wants 121.8 m. Raising the setback is the obvious fix and
 * the obvious fix is what this project's rail rounds keep having refused by a
 * sweep, so it is swept before it is changed. What it can cost is BRANCHES:
 * `_loadingLoop` refuses a row whose two tips will not stand on the straight
 * (`sA < lo + 10 || sB > hi - 40`), and a row with no loading road is a row
 * with no railway.
 *
 *   node rz-setback.mjs [--layouts 6] [--from 102] [--to 150] [--step 6]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUTS = parseInt(arg('layouts', '6'), 10);
const FROM = parseFloat(arg('from', '102'));
const TO = parseFloat(arg('to', '150'));
const STEP = parseFloat(arg('step', '6'));
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
const SET = layouts(LAYOUTS);

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);

const rows = [];
for (let v = FROM; v <= TO + 1e-6; v += STEP) {
  const line = {setback: +v.toFixed(1), layouts: []};
  for (let L = 0; L < SET.length; L++) {
    await p.evaluate(([fleet, pos, sb]) => {
      const rail = window.__lemWorld.subsystems.get('rail');
      rail.loadSetback = sb;
      window.__lemWorld.setMachines(fleet.map(([uid, title, status], i) => ({
        machine_uid: uid, title, status, pos: pos[i], reason: 'rzsetback',
        sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
        module_running: true, module_state: 'running',
        effective_specs: [], qc_targets: [], maintenance: [],
      })));
    }, [FLEET, SET[L], v]);
    await p.waitForTimeout(2200);
    line.layouts.push(await p.evaluate(RAKE => {
      const rail = window.__lemWorld.subsystems.get('rail');
      let inside = 0, worst = Infinity, stands = 0;
      for (const [, sd] of rail.sidings) {
        stands++;
        const list = rail._sections?.get(sd.track.name) || [];
        const head = sd.sDock, tail = Math.max(0, sd.sDock - RAKE);
        for (const s of list) {
          if (!s.junction) continue;
          if (s.b > tail && s.a < head) { inside++; break; }
        }
        for (const s of list) {
          if (!s.junction) continue;
          const d = s.a >= head ? s.a - head : (s.b <= tail ? tail - s.b : -1);
          if (d >= 0 && d < worst) worst = d;
        }
      }
      return {branches: rail.branches.length, roads: new Set(
                [...rail.sidings.values()].map(s => s.track.name)).size,
              stands, standsInJunction: inside,
              worstMarginM: Number.isFinite(worst) ? +worst.toFixed(1) : null,
              exceptions: rail.exceptions.length,
              loopsBuilt: rail.passingLoops.built.length,
              loopsRefused: rail.passingLoops.refused.length,
              deadTracks: (rail.deadTracks || []).length};
    }, RAKE));
  }
  rows.push(line);
  const f = line.layouts;
  console.log(`setback ${line.setback}  stationsServed ${f.map(x => x.stands).join(',')}` +
              `  branches ${f.map(x => x.branches).join(',')}` +
              `  standsOverPoints ${f.map(x => x.standsInJunction).join(',')}` +
              `  loops ${f.map(x => x.loopsBuilt).join(',')}` +
              `  exc ${f.map(x => x.exceptions).join(',')}`);
}
console.log(JSON.stringify(rows));
await b.close();
