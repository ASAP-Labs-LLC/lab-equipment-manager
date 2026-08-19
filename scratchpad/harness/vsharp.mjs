/* vsharp.mjs — sweep the far tiers' alpha window in ONE page session and shoot
 * each setting, so the terrain (which other agents rewrite hourly) is identical
 * across the comparison. Also dumps the fog factor at the crop's own depth and
 * a fog-off frame, so colour and coverage can be told apart.
 *
 *   node vsharp.mjs <url> <outprefix>
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
let URL = process.argv[2];
if (URL.includes("solo.html") && !/[?&]quality=/.test(URL)) URL += "&quality=ultra";
const OUT = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/SH';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = []; p.on('pageerror', e => errs.push(String(e).slice(0,240)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0,200)); });
await p.goto(URL, {waitUntil:'load', timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
await p.waitForTimeout(10000);

const shot = async n => { await p.waitForTimeout(1500);
  fs.writeFileSync(`${OUT}-${n}.png`, await p.screenshot()); console.log('shot', n); };
const setSharp = (a, c) => p.evaluate(([lo, hi]) => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const out = [];
  for (const m of v.materials || []) {
    const u = m.userData?.lem?.uVegSharp;
    if (u) { u.value.set(lo, hi); out.push(u.value.toArray()); }
  }
  return out.length;
}, [a, c]);

console.log('materials with uVegSharp:', await setSharp(1.2, 3.0));

for (const [lo, hi, name] of [[99, 100, 'off'], [1.2, 3.0, 'a12-30'],
                              [0.8, 2.2, 'a08-22'], [0.4, 1.6, 'a04-16']]) {
  await setSharp(lo, hi);
  await shot(name);
}

/* and the same frame with the haze taken out, to separate colour from coverage */
await setSharp(1.2, 3.0);
await p.evaluate(() => {
  const f = window.__lemWorld.scene.fog;
  window.__d = f.density;
  Object.defineProperty(f, 'density', {get: () => 0, set: () => {}, configurable: true});
});
await shot('a12-30-nofog');
await p.evaluate(() => { const f = window.__lemWorld.scene.fog;
  delete f.density; f.density = window.__d; });
await p.evaluate(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of v.materials || []) m.userData?.lem?.uVegSharp?.value.set(99, 100); });
await p.evaluate(() => {
  const f = window.__lemWorld.scene.fog;
  Object.defineProperty(f, 'density', {get: () => 0, set: () => {}, configurable: true});
});
await shot('off-nofog');
await p.evaluate(() => { const f = window.__lemWorld.scene.fog;
  delete f.density; f.density = window.__d; });

/* vegetation hidden, haze on — the background the canopy is measured against */
await p.evaluate(() => { window.__lemWorld.subsystems.get('vegetation').group.visible = false; });
await shot('noveg');
await p.evaluate(() => { window.__lemWorld.subsystems.get('vegetation').group.visible = true; });

console.log('errors', JSON.stringify(errs.slice(0,6)));
await b.close();
