/* tq-plat.mjs — DOES THE PLATEAU'S INTERIOR CARRY ANY INFORMATION?
 *
 * The round-32 charge, verbatim: "the plateau itself … is still one
 * low-frequency wash. Across the whole industrial terrace there is no gravel,
 * no compaction difference between trafficked and untrafficked ground, no
 * wheel-rut darkening, no puddling in the low spots, no colour shift where the
 * fill was placed versus where the native ground was left. THE BIGGEST
 * CONTIGUOUS SURFACE STILL CARRIES THE LEAST INFORMATION."
 *
 * That is two questions and this asks both:
 *
 *  1. SEPARATION. Pixels are classified GEOMETRICALLY — unprojected, marched
 *     against terrain.heightAt, then bucketed on facts the renderer cannot fake
 *     (bench mask, slope, distance to the roads/ballast/aprons, sign of
 *     h - smoothBase, drainage flow). Colour-thresholded masks have given this
 *     project false answers four times. Reported per class and as the pairwise
 *     deltas that ARE the complaint.
 *
 *  2. INFORMATION. Local sigma of L in a 5x5 window, measured over the terrace
 *     pixels only, plus the mean absolute adjacent-pixel step. "One
 *     low-frequency wash" is a statement about these two numbers and nothing
 *     else. A wash has a low local sigma and a low step; a surface with gravel,
 *     ruts and a fill boundary on it does not.
 *
 * gi.setExposureLocked(true) is called before the capture. The adaptive meter
 * is negative feedback and absorbs more than half of any change made upstream
 * (REQUESTS.md, sk-milk.mjs: 19.4 L frozen against 7.7 L live), and it has
 * invalidated numbers in three separate rounds of this file.
 *
 * --ablate sets window.__lemAblateYard, which takes BOTH halves of this round
 * out before the world builds — the splat weights and the shader tints — so a
 * before/after is two page loads a minute apart in ONE session. Four modules
 * are live in this world; a cross-session pair is not a measurement.
 *
 *   node tq-plat.mjs [--cam far|street|yard] [--ablate] [--out shot.png]
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) {
    const k = process.argv[i].slice(2);
    a[k] = (process.argv[i + 1] && !process.argv[i + 1].startsWith('--')) ? process.argv[++i] : true;
  }
const mods = a.mods || 'sky,gi,terrain';
const cam = a.cam || 'far';
const G = +(a.grid || 340);
const time = a.time || '9';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
if (a.ablate) await page.addInitScript(() => { window.__lemAblateYard = true; });
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text())) errors.push(m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});

let settled = false, stable = 0, prev = null;
const t1 = Date.now();
while (Date.now() - t1 < 25000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => {
    const s = window.__lemWorld && window.__lemWorld.stats ? window.__lemWorld.stats() : null;
    return s ? [s.drawCalls, s.triangles] : null;
  });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 8) { settled = true; break; }
}

/* THE STOP IS FROZEN FROM HERE. Everything below is a colour number.
 *
 * setExposureLocked alone is NOT enough for an A/B, and finding that out cost a
 * measurement: it freezes the meter at whatever each run happened to adapt to,
 * and the two runs of an ablation pair adapt to DIFFERENT values by
 * construction — the change under test is exactly what the meter is reacting
 * to. The first cam=yard pair came back at 2.5646 against 2.9207, a 14% stop
 * difference sitting on top of the number being measured. --pin writes gi's own
 * accumulator before locking it, so both runs of a pair are photographed at the
 * same stop and the delta is the material. */
const expo = await page.evaluate((pin) => {
  const g = window.__lemWorld.subsystems.get('gi');
  if (!g) return null;
  const before = +(+g.exposure).toFixed(4);
  if (pin) { g._expNow = +pin; g.exposure = +pin; }
  const locked = g.setExposureLocked ? g.setExposureLocked(true) : 'UNSUPPORTED';
  return {locked, settledAt: before, exposure: +(+g.exposure).toFixed(4)};
}, a.pin || null);
await page.waitForTimeout(1200);
const expo2 = await page.evaluate(() => {
  const g = window.__lemWorld.subsystems.get('gi');
  return g ? +(+g.exposure).toFixed(4) : null;
});

