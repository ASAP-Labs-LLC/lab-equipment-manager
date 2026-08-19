/* gy-ab.mjs — the ground-anchored shadow fit, A/B'd in ONE page session.
 *
 * Three modules were being rewritten in the same hour this was measured, so two
 * page loads are not comparable. `gi.setShadowAnchor(false)` puts the old
 * behaviour back in place on the next refit, with the same world, the same
 * camera and the same driver state underneath it — and it is read at the fit
 * rather than written onto objects, so `_adopt`/`_enrol` cannot undo it.
 *
 * Records, per state, over N frames: draw calls and triangles (mean / p95 /
 * max, and how many frames carried a shadow-map redraw), frame time, and the
 * luminance of the frame with the stop FROZEN.
 *
 *   node gy-ab.mjs [--cam far] [--time 9] [--frames 240] [--headed]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2);
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const cam = a.cam || 'far', time = a.time || '9';
const FRAMES = parseInt(a.frames || '240', 10);
const OUT = a.out || '/tmp/gy-ab';
fs.mkdirSync(OUT, {recursive: true});
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: !a.headed, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader',
         '--enable-gpu-rasterization']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
p.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 160)); });
await p.goto(url, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(10000);
await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); }
  gi.setExposureLocked(true);
});
await p.waitForTimeout(1200);

async function sample(frames) {
  return await p.evaluate((frames) => new Promise(res => {
    const w = window.__lemWorld, rn = w.engine.renderer;
    const draws = [], tris = [], dt = [];
    let last = performance.now(), n = 0;
    const tick = () => {
      const now = performance.now();
      draws.push(rn.info.render.calls); tris.push(rn.info.render.triangles);
      dt.push(now - last); last = now;
      if (++n < frames) requestAnimationFrame(tick);
      else {
        const st = v => {
          const s = [...v].sort((x, y) => x - y);
          return {mean: +(v.reduce((q, r) => q + r, 0) / v.length).toFixed(1),
                  p95: s[Math.floor(s.length * 0.95)], max: s[s.length - 1], min: s[0]};
        };
        const dS = st(draws);
        /* a frame that redrew three's shadow map costs a step of draws */
        const spikes = draws.filter(v => v > dS.min + 12).length;
        res({draws: dS, tris: st(tris), frameMs: st(dt),
             shadowRedrawFrames: spikes, frames: draws.length});
      }
    };
    requestAnimationFrame(tick);
  }), frames);
}
async function state() {
  return await p.evaluate(() => {
    const gi = window.__lemWorld.subsystems.get('gi');
    let nearCasters = 0;
    window.__lemWorld.scene.traverse(o => {
      if ((o.isMesh || o.isInstancedMesh) && o.castShadow && o.parent) nearCasters++;
    });
    return {anchor: !gi._noAnchor, exposure: gi._expNow,
            nearCentre: gi.uniforms.lemNearCentre.value.toArray().map(v => +v.toFixed(1)),
            nearRadius: gi.uniforms.lemNearRadius.value,
            box0: gi.uniforms.lemCsmBox0.value.toArray().map(v => +v.toFixed(1)),
            nearCasters,
            cascadeCost: gi._csm.map(c => ({i: c.i, cost: c.cost, tris: c.tris}))};
  });
}
async function lum(tag) {
  const buf = await p.screenshot({type: 'png'});
  fs.writeFileSync(`${OUT}/${cam}-${time}-${tag}.png`, buf);
  const src = 'data:image/png;base64,' + buf.toString('base64');
  return await p.evaluate(async (src) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const d = g.getImageData(0, 0, im.width, im.height).data;
    const L = [];
    for (let i = 0; i < d.length; i += 4) L.push(0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]);
    L.sort((x, y) => x - y);
    const q = f => +L[Math.floor(L.length * f)].toFixed(1);
    const mean = L.reduce((s, v) => s + v, 0) / L.length;
    const sd = Math.sqrt(L.reduce((s, v) => s + (v - mean) ** 2, 0) / L.length);
    return {mean: +mean.toFixed(1), sigma: +sd.toFixed(1),
            p1: q(0.01), p5: q(0.05), p50: q(0.5), p95: q(0.95), p99: q(0.99)};
  }, src);
}

const out = {};
for (const [tag, on] of [['anchor-on', true], ['anchor-off', false], ['anchor-on-again', true]]) {
  await p.evaluate((on) => window.__lemWorld.subsystems.get('gi').setShadowAnchor(on), on);
  await p.waitForTimeout(4000);          // let both coarse cascades come round
  out[tag] = {state: await state(), perf: await sample(FRAMES), lum: await lum(tag)};
}
console.log(JSON.stringify({cam, time, frames: FRAMES, headed: !!a.headed, out,
  pageErrors: errs.slice(0, 8)}, null, 1));
await b.close();
