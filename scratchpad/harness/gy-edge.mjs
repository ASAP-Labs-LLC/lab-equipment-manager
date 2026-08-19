/* gy-edge.mjs — how HARD is the shadow edge on the plant, and does the map
 * agree with the geometry? Both, with the shadow fit anchored and un-anchored,
 * in ONE page session and with the stop frozen.
 *
 * Three answers per state, over the same lattice of pad pixels:
 *
 *   MISS RATE   geometry says this point is occluded from the sun and the map
 *               says it is lit. Cross-tabbed by what occludes it.
 *   EDGE        the luminance profile across the boundary between occluded and
 *               open ground, in screen pixels. A hard edge completes its step
 *               inside a pixel or two; a soft one spreads it over five or six.
 *               Distance is a BFS over the sampled lattice, so it is measured
 *               against the GEOMETRY's boundary, not against the map's.
 *   TEXEL       what the cascade actually covering each point is worth, in
 *               metres per texel and in screen pixels per texel.
 *
 *   node gy-edge.mjs [--cam far] [--time 9] [--step 2]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const cam = a.cam || 'far', time = a.time || '9', step = parseInt(a.step || '2', 10);
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
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  const gi = w.subsystems.get('gi');
  gi.setExposureLocked(true);
});
await page.waitForTimeout(1200);

/* --- the geometry, once: it does not depend on any shadow map --- */
const geo = await page.evaluate((step) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE, cam = w.camera;
  const d = gi.sunDirection.clone();
  const occ = [];
  w.scene.traverse(o => { if ((o.isMesh || o.isInstancedMesh) && o.visible &&
    !/^terrain|ocean|horizon|weather/.test(o.name || '') && o.geometry) occ.push(o); });
  const pads = [];
  w.scene.traverse(o => { if (o.isMesh && o.visible && /:concrete$/.test(o.name || '')) pads.push(o); });
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2();
  const pts = [];
  const W = innerWidth, H = innerHeight;
  for (let sy = 0; sy < H; sy += step) for (let sx = 0; sx < W; sx += step) {
    ndc.set((sx + .5) / W * 2 - 1, -((sy + .5) / H * 2 - 1));
    rc.setFromCamera(ndc, cam);
    const h = rc.intersectObjects(pads, false)[0];
    if (!h || !h.face) continue;
    const n = h.face.normal.clone().applyNormalMatrix(
      new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
    if (n.y < 0.92) continue;
    const sr = new THREE.Raycaster(h.point.clone().addScaledVector(d, 0.08), d, 0.05, 500);
    sr.layers.enableAll();
    const hit = sr.intersectObjects(occ, false)[0];
    let cls = 'open';
    if (hit) {
      const rise = hit.point.y - h.point.y;
      if (/vegetation|tree|canopy|trunk|foliage/i.test(hit.object.name || '') || hit.object.isInstancedMesh) cls = 'tree';
      else if (rise > 18) cls = 'tall';
      else if (rise > 3) cls = 'mid';
      else cls = 'low';
    }
    pts.push({sx, sy, cls, p: [h.point.x, h.point.y, h.point.z], n: [n.x, n.y, n.z]});
  }
  return {pts, sun: d.toArray().map(v => +v.toFixed(3)),
          elev: +(Math.asin(d.y) * 180 / Math.PI).toFixed(2), W, H};
}, step);

/* signed distance, in samples, to the nearest opposite-class sample (BFS on the
 * lattice). Positive = occluded side, negative = open side. */
const key = (x, y) => x + ',' + y;
const idx = new Map(geo.pts.map((p, i) => [key(p.sx, p.sy), i]));
const occl = geo.pts.map(p => p.cls !== 'open');
const dist = new Array(geo.pts.length).fill(99);
{
  const q = [];
  geo.pts.forEach((p, i) => {
    for (const [dx, dy] of [[step, 0], [-step, 0], [0, step], [0, -step]]) {
      const j = idx.get(key(p.sx + dx, p.sy + dy));
      if (j !== undefined && occl[j] !== occl[i]) { dist[i] = 0; q.push(i); break; }
    }
  });
  for (let h = 0; h < q.length; h++) {
    const i = q[h], p = geo.pts[i];
    for (const [dx, dy] of [[step, 0], [-step, 0], [0, step], [0, -step]]) {
      const j = idx.get(key(p.sx + dx, p.sy + dy));
      if (j === undefined || dist[j] !== 99 || occl[j] !== occl[i]) continue;
      dist[j] = dist[i] + 1; q.push(j);
    }
  }
}
const signed = dist.map((d, i) => (d === 99 ? null : (occl[i] ? d : -d)));

async function frameL() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, pts}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const sc = im.width / innerWidth;
    return pts.map(p => { const o = (Math.round(p.sy * sc) * im.width + Math.round(p.sx * sc)) * 4;
      return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(2); });
  }, {src, pts: geo.pts});
}

