/* gx-decomp.mjs — decompose the plant's pad pixel, with the stop frozen, on
 * pixels selected by the shader's OWN shadow answer rather than by a rectangle.
 *
 * Three sets, all on `:concrete` facing up:
 *   shadowed  lemFarShadow < 0.05 and a CPU sun ray that hits something 3-25 m up
 *   open      lemFarShadow > 0.98 and no CPU sun ray hit
 *   wall      a vertical `:brick` face, sunlit, for the key:fill comparison the
 *             buildings.js note is written against
 *
 * States, in one page session, in this order, each restored before the next:
 *   full / far-shadow gated off / sun off / probe GI off / env off / all off
 *
 *   node gx-decomp.mjs [--cam far] [--time 9]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(10000);
await page.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } w.camera.updateMatrixWorld(true); });
await page.waitForTimeout(600);

const pick = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera, rn = w.engine.renderer;
  const d = gi.sunDirection.clone();
  const maps = gi._csm.map(c => {
    const N = c.rt.width, buf = new Uint8Array(N * N * 4);
    try { rn.readRenderTargetPixels(c.rt, 0, 0, N, N, buf); } catch (e) { void e; }
    return {i: c.i, N, buf};
  });
  const unpack = (m, ix, iy) => {
    ix = Math.min(m.N - 1, Math.max(0, ix)); iy = Math.min(m.N - 1, Math.max(0, iy));
    const o = (iy * m.N + ix) * 4;
    return m.buf[o] / 255 + m.buf[o + 1] / 65025 + m.buf[o + 2] / 16581375 + m.buf[o + 3] / 4228250625;
  };
  const TAPS = [[-0.8, 0.4], [0.4, 0.8], [0.8, -0.4], [-0.4, -0.8]];
  const cascadeAt = (m, P, N3) => {
    const e = gi.uniforms[`lemCsmMat${m.i}`].value.elements;
    const par = gi.uniforms[`lemCsmParam${m.i}`].value;
    const X = P.x + N3.x * par.w, Y = P.y + N3.y * par.w, Z = P.z + N3.z * par.w;
    const cw = e[3] * X + e[7] * Y + e[11] * Z + e[15];
    const px = (e[0] * X + e[4] * Y + e[8] * Z + e[12]) / cw;
    const py = (e[1] * X + e[5] * Y + e[9] * Z + e[13]) / cw;
    const pz = (e[2] * X + e[6] * Y + e[10] * Z + e[14]) / cw;
    if (px < 0 || px > 1 || py < 0 || py > 1 || pz > 1 || pz < 0) return 1;
    const dd = pz - par.z;
    let s = 0;
    for (const [tx, ty] of TAPS)
      if (dd <= unpack(m, Math.floor((px + tx * par.x) * m.N), Math.floor((py + ty * par.y) * m.N))) s++;
    return s * 0.25;
  };
  const right = gi.uniforms.lemLightRight.value, up = gi.uniforms.lemLightUp.value;
  const ss = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };
  const boxW = (P, c, r) => {
    const dx = P.x - c.x, dy = P.y - c.y, dz = P.z - c.z;
    const q = Math.max(Math.abs(dx * right.x + dy * right.y + dz * right.z),
                       Math.abs(dx * up.x + dy * up.y + dz * up.z));
    return 1 - ss(r * 0.80, r * 0.97, q);
  };
  const farShadow = (P, N3) => {
    const nw = boxW(P, gi.uniforms.lemNearCentre.value, gi.uniforms.lemNearRadius.value);
    if (nw >= 0.999) return 1;
    let s = 1;
    if (maps[1] && gi.uniforms.lemCsmReady1.value > 0.5) s = cascadeAt(maps[1], P, N3);
    if (maps[0] && gi.uniforms.lemCsmReady0.value > 0.5) {
      const bx = gi.uniforms.lemCsmBox0.value;
      s = s + (cascadeAt(maps[0], P, N3) - s) * boxW(P, {x: bx.x, y: bx.y, z: bx.z}, bx.w);
    }
    return s + (1 - s) * nw;
  };
  const occ = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible &&
    !/^terrain|ocean|horizon|weather/.test(o.name || '') && o.geometry) occ.push(o); });
  const pads = [], walls = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && /:concrete$/.test(o.name || '')) pads.push(o);
                          if (o.isMesh && o.visible && /:brick$/.test(o.name || '')) walls.push(o); });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  const sets = {shadowed: [], open: [], wallLit: []};
  const hist = {};
  const W = innerWidth, H = innerHeight;
  const scan = (targets, take) => {
    for (let sy = 0; sy < H; sy += 2) for (let sx = 0; sx < W; sx += 2) {
      ndc.set((sx + .5) / W * 2 - 1, -((sy + .5) / H * 2 - 1));
      rc.setFromCamera(ndc, cam);
      const h = rc.intersectObjects(targets, false)[0];
      if (!h || !h.face) continue;
      const n = h.face.normal.clone().applyNormalMatrix(
        new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
      const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.08), d, 0.05, 500);
      sr.layers.enableAll();
      const hit = sr.intersectObjects(occ, false)[0];
      take({sx, sy, n, p: h.point, hit, fs: farShadow(h.point, n), NL: n.dot(d)});
    }
  };
  scan(pads, q => {
    if (q.n.y < 0.92) return;
    const k = q.fs.toFixed(2); hist[k] = (hist[k] || 0) + 1;
    const rise = q.hit ? q.hit.point.y - q.p.y : 0;
    if (q.fs < 0.05 && q.hit && rise > 3 && rise < 25) sets.shadowed.push({sx: q.sx, sy: q.sy});
    else if (q.fs > 0.98 && !q.hit) sets.open.push({sx: q.sx, sy: q.sy});
  });
  scan(walls, q => {
    if (Math.abs(q.n.y) > 0.3) return;
    if (q.fs > 0.98 && !q.hit && q.NL > 0.35) sets.wallLit.push({sx: q.sx, sy: q.sy});
  });
  return {sets, hist, sun: d.toArray().map(v => +v.toFixed(3)),
          elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2),
          iblDiffuse: gi.uniforms.lemIblDiffuse?.value ?? null,
          giStrength: gi.uniforms.lemGIStrength.value,
          envIntensity: w.scene.environmentIntensity,
          aoStrength: gi.uniforms.lemAOStrength?.value,
          aoContact: gi.uniforms.lemAOContact?.value,
          skyIrr: gi.uniforms.lemSkyIrradiance?.value?.toArray?.().map(v => +v.toFixed(4)),
          gndIrr: gi.uniforms.lemGroundIrradiance?.value?.toArray?.().map(v => +v.toFixed(4)),
          sunI: gi.sun.intensity};
});

async function readPixels() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, sets}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const dd = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth;
    const out = {};
    for (const k in sets) {
      const v = sets[k].map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
        return 0.2126 * dd[o] + 0.7152 * dd[o + 1] + 0.0722 * dd[o + 2]; });
      v.sort((x, y) => x - y);
      out[k] = v.length ? +v[v.length >> 1].toFixed(2) : null;
    }
    return out;
  }, {src, sets: pick.sets});
}

await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  if (typeof gi.setExposureLocked === 'function') gi.setExposureLocked(true);
  else { gi.__grade = gi._applyGrade; gi._applyGrade = () => {}; }
  gi.__svc = gi._serviceCascades; gi._serviceCascades = () => {}; });
await page.waitForTimeout(1000);

const rows = [];
const set = async (fn, label, wait = 1400) => {
  await page.evaluate(fn);
  await page.waitForTimeout(wait);
  rows.push({state: label, ...(await readPixels())});
};
await set(() => {}, 'full');
await set(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.uniforms.lemCsmReady0.value = 0; g.uniforms.lemCsmReady1.value = 0; }, 'far shadow OFF');
await set(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.uniforms.lemCsmReady0.value = 1; g.uniforms.lemCsmReady1.value = 1; }, 'restored');
await set(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.__i = g.sun.intensity; g.sun.intensity = 0; }, 'sun OFF  (fill only)');
await set(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.__g = g.uniforms.lemGIStrength.value; g.uniforms.lemGIStrength.value = 0; }, 'sun+probe OFF (env only)');
await set(() => { const w = window.__lemWorld;
  w.__env = w.scene.environment; w.scene.environment = null; }, 'sun+probe+env OFF (floor)', 1800);
await set(() => { const w = window.__lemWorld, g = w.subsystems.get('gi');
  w.scene.environment = w.__env; g.uniforms.lemGIStrength.value = g.__g;
  g.sun.intensity = g.__i; }, 'all back', 1800);
await set(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.__g2 = g.uniforms.lemGIStrength.value; g.uniforms.lemGIStrength.value = 0; }, 'probe OFF only');
await set(() => { const g = window.__lemWorld.subsystems.get('gi');
  g.uniforms.lemGIStrength.value = g.__g2; const w = window.__lemWorld;
  w.__env2 = w.scene.environment; w.scene.environment = null; }, 'env OFF only', 1800);

console.log(JSON.stringify({cam, time, sun: pick.sun, elev: pick.elev,
  n: {shadowed: pick.sets.shadowed.length, open: pick.sets.open.length, wallLit: pick.sets.wallLit.length},
  uniforms: {iblDiffuse: pick.iblDiffuse, giStrength: pick.giStrength, envIntensity: pick.envIntensity,
             aoStrength: pick.aoStrength, aoContact: pick.aoContact, sunI: pick.sunI,
             skyIrr: pick.skyIrr, gndIrr: pick.gndIrr},
  fsHistogram: pick.hist, rows, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
