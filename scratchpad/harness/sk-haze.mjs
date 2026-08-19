/* sk-haze.mjs — HOW MUCH OF THIS PIXEL IS AIR?
 *
 * The terrain round measured the ground material's share of a pixel by zeroing
 * its albedo. That answers "how much does the material paint" but not "why",
 * and it cannot separate distance from the fog model's HEIGHT term. This does.
 *
 * Pixels are classified geometrically (unproject, march terrain.heightAt) the
 * way tq-value.mjs does — colour masks have lied on this project repeatedly.
 * For every classified ground hit it then evaluates the EXACT fog chunk sky.js
 * compiled into every material, read back out of THREE.ShaderChunk in the live
 * page rather than copied from the source, so the instrument cannot drift from
 * the thing it measures:
 *
 *     fogFactor = MAX * (1 - exp(-(density*depth*heightTerm)^P * K))
 *
 * and reports, per band of height-above-water and per distance band:
 *   f       the fog factor (luminance-weighted over the three channels)
 *   hterm   the height term alone — the multiplier on optical depth that the
 *           beach gets and the hilltop does not
 *   L / L0  rendered luminance with the fog live and with it pinned to ~0, in
 *           the SAME page session, so the material's share of the pixel is
 *           (1-f)*L0/L and can be checked against f directly.
 *
 *   node sk-haze.mjs [--mods sky,gi,terrain] [--cam far] [--grid 200]
 *                    [--time 9] [--fog '{"h":260}'] [--pin 0.00042] [--out x.png]
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'sky,gi,terrain';
const cam = a.cam || 'far';
const G = +(a.grid || 200);
const time = a.time || '9';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
          + `&cam=${cam}&time=${time}&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 200)); });
if (a.fog) await page.addInitScript(`window.__lemFog = ${a.fog};`);
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});

/* settle on shot.mjs's rule */
let settled = false, stable = 0, prev = null;
const t1 = Date.now();
while (Date.now() - t1 < 25000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => {
    const s = window.__lemWorld?.stats ? window.__lemWorld.stats() : null;
    return s ? [s.drawCalls, s.triangles] : null;
  });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 10) { settled = true; break; }
}
const settledMs = Date.now() - t1;

if (a.pin) await page.evaluate(p => window.__lemWorld.subsystems.get('sky').setFogDensity(+p), a.pin);
await page.waitForTimeout(400);

