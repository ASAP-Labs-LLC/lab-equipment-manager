/* sk-milk.mjs — WHO LIFTED THE FRAME: the fog, or the exposure that reacted to it?
 *
 * A blind critic called the frame "a milk bath" and said the near plane never
 * reaches full local colour. sky.js's own fog factor at 300-600 m is 0.019, which
 * cannot mix 2% of a pale colour into a pixel and produce that. So either the
 * complaint is about something else, or something is amplifying it.
 *
 * gi.js runs an ADAPTIVE exposure: `_applyGrade` reads a metered log-luminance of
 * the frame (`_sceneEV`) and moves `comp.uExposure`. A haze change moves the meter,
 * the meter moves the exposure, and the exposure moves EVERY pixel including the
 * ones the haze never touched. That is a feedback loop, and if it is real then no
 * fog constant can fix the milk bath.
 *
 * Four states, one page session, so nothing else can move between them:
 *
 *   A  fog live,  exposure adaptive     the shipped frame
 *   B  fog ~0,    exposure adaptive     what sk-haze's "fog off" control really is
 *   C  fog live,  exposure FROZEN at A's value
 *   D  fog ~0,    exposure FROZEN at A's value
 *
 *   A-D  = everything the haze does at a fixed camera stop  (the honest fog cost)
 *   A-B  = what sk-haze and every earlier round measured     (fog + the loop)
 *   B-D  = the exposure loop's own contribution, in bytes    (gi.js's share)
 *
 * Reports whole-frame statistics and, separately, the NEAREST DECILE of ground
 * by true distance — because "the near plane is hazed too" is a claim about
 * pixels the bands never isolate.
 *
 * `--fog` takes the same `globalThis.__lemFog` override the other sky instruments
 * use, so a candidate curve can be measured without editing sky.js. `--fixexp`
 * pins the composite's exposure to a stated value in states C and D, which is
 * the ONLY way to compare two fog curves across page loads: gi.js re-meters the
 * frame, so two configs left to adapt are two different stops.
 *
 *   node sk-milk.mjs [--cam far] [--time 9] [--quality ultra] [--mods ...]
 *                    [--fog '{"p":3.25,"density":0.00064}'] [--fixexp 3.2]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const mods = a.mods || 'sky,gi,terrain,vegetation,buildings,rail,trains';
const cam = a.cam || 'far';
const time = a.time || '9';
const quality = a.quality || 'ultra';
const PIN = +(a.pin || 1e-9);

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}`
          + `&cam=${cam}&time=${time}&hud=0&quality=${quality}&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
if (a.fog) await page.addInitScript(`window.__lemFog = ${a.fog};`);
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});

/* settle geometry */
let stable = 0, prev = null;
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
  if (stable >= 10) break;
}

/* wait for the exposure itself to settle — it is a first-order filter with a
 * 0.95 s time constant on the way up, so geometry stability is not enough. */
const expo = () => page.evaluate(() => {
  const g = window.__lemWorld.subsystems.get('gi');
  const comp = window.__lemWorld.engine?._passes?.composite?.material?.uniforms;
  return {e: g.exposure, analytic: g.analyticExposure, ev: g._sceneEV,
          evLow: g._sceneEVLow, u: comp?.uExposure?.value,
          bp: comp?.uBlackPoint?.value, sat: comp?.uSaturation?.value,
          frozen: !!g.__milkFrozen};
});
const settleExposure = async (label) => {
  let last = null, same = 0;
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(250);
    const e = (await expo()).e;
    if (last !== null && Math.abs(e - last) < 2e-4) same++; else same = 0;
    last = e;
    if (same >= 4) break;
  }
  return last;
};
await settleExposure('A');

