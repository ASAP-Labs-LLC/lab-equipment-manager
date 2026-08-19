/* skyfog.mjs — where is the canopy, how far away is it, and what colour did the
 * haze make it.
 *
 *   node skyfog.mjs <url> <out.png> [--fog '{"k":[..],"max":..,"density":..}']
 *                                   [--pin <density>] [--map]
 *
 * `--fog` is applied with an init script, before the world builds, because the
 * chromatic weights are compiled into every material's fog chunk. `--pin` goes
 * through sky.setFogDensity after boot, which survives a recompute.
 *
 * With `--map` it also writes <out>.map.json: for every 32x32 screen cell, the
 * median distance of the scene geometry that projects into it and what that
 * geometry is called. That is how a crop gets a range attached to it instead of
 * a guess.
 */
import {chromium} from 'playwright';
import fs from 'fs';

const args = process.argv.slice(2);
const url = args[0], out = args[1];
const grab = f => { const i = args.indexOf(f); return i < 0 ? null : args[i + 1]; };
const fogInit = grab('--fog');
const pin = grab('--pin');
const wantMap = args.includes('--map');

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
p.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); });
if (fogInit) await p.addInitScript(`window.__lemFog = ${fogInit};`);
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(6000);

const info = await p.evaluate((pinArg) => {
  const w = window.__lemWorld, sky = w.subsystems.get('sky');
  if (pinArg != null && sky?.setFogDensity) sky.setFogDensity(parseFloat(pinArg));
  const f = w.scene.fog;
  const cam = w.camera;
  return {
    density: f ? f.density : null,
    fogColour: f ? [f.color.r, f.color.g, f.color.b] : null,
    cam: [cam.position.x, cam.position.y, cam.position.z],
    /* The tier matters more than it looks: bloom is on at ultra and off below
     * it, and it spills the bright hazy far field across the whole frame, so
     * the same fog measures roughly twice the blue-minus-red at ultra. Two
     * numbers from different tiers are not comparable. */
    tier: w.stats ? w.stats().tier : null,
  };
}, pin);
await p.waitForTimeout(1200);

let map = null;
if (wantMap) {
  map = await p.evaluate(() => {
    const w = window.__lemWorld, cam = w.camera, THREE = w.THREE || null;
    cam.updateMatrixWorld();
    const W = window.innerWidth, H = window.innerHeight, CELL = 32;
    const cw = Math.ceil(W / CELL), ch = Math.ceil(H / CELL);
    const cells = new Array(cw * ch);
    const push = (x, y, d, name) => {
      const cx = Math.floor(x / CELL), cy = Math.floor(y / CELL);
      if (cx < 0 || cy < 0 || cx >= cw || cy >= ch) return;
      const i = cy * cw + cx;
      (cells[i] || (cells[i] = {d: [], n: {}}));
      cells[i].d.push(d);
      cells[i].n[name] = (cells[i].n[name] || 0) + 1;
    };
    const v = new (cam.position.constructor)();
    const project = (x, y, z, name) => {
      v.set(x, y, z);
      const d = v.distanceTo(cam.position);
      v.project(cam);
      if (v.z > 1 || v.z < -1) return;
      push((v.x * 0.5 + 0.5) * W, (-v.y * 0.5 + 0.5) * H, d, name);
    };
    const named = o => o.userData?.lemPart || o.name || o.material?.name ||
                       (o.isInstancedMesh ? 'instanced' : 'mesh');
    w.scene.traverse(o => {
      if (!o.visible || (!o.isMesh && !o.isInstancedMesh)) return;
      const nm = named(o);
      if (o.isInstancedMesh && o.instanceMatrix) {
        const a = o.instanceMatrix.array, n = o.count;
        const step = Math.max(1, Math.floor(n / 4000));
        for (let i = 0; i < n; i += step) {
          const m = i * 16;
          /* Instance translation in the mesh's own space, then to world. */
          v.set(a[m + 12], a[m + 13], a[m + 14]).applyMatrix4(o.matrixWorld);
          project(v.x, v.y, v.z, nm);
        }
      } else if (o.geometry) {
        if (!o.geometry.boundingSphere) o.geometry.computeBoundingSphere();
        const bs = o.geometry.boundingSphere;
        if (!bs) return;
        v.copy(bs.center).applyMatrix4(o.matrixWorld);
        project(v.x, v.y, v.z, nm);
      }
    });
    const med = a => { a.sort((x, y) => x - y); return a[a.length >> 1]; };
    return cells.map((c, i) => c ? {
      x: (i % cw) * 32, y: Math.floor(i / cw) * 32, n: c.d.length,
      dist: Math.round(med(c.d)),
      what: Object.entries(c.n).sort((a, b) => b[1] - a[1])[0][0],
    } : null).filter(Boolean);
  });
  fs.writeFileSync(out.replace(/\.png$/, '') + '.map.json', JSON.stringify(map));
}

await p.screenshot({path: out});
console.log(JSON.stringify({...info, cells: map ? map.length : 0, errs: errs.slice(0, 6)}));
await b.close();