/* --- the fog chunk, as compiled ------------------------------------------ */
const chunk = await page.evaluate(async () => {
  try {
    const T = await import('three');
    const src = T.ShaderChunk?.fog_fragment || '';
    const num = re => { const m = src.match(re); return m ? m.slice(1).map(Number) : null; };
    return {
      patched: !!T.ShaderChunk.__lemAerial,
      H: num(/lemA = exp\( - max\( lemH0, -600.0 \) \/ ([\d.]+) \)/)?.[0],
      P: num(/pow\( max\( lemTau, 1e-5 \), ([\d.]+) \)/)?.[0],
      MAX: num(/vec3\( ([\d.]+) \)\s*\n?\s*\* \( 1.0 - exp/)?.[0],
      K: num(/exp\( - lemU \* vec3\( ([\d.]+), ([\d.]+), ([\d.]+) \) \)/),
      /* 2026-08-09: the chunk grew a baked scale and a clear shell. Parsed the
       * same way as everything else here — out of the compiled string, so this
       * instrument still cannot drift from the thing it measures. A build
       * without them reads S = 1, T0 = 0, W = 0 and the maths below is
       * unchanged, so old captures stay comparable. */
      S: num(/lemTau = fogDensity \* ([\d.]+) \* vFogDepth/)?.[0] ?? 1,
      T0: num(/lemX = lemTau - ([\d.]+)/)?.[0]
          ?? num(/lemTau = max\( lemTau - ([\d.]+)/)?.[0] ?? 0,
      SW: num(/\+ ([\d.]+) \* log\( 1\.0 \+ exp/)?.[0] ?? 0,
      raw: src.slice(0, 40),
    };
  } catch (e) { return {err: String(e).slice(0, 160)}; }
});

/* --- geometry + fog factor, in the page ---------------------------------- */
const probe = async (fogOn) => page.evaluate(({G, fogOn, K, H, P, MAX, S, T0, SW}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  const f = w.scene.fog;
  const o = {x: cam.position.x, y: cam.position.y, z: cam.position.z};
  const e = cam.matrixWorld.elements;
  const bx = {x: e[0], y: e[1], z: e[2]};
  const by = {x: e[4], y: e[5], z: e[6]};
  const bz = {x: e[8], y: e[9], z: e[10]};       // camera +Z; forward is -bz
  const ty = Math.tan(cam.fov * Math.PI / 360), tx = ty * cam.aspect;
  const HH = Math.round(G * 9 / 16);
  const out = [];
  const d = 3.0;
  const density = f ? f.density : 0;
  for (let j = 0; j < HH; j++) {
    for (let i = 0; i < G; i++) {
      const nx = ((i + 0.5) / G) * 2 - 1, ny0 = 1 - ((j + 0.5) / HH) * 2;
      const cxr = nx * tx, cyr = ny0 * ty;
      let vx = bx.x * cxr + by.x * cyr - bz.x;
      let vy = bx.y * cxr + by.y * cyr - bz.y;
      let vz = bx.z * cxr + by.z * cyr - bz.z;
      const L = Math.hypot(vx, vy, vz); vx /= L; vy /= L; vz /= L;
      let pr = 0, hit = -1, step = 4;
      for (let s = step; s < 9000; s += step) {
        const gap = o.y + vy * s - t.heightAt(o.x + vx * s, o.z + vz * s);
        if (gap <= 0) {
          let lo = pr, hi = s;
          for (let k = 0; k < 24; k++) {
            const m = (lo + hi) * 0.5;
            const g = o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m);
            if (g <= 0) hi = m; else lo = m;
          }
          hit = (lo + hi) * 0.5; break;
        }
        pr = s; step = Math.min(60, Math.max(4, gap * 0.55));
      }
      if (hit < 0) continue;
      const x = o.x + vx * hit, z = o.z + vz * hit;
      const h = t.heightAt(x, z);
      if (h <= t.waterY + 0.05) continue;
      const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
      const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
      const sl = Math.hypot(gx, gz);
      const nyw = 1 / Math.sqrt(1 + sl * sl);

      /* the fog chunk, exactly */
      const depth = -( -(x - o.x) * bz.x - (h - o.y) * bz.y - (z - o.z) * bz.z ) * -1;
      const dep = (x - o.x) * -bz.x + (h - o.y) * -bz.y + (z - o.z) * -bz.z;
      const h0 = o.y, h1 = h;
      const A = Math.exp(-Math.max(h0, -600) / H);
      const B = Math.exp(-Math.max(h1, -600) / H);
      const dy = h1 - h0;
      let avg = Math.abs(dy) < 1 ? 0.5 * (A + B) : H * (A - B) / dy;
      avg = Math.min(Math.max(avg, 0), 6);
      let tau = density * S * dep * avg;
      if (T0 > 0) { const xs = tau - T0;
        tau = SW > 0 ? Math.max(xs, 0) + SW * Math.log(1 + Math.exp(-Math.abs(xs) / SW))
                     : Math.max(xs, 0); }
      const u = Math.pow(Math.max(tau, 1e-5), P);
      const ff = K.map(k => MAX * (1 - Math.exp(-u * k)));
      const fl = 0.2126 * ff[0] + 0.7152 * ff[1] + 0.0722 * ff[2];

      out.push({i, j, H: HH, aw: +(h - t.waterY).toFixed(2),
                deg: +(Math.atan(sl) * 180 / Math.PI).toFixed(2),
                nz: +(-gz * nyw).toFixed(3), dist: Math.round(hit),
                dep: Math.round(dep), avg: +avg.toFixed(3), f: +fl.toFixed(4),
                fb: +ff[2].toFixed(4), fr: +ff[0].toFixed(4)});
    }
  }
  return {hits: out, camY: +o.y.toFixed(1), density,
          fogColour: [f.color.r, f.color.g, f.color.b].map(v => +v.toFixed(4)),
          waterY: t.waterY, tier: w.stats().tier};
}, {G, fogOn, K: chunk.K, H: chunk.H, P: chunk.P, MAX: chunk.MAX,
     S: chunk.S ?? 1, T0: chunk.T0 ?? 0, SW: chunk.SW ?? 0});

const readLums = async (hits, out) => {
  const buf = await page.screenshot({type: 'png'});
  if (out) fs.writeFileSync(out, buf);
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
      const q = (Y * im.width + X) * 4;
      return +(0.2126 * dd[q] + 0.7152 * dd[q + 1] + 0.0722 * dd[q + 2]).toFixed(1);
    });
  }, {src: 'data:image/png;base64,' + buf.toString('base64'),
      uv: hits.map(p => [(p.i + 0.5) / G, (p.j + 0.5) / p.H])});
};

