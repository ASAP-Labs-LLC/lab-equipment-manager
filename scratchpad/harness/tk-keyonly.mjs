/* tk-keyonly.mjs — kill the fill (probe GI + env) and photograph what the SUN
 * alone is doing. Anything not in sun goes near-black, so a cast shadow is
 * either there or it is not, with nothing to argue about.
 *   node tk-keyonly.mjs [--cam wide] [--time 9] [--stem /tmp/tk/key]
 */
import {chromium} from 'playwright';
import fs from 'fs';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'wide', time = a.time || '9', stem = a.stem || '/tmp/tk/key';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
          + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=ultra`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1600, height: 900}});
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(9000);
await page.evaluate(() => { const w = window.__lemWorld;
  if (w.rig) { w.rig.idleDrift = false; w.rig.apply(1); } w.camera.updateMatrixWorld(true); });
await page.screenshot({path: `${stem}-full.png`});
await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  gi.uniforms.lemGIStrength.value = 0;
  w.scene.environment = null;
  gi.sun.intensity = gi.sun.intensity * 2.2;
});
await page.waitForTimeout(2500);
await page.screenshot({path: `${stem}-key.png`});
console.log('wrote', `${stem}-key.png`);
await b.close();
