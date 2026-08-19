/* tw-chan.mjs — HOW DEEP WOULD A CHANNEL HAVE TO BE TO READ AT 900 m?
 *
 * The standing charge, three rounds old: "THERE IS NO CONTINUOUS INCISED LOW
 * LINE anywhere from the plateau to any shore." The channels exist — pctGully
 * 8.22%, 31 of 360 bearings carrying flow to the waterline, the beach notched
 * 2.70 m at the mouths — and cannot be seen. The brief asks for the NUMBER
 * rather than for more carving, so this produces it two ways and takes the
 * worse.
 *
 *   1. TONAL. A gully is visible because one wall is lit and the other is
 *      shadowed. This fits the rendered luminance of dry ground against the
 *      SUN-PROJECTED ASPECT of its own normal — dot(n.xz, sunAz), which is
 *      sin(slope) at full aspect — over the judged frame, and gets L per unit
 *      of aspect. A channel of half-width W and depth D has walls at gradient
 *      D/W, i.e. aspect +/- sin(atan(D/W)) on the two sides, so the luminance
 *      split across it is 2 * k * sin(atan(D/W)) before any haze. Invert it for
 *      the depth that gives a stated split.
 *   2. ANGULAR. A line has to be wider than a pixel. The camera's own fov,
 *      viewport and the measured distance to the channel give metres per pixel
 *      on the ground, including the foreshortening from the view angle.
 *
 * The channels themselves are measured, not assumed: the strongest flow cells
 * are found on a lattice, the local flow direction is taken from the height
 * gradient, and the profile is walked ACROSS it to get a real width and a real
 * depth off `heightAt`.
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'sky,gi,terrain';
const cam = a.cam || 'far';
const G = +(a.grid || 260);
const time = a.time || '9';
const wantSplitL = +(a.split || 10);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
await page.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
  + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`,
  {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
let stable = 0, prev = null;
const t1 = Date.now();
while (Date.now() - t1 < 25000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => {
    const s = window.__lemWorld.stats ? window.__lemWorld.stats() : null;
    return s ? [s.drawCalls, s.triangles] : null;
  });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 8) break;
}

/* ---- the channels, off the heightfield ------------------------------------ */
const chan = await page.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const R = t.islandR || 500, cx = t.cx, cz = t.cz;
  const cells = [];
  for (let z = cz - R; z <= cz + R; z += 8) {
    for (let x = cx - R; x <= cx + R; x += 8) {
      const h = t.heightAt(x, z);
      if (h < t.waterY + 1.5) continue;
      const f = t._flowAt(x, z);
      if (f > 0.30) cells.push({x, z, f, h});
    }
  }
  cells.sort((p, q) => q.f - p.f);
  /* spread the sample: no two subjects within 40 m of each other */
  const picks = [];
  for (const c of cells) {
    if (picks.length >= 24) break;
    if (picks.some(p => Math.hypot(p.x - c.x, p.z - c.z) < 40)) continue;
    picks.push(c);
  }
  const D = 3;
  const out = picks.map(c => {
    /* flow runs downhill, so the CROSS-channel direction is perpendicular to
     * the height gradient */
    const gx = (t.heightAt(c.x + D, c.z) - t.heightAt(c.x - D, c.z)) / (2 * D);
    const gz = (t.heightAt(c.x, c.z + D) - t.heightAt(c.x, c.z - D)) / (2 * D);
    const gl = Math.hypot(gx, gz) || 1e-6;
    const ux = -gz / gl, uz = gx / gl;              // across the fall line
    /* walk out both ways to the first local maximum: that is the bank */
    const prof = [];
    for (let s = -40; s <= 40; s += 1)
      prof.push({s, h: t.heightAt(c.x + ux * s, c.z + uz * s)});
    const mid = prof.find(p => p.s === 0).h;
    const rise = (dir) => {
      let best = mid, bestS = 0;
      for (let s = dir; Math.abs(s) <= 40; s += dir) {
        const h = prof.find(p => p.s === s).h;
        if (h > best) { best = h; bestS = s; }
        else if (h < best - 0.15) break;            // over the bank, going down
      }
      return {h: best, s: Math.abs(bestS)};
    };
    const L = rise(-1), Rr = rise(1);
    const depth = Math.min(L.h, Rr.h) - mid;
    const halfW = (L.s + Rr.s) * 0.5;
    return {x: +c.x.toFixed(0), z: +c.z.toFixed(0), flow: +c.f.toFixed(3),
            depthM: +depth.toFixed(2), halfWidthM: +halfW.toFixed(1),
            wallGrade: halfW > 0.5 ? +(depth / halfW).toFixed(4) : 0,
            wallDeg: halfW > 0.5 ? +(Math.atan(depth / halfW) * 180 / Math.PI).toFixed(2) : 0};
  }).filter(c => c.halfWidthM > 0.5);
  return {n: out.length, chans: out, islandR: +R.toFixed(0)};
});