/* the shader's own answer, re-run on the CPU against the read-back maps */
async function mapSays() {
  for (let attempt = 0; attempt < 4; attempt++) {
    const r = await page.evaluate((pts) => {
      const w = window.__lemWorld, gi = w.subsystems.get('gi'), rn = w.engine.renderer;
      const maps = gi._csm.map(c => {
        const N = c.rt.width, buf = new Uint8Array(N * N * 4);
        try { rn.readRenderTargetPixels(c.rt, 0, 0, N, N, buf); } catch (e) { void e; }
        let nz = 0;
        for (let i = 0; i < buf.length; i += 4) if (buf[i] !== 0) { nz++; if (nz > 64) break; }
        return {i: c.i, N, buf, live: nz > 64, radius: c.radius};
      });
      if (maps.some(m => !m.live)) return {stale: true};
      const unpack = (m, ix, iy) => {
        ix = Math.min(m.N - 1, Math.max(0, ix)); iy = Math.min(m.N - 1, Math.max(0, iy));
        const o = (iy * m.N + ix) * 4;
        return m.buf[o] / 255 + m.buf[o + 1] / 65025 + m.buf[o + 2] / 16581375 + m.buf[o + 3] / 4228250625;
      };
      const TAPS = [[-0.8, 0.4], [0.4, 0.8], [0.8, -0.4], [-0.4, -0.8]];
      const casc = (m, P, N3) => {
        const e = gi.uniforms[`lemCsmMat${m.i}`].value.elements;
        const par = gi.uniforms[`lemCsmParam${m.i}`].value;
        const X = P[0] + N3[0] * par.w, Y = P[1] + N3[1] * par.w, Z = P[2] + N3[2] * par.w;
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
        const dx = P[0] - c.x, dy = P[1] - c.y, dz = P[2] - c.z;
        const q = Math.max(Math.abs(dx * right.x + dy * right.y + dz * right.z),
                           Math.abs(dx * up.x + dy * up.y + dz * up.z));
        return 1 - ss(r * 0.80, r * 0.97, q);
      };
      const bx = gi.uniforms.lemCsmBox0.value;
      const nc = gi.uniforms.lemNearCentre.value, nr = gi.uniforms.lemNearRadius.value;
      return {s: pts.map(p => {
        let v = 1;
        if (maps[1] && gi.uniforms.lemCsmReady1.value > 0.5) v = casc(maps[1], p.p, p.n);
        let which = maps[1] ? 1 : -1;
        if (maps[0] && gi.uniforms.lemCsmReady0.value > 0.5) {
          const wgt = boxW(p.p, {x: bx.x, y: bx.y, z: bx.z}, bx.w);
          v = v + (casc(maps[0], p.p, p.n) - v) * wgt;
          if (wgt > 0.5) which = 0;
        }
        const nw = boxW(p.p, nc, nr);
        if (nw > 0.5) which = -2;                       // three's own near map owns it
        return [+((v + (1 - v) * nw)).toFixed(3), which];
      })};
    }, geo.pts);
    if (!r.stale) return r.s;
    await page.waitForTimeout(1500);
  }
  return null;
}

const med = v => { if (!v.length) return null; const s = [...v].sort((x, y) => x - y); return +s[s.length >> 1].toFixed(1); };
const out = {};
for (const [tag, on] of [['anchored', true], ['slice-fit(old)', false]]) {
  await page.evaluate((on) => window.__lemWorld.subsystems.get('gi').setShadowAnchor(on), on);
  await page.waitForTimeout(4500);
  const L = await frameL();
  const S = await mapSays();
  const st = await page.evaluate(() => {
    const gi = window.__lemWorld.subsystems.get('gi'), cam = window.__lemWorld.camera;
    const tanH = Math.tan((cam.fov || 42) * 0.5 * Math.PI / 180);
    const px = 2 * tanH * (gi._vg?.dAim || 1) / (gi.ctx.renderer.domElement.height || 1080);
    return {box0: gi.uniforms.lemCsmBox0.value.toArray().map(v => +v.toFixed(1)),
            nearRadius: gi.uniforms.lemNearRadius.value,
            texels: gi._csm.map(c => +((c.radius * 2) / c.rt.width).toFixed(3)),
            metresPerPixelAtAim: +px.toFixed(3)};
  });
  /* cross-tab */
  const tab = {};
  geo.pts.forEach((p, i) => {
    if (!S) return;
    const v = S[i][0];
    const says = v < 0.3 ? 'MAP:shadow' : v < 0.9 ? 'MAP:penumbra' : 'MAP:lit';
    const k = `GEO:${p.cls.padEnd(4)} ${says}`;
    (tab[k] = tab[k] || {n: 0, L: []}); tab[k].n++; tab[k].L.push(L[i]);
  });
  let occN = 0, missN = 0;
  for (const [k, v] of Object.entries(tab)) {
    if (/GEO:open/.test(k)) continue;
    occN += v.n; if (/MAP:lit/.test(k)) missN += v.n;
  }
  /* edge profile, tall+mid casters only — the "bar of dark" */
  const prof = {};
  geo.pts.forEach((p, i) => {
    const d = signed[i];
    if (d === null || Math.abs(d) > 6) return;
    if (p.cls !== 'open' && p.cls !== 'tall' && p.cls !== 'mid') return;
    (prof[d] = prof[d] || []).push(L[i]);
  });
  const profile = {};
  for (let d = -6; d <= 6; d++) if (prof[d]) profile[d] = {n: prof[d].length, L: med(prof[d])};
  out[tag] = {
    state: st,
    occluded: occN, mapSaysLit: missN,
    missPct: occN ? +(100 * missN / occN).toFixed(1) : null,
    cross: Object.entries(tab).sort((x, y) => y[1].n - x[1].n)
      .map(([k, v]) => ({cell: k, n: v.n, L: med(v.L)})),
    edgeProfile: profile,
  };
}
console.log(JSON.stringify({cam, time, step, sun: geo.sun, elev: geo.elev,
  samples: geo.pts.length, out, pageErrors: errs.slice(0, 5)}, null, 1));
await b.close();
