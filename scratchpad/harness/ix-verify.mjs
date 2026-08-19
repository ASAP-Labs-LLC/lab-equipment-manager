/* ix-verify.mjs — read the published bench schedule back off a live world, and
 * ablate the two things about it that could quietly be wrong.
 *
 *   1. DATUM INVARIANCE. `level` is claimed to be independent of the sampler's
 *      datum. Re-run the schedule with a constant added to every natural
 *      sample; every `level` must be identical to the last decimal.
 *   2. GRADE LEGALITY. Every published step must be inside the ruling gradient
 *      over its own published run.
 *
 * Also reports what the schedule would move, against what the design plane
 * moves today, over the same block.
 *
 *   node ix-verify.mjs [--mods terrain,buildings,rail,trains] [--layout 0]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const MODS = a.mods || 'terrain,buildings,rail,trains';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}&cam=top&time=13&hud=0&quality=ultra`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
const out = await p.evaluate(async () => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain'), rail = w.subsystems.get('rail');
  const sb = w.ctx.siteBenches;
  const mod = await import('/static/world/index.js');
  const r2 = v => (v === null || v === undefined || !isFinite(v)) ? v : +v.toFixed(2);
  /* ablation: same benches, natural samples shifted by +1000 m */
  const nat = sb.benches.map(bb => bb.naturalM);
  const A = mod.benchSchedule(sb.benches, nat);
  const B = mod.benchSchedule(sb.benches, nat.map(v => v + 1000));
  const invariance = A.level.map((v, i) => +(v - B.level[i]).toFixed(9));
  /* what the plane does today, over the same probe points */
  let planeMoved = 0, benchMoved = 0, cells = 0;
  for (const bb of sb.benches) {
    for (const [x, z] of mod.benchProbePoints(bb.probe)) {
      const n = t._smoothBase(x, z);
      const plane = t._designAt(x, z) - t.yShift;
      cells++;
      planeMoved += Math.abs(plane - n);
      benchMoved += Math.abs(bb.levelAbsolute - n);
    }
  }
  /* the true rail run from each station to the terminal, so the Manhattan
   * lower bound the schedule uses can be quoted against it */
  const routes = [];
  for (const st of w.plan.stations) {
    try {
      const r = rail && rail.route ? rail.route(st.uid) : null;
      const len = r && (r.totalLength || r.len || (r.getLength && r.getLength()));
      if (len) routes.push({uid: st.uid, bench: st.bench, railRunM: Math.round(len),
                            manhattanM: Math.round(Math.abs(st.x - w.plan.hub.x)
                                                 + Math.abs(st.z - w.plan.hub.z))});
    } catch { /* not routed */ }
  }
  return {
    sampler: sb.sampler, version: sb.version, grouping: sb.grouping,
    scale: r2(sb.scale), binding: sb.binding,
    naturalSpanM: r2(sb.naturalSpanM), expressedM: r2(sb.expressedM),
    maxCutM: r2(sb.maxCutM), maxFillM: r2(sb.maxFillM),
    datumAbsolute: r2(sb.datumAbsolute), batter: sb.batter,
    benches: sb.benches.map(bb => ({id: bb.id, n: bb.n, cx: r2(bb.cx), cz: r2(bb.cz),
      naturalM: r2(bb.naturalM), levelAbsolute: r2(bb.levelAbsolute),
      level: r2(bb.level), moveM: r2(bb.moveM), uids: bb.uids})),
    steps: sb.steps.map(s => ({...s, riseM: r2(s.riseM), runM: r2(s.runM),
                               gradePct: r2(s.gradePct)})),
    stationBenches: w.plan.stations.map(s => ({uid: s.uid, row: s.row, bench: s.bench,
                                               x: r2(s.x), z: r2(s.z)})),
    ablation: {datumInvariance: invariance,
               invariant: invariance.every(v => Math.abs(v) < 1e-6),
               scaleSame: +(A.scale - B.scale).toFixed(9)},
    earthwork: {probeCells: cells,
                planeMeanAbsMoveM: r2(planeMoved / cells),
                benchMeanAbsMoveM: r2(benchMoved / cells)},
    routes,
    /* the batter a step would be built with */
    batters: sb.steps.filter(s => Math.abs(s.riseM) > 0.05).map(s => ({
      step: `${s.from}->${s.to}`, riseM: r2(s.riseM),
      runM: r2(Math.min(sb.batter.maxRunM,
              Math.max(sb.batter.minRunM, Math.abs(s.riseM) / sb.batter.grade))),
      faceDeg: r2(Math.atan(Math.abs(s.riseM) / Math.max(sb.batter.minRunM,
                  Math.abs(s.riseM) / sb.batter.grade)) * 180 / Math.PI)})),
  };
});
out.pageErrors = errs.slice(0, 8);
console.log(JSON.stringify(out, null, 1));
await b.close();
