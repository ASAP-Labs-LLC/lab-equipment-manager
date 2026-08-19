/* terrprobe2.mjs — raycast a handful of screen pixels into the terrain and
 * print the splat weights, the aux channels and the baked sky visibility at the
 * vertex that shaded them.
 *
 * This exists because three rounds of critics have described a dark band in the
 * middle distance as a hole in the ground, and guessing at which term in a
 * seven-layer material produced it is how you spend a round fixing the wrong
 * thing. It answers "is that water, canopy, mud, occlusion, or shading?" in one
 * run. (It was shading. See scratchpad/REQUESTS.md.)
 *
 *   node terrprobe2.mjs "http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&cam=yard&time=13"
 */
import {chromium} from 'playwright';

const url = process.argv[2];
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
await page.goto(url + '&hud=0', {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__lemWorld?.subsystems?.size > 0, null, {timeout: 60000});
await page.waitForTimeout(3500);
const r = await page.evaluate(async () => {
  const THREE = await import('three');
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const rc = new THREE.Raycaster();
  const core = t.meshes.find(m => m.name === 'terrain-core');
  const g = core.geometry;
  const A = g.getAttribute('splatA'), B = g.getAttribute('splatB'),
        X = g.getAttribute('aux'), S = g.getAttribute('aSky');
  const out = [];
  for (const [px, py] of [[420, 380], [900, 345], [300, 392], [600, 500], [1400, 420]]) {
    rc.setFromCamera(new THREE.Vector2(px / 1920 * 2 - 1, -(py / 1080 * 2 - 1)), w.camera);
    const h = rc.intersectObjects(t.meshes, false)[0];
    if (!h) { out.push({px, py, miss: 1}); continue; }
    const i = h.face.a, f = n => Math.round(n * 100) / 100;
    out.push({px, py, name: h.object.name, d: Math.round(h.distance),
      aboveWater: Math.round(h.point.y - t.waterY),
      grass: f(A.getX(i)), forest: f(A.getY(i)), dirt: f(A.getZ(i)), gravel: f(A.getW(i)),
      asph: f(B.getX(i)), mud: f(B.getY(i)), dry: f(B.getZ(i)), rock: f(B.getW(i)),
      puddle: f(X.getX(i)), canopy: f(X.getY(i)), onsite: f(X.getZ(i)), shore: f(X.getW(i)),
      sky: f(S.getX(i))});
  }
  return {waterLevel: t.waterLevel, waterY: t.waterY, out};
});
console.log(JSON.stringify(r, null, 1));
await browser.close();
