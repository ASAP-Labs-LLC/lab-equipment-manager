/* gx-shell.mjs — the second REQUESTS note: does a curved metal shell receive as
 * much fill as a flat wall, and should it?
 *
 * `tk-split.mjs` reported the shell's fill at 78 % of its key against a wall's
 * 44 %, but it did not freeze the stop, and gi's meter absorbs most of any
 * change measured through it. So this re-measures with the stop pinned and with
 * every ablated field replaced by a property that swallows writes, because
 * gi's own `onTime` -> `_readSky` -> `_fitFill` puts `lemGIStrength` and
 * `sun.intensity` back a second or two later and quietly un-does an ablation
 * halfway through a long run.
 *
 * And it measures the thing the note hypothesises rather than assuming it: for
 * every sampled point, the fraction of the sky hemisphere its own geometry can
 * actually see, by casting a cosine-weighted fan of rays and counting how many
 * escape. If the shell's shaded generatrix sees materially less sky than the
 * wall's shaded face and is nevertheless given the same fill, the hypothesis is
 * confirmed with a number attached to it.
 *
 *   node gx-shell.mjs [--cam far] [--time 9]
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

await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), scene = w.scene;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  const pin = (obj, key, initial) => { let v = initial;
    Object.defineProperty(obj, key, {configurable: true, get: () => v, set: () => {}});
    return nv => { v = nv; }; };
  window.__gx = {};
  if (typeof gi.setExposureLocked === 'function') { gi.setExposureLocked(true); window.__gx.lock = 'api'; }
  else { gi._applyGrade = () => {}; window.__gx.lock = 'stub'; }
  gi._serviceCascades = () => {};
  window.__gx.giBase = gi.uniforms.lemGIStrength.value;
  window.__gx.setGI = pin(gi.uniforms.lemGIStrength, 'value', window.__gx.giBase);
  window.__gx.envBase = scene.environment;
  window.__gx.setEnv = pin(scene, 'environment', window.__gx.envBase);
  window.__gx.sunBase = gi.sun.intensity;
  window.__gx.setSun = pin(gi.sun, 'intensity', window.__gx.sunBase);
});
await page.waitForTimeout(1500);

const pick = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera;
  const d = gi.sunDirection.clone();
  const all = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible && o.geometry &&
    !/ocean|horizon|weather|mainland|^terrain/.test(o.name || '')) all.push(o); });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  /* a cosine-weighted fan about the normal, built once in tangent space */
  const FAN = [];
  for (let i = 0; i < 24; i++) {
    const u1 = (i + 0.5) / 24, u2 = ((i * 0.61803398875) % 1);
    const r = Math.sqrt(u1), th = 2 * Math.PI * u2;
    FAN.push([r * Math.cos(th), r * Math.sin(th), Math.sqrt(Math.max(0, 1 - u1))]);
  }
  const T = new THREE.Vector3(), B2 = new THREE.Vector3(), dir = new THREE.Vector3();
  const skyVis = (P, n) => {
    if (Math.abs(n.y) < 0.9) T.set(0, 1, 0).cross(n).normalize();
    else T.set(1, 0, 0).cross(n).normalize();
    B2.crossVectors(n, T);
    let open = 0, tot = 0;
    const ray = new THREE.Raycaster(); ray.layers.enableAll();
    for (const [tx, ty, tz] of FAN) {
      dir.set(T.x * tx + B2.x * ty + n.x * tz, T.y * tx + B2.y * ty + n.y * tz,
              T.z * tx + B2.z * ty + n.z * tz).normalize();
      if (dir.y < -0.05) { tot++; continue; }          // pointing at the ground: not sky
      ray.set(P.clone().addScaledVector(n, 0.08), dir);
      ray.far = 90;
      tot++;
      if (!ray.intersectObjects(all, false).length) open++;
    }
    return tot ? open / tot : 1;
  };
  const sets = {shellLit: [], shellShaded: [], wallLit: [], wallShaded: [], padOpen: []};
  const W = innerWidth, H = innerHeight;
  for (let sy = 0; sy < H; sy += 2) for (let sx = 0; sx < W; sx += 2) {
    ndc.set((sx + .5) / W * 2 - 1, -((sy + .5) / H * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(all, false)[0];
    if (!h || !h.face) continue;
    const nm = h.object.name || '';
    const isSteel = /:steel$/.test(nm), isBrick = /:brick$/.test(nm), isPad = /:concrete$/.test(nm);
    if (!isSteel && !isBrick && !isPad) continue;
    const n = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    const NL = n.dot(d);
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.08), d, 0.05, 400);
    sr.layers.enableAll();
    const sunlit = NL > 0 && sr.intersectObjects(all, false).length === 0;
    const rec = {sx, sy, NL: +NL.toFixed(3), ny: +n.y.toFixed(2)};
    if (isPad && n.y > 0.92 && sunlit) { if (sets.padOpen.length < 900) sets.padOpen.push(rec); continue; }
    if (Math.abs(n.y) > 0.30) continue;                // walls and shells only
    if (isSteel && NL > 0.35 && sunlit && sets.shellLit.length < 220) sets.shellLit.push(rec);
    else if (isSteel && NL < -0.15 && sets.shellShaded.length < 220) sets.shellShaded.push(rec);
    else if (isBrick && NL > 0.35 && sunlit && sets.wallLit.length < 220) sets.wallLit.push(rec);
    else if (isBrick && NL < -0.15 && sets.wallShaded.length < 220) sets.wallShaded.push(rec);
    else continue;
    const last = sets[isSteel ? (NL > 0 ? 'shellLit' : 'shellShaded')
                              : (NL > 0 ? 'wallLit' : 'wallShaded')];
    last[last.length - 1].vis = +skyVis(h.point, n).toFixed(3);
  }
  /* sky visibility on the open pad too, for a baseline */
  return {sets, sun: d.toArray().map(v => +v.toFixed(3))};
});