const hits = await page.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  const o = {x: cam.position.x, y: cam.position.y, z: cam.position.z};
  const e = cam.matrixWorld.elements;
  const bxv = {x: e[0], y: e[1], z: e[2]};
  const byv = {x: e[4], y: e[5], z: e[6]};
  const bzv = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cam.fov * Math.PI / 360), tx = ty * cam.aspect;
  const H = Math.round(G * 9 / 16);
  const out = [];
  const d = 2.0;
  const q = new Float32Array(4);
  const smoothstep = (A, B, X) => {
    const u = Math.max(0, Math.min(1, (X - A) / (B - A)));
    return u * u * (3 - 2 * u);
  };
  for (let j = 0; j < H; j++) {
    for (let i = 0; i < G; i++) {
      const ndcX = ((i + 0.5) / G) * 2 - 1, ndcY = 1 - ((j + 0.5) / H) * 2;
      const cxr = ndcX * tx, cyr = ndcY * ty;
      let vx = bxv.x * cxr + byv.x * cyr - bzv.x;
      let vy = bxv.y * cxr + byv.y * cyr - bzv.y;
      let vz = bxv.z * cxr + byv.z * cyr - bzv.z;
      const L = Math.hypot(vx, vy, vz); vx /= L; vy /= L; vz /= L;
      let prev = 0, hit = -1, step = 3;
      for (let s = step; s < 9000; s += step) {
        const gap = o.y + vy * s - t.heightAt(o.x + vx * s, o.z + vz * s);
        if (gap <= 0) {
          let lo = prev, hi = s;
          for (let k = 0; k < 24; k++) {
            const m = (lo + hi) * 0.5;
            const g2 = o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m);
            if (g2 <= 0) hi = m; else lo = m;
          }
          hit = (lo + hi) * 0.5; break;
        }
        prev = s; step = Math.min(50, Math.max(3, gap * 0.55));
      }
      if (hit < 0) continue;
      const x = o.x + vx * hit, z = o.z + vz * hit;
      const h = t.heightAt(x, z);
      if (h <= t.waterY + 0.05) continue;
      const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
      const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
      const sl = Math.hypot(gx, gz);
      t._distances(x, z, q);
      const gravel = Math.max(smoothstep(1.8, -1.5, q[2]), smoothstep(2, -3, q[3]) * 0.35) * 0.95;
      const asphalt = smoothstep(5, -5, q[1]) * 0.95 * (1 - smoothstep(4, -5, q[2]));
      out.push({i, j, H, x: +x.toFixed(1), z: +z.toFixed(1),
                aw: +(h - t.waterY).toFixed(2),
                deg: +(Math.atan(sl) * 180 / Math.PI).toFixed(2),
                bm: +t._benchMask(x, z).toFixed(2),
                dTraf: +Math.min(q[1], q[2], q[3]).toFixed(1),
                dFoot: Math.round(q[0]),
                hard: +Math.max(gravel, asphalt).toFixed(2),
                moved: +(h - t._smoothBase(x, z)).toFixed(2),
                flow: +t._flowAt(x, z).toFixed(3),
                dist: Math.round(hit)});
    }
  }
  return out;
}, {G});

const buf = await page.screenshot({type: 'png'});
if (a.out) fs.writeFileSync(a.out, buf);

/* Local sigma has to be measured on the IMAGE, in a window, not on the sparse
 * classified lattice — "low-frequency wash" is a statement about neighbouring
 * pixels and the lattice is 3.8 px apart. */
const shot = await page.evaluate(async ({src, uv}) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas');
  cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(im, 0, 0);
  const D = g.getImageData(0, 0, im.width, im.height).data;
  const lum = (o) => 0.2126 * D[o] + 0.7152 * D[o + 1] + 0.0722 * D[o + 2];
  return uv.map(([u, v]) => {
    const X = Math.min(im.width - 1, Math.round(u * im.width));
    const Y = Math.min(im.height - 1, Math.round(v * im.height));
    const o = (Y * im.width + X) * 4;
    /* 5x5 local sigma and the mean absolute one-pixel step inside it */
    let n = 0, s = 0, ss = 0, steps = 0, ns = 0;
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) {
        const XX = X + dx, YY = Y + dy;
        if (XX < 0 || YY < 0 || XX >= im.width || YY >= im.height) continue;
        const oo = (YY * im.width + XX) * 4;
        const L = lum(oo); n++; s += L; ss += L * L;
        if (XX + 1 < im.width) { steps += Math.abs(lum(oo + 4) - L); ns++; }
      }
    }
    const mean = s / n;
    return [D[o], D[o + 1], D[o + 2],
            Math.sqrt(Math.max(0, ss / n - mean * mean)), ns ? steps / ns : 0];
  });
}, {src: 'data:image/png;base64,' + buf.toString('base64'),
    uv: hits.map(p => [(p.i + 0.5) / G, (p.j + 0.5) / p.H])});

