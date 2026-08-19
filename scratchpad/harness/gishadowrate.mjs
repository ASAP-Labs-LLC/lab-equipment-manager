/* gishadowrate.mjs — how often is three's own shadow map actually redrawn, and
 * what does redrawing it every frame cost?
 *
 * engine.js sets `renderer.shadowMap.autoUpdate = false` and redraws only when
 * `engine.shadowNeedsUpdate` is raised. gi.js raises it from `_fitShadow` (which
 * early-returns unless the camera moved) and `_nearCull`. Nothing raises it
 * because a train moved. This counts the raises over a fixed window with a
 * stationary camera, then forces one per frame and reads the cost back.
 */
import {chromium} from 'playwright';

const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=yard&time=16&weather=clear&hud=0';

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(4000);

await page.evaluate(() => {
  const e = window.__lemWorld.engine;
  let v = e.shadowNeedsUpdate;
  window.__raises = 0; window.__frames0 = e.frame | 0;
  Object.defineProperty(e, 'shadowNeedsUpdate', {
    get() { return v; },
    set(x) { if (x && !v) window.__raises++; v = x; },
    configurable: true,
  });
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__p = setInterval(() => w.parse(uids[i++ % uids.length], 'L-RATE'), 1100);
});
await page.waitForTimeout(8000);
console.log('idle window:', await page.evaluate(() => {
  const e = window.__lemWorld.engine;
  return {raises: window.__raises, frames: (e.frame | 0) - window.__frames0,
          stats: window.__lemWorld.stats()};
}));

/* Now force it every frame and read the cost. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  const gi = w.subsystems.get('gi');
  const u = gi.update.bind(gi);
  gi.update = (dt, t) => { u(dt, t); w.engine.shadowNeedsUpdate = true; };
});
await page.waitForTimeout(6000);
console.log('forced every frame:',
  await page.evaluate(() => window.__lemWorld.stats()));
await page.screenshot({path: '/Users/rynatical/LAB-lem/scratchpad/shots/gishadowrate-forced.png'});
await page.evaluate(() => clearInterval(window.__p));
await browser.close();
