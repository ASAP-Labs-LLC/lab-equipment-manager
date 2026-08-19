/* sk-mainflat.mjs — IS THE FAR HEADLAND FLAT, AND WHO FLATTENED IT?
 *
 * A blind critic called the mainland "a single extruded silhouette with a
 * uniform bright rim and zero internal value variation". Four suspects:
 *   (A) sky.js's shared aerial-perspective fog chunk
 *   (B) terrain.js's private per-vertex haze in `_rangeMaterial`
 *   (C) the mesh having no relief to show
 *   (D) the composite's bloom halo around a bright sky / dark silhouette
 *
 * Method, all in one page session so every number is the same frame:
 *
 *  - the mainland's footprint on screen is found by DIFFERENCE, not by colour
 *    or by projection: capture with the mesh visible and with `mesh.visible =
 *    false`, and the pixels that moved are exactly the pixels it painted,
 *    occlusion included. Same trick for the island's ground, which excludes
 *    every pixel a tree or a shed is standing in front of — so the control is
 *    island GROUND, not "the island".
 *  - film grain (uFilmGrain 0.012, ~±1.5 codes) is zeroed for the statistics
 *    captures, so a standard deviation of 2 is not half noise.
 *  - bloom is zeroed live at `engine._passes.composite.material.uniforms.uBloom`
 *    (the render loop rewrites uHasBloom from the tier every frame but never
 *    touches uBloom), so the rim can be measured with and without it.
 *  - the mesh's own relief is read off the geometry in metres, and the shader
 *    in `_rangeMaterial` is re-evaluated per vertex in JS and pushed through
 *    engine.js's composite chain, so the PREDICTED spread of screen luminance
 *    can be compared with the measured one, with the private haze on and off.
 *
 * Read-only with respect to the repo. It mutates only the live page.
 *
 *   node sk-mainflat.mjs [--out prefix]
 */
import {chromium} from 'playwright';
import fs from 'fs';

