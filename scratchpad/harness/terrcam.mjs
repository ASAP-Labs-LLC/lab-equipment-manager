/* terrcam.mjs — shot.mjs with an explicit camera target, so the river can be
 * looked at. solo.html's CAMS presets all frame the yard.
 *
 *   node terrcam.mjs --url "...solo.html?mods=sky,gi,terrain&time=13" \
 *     --out ../shots/x.png --tx 430 --tz 190 --yaw 1.2 --pitch 0.10 --dist 120
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  const k = process.argv[i];
  if (!k.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k.slice(2)] = true; else { a[k.slice(2)] = n; i++; }
}
const out = path.resolve(a.out || 'shot.png');
fs.mkdirSync(path.dirname(out), {recursive: true});

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080},
                                    deviceScaleFactor: 1});
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });
await page.goto(a.url + (a.url.includes('?') ? '&' : '?') + 'hud=0',
                {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__lemWorld?.subsystems?.size > 0, null,
                           {timeout: 60000});
await page.evaluate(([tx, ty, tz, yaw, pitch, dist]) => {
  const w = window.__lemWorld;
  const t = w.subsystems.get('terrain');
  const gy = t?.heightAt ? t.heightAt(tx, tz) : 0;
  Object.assign(w.rig, {goalYaw: yaw, goalPitch: pitch, goalDistance: dist});
  w.rig.goalTarget.set(tx, gy + ty, tz);
  w.rig.apply(1);
  w.rig.idleDrift = false;
}, [parseFloat(a.tx || 0), parseFloat(a.ty || 3), parseFloat(a.tz || 0),
    parseFloat(a.yaw || 1.2), parseFloat(a.pitch || 0.12), parseFloat(a.dist || 120)]);
await page.waitForTimeout(parseFloat(a.seconds || 4) * 1000);
await page.locator('#world').screenshot({path: out});
const stats = await page.evaluate(() => window.__lemWorld.stats());
console.log(JSON.stringify({out, stats, errors}, null, 1));
await browser.close();
