/* pr-fields.mjs — MEASURE THE FIELD BEFORE YOU THRESHOLD IT.
 *
 * Dumps, over LAND ONLY, the distributions of every quantity a region
 * classifier could possibly be written against: altitude above the waterline,
 * slope, moisture, terrain's own `kind`, distance to the waterline (computed
 * here by a chamfer distance transform over a land mask sampled from
 * ctx.ground, so it uses no private terrain API), and distance to the plant.
 *
 *   node pr-fields.mjs
 */
import {chromium} from 'playwright';

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
  '?mods=terrain,buildings,rail&cam=far&time=9&weather=clear&hud=0&quality=ultra';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await (await b.newContext({viewport: {width: 1280, height: 720}})).newPage();
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto(URL, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(2500);

const out = await p.evaluate(() => {
  const w = window.__lemWorld;
  const t = w.subsystems.get('terrain');
  const ground = (x, z) => t.heightAt(x, z);
  const wy = t.waterY;
  const cx = t.cx, cz = t.cz;
  const R = (t.islandR || 500) + (t.coastWobble || 0) + 40;

  /* ---- the land mask and its distance transform ------------------------- */
  const CELL = 8;
  const N = Math.ceil(2 * R / CELL) + 1;
  const x0 = cx - R, z0 = cz - R;
  const land = new Uint8Array(N * N);
  let nLand = 0;
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const h = ground(x0 + i * CELL, z0 + j * CELL);
    const L = Number.isFinite(h) && h > wy ? 1 : 0;
    land[j * N + i] = L; nLand += L;
  }
  /* Chamfer 3-4 style two-pass, in cells, seeded 0 on every WATER cell and on
   * the border (the grid edge is sea by construction of R). */
  const INF = 1e9;
  const d = new Float32Array(N * N);
  for (let k = 0; k < N * N; k++) d[k] = land[k] ? INF : 0;
  const A = 1, B = Math.SQRT2;
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const k = j * N + i; if (!land[k]) continue;
    let v = d[k];
    if (i > 0) v = Math.min(v, d[k - 1] + A);
    if (j > 0) v = Math.min(v, d[k - N] + A);
    if (i > 0 && j > 0) v = Math.min(v, d[k - N - 1] + B);
    if (i < N - 1 && j > 0) v = Math.min(v, d[k - N + 1] + B);
    d[k] = v;
  }
  for (let j = N - 1; j >= 0; j--) for (let i = N - 1; i >= 0; i--) {
    const k = j * N + i; if (!land[k]) continue;
    let v = d[k];
    if (i < N - 1) v = Math.min(v, d[k + 1] + A);
    if (j < N - 1) v = Math.min(v, d[k + N] + A);
    if (i < N - 1 && j < N - 1) v = Math.min(v, d[k + N + 1] + B);
    if (i > 0 && j < N - 1) v = Math.min(v, d[k + N - 1] + B);
    d[k] = v;
  }

  /* ---- the plant ---------------------------------------------------------- */
  const plan = w.plan;
  const bb = plan?.bounds;
  const stations = (plan?.stations || []).map(s => ({x: s.x, z: s.z}));
  const dPlantAt = (x, z) => {
    let m = Infinity;
    for (const s of stations) {
      const dx = x - s.x, dz = z - s.z;
      m = Math.min(m, Math.sqrt(dx * dx + dz * dz));
    }
    return m;
  };

  /* ---- sample every land cell -------------------------------------------- */
  const alt = [], slope = [], dw = [], moist = [], dp = [], sun = [], flow = [];
  const kinds = {};
  const step = 2;   // every other cell -> ~16 m lattice
  for (let j = 0; j < N; j += step) for (let i = 0; i < N; i += step) {
    const k = j * N + i; if (!land[k]) continue;
    const x = x0 + i * CELL, z = z0 + j * CELL;
    const s = t.biomeAt(x, z);
    if (!s || !Number.isFinite(s.altitude)) continue;
    alt.push(s.altitude); slope.push(s.slope); moist.push(s.moisture);
    sun.push(s.sun); flow.push(s.flow);
    dw.push(d[k] * CELL);
    dp.push(dPlantAt(x, z));
    kinds[s.kind] = (kinds[s.kind] || 0) + 1;
  }

  const pct = v => {
    const a = [...v].sort((x, y) => x - y);
    const at = f => a[Math.min(a.length - 1, Math.floor(a.length * f))];
    return {n: a.length, min: +a[0].toFixed(3), p05: +at(0.05).toFixed(3),
            p25: +at(0.25).toFixed(3), p50: +at(0.50).toFixed(3),
            p75: +at(0.75).toFixed(3), p95: +at(0.95).toFixed(3),
            max: +a[a.length - 1].toFixed(3)};
  };

  /* Cross-tab: slope percentiles WITHIN the coastal band, which is the number
   * that decides whether "beach" is a strand or a cliff. */
  const bandSlope = [], bandAlt = [];
  for (let q = 0; q < dw.length; q++) {
    if (dw[q] <= 60) { bandSlope.push(slope[q]); bandAlt.push(alt[q]); }
  }

  return {
    waterY: wy, cx, cz, islandR: t.islandR, coastWobble: t.coastWobble,
    beachW: t.beachW, cliffW: t.cliffW,
    maskCells: N * N, landCells: nLand, landFrac: +(nLand / (N * N)).toFixed(3),
    bounds: bb, stations: stations.length,
    altitude: pct(alt), slope: pct(slope), dWater: pct(dw),
    moisture: pct(moist), dPlant: pct(dp), sun: pct(sun), flow: pct(flow),
    bandSlope: bandSlope.length > 16 ? pct(bandSlope) : null,
    bandAlt: bandAlt.length > 16 ? pct(bandAlt) : null,
    bandFrac: +(bandSlope.length / alt.length).toFixed(3),
    kinds,
  };
});
console.log(JSON.stringify(out, null, 2));
await b.close();
