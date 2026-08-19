/* tq-shore.mjs — the two things terrain round 15 claims, measured.
 *
 * tq-value.mjs buckets ground pixels by the world normal's Z component, because
 * when it was written the ground's lee term was anchored to +Z ("facing the NOON
 * sun"). The judged frames are at time=9, where the sun stands in the NE, so a
 * +Z bucket is not the lee at all. This probe buckets by the LIVE sun azimuth
 * taken from terrain's own `_skyState()`, which is the same number the shader
 * gets, so "does the ground turn away from the key light" is asked against the
 * key light that is actually in the frame.
 *
 * It also measures the waterline from BOTH sides, because the previous round
 * measured a wet band in the material while the critic reported none in the
 * frame, and both were true:
 *
 *   - LAND side: mean rendered L against metres above the waterline.
 *   - SEA side:  mean rendered L against metres of DEPTH under the water, which
 *                is where the foam and the shallow shelf live.
 *   - and the BATHYMETRY itself, with no renderer in it at all: how far offshore
 *     the bed passes 0.75 m and 4.2 m, walked on 180 bearings. If the surf line
 *     is a geometric offset of the coastline those two widths are constants
 *     round the island; if the bottom decides, they are not. `w075cv` / `w420cv`
 *     are their coefficients of variation and are THE number for that claim.
 *
 * Pixels are classified GEOMETRICALLY — unproject, march against
 * `terrain.heightAt`, keep the world hit — never by colour, for the reason
 * tq-value.mjs records.
 *
 *   node tq-shore.mjs [--mods all|sky,gi,terrain] [--cam far] [--time 9]
 *                     [--grid 260] [--out /tmp/x.png]
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'sky,gi,terrain';
const cam = a.cam || 'far';
const G = +(a.grid || 260);
const time = a.time || '9';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html`
          + `?mods=${mods === 'all' ? '' : mods}`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});

/* shot.mjs's settle rule: draws and triangles held still across ten samples. */
let settled = false, stable = 0, prev = null;
const t1 = Date.now();
while (Date.now() - t1 < 40000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => {
    const s = window.__lemWorld && window.__lemWorld.stats ? window.__lemWorld.stats() : null;
    return s ? [s.drawCalls, s.triangles] : null;
  });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 10) { settled = true; break; }
}
const settledMs = Date.now() - t1;

/* ---- the bathymetry, with no renderer in it ------------------------------ */
const bathy = await page.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const wy = t.waterY, cx = t.cx, cz = t.cz;
  const R = t.islandR || 400;
  const w075 = [], w420 = [], w1400 = [];
  /* the same three, measured in `_islandSD` rather than in radial metres. A
   * radial walk across an oblique stretch of coast overstates the width by
   * 1/cos(obliquity) and that alone puts variation in the number, which is
   * exactly the confound this question cannot afford. */
  const s075 = [], s420 = [], s1400 = [];
  const NB = 180;
  for (let i = 0; i < NB; i++) {
    const ang = (i / NB) * Math.PI * 2;
    const ux = Math.cos(ang), uz = Math.sin(ang);
    /* find the coastline on this bearing: the outermost r where the ground is
     * still above the water, stepping in from well outside. */
    let coast = -1;
    for (let r = R * 2.2; r > R * 0.2; r -= 2) {
      if (t.heightAt(cx + ux * r, cz + uz * r) > wy) { coast = r; break; }
    }
    if (coast < 0) continue;
    let a = -1, c = -1, d = -1, sa = -1, sc = -1, sd = -1;
    for (let s = 0; s < 900; s += 2) {
      const px = cx + ux * (coast + s), pz = cz + uz * (coast + s);
      const dep = wy - t.heightAt(px, pz);
      const isd = t._islandSD(px, pz);
      if (a < 0 && dep >= 0.75) { a = s; sa = isd; }
      if (c < 0 && dep >= 4.2) { c = s; sc = isd; }
      if (d < 0 && dep >= 14.0) { d = s; sd = isd; break; }
    }
    if (a >= 0) { w075.push(a); s075.push(sa); }
    if (c >= 0) { w420.push(c); s420.push(sc); }
    if (d >= 0) { w1400.push(d); s1400.push(sd); }
  }
  const st = v => {
    if (!v.length) return {n: 0};
    const m = v.reduce((s, x) => s + x, 0) / v.length;
    const sd = Math.sqrt(v.reduce((s, x) => s + (x - m) ** 2, 0) / v.length);
    const so = v.slice().sort((p, q) => p - q);
    return {n: v.length, mean: +m.toFixed(1), sd: +sd.toFixed(1),
            cv: +(sd / m).toFixed(3), p10: so[(v.length * 0.1) | 0],
            p90: so[(v.length * 0.9) | 0], min: so[0], max: so[so.length - 1]};
  };
  return {w075: st(w075), w420: st(w420), w1400: st(w1400),
          sd075: st(s075), sd420: st(s420), sd1400: st(s1400)};
});

