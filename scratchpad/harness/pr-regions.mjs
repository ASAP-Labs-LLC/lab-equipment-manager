/* pr-regions.mjs — prototype the region classifier OUTSIDE props.js and prove
 * it is not a constant before a line of it is shipped. Prints the histogram
 * over land, the beachness distribution, an ASCII map, and a determinism check
 * (two independent classifications of the same lattice must agree exactly).
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
  const wy = t.waterY, cx = t.cx, cz = t.cz;
  const R = (t.islandR || 500) + (t.coastWobble || 0) + 40;

  const CELL = 8;
  const N = Math.ceil(2 * R / CELL) + 1;
  const x0 = cx - R, z0 = cz - R;
  const land = new Uint8Array(N * N);
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const h = ground(x0 + i * CELL, z0 + j * CELL);
    land[j * N + i] = Number.isFinite(h) && h > wy ? 1 : 0;
  }
  const d = new Float32Array(N * N);
  for (let k = 0; k < N * N; k++) d[k] = land[k] ? 1e9 : 0;
  const A = 1, B = Math.SQRT2;
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const k = j * N + i; if (!land[k]) continue; let v = d[k];
    if (i > 0) v = Math.min(v, d[k - 1] + A);
    if (j > 0) v = Math.min(v, d[k - N] + A);
    if (i > 0 && j > 0) v = Math.min(v, d[k - N - 1] + B);
    if (i < N - 1 && j > 0) v = Math.min(v, d[k - N + 1] + B);
    d[k] = v;
  }
  for (let j = N - 1; j >= 0; j--) for (let i = N - 1; i >= 0; i--) {
    const k = j * N + i; if (!land[k]) continue; let v = d[k];
    if (i < N - 1) v = Math.min(v, d[k + 1] + A);
    if (j < N - 1) v = Math.min(v, d[k + N] + A);
    if (i < N - 1 && j < N - 1) v = Math.min(v, d[k + N + 1] + B);
    if (i > 0 && j < N - 1) v = Math.min(v, d[k + N - 1] + B);
    d[k] = v;
  }
  const dWaterAt = (x, z) => {
    const fi = (x - x0) / CELL, fj = (z - z0) / CELL;
    const i = Math.max(0, Math.min(N - 1, Math.round(fi)));
    const j = Math.max(0, Math.min(N - 1, Math.round(fj)));
    return d[j * N + i] * CELL;
  };

  /* ---- the plant envelope ------------------------------------------------ */
  const plan = w.plan;
  const st = (plan?.stations || []).map(s => ({x: s.x, z: s.z}));
  const nn = [];
  for (const a of st) {
    let m = Infinity;
    for (const c of st) if (c !== a) {
      const dx = a.x - c.x, dz = a.z - c.z;
      m = Math.min(m, Math.sqrt(dx * dx + dz * dz));
    }
    if (Number.isFinite(m)) nn.push(m);
  }
  nn.sort((a, c) => a - c);
  const nnMed = nn.length ? nn[nn.length >> 1] : 90;
  const dPlantAt = (x, z) => {
    let m = Infinity;
    for (const s of st) {
      const dx = x - s.x, dz = z - s.z;
      m = Math.min(m, Math.sqrt(dx * dx + dz * dz));
    }
    return m;
  };

  /* ---- measured ranges over land ----------------------------------------- */
  const pct3 = v => {
    const a = [...v].sort((x, y) => x - y);
    const at = f => a[Math.min(a.length - 1, Math.floor(a.length * f))];
    return [at(0.05), at(0.50), at(0.95)];
  };
  const mapF = (v, r) => v <= r[1]
    ? Math.max(0, Math.min(1, (v - r[0]) / (r[1] - r[0] || 1e-6))) * 0.5
    : 0.5 + Math.max(0, Math.min(1, (v - r[1]) / (r[2] - r[1] || 1e-6))) * 0.5;

  const pts = [];
  const STEP = 2;
  for (let j = 0; j < N; j += STEP) for (let i = 0; i < N; i += STEP) {
    const k = j * N + i; if (!land[k]) continue;
    pts.push(x0 + i * CELL, z0 + j * CELL);
  }
  const dwAll = [], slAll = [], alAll = [];
  for (let q = 0; q < pts.length; q += 2) {
    const s = t.biomeAt(pts[q], pts[q + 1]);
    dwAll.push(dWaterAt(pts[q], pts[q + 1]));
    slAll.push(s.slope); alAll.push(s.altitude);
  }
  const dwR = pct3(dwAll);
  /* the shore band width: the nearer quartile of the land, capped by terrain's
   * own published strand width */
  const dwSorted = [...dwAll].sort((a, c) => a - c);
  const dwP25 = dwSorted[Math.floor(dwSorted.length * 0.25)];
  const shoreW = Math.min(t.beachW || 1e9, dwP25);

  /* ranges WITHIN the band, which is the population the beach rule sorts */
  const bs = [], ba = [];
  for (let q = 0; q < dwAll.length; q++) {
    if (dwAll[q] <= shoreW) { bs.push(slAll[q]); ba.push(alAll[q]); }
  }
  const bsR = pct3(bs), baR = pct3(ba);
  const cityR = nnMed * 0.75;

  const beachness = (s, dw) => {
    if (dw > shoreW) return 0;
    const flat = 1 - mapF(s.slope, bsR);
    const low = 1 - mapF(s.altitude, baR);
    const near = 1 - mapF(dw, [0, shoreW * 0.5, shoreW]);
    return flat * low * Math.max(0.35, near);
  };
  const BEACH_T = 0.28;

  const classify = (x, z) => {
    const h = ground(x, z);
    if (!(h > wy)) return 'water';
    if (dPlantAt(x, z) <= cityR) return 'city';
    const s = t.biomeAt(x, z);
    const dw = dWaterAt(x, z);
    return beachness(s, dw) >= BEACH_T ? 'beach' : 'country';
  };

  /* ---- the histogram, over land ------------------------------------------ */
  const hist = {}; const bn = [];
  for (let q = 0; q < pts.length; q += 2) {
    const r = classify(pts[q], pts[q + 1]);
    hist[r] = (hist[r] || 0) + 1;
    const s = t.biomeAt(pts[q], pts[q + 1]);
    bn.push(beachness(s, dWaterAt(pts[q], pts[q + 1])));
  }
  const nTot = pts.length / 2;

  /* ---- determinism: classify the same lattice twice ---------------------- */
  let same = true;
  for (let q = 0; q < pts.length; q += 2) {
    if (classify(pts[q], pts[q + 1]) !== classify(pts[q], pts[q + 1])) same = false;
  }

  /* ---- ASCII map --------------------------------------------------------- */
  const MW = 76, ch = {water: ' ', country: '.', beach: 'B', city: '#'};
  const rows = [];
  for (let r = 0; r < 38; r++) {
    let line = '';
    for (let c = 0; c < MW; c++) {
      const x = x0 + (c / (MW - 1)) * 2 * R;
      const z = z0 + (r / 37) * 2 * R;
      line += ch[classify(x, z)] || '?';
    }
    rows.push(line);
  }

  const bnS = [...bn].sort((a, c) => a - c);
  const at = f => +bnS[Math.floor(bnS.length * f)].toFixed(3);

  return {
    shoreW: +shoreW.toFixed(1), beachW: +(t.beachW || 0).toFixed(1),
    dwP25: +dwP25.toFixed(1), dwR: dwR.map(v => +v.toFixed(2)),
    bandSlopeR: bsR.map(v => +v.toFixed(3)), bandAltR: baR.map(v => +v.toFixed(2)),
    bandN: bs.length, nnMed: +nnMed.toFixed(1), cityR: +cityR.toFixed(1),
    landSamples: nTot,
    hist, pctOfLand: Object.fromEntries(Object.entries(hist)
      .map(([k, v]) => [k, +(100 * v / nTot).toFixed(1)])),
    beachnessPct: {p05: at(0.05), p25: at(0.25), p50: at(0.5),
                   p75: at(0.75), p95: at(0.95), max: +bnS[bnS.length - 1].toFixed(3)},
    beachnessVariance: +(bnS[bnS.length - 1] - bnS[0]).toFixed(3),
    deterministic: same,
    map: rows,
  };
});
const map = out.map; delete out.map;
console.log(JSON.stringify(out, null, 2));
console.log('\n  ' + map.join('\n  '));
await b.close();
