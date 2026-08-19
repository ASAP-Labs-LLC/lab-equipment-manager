/* tproj.mjs — do the numbers, on the CPU, for one ground point that ought to be
 * in a train's shadow.
 *
 * Everything visual has been ambiguous. This takes a vehicle, walks the sun ray
 * down from it to the ground, projects that ground point through the sun's own
 * `shadow.matrix`, reads the texel it lands on out of the map, and prints the
 * two depths side by side. If the stored depth is the train, the lookup should
 * shadow and the fault is downstream; if the stored depth is the ground, the
 * train is not where the map thinks it is.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await (await browser.newContext({viewport: {width: 1280, height: 720}})).newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(6000);

console.log(JSON.stringify(await page.evaluate(async () => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), tr = w.subsystems.get('trains');
  w.engine.updaters = [];
  /* Silence every caster but the rolling stock, so `stored` can only be a
   * train. With the whole site casting, a ground point three metres from a tank
   * car is occluded by the tree line eighty metres up the sun ray and the
   * comparison says nothing. */
  const trainSet = new Set();
  tr.root.traverse(o => trainSet.add(o));
  w.scene.traverse(o => {
    if ((o.isMesh || o.isInstancedMesh) && !trainSet.has(o)) o.castShadow = false;
  });
  for (const c of gi._csm || []) { c.casters.length = 0; c.dirty = true; }
  w.engine.shadowNeedsUpdate = true;
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  const THREE = w.THREE || Object.getPrototypeOf(w.scene).constructor;
  const map = gi.sun.shadow.map;
  const N = map.width, buf = new Uint8Array(N * N * 4);
  w.engine.renderer.readRenderTargetPixels(map, 0, 0, N, N, buf);
  const unpack = (x, y) => {
    const px = Math.min(N - 1, Math.max(0, Math.round(x))),
          py = Math.min(N - 1, Math.max(0, Math.round(y)));
    const o = (py * N + px) * 4;
    return buf[o] / 255 + buf[o + 1] / 65025 + buf[o + 2] / 16581375 +
           buf[o + 3] / 4228250625;
  };

  /* Sun ray, world space. */
  const dir = gi.sun.target.position.clone().sub(gi.sun.position).normalize();

  const probe = (P, tag) => {
    const gr = (x, z) => w.ctx?.ground?.(x, z) ?? w.subsystems.get('terrain')?.height?.(x, z) ?? 0;
    const g = gr(P.x, P.z);
    /* Walk down the sun ray from the vehicle to ground height. */
    const t = (P.y - g) / -dir.y;
    const G = P.clone().addScaledVector(dir, t);
    G.y = gr(G.x, G.z);
    const c = G.clone().applyMatrix4(gi.sun.shadow.matrix);
    const uv = [c.x, c.y, c.z];
    const stored = unpack(uv[0] * N, uv[1] * N);
    /* And the same for the point directly under the vehicle, as a control. */
    return {tag, P: P.toArray().map(n => +n.toFixed(1)),
            groundHit: G.toArray().map(n => +n.toFixed(1)),
            uv: uv.map(n => +n.toFixed(4)),
            stored: +stored.toFixed(5), receiver: +uv[2].toFixed(5),
            shadowed: stored < uv[2] - 0.00006};
  };

  const out = [];
  const dirArr = dir.toArray().map(n => +n.toFixed(3));
  let k = 0;
  tr.root.traverse(o => {
    if (!o.isMesh || o.isInstancedMesh || !o.visible || !o.parent?.visible) return;
    if (o.material?.isMeshBasicMaterial) return;
    if (k++ % 7) return;
    const P = new o.position.constructor();
    o.getWorldPosition(P);
    out.push(probe(P, 'vehicle' + k));
  });

  /* A control: a tall mast or a building corner, which demonstrably casts. */
  const ctl = [];
  w.scene.traverse(o => {
    if (ctl.length >= 3) return;
    if (!o.isMesh || !o.castShadow) return;
    if (!/lamp|steel|mast|brick/.test(o.name || '')) return;
    const P = new o.position.constructor();
    o.getWorldPosition(P);
    o.geometry?.computeBoundingBox?.();
    const bb = o.geometry?.boundingBox;
    if (bb) P.y = Math.max(P.y, bb.max.y * 0.8);
    ctl.push(probe(P, 'ctl:' + o.name));
  });

  return {dir: dirArr, mapN: N, near: gi.sun.shadow.camera.near,
          far: gi.sun.shadow.camera.far, bias: gi.sun.shadow.bias,
          vehicles: out, control: ctl};
}), null, 1));
await browser.close();
