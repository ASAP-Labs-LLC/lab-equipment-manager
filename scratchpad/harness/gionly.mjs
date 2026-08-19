/* gionly.mjs — the coarse cascades ALONE: three's own map off, near box zeroed.
 * Whatever shadow survives in this frame is what the manual cascades produce. */
import {chromium} from 'playwright';
const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';
const out = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/gi-only-coarse.png';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);
await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  gi.sun.castShadow = false;
  const f = gi._fitShadow.bind(gi);
  gi._fitShadow = (...a) => { f(...a); gi.uniforms.lemNearRadius.value = 1e-6;
                              gi.sun.castShadow = false; };
  gi.uniforms.lemNearRadius.value = 1e-6;
  w.engine.shadowNeedsUpdate = true;
});
await page.waitForTimeout(3000);
await page.screenshot({path: out});
console.log('ok');
await browser.close();
