/* ix-spacing.mjs — sweep the bay's METRE scale in X and watch what breaks.
 *
 * rail.js asked index.js for 115 m of stand spacing on the loading road (today
 * 91.5 m), because the passing loop needs 49.3 m of transition and splitting
 * the apron costs 3 x PAVE_TAPER = 27 m, leaving 15.2 m for a 64.5-84 m
 * consist. Stand spacing is not a rail constant: a stand is
 * `sd.nearest(station.x, dockZ).s`, so it IS the x-spacing of the fleet, which
 * is `METRES_PER_BAY` x the saved bay pitch.
 *
 * The last dimension rail asked index.js for (236 m at hub.z) was refused by a
 * sweep, so this sweeps rather than assuming. One page load; the fleet, terrain
 * and rail are re-planned at every step, exactly as the HUB_SETBACK sweep was.
 *
 *   node ix-spacing.mjs [--from 1.0] [--to 1.4] [--step 0.05] [--layout 0]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const FROM = +(a.from ?? 1.0), TO = +(a.to ?? 1.4), STEP = +(a.step ?? 0.05);
const LAYOUT = a.layout ?? '0';

const BAY = 2.05;
const FLEET = [
  ['multitek-ns', 'Multitek NS'], ['multitek-s', 'Multitek S'],
  ['optimpp-1', 'OptiMPP 1'], ['optimpp-2', 'OptiMPP 2'],
  ['pac-flash-1', 'PAC Flash 1'], ['pac-flash-2', 'PAC Flash 2'],
  ['koehler-cp', 'Koehler CP'],
];
/* soak.mjs's layouts, verbatim, so a result here is a result there. */
function layouts(n) {
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
const LAYOUTS = layouts(6);
const WANT = LAYOUT === 'all' ? LAYOUTS.map((_, i) => i) : [+LAYOUT];

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,buildings,rail,trains&cam=top&time=13&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);

const rows = [];
for (const L of WANT) {
  const pos = LAYOUTS[L];
  for (let k = FROM; k <= TO + 1e-9; k += STEP) {
    const r = await p.evaluate(({k, pos, fleet}) => {
      const w = window.__lemWorld;
      if (!w.__origPlan) {
        w.__origPlan = w._plan.bind(w);
        w._plan = function () {
          const plan = w.__origPlan();
          const s = w.__scaleX || 1;
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
      /* index.js ships METRES_PER_BAY across a rank; renormalise so k = 1 is
       * always the historical 44 m however the file is set today. */
      w.__scaleX = k * 44 / (w.ctx.METRES_PER_BAY || 44);
      w.setMachines(fleet.map(([uid, title], i) => ({
        machine_uid: uid, title, status: 'GREEN', pos: pos[i],
      })));
      /* the layout signature does not know about the scale, so force it */
      w._replan();
      const rail = w.subsystems.get('rail'), terrain = w.subsystems.get('terrain');
      const plan = w.plan;
      const xs = [...new Set(plan.stations.map(s => +s.x.toFixed(1)))].sort((u, v) => u - v);
      const xGap = xs.length > 1 ? +(xs[1] - xs[0]).toFixed(1) : null;
      /* stands, per loading road, from rail's own sidings */
      const roads = new Map();
      for (const [uid, sd] of rail.sidings || []) {
        if (!sd || !sd.track) continue;
        const n = sd.track.name;
        if (!roads.has(n)) roads.set(n, {name: n, stands: [], track: sd.track});
        roads.get(n).stands.push(+sd.sDock.toFixed(1));
      }
      const loads = [...roads.values()].map(r2 => {
        const s = r2.stands.slice().sort((u, v) => u - v);
        const gaps = [];
        for (let i = 1; i < s.length; i++) gaps.push(+(s[i] - s[i - 1]).toFixed(1));
        return {road: r2.name, stands: s.length, gaps,
                minGap: gaps.length ? Math.min(...gaps) : null,
                lengthM: +(r2.track.length || 0).toFixed(1),
                renderFrom: +(r2.track.renderFrom || 0).toFixed(1),
                renderTo: +(r2.track.renderTo || 0).toFixed(1),
                paved: r2.track.paved ? r2.track.paved.map(v => +v.toFixed(1)) : null};
      }).sort((u, v) => u.road.localeCompare(v.road));
      /* every station must still have a route — soak's `unreachable` */
      let routed = 0;
      for (const st of plan.stations) { try { if (rail.route?.(st.uid)) routed++; } catch { /**/ } }
      return {
        scaleX: +k.toFixed(3), metresPerBayX: +(44 * k).toFixed(1),
        standGapM: loads.length ? Math.max(...loads.map(l => l.minGap ?? 0)) : null,
        xGapM: xGap,
        branches: (rail.branches || []).length,
        rows: new Set(plan.stations.map(s => Math.round(s.z / 8))).size,
        exceptions: (rail.exceptions || []).map(e => `${e.track || e.where || '?'}:${e.want ?? ''}/${e.got ?? ''}`),
        deadTracks: (rail.deadTracks || []).length,
        routed, stations: plan.stations.length,
        siteRadial: Math.round(terrain.siteRadial || 0),
        islandR: Math.round(terrain.islandR || 0),
        siteWidthM: Math.round(plan.bounds.maxX - plan.bounds.minX),
        hubZ: +plan.hub.z.toFixed(1),
        loads,
      };
    }, {k, pos, fleet: FLEET});
    r.layout = L;
    rows.push(r);
    console.error(`layout ${L}  k=${r.scaleX}  mpbX=${r.metresPerBayX}  gap=${r.standGapM}  branches=${r.branches}  routed=${r.routed}/${r.stations}  islandR=${r.islandR}`);
  }
}
console.log(JSON.stringify({rows, pageErrors: errs.slice(0, 6)}, null, 1));
await b.close();
