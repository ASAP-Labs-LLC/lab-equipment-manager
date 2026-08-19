/* tq-budget.mjs — how much of the island's radius is actually free for landform.
 *
 * The round-15 terrain note claimed the reason mean slope would not move is
 * STRUCTURAL: "the design plane owns the whole top of a 400 m island and the
 * coast profile owns ~250 m of the 400 m radius, leaving almost no radius for
 * landform." That is a measurable claim and nobody had measured it. This does.
 *
 * On each of `--bearings` rays from (cx, cz) it finds the waterline (the zero of
 * `_islandSD`), then splits the radius into three:
 *
 *   GRADED   |_gradedHeight - _baseHeight| > 0.5 m — the design plane and the
 *            railway's declared formation own this ground outright.
 *   COAST    inside the three-slope coastal band. Its width `s3` is recovered by
 *            the file's OWN formula, fixed-pointed on `aw` (the height of the
 *            land behind), using terrain's own `_coastCliffness`. Converges from
 *            below in 6 iterations, so this is a LOWER bound on the coast's take.
 *   FREE     everything else. This is the radius a landform round has to work in.
 *
 * It also reports, on the FREE band only, the mean slope and — by nulling
 * `t.eros` in the live page and re-reading `heightAt` off the analytic path —
 * how much of that slope the erosion residual is responsible for.
 *
 *   node tq-budget.mjs [--bearings 72] [--step 3]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const NB = +(a.bearings || 72);
const STEP = +(a.step || 3);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=wide&time=9&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3500);

const out = await p.evaluate(({NB, STEP}) => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const cx = t.cx, cz = t.cz, wy = t.waterY;

  /* the file's own coast constants, replicated — see the COAST_* block */
  const C = {TOE_W: 16, TOE_H: 2.6, STRAND_H: 5.5, FACE_H: 16,
             FACE_SLOPE: 0.62, SHELF_SLOPE: 0.22, BACK_SLOPE: 0.32};
  const lerp = (u, v, s) => u + (v - u) * s;

  const rows = [];
  const acc = {graded: 0, coast: 0, free: 0, shore: 0};
  let freeN = 0, freeSlopeSum = 0, freeSlopeNoEros = 0, freeNoErosN = 0;
  const d = 3.0;
  const slopeAt = (x, z) => {
    const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
    const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
    return Math.atan(Math.hypot(gx, gz)) * 180 / Math.PI;
  };

  const freePts = [];
  for (let k = 0; k < NB; k++) {
    const th = (k / NB) * Math.PI * 2, ux = Math.cos(th), uz = Math.sin(th);
    /* the waterline: last r with sd < 0 */
    let shore = 0;
    for (let r = 40; r < 2400; r += 4) {
      if (t._islandSD(cx + ux * r, cz + uz * r) >= 0) { shore = r; break; }
    }
    if (!shore) continue;

    /* the coast band width on this bearing, by the file's own formula */
    let s3 = 60;
    for (let it = 0; it < 6; it++) {
      const rr = Math.max(0, shore - s3);
      const x = cx + ux * rr, z = cz + uz * rr;
      const aw = Math.max(0.1, t.heightAt(x, z) - wy);
      const cliff = t._coastCliffness(x, z, aw);
      const toeW = lerp(t.beachW || 320, C.TOE_W, cliff);
      const toeH = Math.min(aw, lerp(C.STRAND_H, C.TOE_H, cliff));
      const faceH = Math.max(0, Math.min(aw - toeH, C.FACE_H));
      const faceW = faceH / lerp(C.SHELF_SLOPE, C.FACE_SLOPE, cliff);
      const backH = Math.max(0, aw - toeH - faceH);
      s3 = toeW + faceW + backH / C.BACK_SLOPE;
    }

    let g = 0, c = 0, f = 0, fp = 0;
    for (let r = 0; r < shore; r += STEP) {
      const x = cx + ux * r, z = cz + uz * r;
      const gh = t._gradedHeight(x, z), bh = t._baseHeight(x, z);
      const graded = Math.abs(gh - bh) > 0.5;
      if (t._distances(x, z, null) <= 0) fp += STEP;
      const inCoast = (shore - r) < s3;
      if (graded) g += STEP;
      else if (inCoast) c += STEP;
      else { f += STEP; freePts.push([x, z]); }
    }
    acc.graded += g; acc.coast += c; acc.free += f; acc.shore += shore;
    acc.foot = (acc.foot || 0) + fp;
    rows.push({bearing: Math.round(th * 180 / Math.PI), shore: +shore.toFixed(0),
               s3: +s3.toFixed(0), graded: +g.toFixed(0), coast: +c.toFixed(0),
               free: +f.toFixed(0)});
  }

  for (const [x, z] of freePts) { freeSlopeSum += slopeAt(x, z); freeN++; }

  /* the erosion residual's share of that slope: null the grid, re-read */
  const keep = t.eros;
  t.eros = null;
  for (const [x, z] of freePts) { freeSlopeNoEros += slopeAt(x, z); freeNoErosN++; }
  t.eros = keep;

  const n = rows.length;
  return {
    bearings: n,
    islandR: +(t.islandR || 0).toFixed(1),
    siteRadial: +(t.siteRadial || 0).toFixed(1),
    beachW: +(t.beachW || 0).toFixed(1),
    meanShoreR: +(acc.shore / n).toFixed(1),
    meanGradedM: +(acc.graded / n).toFixed(1),
    meanCoastM: +(acc.coast / n).toFixed(1),
    meanFreeM: +(acc.free / n).toFixed(1),
    pctFootprint: +(100 * (acc.foot || 0) / acc.shore).toFixed(1),
    pctGraded: +(100 * acc.graded / acc.shore).toFixed(1),
    pctCoast: +(100 * acc.coast / acc.shore).toFixed(1),
    pctFree: +(100 * acc.free / acc.shore).toFixed(1),
    freeSamples: freeN,
    freeMeanSlopeDeg: freeN ? +(freeSlopeSum / freeN).toFixed(2) : null,
    freeMeanSlopeNoErosDeg: freeNoErosN ? +(freeSlopeNoEros / freeNoErosN).toFixed(2) : null,
    erosStats: t.erosStats || null,
    rows: rows.slice(0, 12),
  };
}, {NB, STEP});

console.log(JSON.stringify(out, null, 1));
await b.close();
