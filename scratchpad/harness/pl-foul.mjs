/* pl-foul.mjs — the one number the passing loop lives or dies on.
 *
 * A train leaving stand B through a mid-rank turnout at s_M runs alongside the
 * train standing at stand A while the diverging road is still closing on the
 * branch. soak.mjs calls two bodies inside 5 m a collision. So: build the lead
 * off the REAL loading road with makeLead's own arithmetic, run a body of
 * length L down it, and measure the true minimum distance to a body of length L
 * parked at the next stand. Continuously, not at soak's three sample points.
 *
 *   node pl-foul.mjs [--road load:0] [--rake 84]
 *
 * Sweeps frog number and the siting offset (tip = stand + eps).  */
import {chromium} from 'playwright';
const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : d; };
const ROAD = arg('road', 'load:0');
const RAKE = parseFloat(arg('rake', '84'));

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 520}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,rail&cam=top&time=13&hud=0&quality=ultra', {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(2000);

const res = await p.evaluate(([ROAD, RAKE]) => {
  const GAUGE = 1.435;
  const rail = window.__lemWorld.subsystems.get('rail');
  let sd = null;
  for (const [, s] of rail.sidings) if (s.track.name === ROAD) { sd = s; break; }
  if (!sd) return {err: 'no such road'};
  const road = sd.track, line = sd.line;
  const ds = (sd.row?.list || [])
    .map(st => road.nearest(st.x, sd.dockZ).s).sort((a, b) => a - b);

  /* makeLead's own curve, from the road's own frame at the tip. */
  const lead = (parent, s, N, hand, pdir) => {
    const f = parent.at(s);
    let tx = f.tangent.x, tz = f.tangent.z;
    const tl = Math.hypot(tx, tz); tx /= tl; tz /= tl;
    const ux = tx * pdir, uz = tz * pdir, rx = -uz, rz = ux;
    const R = 2 * GAUGE * N * N;
    const len = Math.sqrt(2 * R * GAUGE) + 5.4;
    const pts = [];
    const n = Math.max(24, Math.ceil(len / 0.5));
    for (let i = 0; i <= n; i++) {
      const t = (len * i) / n, phi = t / R;
      const u = R * Math.sin(phi), v = R * (1 - Math.cos(phi));
      pts.push({x: f.position.x + ux * u + rx * hand * v,
                y: f.position.y,
                z: f.position.z + uz * u + rz * hand * v});
    }
    const phi = len / R;
    return {pts, R, len,
            tan: {x: ux * Math.cos(phi) + rx * hand * Math.sin(phi),
                  z: uz * Math.cos(phi) + rz * hand * Math.sin(phi)}};
  };

  /* Which hand takes the road toward the branch, and which way is forward. */
  const fMid = road.at(ds[0]);
  const nearLine = line.nearest(fMid.position.x, fMid.position.z);
  const toBranch = {x: line.at(nearLine.s).position.x - fMid.position.x,
                    z: line.at(nearLine.s).position.z - fMid.position.z};
  const t0 = road.at(ds[0]).tangent;
  const hl = Math.hypot(t0.x, t0.z);
  const ux = t0.x / hl, uz = t0.z / hl, rx = -uz, rz = ux;
  const hand = (toBranch.x * rx + toBranch.z * rz) >= 0 ? 1 : -1;
  const spacing = Math.hypot(toBranch.x, toBranch.z);

  /* Points of a body of length L parked with its head at road arc sHead. */
  const parked = (sHead, L) => {
    const out = [];
    for (let d = 0; d <= L; d += 1.0) {
      const q = road.at(Math.max(0, sHead - d));
      out.push({x: q.position.x, y: q.position.y, z: q.position.z});
    }
    return out;
  };

  const out = {road: ROAD, stands: ds.map(v => +v.toFixed(1)),
               spacing: +spacing.toFixed(2), hand, rows: []};

  for (const N of [8, 6, 5.5, 5, 4.5]) {
    for (const eps of [2, 3, 4, 6, 8, 12]) {
      for (let i = 0; i + 1 < ds.length; i++) {
        const sM = ds[i] + eps;
        const L = lead(road, sM, N, hand, 1);
        /* The departure path: the road up to the tip, then the lead, then the
         * straight on the lead's own tangent until it has closed the offset to
         * the branch. That is the whole crossover. */
        const path = [];
        for (let s = Math.max(0, sM - RAKE - 20); s <= sM; s += 1.0) {
          const q = road.at(s);
          path.push({x: q.position.x, y: q.position.y, z: q.position.z});
        }
        for (const q of L.pts) path.push(q);
        const e = L.pts[L.pts.length - 1];
        const run = Math.max(0, (spacing - Math.hypot(e.x - road.at(sM).position.x,
                                                      e.z - road.at(sM).position.z) * 0) );
        for (let t = 1; t <= 120; t += 1.0) {
          path.push({x: e.x + L.tan.x * t, y: e.y, z: e.z + L.tan.z * t});
        }
        /* arc length along the path */
        const acc = [0];
        for (let k = 1; k < path.length; k++) {
          acc.push(acc[k - 1] + Math.hypot(path[k].x - path[k - 1].x,
                                           path[k].z - path[k - 1].z));
        }
        const A = parked(ds[i + 1], RAKE);
        /* Slide the departing body head from the tip onward. */
        let worst = Infinity, worstAt = 0;
        const headStart = acc[Math.max(0, Math.round(RAKE + 20))];
        for (let h = headStart; h < acc[acc.length - 1]; h += 0.5) {
          /* body points of the departing consist, 1 m apart */
          let d = Infinity;
          for (let o = 0; o <= RAKE; o += 2.0) {
            const target = h - o;
            if (target < 0) break;
            let k = 1;
            while (k < acc.length - 1 && acc[k] < target) k++;
            const q = path[k];
            for (const a of A) {
              const dd = Math.hypot(q.x - a.x, q.z - a.z, (q.y || 0) - (a.y || 0));
              if (dd < d) d = dd;
            }
          }
          if (d < worst) { worst = d; worstAt = h; }
        }
        out.rows.push({N, eps, gap: +(ds[i + 1] - ds[i]).toFixed(1),
                       i, sM: +sM.toFixed(1),
                       minDist: +worst.toFixed(2)});
      }
    }
  }
  return out;
}, [ROAD, RAKE]);

if (res.err) { console.log(res.err); await b.close(); process.exit(1); }
console.log(`road ${res.road}  stands ${res.stands.join(' ')}  ` +
            `road-to-branch spacing ${res.spacing} m  rake ${RAKE} m`);
console.log('min body-to-body distance to the train standing at the next stand');
console.log('(soak.mjs calls anything under 5.00 a collision)\n');
const byN = new Map();
for (const r of res.rows) {
  if (!byN.has(r.N)) byN.set(r.N, []);
  byN.get(r.N).push(r);
}
for (const [N, rows] of byN) {
  const R = 2 * 1.435 * N * N;
  console.log(`1:${N}  (R = ${R.toFixed(1)} m)`);
  const eps = [...new Set(rows.map(r => r.eps))];
  console.log('   eps   ' + eps.map(e => String(e).padStart(7)).join(''));
  const gaps = [...new Set(rows.map(r => `${r.i}|${r.gap}`))];
  for (const g of gaps) {
    const [i, gap] = g.split('|');
    const cells = eps.map(e => {
      const r = rows.find(x => x.eps === e && String(x.i) === i);
      return (r ? r.minDist.toFixed(2) : '  -  ').padStart(7);
    });
    console.log(`  gap ${gap}` + cells.join(''));
  }
  console.log('');
}
await b.close();
