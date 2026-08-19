/* gicost.mjs — draws and triangles sampled over a working railway, not once.
 *
 * A single end-of-run snapshot of `renderer.info` swings by a factor of two on
 * this scene depending on how many consists happen to be out and whether the
 * vegetation LOD has settled, which makes any A/B on the shadow pass unreadable.
 * This keeps the parses coming and samples every 100 ms, then reports the mean
 * and the worst frame — the worst is the one the budget is about.
 */
import {chromium} from 'playwright';

const cam = process.argv[2] || 'yard';
const secs = parseFloat(process.argv[3] || '12');
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}` +
            `&time=16&weather=clear&hud=0`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(4000);
await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__p = setInterval(() => w.parse(uids[i++ % uids.length], 'L-COST'), 900);
  window.__samples = [];
  window.__s = setInterval(() => {
    const s = w.stats();
    window.__samples.push([s.drawCalls, s.triangles, s.fps]);
  }, 100);
});
await page.waitForTimeout(secs * 1000);
const r = await page.evaluate(() => {
  clearInterval(window.__p); clearInterval(window.__s);
  const s = window.__samples;
  const col = i => s.map(v => v[i]);
  const mean = a => a.reduce((x, y) => x + y, 0) / a.length;
  return {n: s.length,
          drawsMean: Math.round(mean(col(0))), drawsMax: Math.max(...col(0)),
          trisMean: Math.round(mean(col(1))), trisMax: Math.max(...col(1)),
          fpsMean: Math.round(mean(col(2))), fpsMin: Math.min(...col(2))};
});
console.log(cam, JSON.stringify(r), 'errors', errors.length);
await browser.close();
