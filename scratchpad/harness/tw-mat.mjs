/* tw-mat.mjs — DOES THE BARE GROUND CARRY MORE THAN ONE MATERIAL?
 *
 * The round-30 art direction, verbatim: "A's bare ground is a single
 * low-frequency tan wash across the entire plateau and both dirt flanks", and
 * "your dry sand and your bare plateau dirt are THE SAME TAN AT THE SAME VALUE,
 * so there is no berm crest and no material boundary", and "the faces carry the
 * identical tan diffuse as the flat pads on either side".
 *
 * All three are one question with three windows on it, so this asks it once.
 * Pixels are classified GEOMETRICALLY — unprojected, marched against
 * terrain.heightAt, then bucketed on facts the renderer cannot fake (elevation
 * over the waterline, slope, distance to the earthworks, which side of a bench
 * riser) — exactly the way tq-value.mjs does it, because colour-thresholded
 * masks have given this project false answers four times.
 *
 * Reported, per class: n, mean RGB, L, R-B (the warm/cool axis), and HSV
 * saturation. Plus the pairwise separations that ARE the complaint:
 *
 *   sandVsPlateau   dry beach against inland bare ground: dL, dRB, dSat.
 *                   The critic's "same tan at the same value" is dL ~ 0.
 *   faceVsPad       a bench batter against the pads either side of it, matched
 *                   for distance. "A 26.6 degree batter and a 0 degree bench ARE
 *                   THE SAME COLOUR" is dL ~ 0.
 *   crestStep       the largest single-pixel-row luminance step found walking
 *                   DOWN the frame through a riser crest. The "hard dark line
 *                   along its crest" is this number; a rounded crest gives ~0.
 *
 * And the geometry the renderer actually shipped, which is NOT what bx-face.mjs
 * measures: bx-face walks `_benchLevelAt` and `_designAt`, both ANALYTIC. This
 * walks `heightAt`, i.e. the built mesh after `_gradeTo`, `_railGrade` and the
 * two smoothing passes, which is what a photon meets.
 *
 *   node tw-mat.mjs [--mods sky,gi,terrain] [--cam far] [--grid 300] [--time 9]
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'sky,gi,terrain';
const cam = a.cam || 'far';
const G = +(a.grid || 300);
const time = a.time || '9';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
/* --ablate takes BOTH halves of the substrate change out (the splat weights and
 * the shader tints) before the world builds, so a before/after is two page
 * loads a minute apart in one session rather than two sessions. Two other
 * rounds are live in this world and a parallel sky change moved every RGB in
 * this frame between two of my own runs; a cross-session delta is not a
 * measurement. */
if (a.ablate) await page.addInitScript(() => { window.__lemAblateSubstrate = true; });
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

/* gi.js runs an ADAPTIVE exposure off scene luminance (gi.js `_applyGrade`,
 * `uExposure`), so a change that darkens the land lifts everything else in the
 * frame — including the sea and the strand, which this round does not touch.
 * Any before/after on this project has to read the meter as well as the pixel. */
const expo = await page.evaluate(() => {
  const g = window.__lemWorld.subsystems.get('gi');
  return g ? {exposure: +(+g.exposure).toFixed(4),
              sceneEVLow: g._sceneEVLow === undefined ? null : +(+g._sceneEVLow).toFixed(4)} : null;
});