const state = () => page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  return {gi: +gi.uniforms.lemGIStrength.value.toFixed(4), env: !!w.scene.environment,
          sun: +gi.sun.intensity.toFixed(4),
          ev: +(w.engine._passes?.composite?.material?.uniforms?.uExposure?.value ?? -1).toFixed(4)};
});
async function shot(label) {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  const px = await page.evaluate(async ({src, sets}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const dd = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth, out = {};
    for (const k in sets) {
      const v = sets[k].map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
        return 0.2126 * dd[o] + 0.7152 * dd[o + 1] + 0.0722 * dd[o + 2]; });
      v.sort((x, y) => x - y);
      out[k] = v.length ? +v[v.length >> 1].toFixed(2) : null;
    }
    return out;
  }, {src, sets: pick.sets});
  return {state: label, ...px, live: await state()};
}

const rows = [];
rows.push(await shot('full'));
await page.evaluate(() => window.__gx.setSun(0));
await page.waitForTimeout(1600); rows.push(await shot('sun OFF (fill only)'));
await page.evaluate(() => window.__gx.setGI(0));
await page.waitForTimeout(1600); rows.push(await shot('sun+probe OFF (env only)'));
await page.evaluate(() => window.__gx.setEnv(null));
await page.waitForTimeout(1800); rows.push(await shot('sun+probe+env OFF (floor)'));
await page.evaluate(() => { window.__gx.setEnv(window.__gx.envBase);
  window.__gx.setGI(window.__gx.giBase); window.__gx.setSun(window.__gx.sunBase); });
await page.waitForTimeout(1800); rows.push(await shot('all back'));

const mean = v => v.length ? +(v.reduce((x, y) => x + y, 0) / v.length).toFixed(3) : null;
const vis = {};
for (const k in pick.sets) vis[k] = mean(pick.sets[k].filter(p => p.vis !== undefined).map(p => p.vis));
console.log(JSON.stringify({cam, time, sun: pick.sun,
  n: Object.fromEntries(Object.entries(pick.sets).map(([k, v]) => [k, v.length])),
  meanSkyVisibility: vis,
  meanNL: Object.fromEntries(Object.entries(pick.sets).map(([k, v]) => [k, mean(v.map(p => p.NL))])),
  rows, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