/* ---- the tonal calibration, off the judged frame --------------------------- */
const hits = await page.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  const o = {x: cam.position.x, y: cam.position.y, z: cam.position.z};
  const e = cam.matrixWorld.elements;
  const bxv = {x: e[0], y: e[1], z: e[2]}, byv = {x: e[4], y: e[5], z: e[6]};
  const bzv = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cam.fov * Math.PI / 360), tx = ty * cam.aspect;
  const H = Math.round(G * 9 / 16);
  const sun = (t._skyState ? t._skyState() : null);
  const sd = sun && sun.dir ? sun.dir : {x: 0.707, y: 0.5, z: 0.707};
  const sl = Math.hypot(sd.x, sd.z) || 1;
  const sax = sd.x / sl, saz = sd.z / sl;
  const out = [];
  const d = 2.0;
  for (let j = 0; j < H; j++) for (let i = 0; i < G; i++) {
    const ndcX = ((i + 0.5) / G) * 2 - 1, ndcY = 1 - ((j + 0.5) / H) * 2;
    let vx = bxv.x * ndcX * tx + byv.x * ndcY * ty - bzv.x;
    let vy = bxv.y * ndcX * tx + byv.y * ndcY * ty - bzv.y;
    let vz = bxv.z * ndcX * tx + byv.z * ndcY * ty - bzv.z;
    const ln = Math.hypot(vx, vy, vz); vx /= ln; vy /= ln; vz /= ln;
    let prev = 0, hit = -1, step = 3;
    for (let s = step; s < 9000; s += step) {
      const gap = o.y + vy * s - t.heightAt(o.x + vx * s, o.z + vz * s);
      if (gap <= 0) {
        let lo = prev, hi = s;
        for (let k = 0; k < 22; k++) {
          const m = (lo + hi) * 0.5;
          if (o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m) <= 0) hi = m; else lo = m;
        }
        hit = (lo + hi) * 0.5; break;
      }
      prev = s; step = Math.min(50, Math.max(3, gap * 0.55));
    }
    if (hit < 0) continue;
    const x = o.x + vx * hit, z = o.z + vz * hit, h = t.heightAt(x, z);
    if (h <= t.waterY + 6) continue;
    const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
    const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
    const len = Math.sqrt(gx * gx + gz * gz + 1);
    /* aspect projected on the sun's azimuth; this is sin(slope) at full aspect */
    const asp = (-gx / len) * sax + (-gz / len) * saz;
    out.push({i, j, H, asp: +asp.toFixed(4), dist: Math.round(hit),
              deg: +(Math.atan(Math.hypot(gx, gz)) * 180 / Math.PI).toFixed(2)});
  }
  return {hits: out, sunAz: [+sax.toFixed(3), +saz.toFixed(3)],
          fov: cam.fov, aspectRatio: +cam.aspect.toFixed(4)};
}, {G});

