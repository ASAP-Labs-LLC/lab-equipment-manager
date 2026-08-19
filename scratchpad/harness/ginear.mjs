/* ginear.mjs — would putting the terrain into three's own (near) shadow map
 * show anything? Runtime experiment, before any of it goes in the file. */
import {chromium} from 'playwright';
const url = process.argv[2];
const pre = process.argv[3];
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
page.on('pageerror', e => errs.push('pageerror ' + String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);
await page.screenshot({path: pre + '-off.png'});
const info = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const hit = [];
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh)) return;
    if (!o.userData.lemLandform) return;
    o.castShadow = true;
    o.customDepthMaterial = gi._depthLand;
    hit.push(o.name || o.type);
  });
  w.engine.shadowNeedsUpdate = true;
  return hit;
});
await page.waitForTimeout(2500);
await page.screenshot({path: pre + '-on.png'});
const cost = await page.evaluate(() => ({draws: window.__lemWorld.engine.drawCalls,
                                        tris: window.__lemWorld.engine.triangles}));
console.log(JSON.stringify({info, cost, errs: errs.slice(0, 3)}));
await browser.close();
