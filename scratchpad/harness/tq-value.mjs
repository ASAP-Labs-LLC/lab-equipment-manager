/* tq-value.mjs — DOES THE BARE GROUND CARRY VALUE?
 *
 * The art-direction complaint, stated as a measurement: "the bare sand/earth is
 * one flat tan value ... it holds the same value on a slope facing the light and
 * a slope facing away, so the island's convexity is invisible; it holds the same
 * value dry as at the waterline, so the beach has no wet band."
 *
 * Colour-thresholded masks have given this project false answers three times, so
 * pixels are classified GEOMETRICALLY, the way islframe.mjs does it: unproject
 * the pixel, march it against terrain.heightAt, and keep the world hit. The
 * pixel's RENDERED luminance is then read from the same frame and bucketed by
 * facts about the hit that the renderer cannot fake — surface aspect, slope, and
 * height above the waterline.
 *
 * Reported:
 *   aspectSpreadL   mean L on sun-facing (+Z) ground minus mean L on lee (-Z)
 *                   ground, at matched slope. THE convexity number. 0 = flat.
 *   slopeSpreadL    mean L on ground under 10 degrees minus mean L over 22.
 *   wetBandDrop     mean L in the 0-3 m band above the waterline, as a fraction
 *                   of mean L in the 6-14 m band. 1.0 = no wet sand.
 *   canopyDrop      mean L under the canopy mask vs open ground at like slope.
 *   sigmaL          spread of L over all bare ground - the "flat value" number.
 *
 * Vegetation is OFF by default: the question is what the GROUND MATERIAL does,
 * and 6,000 trees painted over it answer a different question.
 *
 *   node tq-value.mjs [--mods sky,gi,terrain] [--cam far] [--grid 220] [--time 9]
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'sky,gi,terrain';
const cam = a.cam || 'wide';
const G = +(a.grid || 220);
const time = a.time || '9';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 200)); });
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
/* Wait for the world to stop populating, on shot.mjs's own rule: draws and
 * triangles held still across ten 350 ms samples. A frame judged before this is
 * a photograph of an unfinished world. */
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
  if (stable >= 10) { settled = true; break; }
}
const settledMs = Date.now() - t1;

/* the geometric classification, in the page */
const hits = await page.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  /* Rays from fov and the camera's own basis, as islframe.mjs does: solo.html
   * does not put THREE on window, so `unproject` is not available. */
  const o = {x: cam.position.x, y: cam.position.y, z: cam.position.z};
  const e = cam.matrixWorld.elements;
  const bxv = {x: e[0], y: e[1], z: e[2]};
  const byv = {x: e[4], y: e[5], z: e[6]};
  const bzv = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cam.fov * Math.PI / 360), tx = ty * cam.aspect;
  const H = Math.round(G * 9 / 16);
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
      let prev = 0, hit = -1, step = 4;
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
        prev = s; step = Math.min(60, Math.max(4, gap * 0.55));
      }
      if (hit < 0) continue;
      const x = o.x + vx * hit, z = o.z + vz * hit;
      const h = t.heightAt(x, z);
      if (h <= t.waterY + 0.05) continue;                 // sea, not ground
      const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
      const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
      const sl = Math.hypot(gx, gz);
      const ny = 1 / Math.sqrt(1 + sl * sl);
      /* world normal (-gx, 1, -gz) normalised; aspect = its Z component */
      const nz = -gz * ny;
      out.push({i, j, H, x: +x.toFixed(1), z: +z.toFixed(1),
                aw: +(h - t.waterY).toFixed(2),
                deg: +(Math.atan(sl) * 180 / Math.PI).toFixed(2),
                nz: +nz.toFixed(3), ny: +ny.toFixed(3), dist: Math.round(hit)});
    }
  }
  return out;
}, {G});

/* The frame itself. Decoded back inside the page (Image + a 2D canvas) rather
 * than with a PNG library in node, so this needs no dependency the harness does
 * not already have. */
const buf = await page.screenshot({type: 'png'});
if (a.out) fs.writeFileSync(a.out, buf);
const lums = await page.evaluate(async ({src, uv}) => {
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
    return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(1);
  });
}, {src: 'data:image/png;base64,' + buf.toString('base64'),
    uv: hits.map(p => [(p.i + 0.5) / G, (p.j + 0.5) / p.H])});
hits.forEach((p, i) => { p.L = lums[i]; });

const mean = v => v.length ? v.reduce((s, x) => s + x, 0) / v.length : NaN;
const sd = v => { const m = mean(v); return Math.sqrt(mean(v.map(x => (x - m) ** 2))); };
const r = x => +(+x).toFixed(2);

/* dry ground only for the aspect/slope questions: the waterline band has its own */
const dry = hits.filter(p => p.aw > 8);
/* matched slope so aspect is not measuring "steep things happen to be dark" */
const band = dry.filter(p => p.deg > 8 && p.deg < 34);
const sunFace = band.filter(p => p.nz > 0.18).map(p => p.L);
const leeFace = band.filter(p => p.nz < -0.18).map(p => p.L);

const flat = dry.filter(p => p.deg < 10).map(p => p.L);
const steep = dry.filter(p => p.deg > 22).map(p => p.L);

const wet = hits.filter(p => p.aw <= 3).map(p => p.L);
const dryStrand = hits.filter(p => p.aw > 6 && p.aw <= 14).map(p => p.L);
/* the profile up off the water, which is where a wet band either is or is not */
/* Distance is reported per bin because it is the obvious confound: aerial haze
 * lifts far ground towards the fog colour, and on this island the whole beach
 * ring is further from the camera than the dunes behind it. A profile whose
 * bins do not share a distance is measuring the fog. */
const EDGES = [0, 1, 2, 3, 5, 8, 12, 20, 35];
const near = +(a.near || 1e9);
const prof = hs => {
  const out = [];
  for (let k = 0; k + 1 < EDGES.length; k++) {
    const v = hs.filter(p => p.aw > EDGES[k] && p.aw <= EDGES[k + 1]);
    out.push({m: `${EDGES[k]}-${EDGES[k + 1]}`, n: v.length,
              L: r(mean(v.map(p => p.L))), dist: Math.round(mean(v.map(p => p.dist)))});
  }
  return out;
};
const profile = prof(hits);
/* the same profile with distance held down, so haze cannot answer for it */
const profileNear = prof(hits.filter(p => p.dist < (a.near ? near : 700)));

console.log(JSON.stringify({
  mods, cam, time, pixels: hits.length, settled, settledMs, errors: errors.slice(0, 3),
  aspect: {sunFaceN: sunFace.length, sunFaceL: r(mean(sunFace)),
           leeFaceN: leeFace.length, leeFaceL: r(mean(leeFace)),
           aspectSpreadL: r(mean(sunFace) - mean(leeFace))},
  slope: {flatN: flat.length, flatL: r(mean(flat)),
          steepN: steep.length, steepL: r(mean(steep)),
          slopeSpreadL: r(mean(flat) - mean(steep))},
  waterline: {wetN: wet.length, wetL: r(mean(wet)),
              dryStrandN: dryStrand.length, dryStrandL: r(mean(dryStrand)),
              wetBandDrop: r(mean(wet) / mean(dryStrand)), profile, profileNear},
  overall: {meanL: r(mean(hits.map(p => p.L))), sigmaL: r(sd(hits.map(p => p.L)))},
}, null, 1));
await b.close();