/* ---- pixels -------------------------------------------------------------- */
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
  /* The key light, from the same method the ground shader's uniform comes from,
   * so the bucket axis and the shading axis cannot disagree. */
  const sd3 = t._skyState().dir;
  const sl = Math.hypot(sd3.x, sd3.z) || 1;
  const sax = sd3.x / sl, saz = sd3.z / sl;
  const out = [];
  const d = 3.0;
  for (let j = 0; j < H; j++) {
    for (let i = 0; i < G; i++) {
      const ndcX = ((i + 0.5) / G) * 2 - 1, ndcY = 1 - ((j + 0.5) / H) * 2;
      const cxr = ndcX * tx, cyr = ndcY * ty;
      let vx = bxv.x * cxr + byv.x * cyr - bzv.x;
      let vy = bxv.y * cxr + byv.y * cyr - bzv.y;
      let vz = bxv.z * cxr + byv.z * cyr - bzv.z;
      const L = Math.hypot(vx, vy, vz); vx /= L; vy /= L; vz /= L;
      let prevS = 0, hit = -1, step = 4;
      for (let s = step; s < 9000; s += step) {
        const gap = o.y + vy * s - t.heightAt(o.x + vx * s, o.z + vz * s);
        if (gap <= 0) {
          let lo = prevS, hi = s;
          for (let k = 0; k < 24; k++) {
            const m = (lo + hi) * 0.5;
            const g = o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m);
            if (g <= 0) hi = m; else lo = m;
          }
          hit = (lo + hi) * 0.5; break;
        }
        prevS = s; step = Math.min(60, Math.max(4, gap * 0.55));
      }
      /* Where does this ray meet the water PLANE? If that is nearer than the
       * ground hit (or there is no ground hit), the pixel is sea and its fact is
       * the DEPTH under it. */
      let seaT = -1;
      if (vy < -1e-6) {
        const tw = (t.waterY - o.y) / vy;
        if (tw > 0 && tw < 9000) seaT = tw;
      }
      const isSea = seaT > 0 && (hit < 0 || seaT < hit - 0.5);
      if (isSea) {
        const x = o.x + vx * seaT, z = o.z + vz * seaT;
        const dep = t.waterY - t.heightAt(x, z);
        if (dep <= 0) continue;
        out.push({i, j, H, sea: 1, dep: +dep.toFixed(2), dist: Math.round(seaT)});
        continue;
      }
      if (hit < 0) continue;
      const x = o.x + vx * hit, z = o.z + vz * hit;
      const h = t.heightAt(x, z);
      if (h <= t.waterY + 0.05) continue;
      const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
      const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
      const sp = Math.hypot(gx, gz);
      const ny = 1 / Math.sqrt(1 + sp * sp);
      const nx = -gx * ny, nz = -gz * ny;
      out.push({i, j, H, sea: 0,
                aw: +(h - t.waterY).toFixed(2),
                deg: +(Math.atan(sp) * 180 / Math.PI).toFixed(2),
                /* signed aspect against the LIVE sun azimuth: +1 full sunward,
                 * -1 full lee. Same un-normalised form the shader uses. */
                sunA: +(nx * sax + nz * saz).toFixed(3),
                nz: +nz.toFixed(3), ny: +ny.toFixed(3), dist: Math.round(hit)});
    }
  }
  return {out, sunAz: [+sax.toFixed(3), +saz.toFixed(3)]};
}, {G});

const uvAll = hits.out.map(p => [(p.i + 0.5) / G, (p.j + 0.5) / p.H]);

const readFrame = async (tag) => {
  const buf = await page.screenshot({type: 'png'});
  if (a.out) fs.writeFileSync(a.out.replace(/\.png$/, '') + (tag ? '-' + tag : '') + '.png', buf);
  return page.evaluate(async ({src, uv}) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas');
  cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(im, 0, 0);
  const dd = g.getImageData(0, 0, im.width, im.height).data;
  return uv.map(([u, v]) => {
    const X = Math.min(im.width - 1, Math.round(u * im.width));
    const Y = Math.min(im.height - 1, Math.round(v * im.height));
    const o = (Y * im.width + X) * 4;
    return [+(0.2126 * dd[o] + 0.7152 * dd[o + 1] + 0.0722 * dd[o + 2]).toFixed(1),
            dd[o], dd[o + 1], dd[o + 2]];
  });
}, {src: 'data:image/png;base64,' + buf.toString('base64'), uv: uvAll});
};

