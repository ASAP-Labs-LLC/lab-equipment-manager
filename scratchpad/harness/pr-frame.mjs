/* pr-frame.mjs — screenshot the props AND say where they are in the frame, so
 * a crop is aimed rather than hunted for. Projects every prop through the live
 * camera and prints pixel coordinates and the bounding box of the whole set.
 *
 *   node pr-frame.mjs --cam far --out /tmp/x.png [--w 2560] [--h 1440] [--time 9]
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) { args[a.slice(2)] = process.argv[i + 1]; i++; }
}
const W = parseInt(args.w || '2560', 10), H = parseInt(args.h || '1440', 10);
const CAM = args.cam || 'far', TIME = args.time || '9';
const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await (await b.newContext({viewport: {width: W, height: H}})).newPage();
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text()))
  errs.push(m.text().slice(0, 160)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=' + MODS +
  '&cam=' + CAM + '&time=' + TIME + '&weather=clear&hud=0&quality=' + (args.quality || 'ultra'),
  {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(parseInt(args.seconds || '8', 10) * 1000);

const info = await p.evaluate(([W, H]) => {
  const w = window.__lemWorld;
  const pr = w.subsystems.get('props');
  const cam = w.camera?.isCamera ? w.camera : (w.cam || w._camera ||
    (w.subsystems.get('camera')?.camera));
  /* no global THREE on the page: borrow the Vector3 constructor off any
   * object that already has one */
  const V3 = pr.group.position.constructor;
  const proj = (x, y, z) => {
    const v = new V3(x, y, z).project(cam);
    return [Math.round((v.x * 0.5 + 0.5) * W), Math.round((-v.y * 0.5 + 0.5) * H),
            +v.z.toFixed(3)];
  };
  const marks = [];
  for (const s of pr.umbrellaSites || []) marks.push(['umb', proj(s.x, s.y + 2, s.z)]);
  if (pr.pier) {
    marks.push(['pierRoot', proj(pr.pier.x, pr.pier.deckY, pr.pier.z)]);
    marks.push(['pierHead', proj(pr.pier.x + pr.pier.dir.x * pr.pier.length,
      pr.pier.deckY, pr.pier.z + pr.pier.dir.z * pr.pier.length)]);
  }
  for (const s of pr.boatSites || []) marks.push(['boat', proj(s.x, pr.waterY, s.z)]);
  const pts = pr._path?.pts || [];
  for (let i = 0; i < pts.length; i += Math.max(1, Math.floor(pts.length / 5))) {
    marks.push(['path' + i, proj(pts[i].x, pts[i].y, pts[i].z)]);
  }
  const xs = marks.map(m => m[1][0]), ys = marks.map(m => m[1][1]);
  return {
    marks, cam: {fov: cam?.fov, pos: cam ? [+cam.position.x.toFixed(0),
      +cam.position.y.toFixed(0), +cam.position.z.toFixed(0)] : null},
    bbox: xs.length ? {x0: Math.min(...xs), x1: Math.max(...xs),
                       y0: Math.min(...ys), y1: Math.max(...ys)} : null,
    shade: pr.shade, path: pr.path, pier: pr.pier,
    warnings: pr.propWarnings, regionWarnings: pr.regions?.warnings,
    counts: {umb: (pr.umbrellaSites || []).length, boats: (pr.boatSites || []).length},
  };
}, [W, H]);
fs.writeFileSync(args.out || 'pr-frame.png', await p.screenshot());
console.log(JSON.stringify({...info, errors: errs}, null, 1));
await b.close();
