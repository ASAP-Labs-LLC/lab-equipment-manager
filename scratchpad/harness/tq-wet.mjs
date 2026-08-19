/* tq-wet.mjs — the wet band's COLOUR, A/B'd in one page load.
 *
 * The note is "wet sand goes darker AND WARMER AND MORE SATURATED — chroma goes
 * up, not down. What's there now reads as silt or mudflat". Luminance alone
 * cannot answer that, and `tq-shore`'s beachWetDrop is a luminance ratio — which
 * is exactly how an instrument reported 0.8 of a stop of healthy band while an
 * art director reported a grey smear. So this reports RGB, and derives hue and
 * chroma from it, binned by height above the waterline.
 *
 * A is the file as it stands. B reverts THIS ROUND's four wet-band lines in the
 * compiled shader — the `tq-patch` trick, so the before/after is the same frame
 * at the same settle and no file has to be edited to get a baseline.
 *
 *   node tq-wet.mjs [--cam far] [--time 9] [--grid 320]
 */
import {chromium} from 'playwright';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9', G = +(a.grid || 320);
const mods = a.mods || 'sky,gi,terrain';

/* the revert: A (as shipped) -> B (as it was before this round) */
const REVERT = [
  ['albedo *= mix(vec3(1.0), vec3(0.56, 0.42, 0.26), wetAll);',
   'albedo *= mix(vec3(1.0), vec3(0.26), wetAll);'],
  ['1.0 + wetAll * 1.00', '1.0 + wetAll * 0.55'],
  ['float sand = sandRaw * (1.0 - wetSand * 0.50) * (1.0 - damp * 0.20);',
   'float sand = sandRaw * (1.0 - wetSand * 0.95) * (1.0 - damp * 0.42);'],
  ['float levelWet = smoothstep(0.55, 0.86, nLand.y);', 'float levelWet = level;'],
];

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
p.on('pageerror', e => errors.push(String(e).slice(0, 160)));
p.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 160)); });
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=${cam}`
           + `&time=${time}&hud=0&quality=ultra&weather=clear`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(7000);

/* ---- the sample set: pixel -> (height above water, normal Y, range) -------- */
const nHits = await p.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  const cm = w.camera; cm.updateMatrixWorld(true);
  const o = {x: cm.position.x, y: cm.position.y, z: cm.position.z};
  const e = cm.matrixWorld.elements;
  const bx = {x: e[0], y: e[1], z: e[2]}, by = {x: e[4], y: e[5], z: e[6]},
        bz = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cm.fov * Math.PI / 360), tx = ty * cm.aspect;
  const H = Math.round(G * 9 / 16), S = [], out = [], d = 3.0;
  for (let j = 0; j < H; j++) for (let i = 0; i < G; i++) {
    const cxr = (((i + 0.5) / G) * 2 - 1) * tx, cyr = (1 - ((j + 0.5) / H) * 2) * ty;
    let vx = bx.x * cxr + by.x * cyr - bz.x, vy = bx.y * cxr + by.y * cyr - bz.y,
        vz = bx.z * cxr + by.z * cyr - bz.z;
    const L = Math.hypot(vx, vy, vz); vx /= L; vy /= L; vz /= L;
    let prev = 0, hit = -1;
    for (let s = 4; s < 9000; s += 4) {
      const gap = o.y + vy * s - t.heightAt(o.x + vx * s, o.z + vz * s);
      if (gap <= 0) {
        let lo = prev, hi = s;
        for (let k = 0; k < 22; k++) {
          const m = (lo + hi) * 0.5;
          if (o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m) <= 0) hi = m; else lo = m;
        }
        hit = (lo + hi) * 0.5; break;
      }
      prev = s;
    }
    if (hit < 0) continue;
    const X = o.x + vx * hit, Z = o.z + vz * hit;
    const hh = t.heightAt(X, Z);
    const aw = hh - t.waterY;
    if (aw < -0.5 || aw > 30) continue;               // the strand and just inland
    const gx = (t.heightAt(X + d, Z) - t.heightAt(X - d, Z)) / (2 * d);
    const gz = (t.heightAt(X, Z + d) - t.heightAt(X, Z - d)) / (2 * d);
    const ny = 1 / Math.sqrt(gx * gx + gz * gz + 1);
    S.push([(i + 0.5) / G, (j + 0.5) / H]);
    out.push({aw: +aw.toFixed(2), ny: +ny.toFixed(3), dist: Math.round(hit)});
  }
  window.__tqSamples = S; window.__tqMeta = out;
  return out.length;
}, {G});

const readFrame = async () => {
  await p.waitForTimeout(1800);
  const buf = await p.screenshot({type: 'png'});
  return p.evaluate(async src => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas');
    cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true});
    g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    return (window.__tqSamples || []).map(([X, Y]) => {
      const o = (Math.round(Y * im.height) * im.width + Math.round(X * im.width)) * 4;
      return [d[o], d[o + 1], d[o + 2]];
    });
  }, 'data:image/png;base64,' + buf.toString('base64'));
};

const A = await readFrame();

/* ---- revert this round's lines in the compiled program --------------------- */
const applied = await p.evaluate((REV) => {
  const t = window.__lemWorld.subsystems.get('terrain');
  const mats = new Set();
  t.group.traverse(o => {
    if (o.isMesh && o.material)
      (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => mats.add(m));
  });
  if (t._groundMat) mats.add(t._groundMat);
  let found = 0;
  for (const m of mats) {
    if (typeof m.onBeforeCompile !== 'function') continue;
    const prev = m.onBeforeCompile;
    if (prev.length === 0 && prev.toString().length < 40) continue;
    m.onBeforeCompile = function (sh, r) {
      prev.call(this, sh, r);
      for (const [f, t2] of REV) {
        if (sh.fragmentShader.indexOf(f) >= 0) { found++; window.__tqWetHits = found; }
        sh.fragmentShader = sh.fragmentShader.split(f).join(t2);
      }
    };
    /* three's program cache keys on the KEY, never on the source, so a patched
     * shader with an unchanged key silently reuses the cached program and the
     * A/B measures one frame twice. Stable, so it does not thrash. */
    let hk = 0; const kt = JSON.stringify(REV);
    for (let i = 0; i < kt.length; i++) hk = (hk * 31 + kt.charCodeAt(i)) | 0;
    m.customProgramCacheKey = () => 'tqwet' + hk;
    m.needsUpdate = true;
  }
  return {materials: mats.size};
}, REVERT);
await p.waitForTimeout(3500);
const wetHits = await p.evaluate(() => window.__tqWetHits || 0);
const B = await readFrame();

/* ---- report ---------------------------------------------------------------- */
const meta = await p.evaluate(() => window.__tqMeta);
const lum = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
const r2 = x => +(+x).toFixed(2);
const sat = c => { const mx = Math.max(...c), mn = Math.min(...c);
                   return mx > 0 ? r2((mx - mn) / mx) : 0; };

const bin = (rows, edges) => {
  const o = [];
  for (let k = 0; k + 1 < edges.length; k++) {
    const idx = rows.filter(i => meta[i].aw > edges[k] && meta[i].aw <= edges[k + 1]);
    if (!idx.length) { o.push({b: edges[k] + '-' + edges[k + 1], n: 0}); continue; }
    const m = f => idx.reduce((s, i) => s + f(i), 0) / idx.length;
    const row = c => [Math.round(m(i => c[i][0])), Math.round(m(i => c[i][1])),
                      Math.round(m(i => c[i][2]))];
    const rA = row(A), rB = row(B);
    o.push({b: edges[k] + '-' + edges[k + 1], n: idx.length,
            A: {rgb: rA, L: r2(lum(rA)), RmB: rA[0] - rA[2], sat: sat(rA)},
            B: {rgb: rB, L: r2(lum(rB)), RmB: rB[0] - rB[2], sat: sat(rB)}});
  }
  return o;
};

/* the beach proper: nearly level ground. And EVERY arc: ground the old `level`
 * gate excluded (ny between 0.80 and 0.94 is 20-37 deg, the cut arcs). */
const all = meta.map((_, i) => i);
const flat = all.filter(i => meta[i].ny > 0.94);
const steep = all.filter(i => meta[i].ny > 0.80 && meta[i].ny <= 0.94);
const EDGES = [0, 0.6, 1.2, 2, 3, 4.5, 7, 12];

const band = (rows) => {
  const w = rows.filter(i => meta[i].aw <= 1.2);
  const d = rows.filter(i => meta[i].aw > 2.6 && meta[i].aw <= 4.2);
  const g = (idx, c) => idx.length
    ? [0, 1, 2].map(k => Math.round(idx.reduce((s, i) => s + c[i][k], 0) / idx.length)) : null;
  const mk = (c) => {
    const wr = g(w, c), dr = g(d, c);
    if (!wr || !dr) return null;
    return {wet: wr, dry: dr, wetL: r2(lum(wr)), dryL: r2(lum(dr)),
            dropL: r2(lum(wr) / lum(dr)), deltaL: r2(lum(wr) - lum(dr)),
            wetRmB: wr[0] - wr[2], dryRmB: dr[0] - dr[2],
            warmer: (wr[0] - wr[2]) - (dr[0] - dr[2]),
            wetSat: sat(wr), drySat: sat(dr),
            moreSaturated: r2(sat(wr) - sat(dr)),
            wetN: w.length, dryN: d.length};
  };
  return {A: mk(A), B: mk(B)};
};

console.log(JSON.stringify({
  cam, time, mods, samples: nHits, materialsPatched: applied.materials,
  revertHits: wetHits,
  errors: errors.slice(0, 4),
  note: 'A = as shipped this round. B = the four wet-band lines reverted in-page.',
  beachFlat_ny_gt_094: band(flat),
  cutArcs_ny_080_094: band(steep),
  profileFlat: bin(flat, EDGES),
  profileCutArcs: bin(steep, EDGES),
}, null, 1));
await b.close();