/* ---- 1. the geometry that shipped, walked off heightAt --------------------- */
const geom = await page.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const T = t._terrace;
  const r4 = v => +(+v).toFixed(4);
  if (!T) return {terrace: false};
  const risers = T.risers.map(r => {
    const zc = r.z0 + r.run * 0.5;
    /* down the middle of the site, i.e. where the bench mask is 1 */
    const x = t.cx;
    let meshBest = 0, meshZ = 0, designBest = 0;
    const prof = [];
    for (let z = zc - 45; z <= zc + 45; z += 0.25) {
      const gm = (t.heightAt(x, z + 0.25) - t.heightAt(x, z - 0.25)) / 0.5;
      const gd = (t._designAt(x, z + 0.25) - t._designAt(x, z - 0.25)) / 0.5;
      if (Math.abs(gm) > Math.abs(meshBest)) { meshBest = gm; meshZ = z; }
      if (Math.abs(gd) > Math.abs(designBest)) designBest = gd;
    }
    for (let z = zc - 24; z <= zc + 24; z += 2)
      prof.push([+z.toFixed(0), r4(t.heightAt(x, z))]);
    return {
      nominalDeg: r4(Math.atan(Math.abs(r.rise) / r.run) * 180 / Math.PI),
      designDeg: r4(Math.atan(Math.abs(designBest)) * 180 / Math.PI),
      meshDeg: r4(Math.atan(Math.abs(meshBest)) * 180 / Math.PI),
      riseM: r4(r.rise), runM: r4(r.run), zc: r4(zc), meshZ: r4(meshZ),
      /* how much of the analytic step survived into the mesh, as a ratio of
       * the steepest gradient */
      kept: r4(Math.abs(meshBest) / Math.max(1e-6, Math.abs(designBest))),
      profile: prof,
    };
  });
  return {terrace: true, risers, cx: r4(t.cx), cz: r4(t.cz),
          bands: T.bands.map(b => ({id: b.id, level: r4(b.level),
                                    z0: r4(b.z0), z1: r4(b.z1),
                                    x0: r4(b.x0), x1: r4(b.x1)}))};
});

/* ---- 2. classify every pixel ---------------------------------------------- */
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
  const T = t._terrace;
  const risers = T ? T.risers.map(r => ({z0: r.z0, z1: r.z0 + r.run,
                                         zc: r.z0 + r.run * 0.5, rise: r.rise})) : [];
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
            const g = o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m);
            if (g <= 0) hi = m; else lo = m;
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
      const ny = 1 / Math.sqrt(1 + sl * sl);
      /* the two facts that decide "worked": how far from the earthworks, and
       * how far the finished ground stands from the natural ground */
      const dFoot = t._distances ? (() => { const q = new Float32Array(4);
        t._distances(x, z, q); return Math.min(q[0], t._railDist(x, z)); })() : 1e9;
      const nat = t._smoothBase ? t._smoothBase(x, z) : NaN;
      let riser = -1;
      if (T) for (let k = 0; k < risers.length; k++) {
        if (z > risers[k].z0 - 6 && z < risers[k].z1 + 6
            && t._benchMask(x, z) > 0.6) riser = k;
      }
      out.push({i, j, H, x: +x.toFixed(1), z: +z.toFixed(1),
                aw: +(h - t.waterY).toFixed(2),
                deg: +(Math.atan(sl) * 180 / Math.PI).toFixed(2),
                ny: +ny.toFixed(3), nz: +(-gz * ny).toFixed(3),
                dFoot: Math.round(dFoot),
                cut: +(h - nat).toFixed(2),
                mask: T ? +t._benchMask(x, z).toFixed(2) : 0,
                riser, dist: Math.round(hit)});
    }
  }
  return out;
}, {G});

const buf = await page.screenshot({type: 'png'});
if (a.out) fs.writeFileSync(a.out, buf);
const px = await page.evaluate(async ({src, uv}) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas');
  cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  return uv.map(([u, v]) => {
    const X = Math.min(im.width - 1, Math.round(u * im.width));
    const Y = Math.min(im.height - 1, Math.round(v * im.height));
    const o = (Y * im.width + X) * 4;
    return [d[o], d[o + 1], d[o + 2]];
  });
}, {src: 'data:image/png;base64,' + buf.toString('base64'),
    uv: hits.map(p => [(p.i + 0.5) / G, (p.j + 0.5) / p.H])});
hits.forEach((p, i) => {
  p.r = px[i][0]; p.g = px[i][1]; p.b = px[i][2];
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
  dist: Math.round(mean(v.map(p => p.dist))),
  deg: r2(mean(v.map(p => p.deg))),
});

/* the classes. All of them are BARE GROUND questions, so vegetation is off by
 * default and the canopy is not a confound. */
const beach = hits.filter(p => p.aw > 3.2 && p.aw < 8 && p.ny > 0.90 && p.dFoot > 60);
const wetband = hits.filter(p => p.aw <= 2.5 && p.ny > 0.86);
/* inland bare ground OUTSIDE the site: the plateau and the dirt flanks */
const plateau = hits.filter(p => p.aw > 16 && p.deg < 12 && p.dFoot > 90);
const flank = hits.filter(p => p.aw > 10 && p.deg >= 12 && p.deg < 30 && p.dFoot > 90);
/* the worked ground */
const pad = hits.filter(p => p.mask > 0.9 && p.riser < 0 && p.deg < 4);
const face = hits.filter(p => p.riser >= 0 && p.deg > 12);
const cutg = hits.filter(p => p.cut < -2.5 && p.dFoot < 40);
const fillg = hits.filter(p => p.cut > 2.5 && p.dFoot < 40);

