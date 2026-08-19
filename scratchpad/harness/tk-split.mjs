/* tk-split.mjs — decompose a tank shell pixel into key, fill and env, in one
 * page session, on the same pixels.
 *
 *   full      everything on
 *   noSun     gi.sun.intensity = 0            -> what is left is fill
 *   noGI      lemGIStrength = 0               -> what is left is key + env
 *   noEnv     scene.environment = null        -> key + probe fill
 *   noFacadeAO bldAO forced to 1 (uniform)    -> how much my own AO is worth
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
          + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(9000);
await page.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } w.camera.updateMatrixWorld(true); });
await page.waitForTimeout(400);

const uids = (a.uids || 'pac-flash-1,pac-flash-2').split(',');
const pick = await page.evaluate((uids) => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), B = w.subsystems.get('buildings');
  const THREE = w.ctx.THREE, cam = w.camera;
  const sunDir = gi.sun.position.clone().normalize();
  const occ = []; w.scene.traverse(o => { if (o.isMesh && o.visible && o.name.includes(':')) occ.push(o); });
  const shell = [], wall = [], pad = [];
  const rc = new THREE.Raycaster(); rc.layers.enableAll();
  const ndc = new THREE.Vector2(), pv = new THREE.Vector3();
  const scan = (site, push) => {
    const targets = []; site.root.traverse(o => { if (o.isMesh && o.visible) targets.push(o); });
    const c = site.root.position;
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const dx of [-60, 0, 60]) for (const dz of [-60, 0, 60]) for (const dy of [0, 30]) {
      pv.set(c.x + dx, c.y + dy, c.z + dz).project(cam);
      x0 = Math.min(x0, (pv.x * .5 + .5) * innerWidth); x1 = Math.max(x1, (pv.x * .5 + .5) * innerWidth);
      y0 = Math.min(y0, (-pv.y * .5 + .5) * innerHeight); y1 = Math.max(y1, (-pv.y * .5 + .5) * innerHeight);
    }
    for (let sy = Math.max(0, Math.floor(y0)); sy <= Math.min(innerHeight - 1, Math.ceil(y1)); sy++)
      for (let sx = Math.max(0, Math.floor(x0)); sx <= Math.min(innerWidth - 1, Math.ceil(x1)); sx++) {
        ndc.set((sx + .5) / innerWidth * 2 - 1, -((sy + .5) / innerHeight * 2 - 1));
        rc.setFromCamera(ndc, cam);
        const h = rc.intersectObjects(targets, false)[0];
        if (!h || !h.face) continue;
        const n = h.face.normal.clone().applyNormalMatrix(
          new THREE.Matrix3().getNormalMatrix(h.object.matrixWorld)).normalize();
        const sr = new THREE.Raycaster(h.point.clone().addScaledVector(sunDir, 0.05), sunDir, 0.02, 400);
        sr.layers.enableAll();
        const sunlit = sr.intersectObjects(occ, false).length === 0;
        push({sx, sy, NL: +n.dot(sunDir).toFixed(3), ny: +n.y.toFixed(2), sunlit,
              rel: +(h.point.y - site.root.position.y).toFixed(1), nm: h.object.name});
      }
  };
  for (const uid of uids) { const s = B.sites.get(uid); if (s) scan(s, p => {
    if (Math.abs(p.ny) < 0.30 && p.rel > 2.5 && /:steel$/.test(p.nm)) shell.push(p);
    else if (p.ny > 0.9 && p.rel < 3.0 && /:concrete$/.test(p.nm)) pad.push(p); }); }
  const bh = B.sites.get('multitek-ns');
  if (bh) scan(bh, p => { if (Math.abs(p.ny) < 0.30 && p.rel > 3 && /:brick$/.test(p.nm)) wall.push(p); });
  return {shell, wall, pad, sun: [sunDir.x, sunDir.y, sunDir.z].map(v => +v.toFixed(3))};
}, uids);

async function readPixels() {
  const buf = await page.screenshot({type: 'png'});
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await page.evaluate(async ({src, sets}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const out = {};
    for (const k in sets) out[k] = sets[k].map(p => {
      const o = (p.sy * im.width + p.sx) * 4;
      return +(0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]).toFixed(2); });
    return out;
  }, {src, sets: {shell: pick.shell, wall: pick.wall, pad: pick.pad}});
}
const mean = v => v.length ? +(v.reduce((x, y) => x + y, 0) / v.length).toFixed(1) : null;
function digest(px) {
  const sel = (set, L, f) => set.map((p, i) => ({...p, L: L[i]})).filter(f).map(p => p.L);
  return {
    shellLit: mean(sel(pick.shell, px.shell, p => p.sunlit && p.NL > 0.35)),
    shellMid: mean(sel(pick.shell, px.shell, p => p.sunlit && p.NL > -0.15 && p.NL <= 0.35)),
    shellDark: mean(sel(pick.shell, px.shell, p => p.NL < -0.15)),
    shellShaded: mean(sel(pick.shell, px.shell, p => !p.sunlit && p.NL > 0.35)),
    wallLit: mean(sel(pick.wall, px.wall, p => p.sunlit && p.NL > 0.35)),
    wallDark: mean(sel(pick.wall, px.wall, p => p.NL < -0.15)),
    padOpen: mean(sel(pick.pad, px.pad, p => p.sunlit)),
    padShadow: mean(sel(pick.pad, px.pad, p => !p.sunlit)),
  };
}
const rows = [];
rows.push({state: 'full', ...digest(await readPixels())});
await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  gi.__i = gi.sun.intensity; gi.sun.intensity = 0; });
await page.waitForTimeout(1200);
rows.push({state: 'noSun (fill only)', ...digest(await readPixels())});
await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  gi.sun.intensity = gi.__i; gi.__g = gi.uniforms.lemGIStrength.value;
  gi.uniforms.lemGIStrength.value = 0; });
await page.waitForTimeout(1200);
rows.push({state: 'noGI (key + env)', ...digest(await readPixels())});
await page.evaluate(() => { const w = window.__lemWorld, gi = w.subsystems.get('gi');
  gi.uniforms.lemGIStrength.value = gi.__g; w.__env = w.scene.environment; w.scene.environment = null; });
await page.waitForTimeout(1500);
rows.push({state: 'noEnv', ...digest(await readPixels())});
await page.evaluate(() => { const w = window.__lemWorld; w.scene.environment = w.__env; });
await page.waitForTimeout(1200);
rows.push({state: 'restored', ...digest(await readPixels())});
console.log(JSON.stringify({cam, time, sun: pick.sun,
  n: {shell: pick.shell.length, wall: pick.wall.length, pad: pick.pad.length},
  rows, pageErrors: errs}, null, 1));
await b.close();
