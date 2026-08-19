/* gifloor.mjs — sweep lemBounceFloor on one page load and report the histogram
 * numbers the reference sets are compared on, plus two fixed patches. */
import {chromium} from 'playwright';
const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const vals = (process.argv[3] || '0,0.3,0.55,0.8').split(',').map(Number);
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); });
page.on('pageerror', e => errs.push('pageerror ' + String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);
for (const v of vals) {
  await page.evaluate(x => {
    window.__lemWorld.subsystems.get('gi').uniforms.lemBounceFloor.value = x;
  }, v);
  await page.waitForTimeout(1800);
  await page.screenshot({path: `${dir}gifloor-${v}.png`});
}
console.log(errs.length ? 'ERRORS ' + JSON.stringify(errs.slice(0, 5)) : 'clean');
await browser.close();
