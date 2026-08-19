/* pr-band.mjs — the decisive measurement for the tide-line round.
 *
 * terrain.js paints its wet band from ELEVATION ABOVE THE WATERLINE:
 *   strand  = smoothstep(10, 0, h - waterY)
 *   wetSand = smoothstep(0.79, 0.965, strand)   -> full below 1.12 m, 0 by 2.95 m
 *   damp    = smoothstep(0.52, 0.83, strand)    -> full below 2.62 m, 0 by 4.87 m
 *   sandRaw holds to about 8.6 m, so DRY PALE SAND is the window 2.95 .. 8.6 m.
 *
 * The umbrellas currently sit at a median 0.53 m, i.e. inside the saturated
 * wash. This asks: is there anywhere above the wash to put them, and what does
 * the beachness rule say about it?
 *
 *   node pr-band.mjs
 */
import {chromium} from 'playwright';
const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await (await b.newContext({viewport: {width: 1280, height: 720}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=' + MODS +
  '&cam=far&time=9&weather=clear&hud=0&quality=ultra', {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(6000);

const out = await p.evaluate(() => {
  const w = window.__lemWorld;
  const pr = w.subsystems.get('props');
  const t = w.subsystems.get('terrain');
  const m = pr._mask;
  const rows = [];
  for (let j = 0; j < m.N; j++) {
    for (let i = 0; i < m.N; i++) {
      if (!m.land[j * m.N + i]) continue;
      const x = m.x0 + i * m.cell, z = m.z0 + j * m.cell;
      const dw = m.d[j * m.N + i] * m.cell;
      if (dw > pr._shoreW) continue;
      const s = t.biomeAt(x, z);
      if (!s) continue;
      rows.push({x, z, a: s.altitude, sl: s.slope, hard: s.hard, kind: s.kind,
                 dw, b: pr.beachnessAt(x, z, s),
                 dp: pr.dPlantAt(x, z)});
    }
  }
  /* bin the whole shore band by elevation */
  const EDGES = [0, 0.5, 1.12, 2.0, 2.95, 4.0, 5.5, 8.6, 12, 20, 1e9];
  const bins = EDGES.slice(0, -1).map((lo, i) => ({
    lo, hi: EDGES[i + 1], n: 0, nBeach: 0, bSum: 0, slSum: 0, bMax: 0,
  }));
  for (const r of rows) {
    const k = EDGES.findIndex((e, i) => r.a >= e && r.a < EDGES[i + 1]);
    if (k < 0) continue;
    const bn = bins[k];
    bn.n++; bn.bSum += r.b; bn.slSum += r.sl;
    bn.bMax = Math.max(bn.bMax, r.b);
    if (r.b >= 0.28) bn.nBeach++;
  }
  for (const bn of bins) {
    bn.bMean = bn.n ? +(bn.bSum / bn.n).toFixed(3) : 0;
    bn.slMean = bn.n ? +(bn.slSum / bn.n).toFixed(3) : 0;
    bn.bMax = +bn.bMax.toFixed(3);
    delete bn.bSum; delete bn.slSum;
  }

  /* the DRY STRAND window specifically: 2.95 .. 8.6 m, clear of plant/hard */
  const dry = rows.filter(r => r.a >= 2.95 && r.a <= 8.6 && r.hard <= 0.25 &&
                               r.kind !== 'hardstanding' && r.dp > pr._cityR + 14);
  dry.sort((a, b) => b.b - a.b);
  /* how flat is the dry strand, and what would beachness need to be to admit it */
  const drySl = dry.map(r => r.sl).sort((a, b) => a - b);
  const dryB = dry.map(r => r.b).sort((a, b) => a - b);
  const q = (v, f) => v.length ? +v[Math.min(v.length - 1, Math.floor(v.length * f))].toFixed(3) : null;

  /* cluster the dry strand: is there a stretch of it, or scattered cells? */
  const CR = 72;
  let bestC = null;
  for (const c of dry) {
    let n = 0;
    for (const o of dry) if (Math.hypot(c.x - o.x, c.z - o.z) <= CR) n++;
    if (!bestC || n > bestC.n) bestC = {x: c.x, z: c.z, n, a: +c.a.toFixed(2), b: +c.b.toFixed(3)};
  }

  return {
    waterY: t.waterY, shoreW: pr._shoreW,
    bandAlt: pr._bandAltRange, bandSlope: pr._bandSlopeRange,
    bandCells: rows.length, bins,
    dryCells: dry.length,
    drySlopeP: [q(drySl, 0.05), q(drySl, 0.5), q(drySl, 0.95)],
    dryBeachnessP: [q(dryB, 0.05), q(dryB, 0.5), q(dryB, 0.95)],
    dryTop10: dry.slice(0, 10).map(r => ({x: r.x, z: r.z, a: +r.a.toFixed(2),
      sl: +r.sl.toFixed(3), b: +r.b.toFixed(3), dw: +r.dw.toFixed(0), kind: r.kind})),
    dryBestCluster: bestC,
    curAnchor: pr.beachAnchor,
  };
});
console.log(JSON.stringify(out, null, 2));
await b.close();