/* --ablate <json of [[find, repl], ...]> — patch the ground program IN THE PAGE
 * and read a second frame from the same session, on tq-patch.mjs's own method
 * and for its own reason: a same-session pair cancels the frame-to-frame shift
 * that makes a cross-session before/after unreadable below about 1.6 L. */
const lums = await readFrame(a.ablate ? 'A' : '');
hits.out.forEach((p, i) => { p.L = lums[i][0]; p.r = lums[i][1]; p.g = lums[i][2]; p.b = lums[i][3]; });
let lumsB = null, patchHits = 0;
if (a.ablate) {
  const pairs = JSON.parse(fs.readFileSync(a.ablate, 'utf8'));
  patchHits = await page.evaluate(({pairs}) => {
    const t = window.__lemWorld.subsystems.get('terrain');
    const mats = new Set();
    t.group.traverse(o => { if (o.isMesh && o.material) {
      (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => mats.add(m)); } });
    let found = 0;
    for (const mat of mats) {
      if (typeof mat.onBeforeCompile !== 'function') continue;
      const orig = mat.onBeforeCompile;
      if (orig.length === 0 && orig.toString().length < 40) continue;
      mat.onBeforeCompile = function (sh, rend) {
        orig.call(this, sh, rend);
        for (const [f, r] of pairs) {
          if (sh.fragmentShader.indexOf(f) >= 0) { found++; window.__tqShoreFound = found; }
          sh.fragmentShader = sh.fragmentShader.split(f).join(r);
        }
      };
      /* STABLE key: three re-reads it every frame, and a key that changes every
       * frame recompiles continuously and measures a thrashing renderer. */
      let hk = 0; const kt = JSON.stringify(pairs);
      for (let i = 0; i < kt.length; i++) hk = (hk * 31 + kt.charCodeAt(i)) | 0;
      mat.customProgramCacheKey = () => 'tqshore' + hk;
      mat.needsUpdate = true;
    }
    return found;
  }, {pairs});
  await page.waitForTimeout(3000);
  /* read AFTER the recompile: three calls onBeforeCompile during a render, so a
   * counter read straight after needsUpdate is always zero. */
  patchHits = await page.evaluate(() => window.__tqShoreFound || 0);
  lumsB = await readFrame('B');
}

const H = hits.out;
const mean = v => v.length ? v.reduce((s, x) => s + x, 0) / v.length : NaN;
const sdv = v => { const m = mean(v); return Math.sqrt(mean(v.map(x => (x - m) ** 2))); };
const r2 = x => +(+x).toFixed(2);

const land = H.filter(p => !p.sea);
const sea = H.filter(p => p.sea);
const dry = land.filter(p => p.aw > 8);
const band = dry.filter(p => p.deg > 8 && p.deg < 34);
const sunF = band.filter(p => p.sunA > 0.18).map(p => p.L);
const leeF = band.filter(p => p.sunA < -0.18).map(p => p.L);
/* the same question on the NOON axis tq-value uses, so the two can be compared */
const sunZ = band.filter(p => p.nz > 0.18).map(p => p.L);
const leeZ = band.filter(p => p.nz < -0.18).map(p => p.L);
/* …and held at one distance, so haze cannot answer for either */
const bandN = band.filter(p => p.dist < 700);
const sunFN = bandN.filter(p => p.sunA > 0.18).map(p => p.L);
const leeFN = bandN.filter(p => p.sunA < -0.18).map(p => p.L);

const wet = land.filter(p => p.aw <= 3).map(p => p.L);
const dryS = land.filter(p => p.aw > 6 && p.aw <= 14).map(p => p.L);

const bins = (rows, edges, key) => {
  const o = [];
  for (let k = 0; k + 1 < edges.length; k++) {
    const v = rows.filter(p => p[key] > edges[k] && p[key] <= edges[k + 1]);
    o.push({b: `${edges[k]}-${edges[k + 1]}`, n: v.length, L: r2(mean(v.map(p => p.L))),
            rgb: v.length ? [Math.round(mean(v.map(p => p.r))), Math.round(mean(v.map(p => p.g))),
                             Math.round(mean(v.map(p => p.b)))] : null,
            dist: Math.round(mean(v.map(p => p.dist)))});
  }
  return o;
};