const base = await probe(true);
const Lon = await readLums(base.hits, a.out);
base.hits.forEach((p, i) => { p.L = Lon[i]; });

/* the same pixels with the haze taken out — same session, same frame layout */
await page.evaluate(() => window.__lemWorld.subsystems.get('sky').setFogDensity(1e-9));
await page.waitForTimeout(500);
const Loff = await readLums(base.hits, a.out ? a.out.replace(/\.png$/, '') + '.nofog.png' : null);
base.hits.forEach((p, i) => { p.L0 = Loff[i]; });

const mean = v => v.length ? v.reduce((s, x) => s + x, 0) / v.length : NaN;
const r = x => +(+x).toFixed(3);
const grp = (name, sel) => {
  const v = base.hits.filter(sel);
  if (!v.length) return {name, n: 0};
  const L = mean(v.map(p => p.L)), L0 = mean(v.map(p => p.L0)), f = mean(v.map(p => p.f));
  return {name, n: v.length, dist: Math.round(mean(v.map(p => p.dist))),
          dep: Math.round(mean(v.map(p => p.dep))),
          hterm: r(mean(v.map(p => p.avg))), f: r(f),
          fB: r(mean(v.map(p => p.fb))), fR: r(mean(v.map(p => p.fr))),
          L: r(L), L0: r(L0), groundShare: r((1 - f) * L0 / L)};
};

const bands = [
  grp('waterline 0-2m', p => p.aw <= 2),
  grp('strand 2-6m', p => p.aw > 2 && p.aw <= 6),
  grp('strand 6-14m', p => p.aw > 6 && p.aw <= 14),
  grp('inland >20m', p => p.aw > 20),
  grp('sunward 8-34deg', p => p.aw > 8 && p.deg > 8 && p.deg < 34 && p.nz > 0.18),
  grp('lee 8-34deg', p => p.aw > 8 && p.deg > 8 && p.deg < 34 && p.nz < -0.18),
];
const D = [[0, 300], [300, 600], [600, 900], [900, 1300], [1300, 2400]];
const dist = D.map(([lo, hi]) => grp(`${lo}-${hi}m`, p => p.dist >= lo && p.dist < hi));

/* wet band, as it survives to the pixel and as the material sees it */
const wet = base.hits.filter(p => p.aw <= 3);
const dryS = base.hits.filter(p => p.aw > 6 && p.aw <= 14);
console.log(JSON.stringify({
  mods, cam, time, tier: base.tier, camY: base.camY, density: base.density,
  waterY: base.waterY, fogColour: base.fogColour, chunk,
  pixels: base.hits.length, settled, settledMs, errors: errors.slice(0, 3),
  wetBandDrop: r(mean(wet.map(p => p.L)) / mean(dryS.map(p => p.L))),
  wetBandDropNoFog: r(mean(wet.map(p => p.L0)) / mean(dryS.map(p => p.L0))),
  bands, dist,
}, null, 1));
await b.close();
