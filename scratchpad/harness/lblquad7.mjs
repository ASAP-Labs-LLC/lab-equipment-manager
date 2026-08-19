/* lblquad7.mjs — the material renders (emissive proved it) but gets no light.
 * Swap in materials that bypass one suspect at a time: basic (no lighting at
 * all), a fresh standard material gi.js never patched, and a standard material
 * with the normals flipped. */
import {chromium} from 'playwright';

const url = process.env.URL || `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=16&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720}, deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3500);

const read = async (label) => {
  const buf = await page.screenshot({clip: {x: 462, y: 196, width: 24, height: 20}});
  const avg = await page.evaluate(async (b64) => {
    const img = new Image();
    await new Promise(r => { img.onload = r; img.src = 'data:image/png;base64,' + b64; });
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let r = 0, gg = 0, b = 0, n = 0;
    for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; n++; }
    return [Math.round(r / n), Math.round(gg / n), Math.round(b / n)];
  }, buf.toString('base64'));
  console.log(label.padEnd(38), avg.join(','));
};

const run = (src) => page.evaluate(async (s) => {
  const THREE = await import('three');
  const scene = window.__lemWorld.scene || window.__lemWorld.ctx.scene;
  let mesh = null; scene.traverse(o => { if (o.name === 'multitek-s:rust') mesh = o; });
  window.__M = window.__M || mesh.material;
  eval(`(${s})`)(mesh, THREE, window.__lemWorld);
}, src.toString());

await read('as-is');

await run((m, T) => { m.material = new T.MeshBasicMaterial({map: window.__M.map}); });
await page.waitForTimeout(800); await read('MeshBasicMaterial + same map');

await run((m, T) => { m.material = new T.MeshStandardMaterial({map: window.__M.map, roughness: 0.8, metalness: 0}); });
await page.waitForTimeout(900); await read('fresh Standard (gi never patched)');

await run((m, T) => { m.material = new T.MeshNormalMaterial(); });
await page.waitForTimeout(800); await read('MeshNormalMaterial (normals)');

await run((m, T) => { m.material = window.__M; });
await page.waitForTimeout(800); await read('original restored');

/* Does anything sit in front of it that only writes darkness? */
const stack = await page.evaluate(async () => {
  const THREE = await import('three');
  const w = window.__lemWorld, cam = w.camera || w.ctx.camera, scene = w.scene || w.ctx.scene;
  const rc = new THREE.Raycaster();
  rc.setFromCamera(new THREE.Vector2((474 / 1280) * 2 - 1, -((206 / 720) * 2 - 1)), cam);
  return rc.intersectObject(scene, true).slice(0, 6).map(h => ({
    d: +h.distance.toFixed(2), n: h.object.name || h.object.type,
    parent: h.object.parent?.name || h.object.parent?.type,
    mat: h.object.material?.type, vis: h.object.visible,
    depthWrite: h.object.material?.depthWrite, transparent: h.object.material?.transparent,
    blending: h.object.material?.blending, opacity: h.object.material?.opacity,
    renderOrder: h.object.renderOrder,
  }));
});
console.log('STACK', JSON.stringify(stack, null, 1));

await browser.close();