hits.forEach((p, i) => {
  p.r = shot[i][0]; p.g = shot[i][1]; p.b = shot[i][2];
  p.sig = shot[i][3]; p.step = shot[i][4];
  p.L = 0.2126 * p.r + 0.7152 * p.g + 0.0722 * p.b;
  const mx = Math.max(p.r, p.g, p.b), mn = Math.min(p.r, p.g, p.b);
  p.sat = mx > 0 ? (mx - mn) / mx : 0;
});

const mean = v => v.length ? v.reduce((s, x) => s + x, 0) / v.length : NaN;
const r2 = x => +(+x).toFixed(2);
const stat = (v, name) => ({
  cls: name, n: v.length,
  rgb: [Math.round(mean(v.map(p => p.r))), Math.round(mean(v.map(p => p.g))),
        Math.round(mean(v.map(p => p.b)))],
  L: r2(mean(v.map(p => p.L))),
  RB: r2(mean(v.map(p => p.r - p.b))),
  sat: r2(mean(v.map(p => p.sat))),
  sigma: r2(mean(v.map(p => p.sig))),
  step: r2(mean(v.map(p => p.step))),
  dist: Math.round(mean(v.map(p => p.dist))),
});

/* the terrace, as the splat defines it */
const terrace = hits.filter(p => p.bm > 0.9);
const open = terrace.filter(p => p.hard <= 0.45);
const flat = open.filter(p => p.deg < 4);
const classes = [
  stat(terrace, 'terraceAll'),
  stat(open, 'terraceOpen'),
  stat(flat, 'terraceOpenFlat'),
  stat(terrace.filter(p => p.hard > 0.45), 'apron'),
  /* The apron's CORE. The class above is drawn on distances taken straight from
   * `_distances`, but `_splat` displaces those same distances by up to four
   * metres of `edge` noise before it thresholds them, so a band of pixels
   * either side of hard = 0.45 is classified as apron here and as open ground
   * there. That band is the worn margin and it is legitimately in scope; the
   * laid ballast and the asphalt are not, and this is the class that says so. */
  stat(terrace.filter(p => p.hard > 0.90), 'apronCore'),
  /* And DEEP inside it — six metres past the edge, i.e. more than the 3.6 m
   * vertex cell the traffic attribute is interpolated across. If a change is
   * visible in apronCore and not here, what moved is the apron's worn margin
   * and not the laid surface. */
  stat(terrace.filter(p => p.hard > 0.90 && p.dTraf < -6), 'apronDeep'),
  /* the five things the charge names */
  stat(flat.filter(p => p.dTraf < 4), 'trafficked'),
  stat(flat.filter(p => p.dTraf > 18), 'untrafficked'),
  stat(flat.filter(p => p.moved > 3), 'onFill'),
  stat(flat.filter(p => p.moved < -3), 'onCut'),
  stat(flat.filter(p => p.flow > 0.25), 'lowSpot'),
  stat(flat.filter(p => p.flow < 0.02), 'notLow'),
  /* and the reference surfaces that must NOT move */
  stat(hits.filter(p => p.aw > 3.2 && p.aw < 8 && p.deg < 8 && p.dFoot > 60), 'dryBeach'),
  stat(hits.filter(p => p.aw <= 2.5 && p.deg < 12), 'wetBand'),
  stat(hits.filter(p => p.bm < 0.1 && p.dFoot > 150 && p.deg < 12), 'openCountry'),
];
const S = Object.fromEntries(classes.map(c => [c.cls, c]));
const dS = (A, B) => (A.n < 8 || B.n < 8) ? {dL: null, dRB: null, dSat: null}
  : {dL: r2(A.L - B.L), dRB: r2(A.RB - B.RB), dSat: r2(A.sat - B.sat)};

console.log(JSON.stringify({
  mods, cam, time, ablate: !!a.ablate, pixels: hits.length, settled,
  errors: errors.slice(0, 3), pin: a.pin || null, expo, exposureAfterLock: expo2,
  classes,
  separations: {
    traffickedVsNot: dS(S.trafficked, S.untrafficked),
    fillVsCut: dS(S.onFill, S.onCut),
    lowVsNot: dS(S.lowSpot, S.notLow),
    plateauVsBeach: dS(S.terraceOpenFlat, S.dryBeach),
    plateauVsCountry: dS(S.terraceOpenFlat, S.openCountry),
  },
  /* THE headline: how much information the biggest surface carries */
  information: {
    terraceOpenFlat: {sigma: S.terraceOpenFlat.sigma, step: S.terraceOpenFlat.step,
                      n: S.terraceOpenFlat.n},
    openCountry: {sigma: S.openCountry.sigma, step: S.openCountry.step},
    dryBeach: {sigma: S.dryBeach.sigma, step: S.dryBeach.step},
    apron: {sigma: S.apron.sigma, step: S.apron.step},
  },
}, null, 1));
await b.close();
