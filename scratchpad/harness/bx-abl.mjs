/* bx-abl.mjs — the benches, ablated in one page load.
 *
 * Everything is measured through `_gradedHeight`, so nulling `terrain._terrace`
 * really does put the site back on the single plane: `_designAt` reads it live
 * and the whole analytic surface follows. `heightAt` would NOT — it answers off
 * a baked Float32Array, which is the trap tq-form.mjs records.
 *
 * Two windows, because they answer different questions:
 *   siteBlock   760 x 520 m round the site — where the change is
 *   island      the whole land mass, the window tq-form/tq-relief use
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
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}&cam=far&time=9&hud=0&quality=ultra`,
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3000);
const out = await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const r2 = v => (typeof v === 'number' && isFinite(v)) ? +v.toFixed(2) : v;
  const GRID = 8, W = 3;
  const SUN9 = [0.754, 0.404, 0.518];      // sky.js celestial(9), normalised

  const window_ = (x0, x1, z0, z1, landOnly) => {
    const NX = Math.round((x1 - x0) / GRID) + 1, NZ = Math.round((z1 - z0) / GRID) + 1;
    return {x0, z0, NX, NZ, landOnly};
  };
  const measure = (W_) => {
    const {x0, z0, NX, NZ, landOnly} = W_;
    const h = new Float64Array(NX * NZ);
    const land = new Uint8Array(NX * NZ);
    for (let j = 0; j < NZ; j++)
      for (let i = 0; i < NX; i++) {
        const v = t._gradedHeight(x0 + i * GRID, z0 + j * GRID);
        h[j * NX + i] = v;
        land[j * NX + i] = (!landOnly || v > t.waterY + 0.2) ? 1 : 0;
      }
    const nx = new Float64Array(NX * NZ), ny = new Float64Array(NX * NZ),
          nz = new Float64Array(NX * NZ);
    const hist = new Array(10).fill(0);
    let sN = 0, sSum = 0;
    for (let j = 1; j < NZ - 1; j++)
      for (let i = 1; i < NX - 1; i++) {
        const k = j * NX + i;
        if (!land[k]) continue;
        const gx = (h[k + 1] - h[k - 1]) / (2 * GRID);
        const gz = (h[k + NX] - h[k - NX]) / (2 * GRID);
        const inv = 1 / Math.sqrt(gx * gx + gz * gz + 1);
        nx[k] = -gx * inv; ny[k] = inv; nz[k] = -gz * inv;
        const deg = Math.atan(Math.hypot(gx, gz)) * 180 / Math.PI;
        hist[Math.min(9, Math.floor(deg / 5))]++;
        sN++; sSum += deg;
      }
    let dN = 0, dSum = 0, turn = 0;
    for (let j = W + 1; j < NZ - W - 1; j++)
      for (let i = W + 1; i < NX - W - 1; i++) {
        const k = j * NX + i;
        if (!ny[k]) continue;
        let ax = 0, ay = 0, az = 0, c = 0;
        for (let q = -W; q <= W; q++) for (let r = -W; r <= W; r++) {
          const kk = k + q * NX + r;
          if (!ny[kk]) continue;
          ax += nx[kk]; ay += ny[kk]; az += nz[kk]; c++;
        }
        if (c < 12) continue;
        const L = Math.sqrt(ax * ax + ay * ay + az * az);
        dN++; dSum += 1 - L / c;
        if ((nx[k] * ax + ny[k] * ay + nz[k] * az) / L < Math.cos(25 * Math.PI / 180)) turn++;
      }
    /* what the key light does with it */
    const lHist = new Array(10).fill(0);
    let nL = 0, lit = 0, dark = 0, lSum = 0;
    for (let k = 0; k < NX * NZ; k++) {
      if (!ny[k]) continue;
      const d = Math.max(0, nx[k] * SUN9[0] + ny[k] * SUN9[1] + nz[k] * SUN9[2]);
      nL++; lSum += d; lHist[Math.min(9, Math.floor(d * 10))]++;
      if (d > 0.55) lit++; if (d < 0.25) dark++;
    }
    return {cells: sN, meanSlopeDeg: r2(sSum / sN), slopeHist5deg: hist,
            pctOver12deg: r2(100 * hist.slice(3).reduce((s, v) => s + v, 0) / sN),
            pctOver20deg: r2(100 * hist.slice(4).reduce((s, v) => s + v, 0) / sN),
            dispersion: +(dSum / dN).toFixed(4),
            pctTurn: r2(100 * turn / dN),
            nl: {mean: +(lSum / nL).toFixed(3), hist: lHist,
                 pctLit: r2(100 * lit / nL), pctDark: r2(100 * dark / nL),
                 range: r2(Math.max(...lHist.map((v, i) => v > nL * 0.005 ? i : -1)) * 0.1
                        - Math.min(...lHist.map((v, i) => v > nL * 0.005 ? i : 99)) * 0.1)},
            hGrid: h, landGrid: land};
  };

  const R = (t.islandR || 480) * 1.12;
  const WIN_SITE = window_(t.cx - 380, t.cx + 380, t.cz - 260, t.cz + 260, false);
  const WIN_ISLE = window_(t.cx - R, t.cx + R, t.cz - R, t.cz + R, true);

  const onSite = measure(WIN_SITE), onIsle = measure(WIN_ISLE);
  const terrace = t._terrace;
  t._terrace = null;                       // <- the ablation
  const offSite = measure(WIN_SITE), offIsle = measure(WIN_ISLE);
  t._terrace = terrace;

  const dRms = (A, B) => {
    let n = 0, s = 0, mx = 0;
    for (let k = 0; k < A.hGrid.length; k++) {
      if (!A.landGrid[k] || !B.landGrid[k]) continue;
      const d = A.hGrid[k] - B.hGrid[k];
      n++; s += d * d; if (Math.abs(d) > mx) mx = Math.abs(d);
    }
    return {rms: +Math.sqrt(s / Math.max(1, n)).toFixed(2), max: +mx.toFixed(2), n};
  };
  const moved = {site: dRms(onSite, offSite), island: dRms(onIsle, offIsle)};
  for (const o of [onSite, offSite, onIsle, offIsle]) { delete o.hGrid; delete o.landGrid; }
  return {siteBlock: {benched: onSite, plane: offSite},
          island: {benched: onIsle, plane: offIsle},
          moved,
          riser: t._terrace ? t._terrace.risers.map(r => ({
            rise: r2(r.rise), run: r2(r.run),
            faceDeg: r2(Math.atan(Math.abs(r.rise) / r.run) * 180 / Math.PI)})) : null};
});
out.pageErrors = errs.slice(0, 6);
console.log(JSON.stringify(out, null, 1));
await b.close();