/* --- the frame's own statistics ------------------------------------------ */
const stats = async () => {
  const buf = await page.screenshot({type: 'png'});
  return page.evaluate(async ({src}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas');
    cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true});
    g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const L = new Float64Array(im.width * im.height);
    const S = new Float64Array(im.width * im.height);   // saturation, max-min
    let br = 0;
    for (let q = 0, k = 0; q < d.length; q += 4, k++) {
      L[k] = 0.2126 * d[q] + 0.7152 * d[q + 1] + 0.0722 * d[q + 2];
      S[k] = Math.max(d[q], d[q + 1], d[q + 2]) - Math.min(d[q], d[q + 1], d[q + 2]);
      br += d[q + 2] - d[q];
    }
    const srt = Float64Array.from(L).sort();
    const p = t => srt[Math.min(srt.length - 1, Math.floor(t * srt.length))];
    const mean = L.reduce((s, x) => s + x, 0) / L.length;
    const sd = Math.sqrt(L.reduce((s, x) => s + (x - mean) * (x - mean), 0) / L.length);
    /* local contrast: mean |ΔL| between horizontally adjacent pixels, which is
     * the statistic a veil kills and a global lift does not. */
    let ad = 0, n = 0;
    for (let y = 0; y < im.height; y++)
      for (let x = 1; x < im.width; x++) { ad += Math.abs(L[y * im.width + x] - L[y * im.width + x - 1]); n++; }
    return {mean: +mean.toFixed(2), sd: +sd.toFixed(2),
            p1: p(0.01), p5: p(0.05), p50: p(0.5), p95: p(0.95), p99: p(0.99),
            sat: +(S.reduce((s, x) => s + x, 0) / S.length).toFixed(2),
            br: +(br / L.length).toFixed(2),
            adj: +(ad / n).toFixed(3)};
  }, {src: 'data:image/png;base64,' + buf.toString('base64')});
};

/* --- a strip of ground pixels with TRUE distance, for the near plane ------ */
const G = 160;
const geom = await page.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  const o = {x: cam.position.x, y: cam.position.y, z: cam.position.z};
  const e = cam.matrixWorld.elements;
  const bx = {x: e[0], y: e[1], z: e[2]};
  const by = {x: e[4], y: e[5], z: e[6]};
  const bz = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cam.fov * Math.PI / 360), tx = ty * cam.aspect;
  const HH = Math.round(G * 9 / 16);
  const out = [];
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
          for (let k = 0; k < 22; k++) {
            const m = (lo + hi) * 0.5;
            const g2 = o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m);
            if (g2 <= 0) hi = m; else lo = m;
          }
          hit = (lo + hi) * 0.5; break;
        }
        pr = s; step = Math.min(60, Math.max(4, gap * 0.55));
      }
      if (hit < 0) continue;
      const x = o.x + vx * hit, z = o.z + vz * hit;
      const h = t.heightAt(x, z);
      const land = h > t.waterY + 0.05;
      const dep = (x - o.x) * -bz.x + (h - o.y) * -bz.y + (z - o.z) * -bz.z;
      out.push({i, j, H: HH, dist: Math.round(hit), dep: Math.round(dep),
                land, aw: +(h - t.waterY).toFixed(1)});
    }
  }
  const f = w.scene.fog;
  return {hits: out, camY: +o.y.toFixed(1), density: f ? f.density : 0,
          fogColour: [f.color.r, f.color.g, f.color.b].map(v => +v.toFixed(4)),
          tier: w.stats().tier};
}, {G});

const sample = async () => {
  const buf = await page.screenshot({type: 'png'});
  return page.evaluate(async ({src, uv}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas');
    cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true});
    g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    return uv.map(([u, v]) => {
      const X = Math.min(im.width - 1, Math.round(u * im.width));
      const Y = Math.min(im.height - 1, Math.round(v * im.height));
      const q = (Y * im.width + X) * 4;
      return [d[q], d[q + 1], d[q + 2]];
    });
  }, {src: 'data:image/png;base64,' + buf.toString('base64'),
      uv: geom.hits.map(p => [(p.i + 0.5) / G, (p.j + 0.5) / p.H])});
};

