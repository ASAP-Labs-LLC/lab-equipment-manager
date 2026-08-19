/* tq-form.mjs — MID-FREQUENCY landform, measured as a band, with an ablation.
 *
 * The blind critique names one root cause: "between the ~1km island silhouette
 * and the individual tree, there is nothing". That is a statement about a BAND
 * of spatial frequency, and mean slope cannot report it — `tq-relief.mjs`'s own
 * histogram shows why: on this island a quarter of all land samples sit in one
 * 25-30 deg bin, which is `FILL_SLOPE` (0.55 = 28.8 deg), i.e. the design
 * plane's own batter cone. A conical apron round a dome has a large mean slope
 * and no landform, so the two rounds that chased mean slope were chasing a
 * number that answers a different question.
 *
 * So: sample height along radial transects, remove the silhouette with a long
 * moving average, remove nothing at the fine end (the heightfield has no tree
 * scale in it), and report what is left in the 40-400 m band:
 *
 *   midRms        metres RMS of the band-passed height. THE number.
 *   crossPerKm    sign changes of the band-passed height per kilometre of
 *                 transect — how many ridges and valleys you cross walking
 *                 inland. A dome gives ~0.
 *   midSlopeDeg   mean |d(mid)/dr| in degrees: how much of the surface's angle
 *                 the band is responsible for.
 *
 * Everything is measured through `_gradedHeight` rather than `heightAt`, so the
 * ABLATION is real: `heightAt` reads a baked Float32Array and stubbing a method
 * cannot change it, which is a trap this probe fell into once already.
 *
 * Four passes in ONE page load, so no frame-level or file-level drift can get
 * into the comparison:
 *   full            everything on
 *   noIsle          `_islandForm` stubbed to zero
 *   noEros          the erosion residual grid nulled
 *   bare            both, plus `_yardRelief`
 *
 *   node tq-form.mjs [--bearings 96] [--step 4]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const NB = +(a.bearings || 96);
const STEP = +(a.step || 4);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 900, height: 500}});
const errors = [];
p.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=wide&time=9&hud=0&quality=ultra',
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(3500);

const out = await p.evaluate(({NB, STEP}) => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const cx = t.cx, cz = t.cz;

  /* the shorelines, once, off the unablated field — the same transects have to
   * be walked in every pass or the passes are not comparable */
  const rays = [];
  for (let k = 0; k < NB; k++) {
    const th = (k / NB) * Math.PI * 2, ux = Math.cos(th), uz = Math.sin(th);
    let shore = 0;
    for (let r = 40; r < 2400; r += 4) {
      if (t._islandSD(cx + ux * r, cz + uz * r) >= 0) { shore = r; break; }
    }
    if (shore > 80) rays.push([ux, uz, shore]);
  }

  /* A 2-D HIGH PASS, not a 1-D one. The first cut of this filtered along the
   * transect with a 200 m moving average, and a transect on this island is
   * 380 m long — so the filter never had two windows to compare and what came
   * back was the silhouette with the ends reflected, insensitive to everything.
   * Sampling a grid and subtracting a disc mean of radius LONG is the same idea
   * done where there is room for it: LONG sets the low edge of the band, the
   * grid step sets the high edge, and the ablation controls for whatever the
   * coast profile leaves in it. */
  const GRID = 8, LONG = 120;
  const R = (t.islandR || 480) * 1.12;
  const G = Math.ceil((2 * R) / GRID);
  const KR = Math.round(LONG / GRID);
  const measure = () => {
    const h = new Float64Array(G * G);
    const land = new Uint8Array(G * G);
    const wy = t.waterY;
    for (let j = 0; j < G; j++) {
      const z = cz - R + j * GRID;
      for (let i = 0; i < G; i++) {
        const x = cx - R + i * GRID;
        const v = t._gradedHeight(x, z);
        h[j * G + i] = v;
        land[j * G + i] = v > wy + 0.2 ? 1 : 0;
      }
    }
    /* disc mean, done as two box passes (separable), land only */
    const sx = new Float64Array(G * G), cxb = new Float64Array(G * G);
    for (let j = 0; j < G; j++) {
      for (let i = 0; i < G; i++) {
        let s = 0, c = 0;
        for (let d = -KR; d <= KR; d++) {
          const ii = i + d; if (ii < 0 || ii >= G) continue;
          if (!land[j * G + ii]) continue;
          s += h[j * G + ii]; c++;
        }
        sx[j * G + i] = s; cxb[j * G + i] = c;
      }
    }
    /* ---- and the AZIMUTHAL residual, which is the critique's actual number ---
     *
     * "B's island is a lozenge. It reads as an ellipse with a smooth dome pushed
     * up in the middle." A high-pass alone cannot report that, because the two
     * biggest mid-frequency structures this island has — the design plane's
     * batter cone and the three-slope coast band — are FUNCTIONS OF RADIUS
     * ALONE. They put 10 m of RMS into a 120 m high pass and none of it into the
     * picture, because a figure of revolution has no landform in it whatever its
     * profile does.
     *
     * So: subtract, from every land sample, the mean height of every other land
     * sample at the same radius. What is left is the departure from a solid of
     * revolution — i.e. every ridge, spur, gully and valley on the island, and
     * nothing else. A perfect dome scores zero. */
    const NRING = 90, ringS = new Float64Array(NRING), ringC = new Float64Array(NRING);
    const rMax = R;
    for (let j = 0; j < G; j++) {
      const z = cz - R + j * GRID;
      for (let i = 0; i < G; i++) {
        if (!land[j * G + i]) continue;
        const x = cx - R + i * GRID;
        const rr = Math.hypot(x - cx, z - cz);
        const b0 = Math.min(NRING - 1, Math.floor(rr / rMax * NRING));
        ringS[b0] += h[j * G + i]; ringC[b0]++;
      }
    }
    let aN = 0, aSq = 0, aGs = 0, aGn = 0;
    const azi = new Float64Array(G * G);
    for (let j = 0; j < G; j++) {
      const z = cz - R + j * GRID;
      for (let i = 0; i < G; i++) {
        const k = j * G + i;
        if (!land[k]) { azi[k] = NaN; continue; }
        const x = cx - R + i * GRID;
        const rr = Math.hypot(x - cx, z - cz);
        const b0 = Math.min(NRING - 1, Math.floor(rr / rMax * NRING));
        azi[k] = ringC[b0] > 8 ? h[k] - ringS[b0] / ringC[b0] : NaN;
      }
    }
    for (let j = 1; j < G - 1; j++) {
      for (let i = 1; i < G - 1; i++) {
        const v = azi[j * G + i];
        if (!isFinite(v)) continue;
        aN++; aSq += v * v;
        const a1 = azi[j * G + i + 1], a2 = azi[j * G + i - 1];
        const a3 = azi[(j + 1) * G + i], a4 = azi[(j - 1) * G + i];
        if (isFinite(a1) && isFinite(a2) && isFinite(a3) && isFinite(a4)) {
          const dx = (a1 - a2) / (2 * GRID), dz = (a3 - a4) / (2 * GRID);
          aGs += Math.atan(Math.hypot(dx, dz)) * 180 / Math.PI; aGn++;
        }
      }
    }

    let n = 0, sq = 0, gs = 0, gn = 0;
    const res = new Float64Array(G * G);
    for (let j = 0; j < G; j++) {
      for (let i = 0; i < G; i++) {
        let s = 0, c = 0;
        for (let d = -KR; d <= KR; d++) {
          const jj = j + d; if (jj < 0 || jj >= G) continue;
          s += sx[jj * G + i]; c += cxb[jj * G + i];
        }
        res[j * G + i] = c > 40 && land[j * G + i] ? h[j * G + i] - s / c : NaN;
      }
    }
    for (let j = 1; j < G - 1; j++) {
      for (let i = 1; i < G - 1; i++) {
        const v = res[j * G + i];
        if (!isFinite(v)) continue;
        n++; sq += v * v;
        const a = res[j * G + i + 1], b2 = res[j * G + i - 1];
        const c2 = res[(j + 1) * G + i], d2 = res[(j - 1) * G + i];
        if (isFinite(a) && isFinite(b2) && isFinite(c2) && isFinite(d2)) {
          const dx = (a - b2) / (2 * GRID), dz = (c2 - d2) / (2 * GRID);
          gs += Math.atan(Math.hypot(dx, dz)) * 180 / Math.PI; gn++;
        }
      }
    }
    /* ---- NORMAL DISPERSION, and this is the one that answers the note --------
     *
     * "It is why the ground can't turn against the key light (no surfaces angled
     * to turn)." That is a statement about the surface normal varying over a few
     * tens of metres, and it is neither a height statistic nor a slope average:
     * a 28 deg batter cone has a large mean slope and every normal on it agrees
     * with its neighbours, so nothing on it can turn against anything.
     *
     * So: take the unit normal at a 16 m baseline, average it over a 56 m
     * neighbourhood, and report 1 - |mean|. A plane, a cone and a smooth dome all
     * score ~0 however steep they are. Ground that has ridges and gullies in it
     * scores in the tenths. `pctTurn` is the share of land whose own normal is
     * more than 25 deg off its 56 m neighbourhood mean — the ground that can
     * actually go dark while the ground beside it stays lit. */
    const nx = new Float64Array(G * G), ny = new Float64Array(G * G),
          nz = new Float64Array(G * G);
    const B = 2;                                   // 2 cells = 16 m baseline
    for (let j = B; j < G - B; j++) {
      for (let i = B; i < G - B; i++) {
        const k = j * G + i;
        if (!land[k]) continue;
        const gx = (h[k + B] - h[k - B]) / (2 * B * GRID);
        const gz = (h[k + B * G] - h[k - B * G]) / (2 * B * GRID);
        const inv = 1 / Math.sqrt(gx * gx + gz * gz + 1);
        nx[k] = -gx * inv; ny[k] = inv; nz[k] = -gz * inv;
      }
    }
    const W = 3;                                   // 3 cells = 56 m window
    let dN = 0, dSum = 0, turn = 0, s8 = 0, s8N = 0;
    for (let j = W + B; j < G - W - B; j++) {
      for (let i = W + B; i < G - W - B; i++) {
        const k = j * G + i;
        if (!land[k] || ny[k] === 0) continue;
        let ax = 0, ay = 0, az = 0, c = 0;
        for (let b2 = -W; b2 <= W; b2++) {
          for (let a2 = -W; a2 <= W; a2++) {
            const kk = k + b2 * G + a2;
            if (!land[kk] || ny[kk] === 0) continue;
            ax += nx[kk]; ay += ny[kk]; az += nz[kk]; c++;
          }
        }
        if (c < 12) continue;
        const L = Math.sqrt(ax * ax + ay * ay + az * az) / c;
        dN++; dSum += 1 - L;
        const dotv = (nx[k] * ax + ny[k] * ay + nz[k] * az) / Math.sqrt(ax * ax + ay * ay + az * az);
        if (dotv < Math.cos(25 * Math.PI / 180)) turn++;
        s8N++; if (ny[k] < Math.cos(8 * Math.PI / 180)) s8++;
      }
    }

    return {
      samples: n,
      midRms: +Math.sqrt(sq / Math.max(1, n)).toFixed(2),
      midSlopeDeg: +(gs / Math.max(1, gn)).toFixed(2),
      aziRms: +Math.sqrt(aSq / Math.max(1, aN)).toFixed(2),
      aziSlopeDeg: +(aGs / Math.max(1, aGn)).toFixed(2),
      dispersion: +(dSum / Math.max(1, dN)).toFixed(4),
      pctTurn: +(100 * turn / Math.max(1, dN)).toFixed(2),
      pctSlope8: +(100 * s8 / Math.max(1, s8N)).toFixed(1),
      hGrid: h, landGrid: land,
    };
  };

  const isle = t._islandForm, yard = t._yardRelief, eros = t.eros;
  const zero = () => 0;

  const full = measure();
  t._islandForm = zero;
  const noIsle = measure();
  t._islandForm = isle;
  t.eros = null;
  const noEros = measure();
  t._islandForm = zero; t._yardRelief = zero;
  const bare = measure();
  t._islandForm = isle; t._yardRelief = yard; t.eros = eros;

  /* the unambiguous one: how many metres of ground the round's own work moved */
  const dRms = (A, B2) => {
    let n2 = 0, s2 = 0, mx = 0;
    for (let k = 0; k < A.hGrid.length; k++) {
      if (!A.landGrid[k] || !B2.landGrid[k]) continue;
      const d = A.hGrid[k] - B2.hGrid[k];
      n2++; s2 += d * d; if (Math.abs(d) > mx) mx = Math.abs(d);
    }
    return {rms: +Math.sqrt(s2 / Math.max(1, n2)).toFixed(2), max: +mx.toFixed(2)};
  };
  const moved = {isleOnly: dRms(full, noIsle), erosOnly: dRms(full, noEros),
                 all: dRms(full, bare)};
  for (const o of [full, noIsle, noEros, bare]) { delete o.hGrid; delete o.landGrid; }

  /* how much of the island has a watercourse on it */
  let flowN = 0, wet = 0, gully = 0;
  for (let j = 0; j < G; j++) {
    const z = cz - R + j * GRID;
    for (let i = 0; i < G; i++) {
      const x = cx - R + i * GRID;
      if (t._gradedHeight(x, z) <= t.waterY + 0.2) continue;
      const f = t._flowAt(x, z);
      flowN++; if (f > 0.55) wet++; if (f > 0.20) gully++;
    }
  }

  return {
    bearings: rays.length, waterY: +t.waterY.toFixed(2),
    full, noIsle, noEros, bare, moved,
    drainage: {samples: flowN,
               pctWatercourse: +(100 * wet / Math.max(1, flowN)).toFixed(2),
               pctGully: +(100 * gully / Math.max(1, flowN)).toFixed(2)},
    erosStats: t.erosStats || null,
  };
}, {NB, STEP});

out.pageErrors = errors;
console.log(JSON.stringify(out, null, 1));
await b.close();
