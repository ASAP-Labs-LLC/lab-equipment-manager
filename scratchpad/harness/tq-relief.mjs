/* tq-relief.mjs — how much LANDFORM is there, in numbers.
 *
 * Job 1 (the sand holds one value on every slope) and job 3 (the landform is
 * smooth) may be the same defect: if the ground has no slope, N.L cannot vary,
 * and no amount of shader work puts value into a plane. So measure the slope
 * the heightfield actually has, on land, away from the graded site.
 *
 *   node tq-relief.mjs [--mods terrain] [--grid 160]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'terrain';
const G = +(a.grid || 160);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=wide&time=9&hud=0&quality=ultra`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3500);
console.log(JSON.stringify(await p.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const cx = t.cx, cz = t.cz, R = t.islandR || 480;
  const span = R * 2.1, step = span / G, d = 3.0;
  const bins = new Array(10).fill(0);      // slope degrees 0-5,5-10,...45+
  const hist = new Array(24).fill(0);      // height above water, 5m bins
  let n = 0, sumS = 0, maxS = 0, sumH = 0, maxH = -1e9, minH = 1e9;
  const nyBins = new Array(10).fill(0);
  /* slope only where the terrain is NATURAL: away from the site's design
   * plane, which is flat by construction and would swamp the histogram. */
  const site = t.siteRadial || 0;
  let onSite = 0;
  const rows = [];
  for (let j = 0; j <= G; j++) {
    for (let i = 0; i <= G; i++) {
      const x = cx - span / 2 + i * step, z = cz - span / 2 + j * step;
      const h = t.heightAt(x, z);
      if (!isFinite(h) || h <= t.waterY + 0.2) continue;
      const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
      const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
      const s = Math.hypot(gx, gz);
      const deg = Math.atan(s) * 180 / Math.PI;
      const ny = 1 / Math.sqrt(1 + s * s);
      n++; sumS += deg; sumH += h - t.waterY;
      if (deg > maxS) maxS = deg;
      if (h > maxH) maxH = h;
      if (h < minH) minH = h;
      bins[Math.min(9, Math.floor(deg / 5))]++;
      hist[Math.min(23, Math.floor((h - t.waterY) / 5))]++;
      nyBins[Math.min(9, Math.floor(ny * 10))]++;
      const rr = Math.hypot(x - cx, z - cz);
      if (rr < site) onSite++;
    }
  }
  /* plan silhouette: radius on 72 bearings, to see how round/rectangular it is */
  const radii = [];
  for (let k = 0; k < 72; k++) {
    const ang = k / 72 * Math.PI * 2;
    let lo = 0, hi = (t.islandR || 480) + 600;
    for (let it = 0; it < 28; it++) {
      const m = (lo + hi) / 2;
      const hh = t.heightAt(cx + Math.cos(ang) * m, cz + Math.sin(ang) * m);
      if (isFinite(hh) && hh > t.waterY) lo = m; else hi = m;
    }
    radii.push(+lo.toFixed(1));
  }
  const rMean = radii.reduce((s, v) => s + v, 0) / radii.length;
  const rVar = Math.sqrt(radii.reduce((s, v) => s + (v - rMean) ** 2, 0) / radii.length);
  /* second difference around the ring: how "wobbly" the outline is */
  let curv = 0;
  for (let k = 0; k < 72; k++) {
    const a0 = radii[(k + 71) % 72], a1 = radii[k], a2 = radii[(k + 1) % 72];
    curv += Math.abs(a0 - 2 * a1 + a2);
  }
  return {
    landSamples: n, onSiteSamples: onSite,
    meanSlopeDeg: +(sumS / n).toFixed(2), maxSlopeDeg: +maxS.toFixed(1),
    slopeHistDeg: bins, normalYHist: nyBins,
    meanHeightAboveWater: +(sumH / n).toFixed(1),
    maxHeight: +maxH.toFixed(1), minHeight: +minH.toFixed(1),
    waterY: +t.waterY.toFixed(1), islandR: t.islandR,
    coastRMin: t.coastRMin, coastRMean: t.coastRMean, coastRMax: t.coastRMax,
    heightHist5m: hist,
    radii, radiusMean: +rMean.toFixed(1), radiusSigma: +rVar.toFixed(1),
    outlineRoughness: +(curv / 72).toFixed(2),
  };
}, {G}), null, 1));
await b.close();
