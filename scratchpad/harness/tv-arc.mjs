/* tv-arc.mjs — is a variant's arc length the SAME COORDINATE as the full
 * circuit's, AFTER trains.js has resampled both?
 *
 * rail.js proved its own arrays byte-identical over the shared prefix
 * (pl-pass.mjs, 0 mm). trains.js does not run on rail's arrays: `sampleRoute`
 * re-splines the polyline through a centripetal Catmull-Rom and re-samples it
 * at n equal FRACTIONS of the whole curve, where n = ceil(len/3). Two routes of
 * different total length therefore get different sample spacing, and nothing
 * about rail's byte-identity survives that automatically.
 *
 * `trains.js:_berth` — the rule that keeps two workings on one road apart — is
 * a subtraction of two arc lengths. If a consist on a variant and a consist on
 * the full circuit are to be compared at all, the same physical point has to
 * have the same arc length on both AFTER resampling. This measures that, by
 * reproducing sampleRoute exactly in the page and walking rail's own shared
 * prefix points through both.
 *
 *   node tv-arc.mjs [--layout 0|1]
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
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail,trains&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);
if (LAYOUT === 1) {
  await p.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvarc',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await p.waitForTimeout(2800);
}

const out = await p.evaluate(async () => {
  const THREE = await import('three');
  const rail = window.__lemWorld.subsystems.get('rail');

  /* trains.js:sampleRoute, array branch, verbatim. */
  const sampleRoute = (raw, step = 3.0) => {
    let pts = raw.points.map(q => new THREE.Vector3(q.x, q.y, q.z));
    const spline = new THREE.CatmullRomCurve3(pts, false, 'centripetal', 0.5);
    const len = spline.getLength();
    const n = Math.max(8, Math.min(600, Math.ceil(len / step)));
    pts = [];
    for (let i = 0; i <= n; i++) pts.push(spline.getPointAt(i / n));
    const C = [0];
    for (let i = 1; i < pts.length; i++) C.push(C[i - 1] + pts[i].distanceTo(pts[i - 1]));
    return {pts, C, len: C[C.length - 1], n: pts.length};
  };
  /* Arc length, on a sampled route, of the point nearest q. */
  const arcOf = (r, q) => {
    let best = 0, bd = Infinity;
    for (let i = 1; i < r.n; i++) {
      const a = r.pts[i - 1], b = r.pts[i];
      const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
      const L2 = dx * dx + dy * dy + dz * dz || 1;
      let t = ((q.x - a.x) * dx + (q.y - a.y) * dy + (q.z - a.z) * dz) / L2;
      t = Math.max(0, Math.min(1, t));
      const px = a.x + dx * t, py = a.y + dy * t, pz = a.z + dz * t;
      const d = (q.x - px) ** 2 + (q.y - py) ** 2 + (q.z - pz) ** 2;
      if (d < bd) { bd = d; best = r.C[i - 1] + Math.sqrt(L2) * t; }
    }
    return {s: best, off: Math.sqrt(bd)};
  };

  const res = [];
  const seen = new Set();
  for (const [uid, sd] of rail.sidings) {
    if (seen.has(sd.track.name)) continue;
    let cyc = null; try { cyc = rail.cycle(uid); } catch { continue; }
    if (!cyc?.variants || cyc.variants.length < 2) continue;
    seen.add(sd.track.name);
    const full = cyc.variants[cyc.variants.length - 1];
    const sFull = sampleRoute(full.route);
    for (let vi = 0; vi < cyc.variants.length - 1; vi++) {
      const v = cyc.variants[vi];
      const sV = sampleRoute(v.route);
      /* rail's own shared prefix: identical points, index for index */
      const A = v.route.points, B = full.route.points;
      let shared = 0;
      for (let i = 0; i < Math.min(A.length, B.length); i++) {
        if (A[i].distanceTo(B[i]) > 0.001) break;
        shared = i;
      }
      let worst = 0, worstAt = 0, worstOff = 0;
      for (let i = 0; i <= shared; i += 3) {
        const q = A[i];
        const a = arcOf(sV, q), c = arcOf(sFull, q);
        const d = Math.abs(a.s - c.s);
        if (d > worst) { worst = d; worstAt = full.route.acc[i];
                         worstOff = Math.max(a.off, c.off); }
      }
      res.push({
        road: sd.track.name, variant: v.line,
        railSharedArc: +full.route.acc[shared].toFixed(1),
        railLen: +full.route.length.toFixed(1), varRailLen: +v.route.length.toFixed(1),
        sampledFullLen: +sFull.len.toFixed(1), sampledVarLen: +sV.len.toFixed(1),
        worstArcDisagreementM: +worst.toFixed(6), atRailArc: +worstAt.toFixed(1),
        worstProjectionOffsetM: +worstOff.toFixed(3),
        docksVar: v.docks.map(d => `${d.uid}@${d.s.toFixed(0)}`),
        docksFull: full.docks.map(d => `${d.uid}@${d.s.toFixed(0)}`),
      });
    }
  }
  return res;
});

for (const r of out) {
  console.log(`${r.road}  ${r.variant}`);
  console.log(`  rail: shared prefix ${r.railSharedArc} m; variant ${r.varRailLen} m, full ${r.railLen} m`);
  console.log(`  trains sampleRoute: variant ${r.sampledVarLen} m, full ${r.sampledFullLen} m`);
  console.log(`  WORST arc-length disagreement over the shared prefix: ${r.worstArcDisagreementM} m (at rail arc ${r.atRailArc}, projection offset ${r.worstProjectionOffsetM} m)`);
  console.log(`  docks on variant: ${r.docksVar.join(' ')}`);
  console.log(`  docks on full:    ${r.docksFull.join(' ')}\n`);
}
if (!out.length) console.log('no road on this layout publishes a variant');
await b.close();
