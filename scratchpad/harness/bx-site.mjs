/* bx-site.mjs — did the benches land, and WHERE is the fall?
 *
 * Three things the island-wide probes cannot say:
 *   1. the design surface at each bench, against the schedule it was published
 *      with (a bench that is not at its own level has not been consumed);
 *   2. a z-transect of the finished ground through the middle of the site, so
 *      the riser is a number and not an intention;
 *   3. slope histogram + normal dispersion RESTRICTED TO THE SITE BLOCK, which
 *      is where the whole change is — tq-form averages over the whole island
 *      and the site is a fifth of it.
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const MODS = a.mods || 'terrain,buildings,rail,trains';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}&cam=far&time=9&hud=0&quality=ultra`,
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
const out = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const r2 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(2) : v;
  const sb = w.ctx.siteBenches;
  const SITE_Y = 3.0;
  const benches = (sb && sb.benches) || [];

  /* 1. every bench, at its own level? measured on the FINISHED ground */
  const at = benches.map(bb => {
    const g = [];
    for (let dx = -20; dx <= 20; dx += 10)
      for (let dz = -20; dz <= 20; dz += 10) g.push(t.heightAt(bb.cx + dx, bb.cz + dz));
    const mean = g.reduce((s, v) => s + v, 0) / g.length;
    return {id: bb.id, level: r2(bb.level), wantY: r2(SITE_Y + bb.level),
            designY: r2(t._designAt(bb.cx, bb.cz)),
            groundMeanY: r2(mean),
            groundSpread: r2(Math.max(...g) - Math.min(...g))};
  });

  /* 2. z-transects of the finished ground, at three x through the site */
  const trans = [];
  for (const x of [60, 175, 300]) {
    const row = [];
    for (let z = -260; z <= 150; z += 2) row.push(+t.heightAt(x, z).toFixed(3));
    /* the steepest 8 m window anywhere on the transect, and where */
    let best = 0, bz = 0;
    for (let i = 4; i < row.length - 4; i++) {
      const d = Math.abs(row[i + 2] - row[i - 2]) / 8;
      if (d > best) { best = d; bz = -260 + i * 2; }
    }
    trans.push({x, maxSlopePct: r2(best * 100),
                maxSlopeDeg: r2(Math.atan(best) * 180 / Math.PI), atZ: bz,
                z: row});
  }

  /* 3. the site block: slope histogram and normal dispersion, on an 8 m grid */
  const GRID = 8;
  const x0 = t.cx - 380, x1 = t.cx + 380, z0 = t.cz - 260, z1 = t.cz + 260;
  const NX = Math.round((x1 - x0) / GRID) + 1, NZ = Math.round((z1 - z0) / GRID) + 1;
  const h = new Float64Array(NX * NZ);
  for (let j = 0; j < NZ; j++)
    for (let i = 0; i < NX; i++) h[j * NX + i] = t._gradedHeight(x0 + i * GRID, z0 + j * GRID);
  const hist = new Array(10).fill(0);
  const nx = new Float64Array(NX * NZ), ny = new Float64Array(NX * NZ), nz = new Float64Array(NX * NZ);
  let sN = 0, sSum = 0;
  for (let j = 1; j < NZ - 1; j++) {
    for (let i = 1; i < NX - 1; i++) {
      const k = j * NX + i;
      const gx = (h[k + 1] - h[k - 1]) / (2 * GRID);
      const gz = (h[k + NX] - h[k - NX]) / (2 * GRID);
      const inv = 1 / Math.sqrt(gx * gx + gz * gz + 1);
      nx[k] = -gx * inv; ny[k] = inv; nz[k] = -gz * inv;
      const deg = Math.atan(Math.hypot(gx, gz)) * 180 / Math.PI;
      hist[Math.min(9, Math.floor(deg / 5))]++;
      sN++; sSum += deg;
    }
  }
  const W = 3;
  let dN = 0, dSum = 0, turn = 0;
  for (let j = W + 1; j < NZ - W - 1; j++) {
    for (let i = W + 1; i < NX - W - 1; i++) {
      const k = j * NX + i;
      if (ny[k] === 0) continue;
      let ax = 0, ay = 0, az = 0, c = 0;
      for (let q = -W; q <= W; q++) for (let r = -W; r <= W; r++) {
        const kk = k + q * NX + r;
        if (ny[kk] === 0) continue;
        ax += nx[kk]; ay += ny[kk]; az += nz[kk]; c++;
      }
      if (c < 12) continue;
      const L = Math.sqrt(ax * ax + ay * ay + az * az);
      dN++; dSum += 1 - L / c;
      if ((nx[k] * ax + ny[k] * ay + nz[k] * az) / L < Math.cos(25 * Math.PI / 180)) turn++;
    }
  }

  /* 4. and what the key light would do to it: N.L at time=9 */
  const S = [0.754, 0.404, 0.518];
  let lit = 0, dark = 0, nL = 0;
  const lHist = new Array(10).fill(0);
  for (let k = 0; k < NX * NZ; k++) {
    if (ny[k] === 0) continue;
    const d = Math.max(0, nx[k] * S[0] + ny[k] * S[1] + nz[k] * S[2]);
    nL++; lHist[Math.min(9, Math.floor(d * 10))]++;
    if (d > 0.55) lit++; if (d < 0.25) dark++;
  }

  return {
    terraceBuilt: !!t._terrace,
    risers: t._terrace ? t._terrace.risers.map(r => ({
      rise: r2(r.rise), run: r2(r.run), z0: r2(r.z0), filletM: r2(r.k),
      faceDeg: r2(Math.atan(Math.abs(r.rise) / r.run) * 180 / Math.PI)})) : null,
    bands: t._terrace ? t._terrace.bands.map(bb => ({id: bb.id, level: r2(bb.level),
      z: [r2(bb.z0), r2(bb.z1)], x: [r2(bb.x0), r2(bb.x1)]})) : null,
    benchKeyMatches: !!(t._benchKey), benchPasses: t._benchPasses || 0,
    schedule: sb ? {scale: r2(sb.scale), expressedM: r2(sb.expressedM),
                    binding: sb.binding,
                    levels: benches.map(bb => ({id: bb.id, level: r2(bb.level)}))} : null,
    at,
    transects: trans.map(o => ({x: o.x, maxSlopePct: o.maxSlopePct,
                                maxSlopeDeg: o.maxSlopeDeg, atZ: o.atZ})),
    siteBlock: {
      cells: sN, meanSlopeDeg: r2(sSum / sN),
      slopeHist5deg: hist, dispersion: +(dSum / dN).toFixed(4),
      pctTurn: r2(100 * turn / dN),
      pctOver12deg: r2(100 * (hist.slice(3).reduce((s, v) => s + v, 0)) / sN),
    },
    keyLight: {nLHist: lHist, pctLit: r2(100 * lit / nL), pctDark: r2(100 * dark / nL)},
    profileAtX175: trans[1].z,
  };
});
out.pageErrors = errs.slice(0, 8);
const prof = out.profileAtX175; delete out.profileAtX175;
console.log(JSON.stringify(out, null, 1));
if (process.env.BX_PROFILE) {
  console.log('--- z profile at x=175, z from -260 step 2');
  console.log(prof.join(','));
}
await b.close();
