/* tk-scan.mjs — the two numbers the critic named, measured on the tank shells.
 *
 *   1. FORM SHADING: raycast a grid at the tank farm, keep the hits whose world
 *      normal is horizontal (the cylinder shell), and correlate N.L against the
 *      pixel luminance at the same screen position. A shell with no terminator
 *      has a wide N.L spread and a flat L spread.
 *   2. CAST SHADOW: keep the hits on the bund/pad whose normal is up, march each
 *      toward the sun against the site's own meshes, and compare pixel L for the
 *      occluded points against the open ones.
 *
 *   node tk-scan.mjs [--cam far] [--time 9] [--out /tmp/x.png] [--tag before]
 */
import {chromium} from 'playwright';
import fs from 'fs';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
          + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const W = +(a.w || 1600), H = +(a.h || 900);
const page = await b.newPage({viewport: {width: W, height: H}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(9000);

/* Freeze the rig so the screenshot and the raycast agree to the pixel. */
const meta = await page.evaluate(() => {
  const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  return {buildStable: window.__buildStable !== false,
          camY: +w.camera.position.y.toFixed(1), fov: w.camera.fov};
});
await page.waitForTimeout(600);
const buf = await page.screenshot({type: 'png'});
if (a.out) fs.writeFileSync(a.out, buf);
const src = 'data:image/png;base64,' + buf.toString('base64');

const res = await page.evaluate(async ({src, uids}) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), B = w.subsystems.get('buildings');
  const THREE = w.ctx.THREE || window.THREE;
  const cam = w.camera;
  /* pixels */
  const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
  const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
  const g2 = cv.getContext('2d', {willReadFrequently: true}); g2.drawImage(im, 0, 0);
  const px = g2.getImageData(0, 0, im.width, im.height).data;
  const SW = im.width, SH = im.height;
  const lumAt = (sx, sy) => {
    sx = Math.round(sx); sy = Math.round(sy);
    if (sx < 0 || sy < 0 || sx >= SW || sy >= SH) return null;
    const o = (sy * SW + sx) * 4;
    return 0.2126 * px[o] + 0.7152 * px[o + 1] + 0.0722 * px[o + 2];
  };
  const rgbAt = (sx, sy) => {
    sx = Math.round(sx); sy = Math.round(sy);
    const o = (sy * SW + sx) * 4;
    return [px[o], px[o + 1], px[o + 2]];
  };
  /* sun */
  const sunDir = gi.sun.position.clone().normalize();

  const out = {sun: {x: +sunDir.x.toFixed(3), y: +sunDir.y.toFixed(3), z: +sunDir.z.toFixed(3),
                     elevDeg: +(Math.asin(sunDir.y) * 180 / Math.PI).toFixed(1)},
               screen: {w: SW, h: SH}, sites: []};

  for (const uid of uids) {
    const site = B.sites.get(uid);
    if (!site) continue;
    const targets = [];
    site.root.traverse(o => { if (o.isMesh && o.visible) targets.push(o); });
    /* everything the sun could be blocked by, not just this site */
    const occluders = [];
    w.scene.traverse(o => { if (o.isMesh && o.visible && !o.material?.transparent &&
      /^(site:)?/.test(o.name) && o.name.includes(':')) occluders.push(o); });

    const c = site.root.position;
    /* screen-space box of the site */
    const probe = new THREE.Vector3();
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const dx of [-60, 0, 60]) for (const dz of [-60, 0, 60]) for (const dy of [0, 30]) {
      probe.set(c.x + dx, c.y + dy, c.z + dz).project(cam);
      const sx = (probe.x * 0.5 + 0.5) * SW, sy = (-probe.y * 0.5 + 0.5) * SH;
      x0 = Math.min(x0, sx); x1 = Math.max(x1, sx); y0 = Math.min(y0, sy); y1 = Math.max(y1, sy);
    }
    /* terrain has to be in the target list or the reference cannot be sampled */
    w.scene.traverse(o => { if (o.isMesh && o.visible && /^terrain/.test(o.name || '')) targets.push(o); });
    const rc = new THREE.Raycaster();
    rc.layers.enableAll();
    rc.firstHitOnly = false;
    const shell = [], pad = [], ground = [];
    const ndc = new THREE.Vector2();
    const step = Math.max(1, Math.round(Math.max(x1 - x0, y1 - y0) / 150));
    for (let sy = Math.max(0, Math.floor(y0)); sy <= Math.min(SH - 1, Math.ceil(y1)); sy += step) {
      for (let sx = Math.max(0, Math.floor(x0)); sx <= Math.min(SW - 1, Math.ceil(x1)); sx += step) {
        ndc.set((sx + 0.5) / SW * 2 - 1, -((sy + 0.5) / SH * 2 - 1));
        rc.setFromCamera(ndc, cam);
        const hits = rc.intersectObjects(targets, false);
        if (!hits.length) continue;
        const h = hits[0];
        if (!h.face) continue;
        const n = h.face.normal.clone()
          .applyNormalMatrix(new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
        const NL = n.dot(sunDir);
        const L = lumAt(sx, sy);
        if (L == null) continue;
        const rel = h.point.y - site.root.position.y;
        const nm = h.object.name;
        if (Math.abs(n.y) < 0.30 && rel > 2.5 && /:(steel|rust)$/.test(nm)) {
          shell.push({NL: +NL.toFixed(3), L: +L.toFixed(1), y: +rel.toFixed(1),
                      rgb: rgbAt(sx, sy), sx, sy, nx: +n.x.toFixed(2), nz: +n.z.toFixed(2)});
        } else if (/^terrain/.test(nm) && n.y > 0.7) {
          /* The in-frame reference. A parallel sky round can move every RGB in
           * the frame between two runs, so nothing here is quoted against a
           * previous session's absolute level — only against the dirt this
           * facility is standing on, in the same screenshot. */
          ground.push({L: +L.toFixed(1), rgb: rgbAt(sx, sy)});
        } else if (n.y > 0.9 && rel < 3.0 && /:concrete$/.test(nm)) {
          /* is the sun blocked from here? */
          const orig = h.point.clone().addScaledVector(sunDir, 0.05);
          const sr = new THREE.Raycaster(orig, sunDir, 0.02, 400);
          sr.layers.enableAll();
          const occ = sr.intersectObjects(occluders, false).length > 0;
          pad.push({occ, L: +L.toFixed(1), rgb: rgbAt(sx, sy), sx, sy});
        }
      }
    }
    const stat = arr => {
      if (!arr.length) return null;
      const s = arr.slice().sort((p, q) => p - q);
      const mean = s.reduce((x, y) => x + y, 0) / s.length;
      return {n: s.length, min: +s[0].toFixed(1), p10: +s[Math.floor(s.length * .1)].toFixed(1),
              p50: +s[Math.floor(s.length * .5)].toFixed(1),
              p90: +s[Math.floor(s.length * .9)].toFixed(1),
              max: +s[s.length - 1].toFixed(1), mean: +mean.toFixed(1)};
    };
    /* the terminator test: shell pixels binned by N.L */
    const bins = [];
    for (let i = 0; i < 5; i++) {
      const lo = -1 + i * 0.4, hi = lo + 0.4;
      const sel = shell.filter(s => s.NL >= lo && s.NL < hi);
      bins.push({NL: `${lo.toFixed(1)}..${hi.toFixed(1)}`, n: sel.length,
                 L: stat(sel.map(s => s.L))});
    }
    const lit = shell.filter(s => s.NL > 0.35).map(s => s.L);
    const dark = shell.filter(s => s.NL < -0.15).map(s => s.L);
    const padOcc = pad.filter(p => p.occ).map(p => p.L);
    const padOpen = pad.filter(p => !p.occ).map(p => p.L);
    out.sites.push({uid, arch: site.materials.arch,
      screenBox: [Math.round(x0), Math.round(y0), Math.round(x1 - x0), Math.round(y1 - y0)],
      step, shellHits: shell.length, padHits: pad.length,
      shellAll: stat(shell.map(s => s.L)),
      NLrange: shell.length ? {min: +Math.min(...shell.map(s => s.NL)).toFixed(2),
                               max: +Math.max(...shell.map(s => s.NL)).toFixed(2)} : null,
      bins,
      litSide: stat(lit), darkSide: stat(dark),
      formSpread: (lit.length && dark.length)
        ? +(lit.reduce((x, y) => x + y, 0) / lit.length - dark.reduce((x, y) => x + y, 0) / dark.length).toFixed(1)
        : null,
      padGeomOccluded: stat(padOcc), padGeomOpen: stat(padOpen),
      padShadowDepth: (padOcc.length && padOpen.length)
        ? +(padOpen.reduce((x, y) => x + y, 0) / padOpen.length - padOcc.reduce((x, y) => x + y, 0) / padOcc.length).toFixed(1)
        : null,
      groundHits: ground.length,
      groundL: stat(ground.map(g => g.L)),
      groundRGB: ground.length ? [0, 1, 2].map(i =>
        +(ground.reduce((s, h) => s + h.rgb[i], 0) / ground.length).toFixed(1)) : null,
      shellMinusGround: (shell.length && ground.length)
        ? +(shell.reduce((s, h) => s + h.L, 0) / shell.length
            - ground.reduce((s, h) => s + h.L, 0) / ground.length).toFixed(1) : null,
      litMinusGround: (lit.length && ground.length)
        ? +(lit.reduce((x, y) => x + y, 0) / lit.length
            - ground.reduce((s, h) => s + h.L, 0) / ground.length).toFixed(1) : null,
      darkMinusGround: (dark.length && ground.length)
        ? +(dark.reduce((x, y) => x + y, 0) / dark.length
            - ground.reduce((s, h) => s + h.L, 0) / ground.length).toFixed(1) : null,
      shellMeanRGB: shell.length ? [0, 1, 2].map(i =>
        +(shell.reduce((s, h) => s + h.rgb[i], 0) / shell.length).toFixed(1)) : null,
      padMeanRGB: pad.length ? [0, 1, 2].map(i =>
        +(pad.reduce((s, h) => s + h.rgb[i], 0) / pad.length).toFixed(1)) : null,
    });
  }
  return out;
}, {src, uids: (a.uids || 'pac-flash-1,pac-flash-2').split(',')});
res.meta = meta; res.cam = cam; res.time = time; res.tag = a.tag || null;
res.pageErrors = errs;
console.log(JSON.stringify(res, null, 1));
await b.close();
