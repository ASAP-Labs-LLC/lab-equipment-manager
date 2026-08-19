/* ix-ework.mjs — is `deepestCut` a function of the site's WIDTH, or of where
 * the ground happens to be?
 *
 * `harness/ework.mjs` reports deepestCut 8.9 m at METRES_PER_BAY_X = 44 and
 * 9.0 at 57, against rail's TUNNEL_CUT = 9.0. That matters: a sample deeper
 * than TUNNEL_CUT is classified `tunnel`, so a `cut` span cannot normally
 * exceed 9.0 — EXCEPT that a tunnel run shorter than `minN` and not deep enough
 * to be widened is dissolved back to `cut` (rail.js, `earthworks()`), and that
 * one carries its depth with it. So `cutsDeeperThan9m` is reachable and the
 * headroom is real.
 *
 * Same URL and the same reads as ework.mjs, swept in one page load.
 *
 *   node ix-ework.mjs [--from 1.0] [--to 1.35] [--step 0.05]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const FROM = +(a.from ?? 1.0), TO = +(a.to ?? 1.35), STEP = +(a.step ?? 0.05);
const BASE = 44;   /* the scale the sweep multiplies; index.js ships 57 */

const FLEET = [
  ['multitek-ns', 'Multitek NS'], ['multitek-s', 'Multitek S'],
  ['optimpp-1', 'OptiMPP 1'], ['optimpp-2', 'OptiMPP 2'],
  ['pac-flash-1', 'PAC Flash 1'], ['pac-flash-2', 'PAC Flash 2'],
  ['koehler-cp', 'Koehler CP'],
];
const POS = [[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]];

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await (await b.newContext({viewport: {width: 1280, height: 720}})).newPage();
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 140)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=far&time=9&weather=clear&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);

const rows = [];
for (let k = FROM; k <= TO + 1e-9; k += STEP) {
  const r = await p.evaluate(({k, pos, fleet, BASE}) => {
    const w = window.__lemWorld;
    if (!w.__origPlan) {
      w.__origPlan = w._plan.bind(w);
      w._plan = function () {
        const plan = w.__origPlan();
        const s = w.__mpbX / BASE;
        if (s !== 1) {
          for (const st of plan.stations) st.x *= s;
          const xs = plan.stations.map(t => t.x);
          plan.hub.x = (Math.min(...xs, 0) + Math.max(...xs, 0)) / 2;
          plan.bounds.minX = Math.min(...xs, plan.hub.x);
          plan.bounds.maxX = Math.max(...xs, plan.hub.x);
        }
        return plan;
      };
    }
    /* the page ships METRES_PER_BAY_X already; renormalise to BASE first */
    const shipped = w.ctx.METRES_PER_BAY || BASE;
    w.__mpbX = (k * BASE) * BASE / shipped;
    w.setMachines(fleet.map(([uid, title], i) => ({
      machine_uid: uid, title, status: 'GREEN', pos: pos[i],
    })));
    w._replan();
    const rail = w.subsystems.get('rail');
    const ew = rail && typeof rail.earthworks === 'function' ? rail.earthworks() : null;
    if (!ew) return {mpbX: +(k * BASE).toFixed(1), published: false};
    const by = {}; let len = 0;
    for (const e of ew) { by[e.kind] = (by[e.kind] || 0) + 1; len += e.length || 0; }
    const cuts = ew.filter(e => e.kind === 'cut').map(e => Math.abs(e.maxDepth || 0));
    return {mpbX: +(k * BASE).toFixed(1), spans: ew.length, byKind: by,
            totalLengthM: Math.round(len),
            deepestCut: +Math.max(0, ...cuts).toFixed(2),
            cutsDeeperThan9m: cuts.filter(v => v > 9).length,
            cutsOver8m: cuts.filter(v => v > 8).length};
  }, {k, pos: POS, fleet: FLEET, BASE});
  rows.push(r);
  console.error(`mpbX=${r.mpbX}  spans=${r.spans}  deepestCut=${r.deepestCut}  >9m=${r.cutsDeeperThan9m}  >8m=${r.cutsOver8m}  totalM=${r.totalLengthM}  ${JSON.stringify(r.byKind)}`);
}
console.log(JSON.stringify({rows, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
