/* gx-farabl.mjs — ablate the FAR SHADOW TERM ITSELF, with the stop frozen.
 *
 * Two earlier probes ablated `castShadow` and cascade layer membership and got
 * 0.00 L at every hour, because gi.js re-establishes both every sweep. This one
 * turns off the only thing nothing re-establishes on its own clock: the
 * `lemCsmReady{0,1}` gates the shader tests before it samples a cascade — with
 * `_serviceCascades` stubbed so no refit turns them back on.
 *
 * The stop is frozen first (`gi.setExposureLocked(true)` if present, else
 * `_applyGrade` stubbed), because gi's meter is negative feedback and absorbs
 * about 60 % of any change measured through it.
 *
 * Pixels are chosen on the plant's own concrete, classified two independent
 * ways: by a CPU ray to the sun (geometry), and by re-running `lemFarShadow` on
 * the CPU against the read-back cascade maps (the shader's own answer).
 *
 *   node gx-farabl.mjs [--cam far] [--time 9]
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

  /* --- cascade maps, read back once --- */
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

  /* --- sample the plant's concrete over the whole screen --- */
  const occ = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible && /site:|:/.test(o.name || '')) occ.push(o); });
  const pads = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && /:concrete$/.test(o.name || '')) pads.push(o); });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  const pts = [];
  const W = innerWidth, H = innerHeight;
  for (let sy = 0; sy < H; sy += 3) for (let sx = 0; sx < W; sx += 3) {
    ndc.set((sx + .5) / W * 2 - 1, -((sy + .5) / H * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(pads, false)[0];
    if (!h || !h.face) continue;
    const n = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    if (n.y < 0.92) continue;                       // horizontal pad only
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.08), d, 0.05, 600);
    sr.layers.enableAll();
    const geoOcc = sr.intersectObjects(occ, false).length > 0;
    pts.push({sx, sy, geoOcc, fs: +farShadow(h.point, n).toFixed(3),
              wp: [+h.point.x.toFixed(1), +h.point.y.toFixed(1), +h.point.z.toFixed(1)]});
  }
  return {pts, sun: d.toArray().map(v => +v.toFixed(3)),
          elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2)};
});

async function readPixels() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, pts}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const dd = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth;
    return pts.map(p => {
      const o = ((Math.round(p.sy * sc)) * im.width + Math.round(p.sx * sc)) * 4;
      return +(0.2126 * dd[o] + 0.7152 * dd[o + 1] + 0.0722 * dd[o + 2]).toFixed(2);
    });
  }, {src, pts: pick.pts});
}
const med = v => { if (!v.length) return null; const s = [...v].sort((x, y) => x - y);
  return +s[s.length >> 1].toFixed(2); };
const groups = {
  padShadowed: p => p.fs < 0.3,
  padPenumbra: p => p.fs >= 0.3 && p.fs < 0.9,
  padOpen: p => p.fs >= 0.9 && !p.geoOcc,
  geoOccluded: p => p.geoOcc,
  geoOpen: p => !p.geoOcc,
};
function digest(L) {
  const o = {};
  for (const k in groups) o[k] = med(pick.pts.map((p, i) => [p, L[i]]).filter(([p]) => groups[k](p)).map(([, l]) => l));
  o.n = {}; for (const k in groups) o.n[k] = pick.pts.filter(groups[k]).length;
  return o;
}

const rows = [];
const exposure = () => page.evaluate(() => {
  const w = window.__lemWorld;
  return +(w.engine._passes?.composite?.material?.uniforms?.uExposure?.value ?? -1).toFixed(4);
});

rows.push({state: 'baseline (stop live)', ev: await exposure(), ...digest(await readPixels())});

/* freeze the stop */
const lock = await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  if (typeof gi.setExposureLocked === 'function') { gi.setExposureLocked(true); return 'api'; }
  gi.__grade = gi._applyGrade; gi._applyGrade = () => {}; return 'stub';
});
await page.waitForTimeout(900);
rows.push({state: `stop frozen (${lock})`, ev: await exposure(), ...digest(await readPixels())});

/* ablate the far-shadow term */
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.__svc = gi._serviceCascades; gi._serviceCascades = () => {};
  gi.uniforms.lemCsmReady0.value = 0; gi.uniforms.lemCsmReady1.value = 0;
});
await page.waitForTimeout(1400);
rows.push({state: 'FAR SHADOW OFF', ev: await exposure(), ...digest(await readPixels())});

/* put it back, then split key / fill on the same pixels with the stop still frozen */
await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.uniforms.lemCsmReady0.value = 1; gi.uniforms.lemCsmReady1.value = 1;
});
await page.waitForTimeout(1200);
rows.push({state: 'restored', ev: await exposure(), ...digest(await readPixels())});

await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  gi.__i = gi.sun.intensity; gi.sun.intensity = 0; });
await page.waitForTimeout(1400);
rows.push({state: 'sun OFF (fill only)', ev: await exposure(), ...digest(await readPixels())});

await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  gi.sun.intensity = gi.__i; gi.__g = gi.uniforms.lemGIStrength.value;
  gi.uniforms.lemGIStrength.value = 0; });
await page.waitForTimeout(1400);
rows.push({state: 'GI OFF (key + env)', ev: await exposure(), ...digest(await readPixels())});

await page.evaluate(() => { const w = window.__lemWorld, gi = w.subsystems.get('gi');
  gi.uniforms.lemGIStrength.value = gi.__g; w.__env = w.scene.environment; w.scene.environment = null; });
await page.waitForTimeout(1600);
rows.push({state: 'ENV OFF', ev: await exposure(), ...digest(await readPixels())});

console.log(JSON.stringify({cam, time, sun: pick.sun, elev: pick.elev,
  nPts: pick.pts.length, rows, pageErrors: errs.slice(0, 6)}, null, 1));
await b.close();