const setFog = d => page.evaluate(v => window.__lemWorld.subsystems.get('sky').setFogDensity(v), d);
/* Freezing the grade is what makes C and D an honest A/B: `_applyGrade` is the
 * only thing that writes the composite's exposure, so stubbing it holds the
 * stop. With `--fixexp` it is additionally *placed*, because two fog curves in
 * two page loads each adapt to their own frame and would otherwise be compared
 * at two different stops. The vignette and saturation are functions of the
 * exposure in gi.js, so they are set from the same number; the black point is
 * pinned outright because it is metered from a percentile the haze also moves. */
const freeze = (fix, bp) => page.evaluate(({fix, bp}) => {
  const g = window.__lemWorld.subsystems.get('gi');
  g.__milkFrozen = true;
  g.__realGrade = g._applyGrade.bind(g);
  g._applyGrade = () => {};
  if (fix) {
    const c = window.__lemWorld.engine?._passes?.composite?.material?.uniforms;
    g.exposure = g._expNow = fix;
    if (c?.uExposure) c.uExposure.value = fix;
    if (c?.uVignette) c.uVignette.value = 0.34 - (fix - 1) * 0.055;
    if (c?.uSaturation) c.uSaturation.value = 1.05 + (fix - 1) * 0.045;
    if (c?.uBlackPoint) c.uBlackPoint.value = bp;
  }
}, {fix, bp});
const thaw = () => page.evaluate(() => {
  const g = window.__lemWorld.subsystems.get('gi');
  if (g.__realGrade) g._applyGrade = g.__realGrade;
  g.__milkFrozen = false;
});

const D0 = geom.density;
const states = {};

/* A — fog live, adaptive */
states.A = {expo: await expo(), stats: await stats(), px: await sample()};

/* B — fog off, adaptive (what every earlier control really was) */
await setFog(PIN);
await settleExposure('B');
states.B = {expo: await expo(), stats: await stats(), px: await sample()};

/* back to A, exposure re-settled, then FREEZE */
await setFog(D0);
await settleExposure('A2');
states.A2 = {expo: await expo(), stats: await stats(), px: await sample()};
await freeze(a.fixexp ? +a.fixexp : 0, +(a.fixbp || 0.003));
await page.waitForTimeout(500);
states.C = {expo: await expo(), stats: await stats(), px: await sample()};

/* D — fog off with the stop held exactly where A had it */
await setFog(PIN);
await page.waitForTimeout(700);
states.D = {expo: await expo(), stats: await stats(), px: await sample()};
await thaw();

/* --- report -------------------------------------------------------------- */
const lum = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
const mean = v => v.length ? v.reduce((s, x) => s + x, 0) / v.length : NaN;
const r3 = x => +(+x).toFixed(3);

const land = geom.hits.map((p, i) => ({...p, k: i})).filter(p => p.land);
const dists = land.map(p => p.dist).sort((x, y) => x - y);
const q = t => dists[Math.min(dists.length - 1, Math.floor(t * dists.length))];

console.log(`sk-milk  cam=${cam} time=${time} tier=${geom.tier} camY=${geom.camY}`);
console.log(`density ${D0}  fogColour ${JSON.stringify(geom.fogColour)} (L ${r3(lum(geom.fogColour) * 255)})`);
console.log(`land hits ${land.length}  distance deciles p10 ${q(0.1)}  p25 ${q(0.25)}  p50 ${q(0.5)}  p90 ${q(0.9)}  min ${dists[0]}  max ${dists[dists.length - 1]}`);
console.log(`errors ${JSON.stringify(errors.slice(0, 3))}`);
console.log('');
console.log('EXPOSURE');
for (const k of ['A', 'B', 'A2', 'C', 'D']) {
  const e = states[k].expo;
  console.log(`  ${k.padEnd(3)} exposure ${r3(e.e)}  analytic ${r3(e.analytic)}  sceneEV ${r3(e.ev)}  uExposure ${r3(e.u)}  blackPoint ${r3(e.bp)}  sat ${r3(e.sat)}  frozen ${e.frozen}`);
}
console.log('');
console.log('WHOLE FRAME');
console.log('  state      mean    sd     p1    p5   p50   p95   p99   satur   B-R   adjΔL');
for (const k of ['A', 'B', 'A2', 'C', 'D']) {
  const s = states[k].stats;
  console.log(`  ${k.padEnd(6)} ${String(s.mean).padStart(7)} ${String(s.sd).padStart(6)} `
    + `${String(s.p1).padStart(5)} ${String(s.p5).padStart(5)} ${String(s.p50).padStart(5)} `
    + `${String(s.p95).padStart(5)} ${String(s.p99).padStart(5)} ${String(s.sat).padStart(6)} `
    + `${String(s.br).padStart(6)} ${String(s.adj).padStart(7)}`);
}
console.log('');
console.log('  A-D  fog\'s own cost at a fixed stop : dmean '
  + r3(states.A.stats.mean - states.D.stats.mean) + '  dsd ' + r3(states.A.stats.sd - states.D.stats.sd)
  + '  dadj ' + r3(states.A.stats.adj - states.D.stats.adj));
