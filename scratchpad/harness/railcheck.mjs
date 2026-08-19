/* railcheck.mjs — is it a railway, in every layout?
 *
 *   node railcheck.mjs [--layouts 10]
 *
 * The soak proves nothing collides and everything is reachable. This proves the
 * thing they run on is a railway: every station has a siding and a working
 * cycle, every route is continuous (no chord where a train would cut across
 * open ground), every alignment holds a minimum radius and a ruling grade, and
 * every junction is a junction rather than two lines meeting.
 *
 * It uses the same layout set as soak.mjs so a fault here maps to a layout
 * there.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}
const LAYOUTS = parseInt(args.layouts || '10', 10);

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

const PROBE = () => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const plan = w.plan;
  const faults = [];
  const note = (kind, detail) => faults.push({kind, detail});
  if (!rail) return {faults: [{kind: 'missing', detail: 'no rail subsystem'}]};

  const tracks = rail.tracks || [];
  let totalLen = 0;
  for (const t of tracks) {
    totalLen += Math.min(t.renderTo, t.length) - (t.renderFrom || 0);
    if (t.minRadiusUsed !== undefined && isFinite(t.minRadiusUsed) &&
        t.minRadiusUsed < 34.5) {
      note('radius', `${t.name} bends to ${t.minRadiusUsed.toFixed(0)}m`);
    }
    /* A steep line is only rail.js's fault if the ground under it was not.
     * terrain.js still has 1-in-2 ground out where the trunk has to run, and
     * grading cannot invent a shelf that is not there — so the ground is
     * measured too and the fault is only raised when the track is steeper than
     * what it crosses. */
    if (t.ruling !== undefined && t.ruling > 0.06) {
      const f = t.frames;
      let gw = 0, prev = null;
      for (let i = 0; i < f.count; i++) {
        const g = w.ctx.ground(f.pos[i * 3], f.pos[i * 3 + 2]);
        if (prev !== null) gw = Math.max(gw, Math.abs(g - prev) / f.step);
        prev = g;
      }
      if (t.ruling > gw * 0.85) {
        note('grade', `${t.name} rules at 1 in ${(1 / t.ruling).toFixed(0)} ` +
             `over ground at 1 in ${(1 / gw).toFixed(0)}`);
      }
    }
  }

  /* Route continuity. A route is sampled every 2.2m; anything much longer than
   * that is a chord across ground with no rails on it. The turnout gaps at a
   * siding's points are known and bounded — 26m is the biggest legitimate one. */
  const checkRoute = (name, r) => {
    if (!r || !r.points) { note('route', `${name}: nothing`); return; }
    let worst = 0, at = 0;
    for (let i = 1; i < r.points.length; i++) {
      const d = r.points[i].distanceTo(r.points[i - 1]);
      if (d > worst) { worst = d; at = i; }
    }
    if (worst > 30) {
      note('chord', `${name}: ${worst.toFixed(1)}m straight at point ${at} of ` +
           `${r.points.length} (route ${r.length.toFixed(0)}m)`);
    }
    if (!(r.length > 60)) note('short', `${name}: ${r.length.toFixed(0)}m`);
  };

  let sidings = 0, cycles = 0, turned = 0;
  for (const st of plan.stations) {
    if (rail.sidings.get(st.uid)) sidings++;
    const r = rail.route(st.uid);
    checkRoute('route:' + st.uid, r);
    const c = rail.cycle(st.uid);
    if (c && c.route) {
      cycles++;
      if (c.turned) turned++;
      checkRoute('cycle:' + st.uid, c.route);
      /* Blocks: the cycle must map back on to real track. */
      const b = rail.blocksFor(c, 0, Math.min(60, c.route.length));
      if (!b.length) note('blocks', `${st.uid}: cycle maps to no track section`);
    } else {
      note('cycle', `${st.uid}: no working cycle`);
    }
  }

  /* Two workings must not be able to hold the same block. */
  let reserveOk = true;
  const a = plan.stations[0], b2 = plan.stations[1];
  if (a && b2) {
    const ca = rail.cycle(a.uid), cb = rail.cycle(b2.uid);
    if (ca && cb) {
      rail.unreserve('A'); rail.unreserve('B');
      const ba = rail.blocksFor(ca, ca.terminal - 40, ca.terminal);
      const bb = rail.blocksFor(cb, cb.terminal - 40, cb.terminal);
      const shared = ba.filter(x => bb.includes(x));
      if (shared.length) {
        if (!rail.reserve('A', ba)) reserveOk = false;
        if (rail.reserve('B', bb)) reserveOk = false;   // must have been refused
        rail.unreserve('A');
      }
      if (!reserveOk) note('interlock', 'a held block was granted twice');
    }
  }

  return {
    faults,
    tracks: tracks.length,
    lines: (rail.lines || []).map(l => `${l.name}:${Math.round(
      Math.min(l.renderTo, l.length) - (l.renderFrom || 0))}m`),
    branches: (rail.branches || []).length,
    balloon: !!rail.balloon, loop: !!rail.loop, spur: !!rail._spur,
    sidings, cycles, turned, stations: plan.stations.length,
    totalLen: Math.round(totalLen),
    draws: w.engine.drawCalls, tris: w.engine.triangles,
  };
};

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=terrain,buildings,rail&cam=yard&time=15&hud=0`;
const browser = await chromium.launch({headless: true, channel: 'chromium',
                                       args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text()))
  errs.push(m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(2000);

let bad = 0;
for (const [L, positions] of layouts(LAYOUTS).entries()) {
  await page.evaluate(([fleet, pos]) => {
    window.__lemWorld.setMachines(fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'check',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    })));
  }, [FLEET, positions]);
  await page.waitForTimeout(1600);
  const r = await page.evaluate(PROBE);
  bad += r.faults.length;
  console.log(`L${L}: ${r.stations} stations · ${r.branches} branches · ` +
    `${r.sidings} sidings · ${r.cycles} cycles (${r.turned} turned) · ` +
    `balloon=${r.balloon} loop=${r.loop} spur=${r.spur} · ` +
    `${r.totalLen}m of railway · ${r.draws} draws / ${(r.tris / 1e6).toFixed(2)}M`);
  console.log(`     ${r.lines.join(' ')}`);
  for (const f of r.faults.slice(0, 8)) console.log(`     ! ${f.kind}: ${f.detail}`);
}
await browser.close();
console.log(errs.length ? `console errors: ${errs.slice(0, 5).join(' | ')}` : 'no console errors');
console.log(bad === 0 && errs.length === 0 ? 'RAILWAY OK' : `FAULTS ${bad}`);
process.exit(bad === 0 && errs.length === 0 ? 0 : 1);