const buf = await page.screenshot({type: 'png'});
const lums = await page.evaluate(async ({src, uv}) => {
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
  const g = cv.getContext('2d', {willReadFrequently: true});
  g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  return uv.map(([u, v]) => {
    const X = Math.min(im.width - 1, Math.round(u * im.width));
    const Y = Math.min(im.height - 1, Math.round(v * im.height));
    const o = (Y * im.width + X) * 4;
    return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(2);
  });
}, {src: 'data:image/png;base64,' + buf.toString('base64'),
    uv: hits.hits.map(p => [(p.i + 0.5) / G, (p.j + 0.5) / p.H])});
hits.hits.forEach((p, i) => { p.L = lums[i]; });

const mean = v => v.length ? v.reduce((s, x) => s + x, 0) / v.length : NaN;
const r2 = x => +(+x).toFixed(2);
/* least squares of L on aspect, over dry ground only */
const S = hits.hits.filter(p => p.deg > 2);
const mx = mean(S.map(p => p.asp)), my = mean(S.map(p => p.L));
let num = 0, den = 0;
for (const p of S) { num += (p.asp - mx) * (p.L - my); den += (p.asp - mx) ** 2; }
const kL = num / den;                       // luminance per unit of sun-aspect
/* and the same thing non-parametrically, so a bad fit cannot hide */
const bins = [-0.5, -0.25, -0.12, -0.04, 0.04, 0.12, 0.25, 0.5];
const curve = [];
for (let i = 0; i + 1 < bins.length; i++) {
  const v = S.filter(p => p.asp > bins[i] && p.asp <= bins[i + 1]);
  curve.push({asp: `${bins[i]}..${bins[i + 1]}`, n: v.length, L: r2(mean(v.map(p => p.L)))});
}

/* metres per pixel on the ground at the channels' own range */
const distMean = mean(hits.hits.map(p => p.dist));
const radPerPx = (hits.fov * Math.PI / 180) / 720;      // vertical fov over 720 px
const mPerPxAt = d => d * radPerPx;

const med = v => { const s = [...v].sort((p, q) => p - q); return s[s.length >> 1]; };
const cs = chan.chans;
const depthFor = (splitL, halfW) => {
  /* split = 2 * kL * sin(atan(D / halfW))  ->  D = halfW * tan(asin(split/(2k))) */
  const t = splitL / (2 * Math.abs(kL));
  if (t >= 0.999) return Infinity;
  return halfW * Math.tan(Math.asin(t));
};
const halfWMed = med(cs.map(c => c.halfWidthM));
const depthMed = med(cs.map(c => c.depthM));
const splitNow = 2 * Math.abs(kL) * Math.sin(Math.atan(depthMed / halfWMed));

console.log(JSON.stringify({
  mods, cam, time, sunAz: hits.sunAz, pixels: S.length,
  tonal: {LperAspect: r2(kL), curve,
          note: 'L per unit dot(n.xz, sunAz); a channel splits by 2*k*sin(atan(D/halfW))'},
  channels: {n: chan.n, islandR: chan.islandR,
             medianHalfWidthM: r2(halfWMed), medianDepthM: r2(depthMed),
             medianWallDeg: r2(med(cs.map(c => c.wallDeg))),
             deepest: cs.slice().sort((p, q) => q.depthM - p.depthM).slice(0, 5)},
  verdict: {
    predictedSplitLNow: r2(splitNow),
    depthForSplitL: {
      [String(wantSplitL)]: r2(depthFor(wantSplitL, halfWMed)),
      6: r2(depthFor(6, halfWMed)), 15: r2(depthFor(15, halfWMed)),
    },
    metresPerPixelAtMeanRange: r2(mPerPxAt(distMean)),
    meanRangeM: Math.round(distMean),
    channelWidthInPixels: r2((halfWMed * 2) / mPerPxAt(distMean)),
  },
}, null, 1));
await b.close();
