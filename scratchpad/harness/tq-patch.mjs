/* tq-patch.mjs — A/B a one-line change to the ground shader WITHOUT editing
 * terrain.js, so an experiment cannot pollute a parallel round's measurement.
 *
 * terrain.js keeps its compiled shader on `_groundShader` and its material on
 * `_groundMat`. Re-wrapping `onBeforeCompile` and setting `needsUpdate` makes
 * three recompile the program with whatever string surgery we ask for; the two
 * frames are then read at identical camera, time and settle.
 *
 *   node tq-patch.mjs --find '<literal>' --repl '<literal>' [--cam wide]
 *   node tq-patch.mjs --dump            # write the compiled fragment shader out
 */
import {chromium} from 'playwright';
import fs from 'fs';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'wide';
const mods = a.mods || 'sky,gi,terrain';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=${cam}&time=${a.time || 9}&hud=0&quality=ultra&weather=clear`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(6000);

if (a.dump) {
  const src = await p.evaluate(() => {
    const t = window.__lemWorld.subsystems.get('terrain');
    return t._groundShader ? t._groundShader.fragmentShader : null;
  });
  fs.writeFileSync(a.dump === true ? '/tmp/ground.frag' : a.dump, src || '');
  console.log('wrote', (src || '').length, 'chars');
  await b.close(); process.exit(0);
}

/* the geometric sample set, shared by both frames */
const geom = fs.readFileSync(new URL('./tq-value.mjs', import.meta.url), 'utf8');

const measure = async (tag) => {
  await p.waitForTimeout(1500);
  const buf = await p.screenshot({type: 'png'});
  if (a.out) fs.writeFileSync(a.out.replace(/\.png$/, '') + '-' + tag + '.png', buf);
  return p.evaluate(async src => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas');
    cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true});
    g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const S = window.__tqSamples || [];
    return S.map(([X, Y]) => {
      const o = (Math.round(Y * im.height) * im.width + Math.round(X * im.width)) * 4;
      return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(1);
    });
  }, 'data:image/png;base64,' + buf.toString('base64'));
};

/* build the sample set: pixel -> (aw, deg, nz) by ray march, as tq-value does */
const hits = await p.evaluate(({G}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  w.rig.idleDrift = false; w.rig.apply(1);
  const cam = w.camera; cam.updateMatrixWorld(true);
  const o = {x: cam.position.x, y: cam.position.y, z: cam.position.z};
  const e = cam.matrixWorld.elements;
  const bx = {x: e[0], y: e[1], z: e[2]}, by = {x: e[4], y: e[5], z: e[6]}, bz = {x: e[8], y: e[9], z: e[10]};
  const ty = Math.tan(cam.fov * Math.PI / 360), tx = ty * cam.aspect;
  const H = Math.round(G * 9 / 16), out = [], S = [], d = 3.0;
  for (let j = 0; j < H; j++) for (let i = 0; i < G; i++) {
    const cxr = (((i + 0.5) / G) * 2 - 1) * tx, cyr = (1 - ((j + 0.5) / H) * 2) * ty;
    let vx = bx.x * cxr + by.x * cyr - bz.x, vy = bx.y * cxr + by.y * cyr - bz.y, vz = bx.z * cxr + by.z * cyr - bz.z;
    const L = Math.hypot(vx, vy, vz); vx /= L; vy /= L; vz /= L;
    let prev = 0, hit = -1, step = 4;
    for (let s = step; s < 9000; s += step) {
      const gap = o.y + vy * s - t.heightAt(o.x + vx * s, o.z + vz * s);
      if (gap <= 0) { let lo = prev, hi = s;
        for (let k = 0; k < 24; k++) { const m = (lo + hi) * 0.5;
          if (o.y + vy * m - t.heightAt(o.x + vx * m, o.z + vz * m) <= 0) hi = m; else lo = m; }
        hit = (lo + hi) * 0.5; break; }
      prev = s; step = Math.min(60, Math.max(4, gap * 0.55));
    }
    if (hit < 0) continue;
    const x = o.x + vx * hit, z = o.z + vz * hit, h = t.heightAt(x, z);
    if (h <= t.waterY + 0.05) continue;
    const gx = (t.heightAt(x + d, z) - t.heightAt(x - d, z)) / (2 * d);
    const gz = (t.heightAt(x, z + d) - t.heightAt(x, z - d)) / (2 * d);
    const sl = Math.hypot(gx, gz), ny = 1 / Math.sqrt(1 + sl * sl);
    out.push({aw: +(h - t.waterY).toFixed(2), deg: +(Math.atan(sl) * 180 / Math.PI).toFixed(2),
              nz: +(-gz * ny).toFixed(3), dist: Math.round(hit)});
    S.push([(i + 0.5) / G, (j + 0.5) / H]);
  }
  window.__tqSamples = S;
  return out;
}, {G: +(a.grid || 220)});

const before = await measure("before");

/* one --find/--repl, or a JSON file of [[find, repl], ...] for a block edit */
const pairs = a.pairs ? JSON.parse(fs.readFileSync(a.pairs, 'utf8')) : [[a.find, a.repl]];
const ok = await p.evaluate(({pairs}) => {
  const t = window.__lemWorld.subsystems.get('terrain');
  /* every material actually on a terrain mesh, not just `_groundMat`: the core
   * and the rings may carry clones with different defines. */
  const mats = new Set();
  t.group.traverse(o => { if (o.isMesh && o.material) {
    (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => mats.add(m)); } });
  let found = 0, wrapped = 0;
  for (const mat of mats) {
    if (typeof mat.onBeforeCompile !== 'function') continue;
    const orig = mat.onBeforeCompile;
    if (orig.length === 0 && orig.toString().length < 40) continue;
    mat.onBeforeCompile = function (sh, rend) {
      orig.call(this, sh, rend);
      for (const [find, repl] of pairs) {
        if (sh.fragmentShader.indexOf(find) >= 0) found++;
        sh.fragmentShader = sh.fragmentShader.split(find).join(repl);
      }
    };
    /* three's program cache key includes onBeforeCompile.toString(), so a new
     * closure body is a new key and the program is genuinely recompiled. */
    /* STABLE. An earlier version put Math.random() in here and it is worth
     * recording why that is wrong: three calls this on every render to decide
     * whether the program is still valid, so a key that changes every frame
     * recompiles the material continuously and the "after" frame is measuring
     * a thrashing renderer as much as the patch. A hash of the patch text is
     * new when the patch is new and constant thereafter, which is the whole
     * requirement. */
    let hk = 0;
    const keyTxt = JSON.stringify(pairs);
    for (let i = 0; i < keyTxt.length; i++) hk = (hk * 31 + keyTxt.charCodeAt(i)) | 0;
    mat.customProgramCacheKey = () => 'tqpatch' + hk;
    mat.needsUpdate = true;
    wrapped++;
  }
  window.__tqFound = () => [found, wrapped, mats.size];
  return 'ok';
}, {pairs});
if (ok !== 'ok') { console.error(ok); await b.close(); process.exit(2); }
await p.waitForTimeout(2500);
const found = await p.evaluate(() => window.__tqFound());
const after = await measure("after");

const mean = v => v.length ? v.reduce((s, x) => s + x, 0) / v.length : NaN;
const r = x => +(+x).toFixed(2);
const sd = v => { const m = mean(v); return Math.sqrt(mean(v.map(x => (x - m) ** 2))); };
const grp = (f) => {
  const idx = hits.map((h, i) => [h, i]).filter(([h]) => f(h)).map(([, i]) => i);
  return {n: idx.length, before: r(mean(idx.map(i => before[i]))),
          after: r(mean(idx.map(i => after[i]))),
          delta: r(mean(idx.map(i => after[i] - before[i])))};
};
const spread = (fa, fb) => {
  const A = grp(fa), B = grp(fb);
  return {before: r(A.before - B.before), after: r(A.after - B.after)};
};
console.log(JSON.stringify({
  pairs: pairs.length, occurrences: found, pixels: hits.length,
  all: grp(() => true),
  wet0_2: grp(h => h.aw <= 2),
  strand3_8: grp(h => h.aw > 3 && h.aw <= 8),
  inland20: grp(h => h.aw > 20),
  sunFace: grp(h => h.aw > 8 && h.deg > 8 && h.deg < 34 && h.nz > 0.18),
  leeFace: grp(h => h.aw > 8 && h.deg > 8 && h.deg < 34 && h.nz < -0.18),
  flat: grp(h => h.aw > 8 && h.deg < 10),
  steep: grp(h => h.aw > 8 && h.deg > 22),
  aspectSpreadL: spread(h => h.aw > 8 && h.deg > 8 && h.deg < 34 && h.nz > 0.18,
                        h => h.aw > 8 && h.deg > 8 && h.deg < 34 && h.nz < -0.18),
  slopeSpreadL: spread(h => h.aw > 8 && h.deg < 10, h => h.aw > 8 && h.deg > 22),
  wetBandDrop: {before: r(grp(h => h.aw <= 3).before / grp(h => h.aw > 6 && h.aw <= 14).before),
                after: r(grp(h => h.aw <= 3).after / grp(h => h.aw > 6 && h.aw <= 14).after)},
  sigmaL: {before: r(sd(before)), after: r(sd(after))},
}, null, 1));
await b.close();
