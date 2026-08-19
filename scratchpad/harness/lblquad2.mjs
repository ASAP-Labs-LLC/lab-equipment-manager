/* lblquad2.mjs — raycast a grid across the black rectangle and just outside it,
 * and report the first real hit (skipping the sky dome) with uv and material. */
import {chromium} from 'playwright';

const MODS = process.env.MODS || 'sky,gi,terrain,buildings,rail,trains,vegetation,weather';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=yard&time=16&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720}, deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3500);

const out = await page.evaluate(async () => {
  const THREE = await import('three');
  const w = window.__lemWorld;
  const cam = w.camera || w.ctx?.camera, scene = w.scene || w.ctx?.scene;
  const rc = new THREE.Raycaster();
  const probe = (px, py) => {
    rc.setFromCamera(new THREE.Vector2((px / 1280) * 2 - 1, -((py / 720) * 2 - 1)), cam);
    const hs = rc.intersectObject(scene, true);
    for (const h of hs) {
      if (h.distance < 5) continue;              // the sky dome rides the camera
      const o = h.object;
      const chain = (() => { const c = []; let n = o; while (n) { c.push(n.name || n.type); n = n.parent; } return c.join(' < '); })();
      return {px, py, d: +h.distance.toFixed(1), chain,
              mat: o.material?.name || o.material?.type,
              matUuid: o.material?.uuid?.slice(0, 8),
              map: o.material?.map ? (o.material.map.name || 'map') : null,
              mapSize: o.material?.map?.image ? `${o.material.map.image.width}x${o.material.map.image.height}` : null,
              colour: o.material?.color ? '#' + o.material.color.getHexString() : null,
              uv: h.uv ? [+h.uv.x.toFixed(3), +h.uv.y.toFixed(3)] : null,
              face: h.faceIndex, inst: h.instanceId,
              pt: [+h.point.x.toFixed(1), +h.point.y.toFixed(1), +h.point.z.toFixed(1)]};
    }
    return {px, py, miss: true};
  };
  const rows = [];
  for (const py of [180, 195, 205, 215, 222, 235]) {
    for (const px of [440, 460, 470, 480, 490, 505]) rows.push(probe(px, py));
  }
  return rows;
});

for (const r of out) console.log(JSON.stringify(r));
await browser.close();
