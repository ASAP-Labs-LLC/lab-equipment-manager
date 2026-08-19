/* whoquad.mjs — raycast through the pixel where the black rectangle sits and
 * name the object, then list every mesh whose projected centre lands near it. */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const MODS = args.mods || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const PX = parseFloat(args.px || '474.5');
const PY = parseFloat(args.py || '207.5');

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=${args.cam || 'yard'}` +
  `&time=${args.time || 16}&weather=clear&hud=0`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3500);

const out = await page.evaluate(async ({PX, PY}) => {
  const THREE = await import('/static/world/three.module.js').catch(() => null)
             || await import('three').catch(() => null);
  const w = window.__lemWorld;
  const cam = w.camera || w.ctx?.camera;
  const scene = w.scene || w.ctx?.scene;
  const ndc = new THREE.Vector2((PX / 1280) * 2 - 1, -((PY / 720) * 2 - 1));

  const rc = new THREE.Raycaster();
  rc.params.Points.threshold = 1;
  rc.setFromCamera(ndc, cam);
  const hits = rc.intersectObject(scene, true).slice(0, 12).map(h => ({
    d: +h.distance.toFixed(1),
    name: h.object.name || '(unnamed)',
    type: h.object.type,
    mat: h.object.material?.type + ':' + (h.object.material?.name || ''),
    map: !!h.object.material?.map,
    colour: h.object.material?.color ? '#' + h.object.material.color.getHexString() : null,
    visible: h.object.visible,
    instanceId: h.instanceId,
    chain: (() => { const c = []; let o = h.object; while (o) { c.push(o.name || o.type); o = o.parent; } return c.join(' < '); })(),
    point: [+h.point.x.toFixed(1), +h.point.y.toFixed(1), +h.point.z.toFixed(1)],
  }));

  /* Anything whose world-space centre projects within 60px of the quad, so an
   * object the raycaster cannot hit (shader material, no index, sprite) is
   * still named. */
  const near = [];
  const v = new THREE.Vector3();
  scene.traverse(o => {
    if (!o.isMesh && !o.isPoints && !o.isSprite && !o.isLine) return;
    o.getWorldPosition(v);
    const p = v.clone().project(cam);
    if (p.z > 1) return;
    const sx = (p.x * 0.5 + 0.5) * 1280, sy = (-p.y * 0.5 + 0.5) * 720;
    const dist = Math.hypot(sx - PX, sy - PY);
    if (dist < 90) near.push({
      name: o.name || '(unnamed)', type: o.type, dist: +dist.toFixed(0),
      sx: +sx.toFixed(0), sy: +sy.toFixed(0),
      pos: [+v.x.toFixed(1), +v.y.toFixed(1), +v.z.toFixed(1)],
      visible: o.visible, count: o.count ?? null,
      mat: o.material?.type + ':' + (o.material?.name || ''),
      colour: o.material?.color ? '#' + o.material.color.getHexString() : null,
      map: !!o.material?.map, parent: o.parent?.name || o.parent?.type,
    });
  });
  near.sort((a, b) => a.dist - b.dist);

  return {mods: [...w.subsystems.keys()], hits, near: near.slice(0, 40),
          camPos: [cam.position.x, cam.position.y, cam.position.z].map(n => +n.toFixed(1))};
}, {PX, PY});

console.log(JSON.stringify(out, null, 2));
await browser.close();