const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const OUT = a.out || '/Users/rynatical/LAB-lem/scratchpad/harness/mainflat';
const MODS = a.mods || 'sky,gi,terrain,vegetation,buildings,rail,trains';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${MODS}`
          + `&cam=${a.cam || 'far'}&time=${a.time || '9'}&hud=0&quality=ultra&weather=clear`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});

/* settle */
let stable = 0, prev = null; const t1 = Date.now();
while (Date.now() - t1 < 30000) {
  await page.waitForTimeout(350);
  const now = await page.evaluate(() => { const s = window.__lemWorld?.stats?.(); return s ? [s.drawCalls, s.triangles] : null; });
  if (!now) break;
  if (prev && now[0] === prev[0] && Math.abs(now[1] - prev[1]) < 2000) stable++; else stable = 0;
  prev = now;
  if (stable >= 10) break;
}
/* FREEZE.
 *
 * The first run of this instrument produced a "mainland mask" covering the
 * whole frame, because the difference method assumes the only thing that
 * changes between two captures is the thing being hidden — and the world is
 * alive: water normals scroll, trees bend, trains run, the parse timer adds
 * rolling stock every two seconds. Two identical captures differed by up to
 * 117 codes somewhere in the frame.
 *
 * So the clock is pinned rather than the loop stopped: `getDelta` returns 0,
 * which leaves the rAF loop running (and the canvas presenting normally) while
 * every updater gets dt 0 and `engine.time` — which drives the grain, the
 * ripples and the sky — stops advancing. The parse timer is stubbed so no new
 * train can enter between two captures. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  w.rig.idleDrift = false; w.rig.apply(1);
  w.parse = () => {};
  w.engine.clock.getDelta = () => 0;
  window.__caps = {};
});

/* ---- 1. verification: what is the mainland made of --------------------- */
const facts = await page.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const cam = w.camera; cam.updateMatrixWorld(true);
  const named = {};
  w.scene.traverse(o => { if (o.name) named[o.name] = o; });
  const M = named['terrain-mainland'], H = named['terrain-horizon'], O = named['terrain-ocean'];
  const desc = m => {
    if (!m) return null;
    const g = m.geometry, mat = m.material;
    g.computeBoundingBox();
    const bb = g.boundingBox;
    return {name: m.name, matType: mat.type, fog: mat.fog, depthTest: mat.depthTest,
            depthWrite: mat.depthWrite, side: mat.side, renderOrder: m.renderOrder,
            transparent: mat.transparent, blending: mat.blending,
            verts: g.attributes.position.count, tris: g.index.count / 3,
            attrs: Object.keys(g.attributes),
            bbox: {min: [bb.min.x, bb.min.y, bb.min.z].map(v => +v.toFixed(1)),
                   max: [bb.max.x, bb.max.y, bb.max.z].map(v => +v.toFixed(1))},
            uniforms: Object.keys(mat.uniforms || {}),
            fragHasFogChunk: /fog_fragment|vFogDepth|fogFactor/.test(mat.fragmentShader || ''),
            programFogDefine: !!(mat.defines && mat.defines.USE_FOG)};
  };
  const cu = w.engine._passes.composite.material.uniforms;
  const uv = k => { const v = cu[k].value; return v && v.isVector3 ? [v.x, v.y, v.z] : (v && v.isVector2 ? [v.x, v.y] : v); };
  const U = M.material.uniforms;
  const vec = u => { const v = u.value; return v.isColor ? [v.r, v.g, v.b] : (v.isVector3 ? [v.x, v.y, v.z] : v); };
  return {
    mainland: desc(M), horizon: desc(H), ocean: desc(O),
    core: desc(named['terrain-core']),
    ringName: Object.keys(named).find(n => n.startsWith('terrain-ring')),
    sceneFog: w.scene.fog ? {ctor: w.scene.fog.constructor.name, density: w.scene.fog.density,
                             color: [w.scene.fog.color.r, w.scene.fog.color.g, w.scene.fog.color.b].map(v => +v.toFixed(4))} : null,
    rangeUniforms: {uHaze: vec(U.uHaze).map(v => +v.toFixed(4)),
                    uSkyTop: vec(U.uSkyTop).map(v => +v.toFixed(4)),
                    uSunDir: vec(U.uSunDir).map(v => +v.toFixed(4)),
                    uSunColor: vec(U.uSunColor).map(v => +v.toFixed(4)),
                    uWinter: U.uWinter.value},
    composite: {uExposure: cu.uExposure.value, uBloom: cu.uBloom.value,
                uVignette: cu.uVignette.value, uSaturation: cu.uSaturation.value,
                uContrast: cu.uContrast.value, uFilmGrain: cu.uFilmGrain.value,
                uBlackPoint: cu.uBlackPoint.value, uWhitePoint: cu.uWhitePoint.value,
                uToe: cu.uToe.value, uLift: uv('uLift'), uGain: uv('uGain'),
                uAOStrength: cu.uAOStrength.value,
                tierBloom: w.engine.tier.bloom, tierAO: w.engine.tier.ao},
    camera: {pos: [cam.position.x, cam.position.y, cam.position.z].map(v => +v.toFixed(1)),
             fov: cam.fov, far: cam.far},
    waterY: +t.waterY.toFixed(2), mainlandR: +t.mainlandR.toFixed(1),
    islandR: +t.islandR.toFixed(1),
    heightAtCoversMainland: (() => {
      /* does terrain.heightAt know anything about the mainland? sample a point
       * on the mainland's shoreline arc and see what it returns */
      const g = M.geometry, p = g.attributes.position;
      let i = 0; const aUp = g.attributes.aUp;
      for (let k = 0; k < p.count; k++) if (aUp.getX(k) < 0.02) { i = k; break; }
      const x = p.getX(i), z = p.getZ(i), y = p.getY(i);
      return {sampleXZ: [+x.toFixed(1), +z.toFixed(1)], meshY: +y.toFixed(2),
              heightAt: +t.heightAt(x, z).toFixed(2)};
    })(),
  };
});

/* ---- 2. captures --------------------------------------------------------- */
const setState = async (s) => page.evaluate(({bloom, grain, hide}) => {
  const w = window.__lemWorld;
  const cu = w.engine._passes.composite.material.uniforms;
  if (bloom !== undefined) cu.uBloom.value = bloom;
  if (grain !== undefined) cu.uFilmGrain.value = grain;
  const want = new Set(hide || []);
  w.scene.traverse(o => {
    if (!o.name) return;
    if (o.name.startsWith('terrain-')) o.visible = !want.has(o.name) &&
      !(want.has('terrain-ring') && o.name.startsWith('terrain-ring'));
  });
  w.rig.idleDrift = false; w.rig.apply(1);
}, s);

const grab = async (key, file) => {
  await page.waitForTimeout(420);
  const buf = await page.screenshot({type: 'png'});
  if (file) fs.writeFileSync(file, buf);
  await page.evaluate(async ({key, src}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas');
    cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true});
    g.drawImage(im, 0, 0);
    window.__caps[key] = {w: im.width, h: im.height,
                          d: g.getImageData(0, 0, im.width, im.height).data};
  }, {key, src: 'data:image/png;base64,' + buf.toString('base64')});
};

await setState({bloom: 0.55, grain: 0.012, hide: []});
await grab('delivered', OUT + '.delivered.png');
await setState({bloom: 0.55, grain: 0.0, hide: []});
await grab('bloomOn', OUT + '.bloomon.png');
await setState({bloom: 0.0, grain: 0.0, hide: []});
await grab('bloomOff', OUT + '.bloomoff.png');
await grab('bloomOffB', null);                       // repeat: capture noise floor
await setState({bloom: 0.0, grain: 0.0, hide: ['terrain-mainland']});
await grab('noMain', OUT + '.nomain.png');
await setState({bloom: 0.0, grain: 0.0, hide: ['terrain-core', 'terrain-ring']});
await grab('noIsland', OUT + '.noisland.png');
await setState({bloom: 0.55, grain: 0.0, hide: ['terrain-mainland']});
await grab('noMainBloom', null);
await setState({bloom: 0.55, grain: 0.012, hide: []});

/* ---- 3. analysis --------------------------------------------------------- */
const res = await page.evaluate(() => {
  const C = window.__caps;
  const W = C.bloomOff.w, H = C.bloomOff.h;
  const Lof = (cap, x, y) => { const o = (y * cap.w + x) * 4; const d = cap.d;
    return 0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]; };
  const rgb = (cap, x, y) => { const o = (y * cap.w + x) * 4; const d = cap.d;
    return [d[o], d[o + 1], d[o + 2]]; };
  const maxdiff = (A, B, x, y) => { const o = (y * A.w + x) * 4;
    return Math.max(Math.abs(A.d[o] - B.d[o]), Math.abs(A.d[o + 1] - B.d[o + 1]),
                    Math.abs(A.d[o + 2] - B.d[o + 2])); };

  /* capture-to-capture noise floor, same state twice */
  let noiseN = 0, noiseMax = 0, noiseSum = 0;
  for (let y = 0; y < H; y += 3) for (let x = 0; x < W; x += 3) {
    const m = maxdiff(C.bloomOff, C.bloomOffB, x, y);
    noiseSum += m; noiseMax = Math.max(noiseMax, m); noiseN++;
  }
  const THRESH = 8;

  const mkMask = (A, B) => {
    const m = new Uint8Array(W * H); let n = 0;
    let x0 = W, x1 = -1, y0 = H, y1 = -1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      if (maxdiff(A, B, x, y) >= THRESH) {
        m[y * W + x] = 1; n++;
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    return {m, n, box: [x0, y0, x1, y1]};
  };
  const mainMask = mkMask(C.bloomOff, C.noMain);
  const islMask  = mkMask(C.bloomOff, C.noIsland);

  const stats = (mask, cap) => {
    const v = [];
    let adjSum = 0, adjN = 0;
    let vertSum = 0, vertN = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      if (!mask.m[y * W + x]) continue;
      const L = Lof(cap, x, y); v.push(L);
      if (x + 1 < W && mask.m[y * W + x + 1]) { adjSum += Math.abs(Lof(cap, x + 1, y) - L); adjN++; }
      if (y + 1 < H && mask.m[(y + 1) * W + x]) { vertSum += Math.abs(Lof(cap, x, y + 1) - L); vertN++; }
    }
    if (!v.length) return {n: 0};
    v.sort((p, q) => p - q);
    const mean = v.reduce((s, x) => s + x, 0) / v.length;
    const sd = Math.sqrt(v.reduce((s, x) => s + (x - mean) ** 2, 0) / v.length);
    const q = f => v[Math.min(v.length - 1, Math.floor(f * v.length))];
    return {n: v.length, mean: +mean.toFixed(2), sd: +sd.toFixed(2),
            p5: +q(0.05).toFixed(1), p50: +q(0.50).toFixed(1), p95: +q(0.95).toFixed(1),
            p95_p5: +(q(0.95) - q(0.05)).toFixed(1),
            min: +v[0].toFixed(1), max: +v[v.length - 1].toFixed(1),
            meanAbsAdjH: +(adjSum / Math.max(1, adjN)).toFixed(3),
            meanAbsAdjV: +(vertSum / Math.max(1, vertN)).toFixed(3)};
  };

  /* per-column top and bottom edge of the mainland band */
  const cols = [];
  for (let x = 0; x < W; x++) {
    let top = -1, bot = -1, cnt = 0;
    for (let y = 0; y < H; y++) if (mainMask.m[y * W + x]) { if (top < 0) top = y; bot = y; cnt++; }
    cols.push({x, top, bot, cnt});
  }
  const live = cols.filter(c => c.cnt > 6);
  const pickN = 14;
  const picks = [];
  for (let k = 0; k < pickN; k++) {
    const c = live[Math.floor((k + 0.5) / pickN * live.length)];
    if (c) picks.push(c);
  }

  const edgeProfile = (c, edge, cap) => {
    const y0 = edge === 'top' ? c.top : c.bot;
    const p = [];
    for (let dy = -12; dy <= 12; dy++) {
      const y = Math.min(H - 1, Math.max(0, y0 + dy));
      p.push(+Lof(cap, y0 === y ? c.x : c.x, y).toFixed(1));
    }
    return p;
  };
  const rimMetrics = (prof, edge) => {
    /* prof index 12 == the edge pixel itself. inside = below for top, above for bottom */
    const inside = edge === 'top' ? prof.slice(18, 25) : prof.slice(0, 7);
    const outside = edge === 'top' ? prof.slice(0, 7) : prof.slice(18, 25);
    const interior = inside.reduce((s, x) => s + x, 0) / inside.length;
    const back = outside.reduce((s, x) => s + x, 0) / outside.length;
    /* the rim: contiguous run of pixels just inside the edge that stand above
     * the interior by more than 2 L */
    const scan = edge === 'top' ? prof.slice(12, 22) : prof.slice(3, 13).reverse();
    let width = 0, peak = -1e9;
    for (let i = 0; i < scan.length; i++) {
      if (scan[i] > interior + 2) { width = i + 1; peak = Math.max(peak, scan[i]); } else break;
    }
    if (width === 0) peak = scan[0];
    return {interior: +interior.toFixed(1), background: +back.toFixed(1),
            rimPeak: +peak.toFixed(1), rimWidthPx: width,
            rimOverInterior: +(peak - interior).toFixed(1),
            edgeStep: +(interior - back).toFixed(1)};
  };

  const edges = {};
  for (const cap of ['bloomOn', 'bloomOff', 'delivered']) {
    edges[cap] = {top: [], bottom: []};
    for (const c of picks) {
      const pt = edgeProfile(c, 'top', C[cap]);
      const pb = edgeProfile(c, 'bot', C[cap]);
      edges[cap].top.push({x: c.x, yEdge: c.top, bandPx: c.bot - c.top + 1,
                           prof: pt, ...rimMetrics(pt, 'top')});
      edges[cap].bottom.push({x: c.x, yEdge: c.bot,
                              prof: pb, ...rimMetrics(pb, 'bottom')});
    }
  }
  /* what is behind the mainland's top edge, measured with it hidden */
  const behind = picks.map(c => {
    const y = Math.max(0, c.top - 2);
    return {x: c.x, y, sky: rgb(C.noMain, c.x, y), L: +Lof(C.noMain, c.x, y).toFixed(1),
            atEdgeHidden: +Lof(C.noMain, c.x, c.top + 2).toFixed(1)};
  });

  /* a full vertical trace through the band at the widest column */
  const widest = live.reduce((a, b) => (b.cnt > a.cnt ? b : a), live[0]);
  const trace = [];
  for (let y = widest.top; y <= widest.bot; y++)
    trace.push({dy: y - widest.top, on: +Lof(C.bloomOn, widest.x, y).toFixed(1),
                off: +Lof(C.bloomOff, widest.x, y).toFixed(1)});

  /* horizontal trace along the middle of the band, to show along-ridge variation */
  const hz = [];
  {
    const ys = live.map(c => Math.round((c.top + c.bot) / 2));
    for (let i = 0; i < live.length; i += Math.max(1, Math.floor(live.length / 60))) {
      const c = live[i], y = ys[i];
      hz.push({x: c.x, y, L: +Lof(C.bloomOff, c.x, y).toFixed(1)});
    }
  }

  return {
    noise: {meanMaxChannelDiff: +(noiseSum / noiseN).toFixed(3), worst: noiseMax, threshold: THRESH},
    mainland: {mask: {px: mainMask.n, box: mainMask.box,
                      widthPx: mainMask.box[2] - mainMask.box[0] + 1,
                      meanBandHeightPx: +(live.reduce((s, c) => s + c.cnt, 0) / live.length).toFixed(1),
                      maxBandHeightPx: Math.max(...live.map(c => c.cnt)),
                      topRowMin: Math.min(...live.map(c => c.top)),
                      topRowMax: Math.max(...live.map(c => c.top)),
                      botRowMin: Math.min(...live.map(c => c.bot)),
                      botRowMax: Math.max(...live.map(c => c.bot))},
               bloomOff: stats(mainMask, C.bloomOff),
               bloomOn: stats(mainMask, C.bloomOn),
               delivered: stats(mainMask, C.delivered)},
    island: {mask: {px: islMask.n, box: islMask.box},
             bloomOff: stats(islMask, C.bloomOff),
             bloomOn: stats(islMask, C.bloomOn),
             delivered: stats(islMask, C.delivered)},
    edges, behind, trace, hz,
    widestCol: {x: widest.x, top: widest.top, bot: widest.bot},
  };
});

/* ---- 4. the mesh itself, and what the shader does to it ------------------ */
const mesh = await page.evaluate(({box}) => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const cam = w.camera; cam.updateMatrixWorld(true);
  let M = null; w.scene.traverse(o => { if (o.name === 'terrain-mainland') M = o; });
  const g = M.geometry, P = g.attributes.position, N = g.attributes.normal;
  const aUp = g.attributes.aUp, aLit = g.attributes.aLit;
  const NA = 176, NR = 7;
  const U = M.material.uniforms;
  const V3 = cam.position.constructor;
  const uHaze = U.uHaze.value, uSun = U.uSunDir.value;
  const haze = [uHaze.r ?? uHaze.x, uHaze.g ?? uHaze.y, uHaze.b ?? uHaze.z];
  const sun = [uSun.x, uSun.y, uSun.z];
  const sl = Math.hypot(...sun); const sunN = sun.map(v => v / sl);
  const lum = 0.2126 * haze[0] + 0.7152 * haze[1] + 0.0722 * haze[2];
  const cu = w.engine._passes.composite.material.uniforms;
  const E = cu.uExposure.value, BP = cu.uBlackPoint.value, WP = cu.uWhitePoint.value,
        TOE = cu.uToe.value, SAT = cu.uSaturation.value, CON = cu.uContrast.value,
        LIFT = cu.uLift.value, GAIN = cu.uGain.value, VIG = cu.uVignette.value;
  const aces = x => { const A = 2.51, B = 0.03, Cc = 2.43, D = 0.59, Ee = 0.14;
    return Math.min(1, Math.max(0, (x * (A * x + B)) / (x * (Cc * x + D) + Ee))); };
  const srgb = c => c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(Math.max(c, 0), 1 / 2.4) - 0.055;
  const smoothstep = (e0, e1, x) => { const t2 = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t2 * t2 * (3 - 2 * t2); };
  const composite = (lin, uv) => {
    let c = lin.map(v => aces(v * E));
    c = c.map(v => (v - BP) / Math.max(1e-4, WP - BP));
    c = c.map(v => Math.max(v, 0));
    c = c.map(v => v * (v + TOE) / (v + TOE * 0.5 + 1e-4) * (1 + TOE * 0.5));
    c = c.map(v => Math.min(1, Math.max(0, v)));
    const lm = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    c = c.map(v => lm + (v - lm) * SAT);
    c = c.map(v => (v - 0.5) * CON + 0.5);
    c = [LIFT.x + c[0] * GAIN.x, LIFT.y + c[1] * GAIN.y, LIFT.z + c[2] * GAIN.z];
    const d = Math.hypot(uv[0] - 0.5, uv[1] - 0.5);
    const vg = 1 - VIG * smoothstep(0.35, 0.95, d);
    c = c.map(v => Math.min(1, Math.max(0, v * vg)));
    c = c.map(srgb);
    return 255 * (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]);
  };
  const shade = (vUp, ndl, dist, uv, hazeOn) => {
    const wood = [0.13, 0.20, 0.10].map(v => v * lum * 2.1);
    const sand = [0.52, 0.47, 0.37].map(v => v * lum);
    const k = smoothstep(0.02, 0.10, vUp);
    const base = [0, 1, 2].map(i => sand[i] + (wood[i] * (0.40 + 0.60 * ndl) - sand[i]) * k);
    const far = 1 - Math.exp(-dist * 0.00033);
    let hz = Math.min(0.92, Math.max(0, far * (1.12 + (0.86 - 1.12) * vUp)));
    if (!hazeOn) hz = 0;
    const c = [0, 1, 2].map(i => base[i] + (haze[i] - base[i]) * hz);
    return {L: composite(c, uv), hz, base, c};
  };

  /* per-vertex, only the ones that land inside the measured mainland band */
  const Wp = window.innerWidth, Hp = window.innerHeight;
  const V = new V3();
  const rows = [];
  for (let j = 0; j <= NR; j++) {
    const hs = [];
    for (let i = 0; i <= NA; i++) {
      const k = j * (NA + 1) + i;
      hs.push(P.getY(k) - t.waterY);
    }
    hs.sort((p, q) => p - q);
    rows.push({row: j, n: hs.length, minAboveWater: +hs[0].toFixed(1),
               p50: +hs[Math.floor(hs.length / 2)].toFixed(1),
               maxAboveWater: +hs[hs.length - 1].toFixed(1),
               relief: +(hs[hs.length - 1] - hs[0]).toFixed(1)});
  }
  const vis = [];
  for (let k = 0; k < P.count; k++) {
    V.set(P.getX(k), P.getY(k), P.getZ(k));
    const wx = V.x, wy = V.y, wz = V.z;
    const dist = V.distanceTo(cam.position);
    const nx = N.getX(k), ny = N.getY(k), nz = N.getZ(k);
    const nl = Math.hypot(nx, ny, nz) || 1;
    const ndl = Math.max(0, Math.min(1, (nx * sunN[0] + ny * sunN[1] + nz * sunN[2]) / nl));
    V.project(cam);
    if (V.z > 1) continue;
    const sx = (V.x * 0.5 + 0.5) * Wp, sy = (-V.y * 0.5 + 0.5) * Hp;
    if (sx < 0 || sx >= Wp || sy < 0 || sy >= Hp) continue;
    const vUp = aUp.getX(k);
    const uv = [sx / Wp, 1 - sy / Hp];
    const on = shade(vUp, ndl, dist, uv, true);
    const off = shade(vUp, ndl, dist, uv, false);
    vis.push({sx: +sx.toFixed(1), sy: +sy.toFixed(1), dist: Math.round(dist),
              aboveWater: +(wy - t.waterY).toFixed(1), vUp: +vUp.toFixed(3),
              ndl: +ndl.toFixed(3), hz: +on.hz.toFixed(3),
              Lon: +on.L.toFixed(2), Loff: +off.L.toFixed(2), aLit: aLit.getX(k)});
  }
  /* restrict to the band actually measured on screen */
  const inBand = vis.filter(v => v.sx >= box[0] && v.sx <= box[2] && v.sy >= box[1] - 2 && v.sy <= box[3] + 2);
  const st = (arr, f) => {
    const v = arr.map(f).sort((p, q) => p - q);
    if (!v.length) return {n: 0};
    const m = v.reduce((s, x) => s + x, 0) / v.length;
    return {n: v.length, mean: +m.toFixed(2),
            sd: +Math.sqrt(v.reduce((s, x) => s + (x - m) ** 2, 0) / v.length).toFixed(2),
            p5: +v[Math.floor(0.05 * v.length)].toFixed(2),
            p95: +v[Math.floor(0.95 * v.length)].toFixed(2),
            min: +v[0].toFixed(2), max: +v[v.length - 1].toFixed(2)};
  };
  /* the silhouette per screen column: the crest height in metres */
  const colTop = new Map();
  for (const v of inBand) {
    const cx = Math.round(v.sx);
    const cur = colTop.get(cx);
    if (!cur || v.sy < cur.sy) colTop.set(cx, v);
  }
  const crest = [...colTop.values()].sort((a2, b2) => a2.sx - b2.sx);
  const crestH = crest.map(v => v.aboveWater);

  return {
    tris: g.index.count / 3, verts: P.count, grid: {NA, NR},
    radialRows: rows,
    ampConstant: 300,
    reliefAllVerts: {minAboveWater: +st(vis, v => v.aboveWater).min,
                     maxAboveWater: +st(vis, v => v.aboveWater).max},
    visibleVerts: inBand.length,
    inBand: {aboveWater: st(inBand, v => v.aboveWater),
             dist: st(inBand, v => v.dist),
             vUp: st(inBand, v => v.vUp),
             ndl: st(inBand, v => v.ndl),
             hazeFactor: st(inBand, v => v.hz),
             predictedL_hazeOn: st(inBand, v => v.Lon),
             predictedL_hazeOff: st(inBand, v => v.Loff)},
    crestReliefMetres: crest.length ? {n: crest.length,
      min: +Math.min(...crestH).toFixed(1), max: +Math.max(...crestH).toFixed(1),
      range: +(Math.max(...crestH) - Math.min(...crestH)).toFixed(1),
      sample: crest.filter((_, i) => i % Math.max(1, Math.floor(crest.length / 24)) === 0)
                   .map(v => ({sx: Math.round(v.sx), sy: Math.round(v.sy), m: v.aboveWater,
                               dist: v.dist, vUp: v.vUp, hz: v.hz, Lon: v.Lon, Loff: v.Loff}))} : null,
    /* what the private haze costs: same vertices, haze on vs off */
    flatteningRatio: (() => {
      const on = st(inBand, v => v.Lon), off = st(inBand, v => v.Loff);
      return {sdOn: on.sd, sdOff: off.sd, ratio: +(on.sd / off.sd).toFixed(3),
              spreadOn: +(on.max - on.min).toFixed(2), spreadOff: +(off.max - off.min).toFixed(2)};
    })(),
  };
}, {box: res.mainland.mask.box});

console.log(JSON.stringify({url, facts, ...res, mesh, errors: errors.slice(0, 5)}, null, 1));
await b.close();
