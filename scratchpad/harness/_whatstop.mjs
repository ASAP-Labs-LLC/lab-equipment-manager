import {chromium} from 'playwright';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist','--use-angle=metal','--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
await page.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&hud=0&cam=wide&time=16&quality=ultra', {waitUntil:'load', timeout:90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout:90000});
await page.waitForTimeout(1500);
const o = await page.evaluate(() => {
  const w = window.__lemWorld; const t = w.subsystems.get('terrain');
  const c = w.camera; c.updateMatrixWorld(true);
  const out = {};
  /* Where is the horizontal, in frame fractions? Unproject a ray straight out
     from the camera at zero depression and see where it lands. */
  const e = c.matrixWorld.elements;
  const fwd = {x:-e[8], y:-e[9], z:-e[10]};
  out.centreDepressionDeg = +(Math.asin(-fwd.y)*180/Math.PI).toFixed(2);
  out.fov = c.fov; out.aspect = +c.aspect.toFixed(3);
  out.camY = Math.round(c.position.y); out.waterY = +t.waterY.toFixed(1);
  /* project a point on the horizontal plane through the camera, very far away */
  const dir = {x: fwd.x, y: 0, z: fwd.z};
  const L = Math.hypot(dir.x, dir.z);
  const p = {x: c.position.x + dir.x/L*50000, y: c.position.y, z: c.position.z + dir.z/L*50000};
  const v = new (Object.getPrototypeOf(c.position).constructor)(p.x, p.y, p.z);
  v.project(c);
  out.horizonNdcY = +v.y.toFixed(4);
  out.horizonFracFromTop = +((1 - v.y)/2).toFixed(4);
  out.meshes = t.meshes.map(m => ({name: m.name, visible: m.visible,
    order: m.renderOrder, dt: m.material.depthTest, dw: m.material.depthWrite,
    side: m.material.side, transparent: m.material.transparent}));
  return out;
});
console.log(JSON.stringify(o, null, 1));
await browser.close();
