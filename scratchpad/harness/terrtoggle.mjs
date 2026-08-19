/* terrtoggle.mjs — same camera, water on and water off, so a dark strip in the
 * mid distance can be attributed to the river or to the ground under it. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  const k = process.argv[i];
  if (!k.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k.slice(2)] = true; else { a[k.slice(2)] = n; i++; }
}
const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
await page.goto(a.url + '&hud=0', {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__lemWorld?.subsystems?.size > 0, null, {timeout: 60000});
await page.waitForTimeout(4000);
await page.locator('#world').screenshot({path: a.out + '-on.png'});
const info = await page.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  t.water.visible = false;
  return {waterY: t.waterY, waterLevel: t.waterLevel, cx: t.cx, cz: t.cz,
          valleyX: t.valleyX, cam: window.__lemWorld.camera.position.toArray()};
});
await page.waitForTimeout(1200);
await page.locator('#world').screenshot({path: a.out + '-off.png'});
console.log(JSON.stringify(info));
await browser.close();
