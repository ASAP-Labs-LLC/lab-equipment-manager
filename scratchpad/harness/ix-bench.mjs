/* ix-bench.mjs — what the design plane buries, per row, and what a bench plan
 * would express instead.
 *
 * Reads the LIVE terrain: `_smoothBase` is the exact surface `_fitDesignPlane`
 * is fitted to, so a level derived from it is directly comparable to the plane.
 * Note `_smoothBase` is UNSHIFTED (the plane is fitted before `yShift` is
 * known) while `heightAt`/`_designAt` are shifted — so everything here is also
 * reported as an OFFSET from the sample mean, which is shift-invariant and is
 * the form the contract publishes.
 *
 *   node ix-bench.mjs [--mods terrain] [--grid 10]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const MODS = a.mods || 'terrain';
const GRID = +(a.grid || 10);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}&cam=wide&time=9&hud=0&quality=ultra`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
console.log(JSON.stringify(await p.evaluate((GRID) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const plan = w.plan || w.ctx.plan;
  const st = plan.stations, hub = plan.hub;
  const nat = (x, z) => t._smoothBase(x, z);
  const rows = new Map();
  for (const s of st) {
    const k = Math.round(s.z / 8);
    if (!rows.has(k)) rows.set(k, []);
    rows.get(k).push(s);
  }
  const pts = st.map(s => ({uid: s.uid, x: s.x, z: s.z, row: Math.round(s.z / 8),
                            nat: +nat(s.x, s.z).toFixed(2),
                            design: +t._designAt(s.x, s.z).toFixed(2),
                            ground: +t.heightAt(s.x, s.z).toFixed(2)}));
  const hubPt = {x: hub.x, z: hub.z, nat: +nat(hub.x, hub.z).toFixed(2),
                 design: +t._designAt(hub.x, hub.z).toFixed(2),
                 ground: +t.heightAt(hub.x, hub.z).toFixed(2)};
  /* the block the `yard` feature covers: station bbox + 48 m */
  let nx = Infinity, xx = -Infinity, nz = Infinity, zz = -Infinity;
  for (const s of st) { nx = Math.min(nx, s.x); xx = Math.max(xx, s.x);
                        nz = Math.min(nz, s.z); zz = Math.max(zz, s.z); }
  const X0 = nx - 48, X1 = xx + 48, Z0 = nz - 48, Z1 = zz + 48;
  let cut = 0, fill = 0, flat = 0, n = 0, minN = 1e9, maxN = -1e9;
  let maxFill = 0, maxCut = 0, sumAbs = 0;
  const natVals = [];
  for (let x = X0; x <= X1; x += GRID) for (let z = Z0; z <= Z1; z += GRID) {
    const nv = nat(x, z), dv = t._designAt(x, z) - t.yShift;   // both unshifted
    n++; natVals.push(nv);
    minN = Math.min(minN, nv); maxN = Math.max(maxN, nv);
    const d = dv - nv; sumAbs += Math.abs(d);
    if (Math.abs(d) <= 0.4) flat++;
    else if (d > 0) { fill++; maxFill = Math.max(maxFill, d); }
    else { cut++; maxCut = Math.max(maxCut, -d); }
  }
  natVals.sort((p, q) => p - q);
  const q = f => +natVals[Math.min(natVals.length - 1, Math.floor(f * natVals.length))].toFixed(2);
  /* what one plane expresses across the block, vs what per-row levels would */
  const D = (x, z) => t._designAt(x, z) - t.yShift;
  const planeSpan = Math.max(D(X0, Z0), D(X1, Z0), D(X0, Z1), D(X1, Z1))
                  - Math.min(D(X0, Z0), D(X1, Z0), D(X0, Z1), D(X1, Z1));
  const rowOut = [];
  const allNat = pts.map(p2 => p2.nat);
  const mean = allNat.reduce((s2, v) => s2 + v, 0) / allNat.length;
  for (const [k, list] of [...rows.entries()].sort((u, v) => u[0] - v[0])) {
    /* the row's own natural level: median of `_smoothBase` over the row's
     * pads (each pad is hx/hz 27) */
    const vals = [];
    for (const s of list) for (let dx = -27; dx <= 27; dx += 9)
      for (let dz = -27; dz <= 27; dz += 9) vals.push(nat(s.x + dx, s.z + dz));
    vals.sort((u, v) => u - v);
    const med = vals[vals.length >> 1];
    rowOut.push({row: k, n: list.length,
                 z: +(list.reduce((s2, s3) => s2 + s3.z, 0) / list.length).toFixed(1),
                 minX: Math.min(...list.map(s2 => s2.x)),
                 maxX: Math.max(...list.map(s2 => s2.x)),
                 natMedian: +med.toFixed(2), offset: +(med - mean).toFixed(2),
                 stationNat: list.map(s2 => +nat(s2.x, s2.z).toFixed(2))});
  }
  return {
    yShift: +t.yShift.toFixed(2), design: {a: +t.design.a.toFixed(2),
      bx: +t.design.bx.toFixed(5), bz: +t.design.bz.toFixed(5)},
    gradePctX: +(t.design.bx * 100).toFixed(3), gradePctZ: +(t.design.bz * 100).toFixed(3),
    stations: pts, hub: hubPt,
    block: {X0, X1, Z0, Z1, cells: n, gridM: GRID,
            withinP4: +(100 * flat / n).toFixed(1), fill, cut,
            fillOverCut: +(fill / Math.max(1, cut)).toFixed(2),
            maxFillM: +maxFill.toFixed(1), maxCutM: +maxCut.toFixed(1),
            meanAbsMoveM: +(sumAbs / n).toFixed(2),
            naturalSpanM: +(maxN - minN).toFixed(1),
            naturalP5: q(0.05), naturalP50: q(0.5), naturalP95: q(0.95),
            planeExpressesM: +planeSpan.toFixed(1)},
    rows: rowOut,
    rowStepSpanM: +(Math.max(...rowOut.map(r => r.natMedian))
                  - Math.min(...rowOut.map(r => r.natMedian))).toFixed(1),
  };
}, GRID), null, 1));
await b.close();