/* the crest step: walk each image column through a riser and find the biggest
 * luminance jump between two vertically adjacent classified pixels that
 * straddle the riser's top edge. */
const byIJ = new Map();
for (const p of hits) byIJ.set(p.i + ',' + p.j, p);
let crestStep = 0, crestAt = null;
const steps = [];
for (const p of hits) {
  if (p.riser < 0) continue;
  const up = byIJ.get(p.i + ',' + (p.j - 1));
  const dn = byIJ.get(p.i + ',' + (p.j + 1));
  for (const q of [up, dn]) {
    if (!q) continue;
    const s = Math.abs(p.L - q.L);
    steps.push(s);
    if (s > crestStep) { crestStep = s; crestAt = [p.x, p.z, r2(p.L), r2(q.L)]; }
  }
}
steps.sort((u, v) => u - v);
const pct = q => steps.length ? r2(steps[Math.min(steps.length - 1, Math.floor(steps.length * q))]) : NaN;

/* tq-shore's OWN windows, reproduced here for one reason: its dry-strand window
 * is 6-14 m above the waterline and the berm crest this round introduces sits at
 * 8-9 m, so the top half of that window is now inland material by construction
 * and 'wetBandDrop' moves without the band having moved. Measured in the same
 * ablation pair, these say which it was. */
const shWet = hits.filter(p => p.aw <= 3);
const shDry = hits.filter(p => p.aw > 6 && p.aw <= 14);
const shDryLow = hits.filter(p => p.aw > 3 && p.aw <= 8);

const classes = [stat(wetband, 'wetBand'), stat(beach, 'dryBeach'),
                 stat(plateau, 'plateau'), stat(flank, 'dirtFlank'),
                 stat(pad, 'benchPad'), stat(face, 'benchFace'),
                 stat(cutg, 'cutGround'), stat(fillg, 'fillGround')];

const dS = (A, B) => ({dL: r2(A.L - B.L), dRB: r2(A.RB - B.RB), dSat: r2(A.sat - B.sat)});
const S = Object.fromEntries(classes.map(c => [c.cls, c]));

console.log(JSON.stringify({
  mods, cam, time, pixels: hits.length, settled, errors: errors.slice(0, 3), expo,
  geom,
  classes,
  separations: {
    sandVsPlateau: dS(S.dryBeach, S.plateau),
    sandVsFlank: dS(S.dryBeach, S.dirtFlank),
    faceVsPad: dS(S.benchFace, S.benchPad),
    cutVsFill: dS(S.cutGround, S.fillGround),
    wetVsDry: dS(S.wetBand, S.dryBeach),
  },
  /* tq-value's own profileNear bins, reproduced so the berm crest can be shown
   * in an ABLATION PAIR rather than against a number from another session. */
  profileNear: [[0,1],[1,2],[2,3],[3,5],[5,8],[8,12],[12,20],[20,35]].map(([lo,hi]) => {
    const v = hits.filter(p => p.aw > lo && p.aw <= hi && p.dist < 700);
    return {m: lo + '-' + hi, n: v.length, L: r2(mean(v.map(p => p.L))),
            dist: Math.round(mean(v.map(p => p.dist)))};
  }),
  shoreWindows: {
    tqShoreWet0_3: stat(shWet, 'wet0-3'),
    tqShoreDry6_14: stat(shDry, 'dry6-14'),
    sandOnly3_8: stat(shDryLow, 'dry3-8'),
    wetBandDrop_tqShoreWindow: r2(mean(shWet.map(p => p.L)) / mean(shDry.map(p => p.L))),
    wetBandDrop_sandWindow: r2(mean(shWet.map(p => p.L)) / mean(shDryLow.map(p => p.L))),
  },
  crest: {stepMaxL: r2(crestStep), at: crestAt, n: steps.length,
          p50: pct(0.5), p90: pct(0.9), p99: pct(0.99)},
}, null, 1));
await b.close();
