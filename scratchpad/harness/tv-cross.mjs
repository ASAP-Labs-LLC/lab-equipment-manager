/* tv-cross.mjs — where does one row's circuit come within 5 m of another's, and
 * can the interlocking SEE that place?
 *
 * soak.mjs failed `collision` at 4.1 m across `branch0/x1` and `branch1`. Two
 * circuits passing close is not by itself a fault — the whole railway shares a
 * trunk — it is a fault only where nothing stops two workings being there at
 * once. So for every pair of circuits (variants included) this finds the closest
 * approach, says which TRACK each is on there, and whether the two points fall
 * in the same block id. Same id: the interlocking can refuse it. Different ids
 * on different tracks: it cannot, and the geometry has to move.
 *
 *   node tv-cross.mjs [--layout 4]      (soak.mjs's own layout numbering)
 */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const LAYOUT = parseInt(arg('layout', '4'), 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
/* soak.mjs:layouts(), verbatim, so the layout number means the same thing. */
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
    machine_uid: uid, title, status, pos: pos[i], reason: 'tvcross',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  }))), [FLEET, POS]);
await p.waitForTimeout(3500);

const out = await p.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  /* one record per distinct circuit on the railway, variants included */
  const circuits = [];
  const seenLine = new Set();
  for (const [uid, sd] of rail.sidings) {
    let cyc = null; try { cyc = rail.cycle(uid); } catch { continue; }
    if (!cyc) continue;
    for (const v of (cyc.variants || [cyc])) {
      if (!v?.route || seenLine.has(v.line)) continue;
      seenLine.add(v.line);
      let spans = [];
      try { spans = rail.blockSpans(v) || []; } catch {}
      circuits.push({line: v.line, uid, route: v.route, segs: v.segments || [], spans});
    }
  }
  const trackAt = (c, s) => {
    for (const seg of c.segs) {
      const a = c.route.acc[seg.from], b = c.route.acc[seg.to];
      if (s >= Math.min(a, b) - 0.5 && s <= Math.max(a, b) + 0.5) return seg.track;
    }
    return '?';
  };
  const blockAt = (c, s) => {
    for (const sp of c.spans) if (s >= sp.a && s <= sp.b) return sp.id;
    return null;
  };
  const res = [];
  for (let i = 0; i < circuits.length; i++) {
    for (let j = i + 1; j < circuits.length; j++) {
      const A = circuits[i], B = circuits[j];
      let best = Infinity, sa = 0, sb = 0;
      for (let x = 0; x < A.route.length; x += 2) {
        const pa = A.route.pointAtDistance(x);
        for (let y = 0; y < B.route.length; y += 2) {
          const pb = B.route.pointAtDistance(y);
          const d = Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z);
          if (d < best) { best = d; sa = x; sb = y; }
        }
      }
      /* the closest approach where they are NOT on the same track — sharing a
       * track is the ordinary case and the block table handles it */
      let fBest = Infinity, fa = 0, fb = 0;
      for (let x = 0; x < A.route.length; x += 2) {
        const ta = trackAt(A, x);
        const pa = A.route.pointAtDistance(x);
        for (let y = 0; y < B.route.length; y += 2) {
          if (trackAt(B, y) === ta) continue;
          const pb = B.route.pointAtDistance(y);
          const d = Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z);
          if (d < fBest) { fBest = d; fa = x; fb = y; }
        }
      }
      res.push({a: A.line, b: B.line,
                same: {m: +best.toFixed(2), aTrack: trackAt(A, sa), bTrack: trackAt(B, sb),
                       aBlock: blockAt(A, sa), bBlock: blockAt(B, sb)},
                diff: {m: +fBest.toFixed(2), aS: +fa.toFixed(0), bS: +fb.toFixed(0),
                       aTrack: trackAt(A, fa), bTrack: trackAt(B, fb),
                       aBlock: blockAt(A, fa), bBlock: blockAt(B, fb)}});
    }
  }
  return {lines: circuits.map(c => c.line), res};
});

console.log('circuits:', out.lines.join('  '));
for (const r of out.res) {
  const d = r.diff;
  const flag = d.m < 12 ? (d.aBlock && d.aBlock === d.bBlock ? '  (same block — interlocked)'
                                                            : '  *** DIFFERENT BLOCKS ***') : '';
  console.log(`${r.a}  vs  ${r.b}`);
  console.log(`   closest on DIFFERENT tracks: ${d.m} m  ` +
              `${d.aTrack}@${d.aS} [${d.aBlock}]  vs  ${d.bTrack}@${d.bS} [${d.bBlock}]${flag}`);
}
await b.close();