const abl = (() => {
  if (!lumsB) return null;
  const B = hits.out.map((p, i) => ({...p, L: lumsB[i][0]}));
  const grp = rows => r2(mean(rows.map(p => p.L)));
  const pick = (rows, f) => rows.filter(f);
  const lnd = r => r.filter(p => !p.sea);
  const bnd = r => lnd(r).filter(p => p.aw > 8 && p.deg > 8 && p.deg < 34);
  const spread = (r, near) => {
    const b0 = near ? bnd(r).filter(p => p.dist < 700) : bnd(r);
    return r2(grp(pick(b0, p => p.sunA > 0.18)) - grp(pick(b0, p => p.sunA < -0.18)));
  };
  const wetD = r => {
    const b0 = lnd(r).filter(p => p.ny > 0.94);
    return r2(grp(pick(b0, p => p.aw <= 1.2)) - grp(pick(b0, p => p.aw > 2.6 && p.aw <= 4.2)));
  };
  return {patchOccurrences: patchHits,
          note: 'A = the file as it stands, B = the file with the patch applied',
          aspectSunSpreadL: {A: spread(H, false), B: spread(B, false)},
          aspectSunSpreadNear700L: {A: spread(H, true), B: spread(B, true)},
          beachWetDeltaL: {A: wetD(H), B: wetD(B)},
          frameMeanL: {A: r2(mean(H.map(p => p.L))), B: r2(mean(B.map(p => p.L)))}};
})();

console.log(JSON.stringify({
  mods, cam, time, settled, settledMs, errors: errors.slice(0, 3), ablation: abl,
  sunAz: hits.sunAz, landPx: land.length, seaPx: sea.length,
  bathymetry: bathy,
  aspectSun: {sunN: sunF.length, sunL: r2(mean(sunF)),
              leeN: leeF.length, leeL: r2(mean(leeF)),
              spreadL: r2(mean(sunF) - mean(leeF))},
  aspectSunNear700: {sunN: sunFN.length, sunL: r2(mean(sunFN)),
                     leeN: leeFN.length, leeL: r2(mean(leeFN)),
                     spreadL: r2(mean(sunFN) - mean(leeFN))},
  aspectNoonAxis: {sunN: sunZ.length, sunL: r2(mean(sunZ)),
                   leeN: leeZ.length, leeL: r2(mean(leeZ)),
                   spreadL: r2(mean(sunZ) - mean(leeZ))},
  waterline: {wetN: wet.length, wetL: r2(mean(wet)),
              dryStrandN: dryS.length, dryStrandL: r2(mean(dryS)),
              wetBandDrop: r2(mean(wet) / mean(dryS)),
              landProfile: bins(land, [0, 0.6, 1.2, 2, 3, 4.5, 7, 12, 20, 35], 'aw'),
              /* THE wet-band number, and it is deliberately narrow on both ends.
               * "wet vs 6-14 m inland" is not a beach measurement on this island:
               * vegetation.js plants from about 3.5 m up, so the inland half of
               * that ratio is a photograph of trees. This one holds the ground
               * NEARLY LEVEL (ny > 0.94, i.e. no cliff toes) and compares the
               * last metre above the water against the dry strand two to four
               * metres up — both of them sand, both at the same range. */
              beachProfile: bins(land.filter(p => p.ny > 0.94),
                                 [0, 0.6, 1.2, 2, 3, 4.5, 7, 12], 'aw'),
              beachWetDrop: (() => {
                const b = land.filter(p => p.ny > 0.94);
                const w = b.filter(p => p.aw <= 1.2).map(p => p.L);
                const d = b.filter(p => p.aw > 2.6 && p.aw <= 4.2).map(p => p.L);
                return {wetN: w.length, wetL: r2(mean(w)), dryN: d.length,
                        dryL: r2(mean(d)), drop: r2(mean(w) / mean(d)),
                        deltaL: r2(mean(w) - mean(d))};
              })()},
  seaByDepth: bins(sea, [0, 0.4, 0.8, 1.6, 3, 6, 12, 25, 60, 400], 'dep'),
  overall: {meanL: r2(mean(H.map(p => p.L))), sigmaL: r2(sdv(H.map(p => p.L))),
            landMeanL: r2(mean(land.map(p => p.L))), seaMeanL: r2(mean(sea.map(p => p.L)))},
}, null, 1));
await b.close();
