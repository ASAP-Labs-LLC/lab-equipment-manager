/* pwstands.mjs — where every working actually stands to discharge, in world
 * coordinates, and which roads the terminal uses. The audit that found "one
 * discharge stand for the whole railway" did it by resolving every circuit's
 * `terminal` to a point; this keeps that check in the loop. Owned by rail. */
import {chromium} from 'playwright';
const BAY = 2.05;
const FLEET = ['multitek-ns', 'multitek-s', 'optimpp-1', 'optimpp-2',
               'pac-flash-1', 'pac-flash-2', 'koehler-cp'];
const LAYOUTS = {
  real: [[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]],
  rank: Array.from({length: 7}, (_, i) => [i * BAY, 0]),
  file: Array.from({length: 7}, (_, i) => [0, i * BAY]),
  grid: Array.from({length: 7}, (_, i) => [(i % 3) * BAY, ((i / 3) | 0) * BAY]),
};
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 800, height: 500}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=rail,trains&time=13&hud=0',
             {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(1500);
for (const [name, pos] of Object.entries(LAYOUTS)) {
  await p.evaluate(([f, ps]) => window.__lemWorld.setMachines(f.map((uid, i) => ({
    machine_uid: uid, title: uid, status: 'GREEN', pos: ps[i], reason: 'audit',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running', effective_specs: [],
    qc_targets: [], maintenance: []}))), [FLEET, pos]);
  await p.waitForTimeout(2200);
  const r = await p.evaluate(() => {
    const w = window.__lemWorld, rail = w.subsystems.get('rail');
    const seen = new Map();
    for (const s of w.plan.stations) {
      const c = rail.cycle(s.uid);
      if (!c) continue;
      if (seen.has(c.line)) continue;
      const pt = c.route.pointAtDistance(c.terminal, new (c.route.points[0].constructor)());
      seen.set(c.line, {line: c.line, x: +pt.x.toFixed(1), z: +pt.z.toFixed(1),
                        roads: [...new Set(c.segments.map(g => g.track))]});
    }
    const pts = [...seen.values()];
    /* how many DISTINCT stands, at 20m resolution */
    const keys = new Set(pts.map(q => Math.round(q.x / 20) + ':' + Math.round(q.z / 20)));
    const one = rail.oneWayReport();
    return {stands: keys.size, rows: pts.length, pts,
            usesLoop: pts.some(q => q.roads.includes('terminal.loop')),
            oneWay: one.oneWay ?? (one.conflicts.length === 0 && one.overlaps.length === 0),
            open: one.open.length, conflicts: one.conflicts.length,
            overlaps: one.overlaps.length,
            routed: w.plan.stations.filter(s => !!rail.cycle(s.uid)).length};
  });
  console.log(name.padEnd(5), 'rows', r.rows, 'distinct stands', r.stands,
              'loop used', r.usesLoop, 'routed', r.routed,
              'oneWay', r.oneWay, 'open', r.open, 'conflicts', r.conflicts,
              'overlaps', r.overlaps);
  for (const q of r.pts) console.log('     ', q.line, '@', q.x, q.z, q.roads.join(','));
}
if (errs.length) console.log('PAGE ERRORS', errs);
await b.close();
