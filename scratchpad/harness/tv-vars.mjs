/* tv-vars.mjs — what does `rail.cycle(uid).variants` actually contain, PER BENCH?
 *
 * Written before trains.js consumes them, because the consumption has to decide
 * three things it cannot guess: whether every bench on a road publishes the same
 * set of variant lines, where each variant DIVERGES from the full lap (in rail's
 * own arc length, measured off the shared point prefix — not assumed from the
 * link's roadS), and whether `rail.blockSpans()` answers for a variant record at
 * all. A variant with no block table is a circuit the interlocking cannot see,
 * and this file's own `_authority` refuses to move a train on one.
 *
 *   node tv-vars.mjs [--layout 0|1]
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '0'), 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
const ONE_RANK = FLEET.map((_, i) => [i * 2.05, 0]);

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2500);
if (LAYOUT === 1) {
  await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvvars',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(3000);
}

const out = await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const plan = window.__lemWorld.plan;
  const res = [];
  const shared = (a, b) => {
    const A = a.points, B = b.points;
    let k = -1;
    for (let i = 0; i < Math.min(A.length, B.length); i++) {
      const d = Math.hypot(A[i].x - B[i].x, A[i].y - B[i].y, A[i].z - B[i].z);
      if (d > 0.001) break;
      k = i;
    }
    return k < 0 ? 0 : a.acc[k];
  };
  for (const st of plan.stations) {
    let cyc = null;
    try { cyc = rail.cycle(st.uid); } catch (e) { res.push({uid: st.uid, err: String(e)}); continue; }
    if (!cyc) { res.push({uid: st.uid, err: 'null cycle'}); continue; }
    const vs = cyc.variants || null;
    const full = vs ? vs[vs.length - 1] : cyc;
    const row = {
      uid: st.uid,
      base: {line: cyc.line, len: +cyc.route.length.toFixed(1),
             dockS: +(cyc.dockS ?? -1).toFixed(1),
             terminal: +(cyc.terminal ?? -1).toFixed(1),
             closed: !!cyc.closed, turned: !!cyc.turned,
             roadTrack: cyc.segments?.[0]?.track || null,
             docks: (cyc.docks || []).map(d => `${d.uid}@${d.s.toFixed(1)}`)},
      baseIsLastVariant: vs ? (cyc === full) : null,
      baseRouteIsLastVariantRoute: vs ? (cyc.route === full.route) : null,
      nVariants: vs ? vs.length : 0,
      variants: [],
    };
    for (const v of (vs || [])) {
      let spans = null, spanErr = null;
      try { spans = rail.blockSpans(v); } catch (e) { spanErr = String(e).slice(0, 90); }
      row.variants.push({
        line: v.line, len: +v.route.length.toFixed(1),
        dockS: +(v.dockS ?? -1).toFixed(1),
        terminal: +(v.terminal ?? -1).toFixed(1),
        closed: !!v.closed, turned: !!v.turned,
        roadTrack: v.segments?.[0]?.track || null,
        docks: (v.docks || []).map(d => `${d.uid}@${d.s.toFixed(1)}`),
        sharedWithFullTo: +shared(v.route, full.route).toFixed(1),
        nSpans: spans ? spans.length : null,
        spanErr,
        lastRoadSpan: spans && v.segments?.[0]?.track
          ? +Math.max(...spans.filter(s => s.id && s.id.slice(0, s.id.lastIndexOf('#')) === v.segments[0].track)
                            .map(s => s.b), 0).toFixed(1) : null,
        hasVariantsField: !!v.variants,
      });
    }
    res.push(row);
  }
  /* and are two benches on ONE road handed the SAME route object? */
  const byLine = new Map();
  for (const st of plan.stations) {
    let cyc = null; try { cyc = rail.cycle(st.uid); } catch { continue; }
    if (!cyc) continue;
    for (const v of (cyc.variants || [cyc])) {
      if (!byLine.has(v.line)) byLine.set(v.line, []);
      byLine.get(v.line).push({uid: st.uid, route: v.route});
    }
  }
  const identity = [];
  for (const [line, list] of byLine) {
    let same = true;
    for (let i = 1; i < list.length; i++) if (list[i].route !== list[0].route) same = false;
    identity.push({line, benches: list.length, oneRouteObject: same});
  }
  return {res, identity};
});

console.log(JSON.stringify(out, null, 1));
if (errs.length) console.log('ERRORS', errs.slice(0, 5));
await b.close();
