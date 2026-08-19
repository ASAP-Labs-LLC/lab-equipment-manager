/* giland.mjs — A/B the landform casters in the coarse cascades on one load. */
import {chromium} from 'playwright';
const url = process.argv[2];
const pre = process.argv[3];
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); });
page.on('pageerror', e => errs.push('pageerror ' + String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);
await page.screenshot({path: pre + '-with.png'});
const info = await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  const names = [];
  for (const c of gi._csm) {
    const keep = [];
    for (const o of c.casters) {
      if (o.userData.lemLandform) { o.layers.disable(c.layer); names.push([c.i, o.name]); }
      else keep.push(o);
    }
    c.casters = keep; c.dirty = true; c.ready = false;
  }
  gi._enrol = () => {};
  return {removed: names, tris: gi._csm.map(c => c.tris), cost: gi._csm.map(c => c.cost)};
});
await page.waitForTimeout(3000);
await page.screenshot({path: pre + '-without.png'});
const after = await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  return {tris: gi._csm.map(c => c.tris), cost: gi._csm.map(c => c.cost)};
});
console.log(JSON.stringify({info, after, errs: errs.slice(0, 4)}));
await browser.close();
