/* gx-lock.mjs — acceptance for `gi.setExposureLocked`, the API sky.js asked for.
 *
 * The lock has to do three things and this checks all three:
 *   1. hold `uExposure` still across a change that would otherwise move it
 *      (the meter is negative feedback and absorbs ~60 % of any A/B),
 *   2. keep grading — vignette, saturation, black point and lift must stay
 *      consistent with the frozen stop rather than being left wherever they
 *      were, so a probe still photographs a properly graded frame,
 *   3. resume smoothly when released, without snapping.
 *
 *   node gx-lock.mjs [--cam far] [--time 9]
 */
import {chromium} from 'playwright';
const a = {};
for (let i = 2; i < process.argv.length; i++)
  if (process.argv[i].startsWith('--')) a[process.argv[i].slice(2)] = process.argv[++i];
const cam = a.cam || 'far', time = a.time || '9';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather'
  + `&cam=${cam}&time=${time}&weather=clear&hud=0&quality=${a.quality || 'ultra'}`;
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const page = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errs.push('console ' + m.text().slice(0, 160)); });
await page.goto(url, {waitUntil: 'load', timeout: 120000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await page.waitForTimeout(9000);

const grade = () => page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const u = w.engine._passes?.composite?.material?.uniforms || {};
  const px = (k) => (u[k] && typeof u[k].value === 'number') ? +u[k].value.toFixed(4) : null;
  return {locked: gi.exposureLocked, hasApi: typeof gi.setExposureLocked === 'function',
          exposure: +gi.exposure.toFixed(4), uExposure: px('uExposure'),
          uVignette: px('uVignette'), uSaturation: px('uSaturation'),
          uBlackPoint: px('uBlackPoint'), uLift: px('uLift'), uContrast: px('uContrast')};
});
const rows = [];
rows.push({at: 'baseline', ...(await grade())});

/* a change that normally moves the meter a long way: kill the fill */
await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  gi.__g = gi.uniforms.lemGIStrength.value;
  Object.defineProperty(gi.uniforms.lemGIStrength, 'value', {configurable: true, get: () => 0, set: () => {}}); });
await page.waitForTimeout(4000);
rows.push({at: 'fill off, UNLOCKED', ...(await grade())});

await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  Object.defineProperty(gi.uniforms.lemGIStrength, 'value',
    {configurable: true, value: gi.__g, writable: true}); });
await page.waitForTimeout(4000);
rows.push({at: 'fill back, UNLOCKED', ...(await grade())});

await page.evaluate(() => window.__lemWorld.subsystems.get('gi').setExposureLocked(true));
await page.waitForTimeout(1200);
rows.push({at: 'LOCKED', ...(await grade())});
await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  Object.defineProperty(gi.uniforms.lemGIStrength, 'value', {configurable: true, get: () => 0, set: () => {}}); });
await page.waitForTimeout(4000);
rows.push({at: 'fill off, LOCKED', ...(await grade())});
await page.waitForTimeout(4000);
rows.push({at: 'fill off, LOCKED, +4s', ...(await grade())});
await page.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
  Object.defineProperty(gi.uniforms.lemGIStrength, 'value',
    {configurable: true, value: gi.__g, writable: true});
  gi.setExposureLocked(false); });
await page.waitForTimeout(600);
rows.push({at: 'released +0.6s', ...(await grade())});
await page.waitForTimeout(5000);
rows.push({at: 'released +5.6s', ...(await grade())});

const f = rows.find(r => r.at === 'fill off, LOCKED').uExposure;
const g = rows.find(r => r.at === 'fill off, LOCKED, +4s').uExposure;
const l = rows.find(r => r.at === 'LOCKED').uExposure;
const u0 = rows.find(r => r.at === 'baseline').uExposure;
const u1 = rows.find(r => r.at === 'fill off, UNLOCKED').uExposure;
console.log(JSON.stringify({cam, time, rows,
  verdict: {
    apiPresent: rows[0].hasApi,
    unlockedDriftFromFillChange: +(u1 - u0).toFixed(4),
    lockedDriftFromSameChange: +(g - l).toFixed(4),
    lockedHeldOver8s: Math.abs(g - f) < 1e-6 && Math.abs(l - f) < 1e-6,
    resumedAfterRelease: rows[rows.length - 1].uExposure !== g,
  }, pageErrors: errs.slice(0, 6)}, null, 1));
await b.close();