console.log('  A-B  fog + the exposure loop        : dmean '
  + r3(states.A.stats.mean - states.B.stats.mean) + '  dsd ' + r3(states.A.stats.sd - states.B.stats.sd)
  + '  dadj ' + r3(states.A.stats.adj - states.B.stats.adj));
console.log('  B-D  the exposure loop alone        : dmean '
  + r3(states.B.stats.mean - states.D.stats.mean) + '  dsd ' + r3(states.B.stats.sd - states.D.stats.sd)
  + '  dadj ' + r3(states.B.stats.adj - states.D.stats.adj));
console.log('  A-A2 repeatability of the round trip: dmean '
  + r3(states.A.stats.mean - states.A2.stats.mean) + '  dexp ' + r3(states.A.expo.e - states.A2.expo.e));
console.log('');
console.log('GROUND BY TRUE DISTANCE  (L, and blue-minus-red)');
const bands = [[0, 300], [300, 450], [450, 600], [600, 750], [750, 900],
               [900, 1100], [1100, 1400], [1400, 2400]];
console.log('  band          n     L:A      B     A2      C      D  |  A-D    B-D  |  B-R:A     D');
for (const [lo, hi] of bands) {
  const v = land.filter(p => p.dist >= lo && p.dist < hi);
  if (v.length < 8) continue;
  const get = (st, f) => mean(v.map(p => f(states[st].px[p.k])));
  const L = st => get(st, c => lum(c));
  const BR = st => get(st, c => c[2] - c[0]);
  console.log(`  ${String(lo + '-' + hi).padEnd(11)} ${String(v.length).padStart(4)} `
    + `${L('A').toFixed(1).padStart(6)} ${L('B').toFixed(1).padStart(6)} ${L('A2').toFixed(1).padStart(6)} `
    + `${L('C').toFixed(1).padStart(6)} ${L('D').toFixed(1).padStart(6)}  | `
    + `${(L('A') - L('D')).toFixed(1).padStart(5)} ${(L('B') - L('D')).toFixed(1).padStart(6)}  | `
    + `${BR('A').toFixed(1).padStart(6)} ${BR('D').toFixed(1).padStart(6)}`);
}
/* the nearest tenth of the frame's ground, which is what "near plane" means */
const nearCut = q(0.10);
const near = land.filter(p => p.dist <= nearCut);
const gm = (st, f) => mean(near.map(p => f(states[st].px[p.k])));
console.log('');
console.log(`NEAREST DECILE OF GROUND  (dist <= ${nearCut} m, n=${near.length})`);
console.log(`  L      A ${gm('A', lum).toFixed(2)}   B ${gm('B', lum).toFixed(2)}   C ${gm('C', lum).toFixed(2)}   D ${gm('D', lum).toFixed(2)}`);
console.log(`  satur  A ${gm('A', c => Math.max(...c) - Math.min(...c)).toFixed(2)}   D ${gm('D', c => Math.max(...c) - Math.min(...c)).toFixed(2)}`);
console.log(`  fog's own lift on the nearest ground: ${(gm('A', lum) - gm('D', lum)).toFixed(2)} L`);
console.log(`  the exposure loop's lift on it:       ${(gm('B', lum) - gm('D', lum)).toFixed(2)} L`);

await b.close();
