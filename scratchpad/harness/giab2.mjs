/* giab2.mjs — how much of the frame does each shadow source actually own?
 *
 *   node giab2.mjs "<url>" <prefix>
 * writes <prefix>-on.png, <prefix>-nonear.png, <prefix>-nofar.png
 */
import {chromium} from 'playwright';

const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';
const pre = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/giab2';

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);
await page.screenshot({path: pre + '-on.png'});

await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.sun.castShadow = false;
  gi._fitShadow = () => {};
  window.__lemWorld.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(2000);
await page.screenshot({path: pre + '-nonear.png'});

await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.sun.castShadow = true;
  gi.uniforms.lemCsmReady0.value = 0;
  gi.uniforms.lemCsmReady1.value = 0;
  const s = gi._serviceCascades.bind(gi);
  gi._serviceCascades = dt => { s(dt); gi.uniforms.lemCsmReady0.value = 0;
                                gi.uniforms.lemCsmReady1.value = 0; };
  window.__lemWorld.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(2000);
await page.screenshot({path: pre + '-nofar.png'});
await browser.close();
console.log('ok');
