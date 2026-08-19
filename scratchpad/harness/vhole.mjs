/* vhole.mjs — what actually punches the holes in the far treeline. Each
 * candidate is removed on its own, in one page session, and measured with
 * stipple.py on the same crop. */
import {chromium} from 'playwright';
import fs from 'node:fs';
let URL = process.argv[2];
if (!/[?&]quality=/.test(URL)) URL += '&quality=ultra';
const OUT = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/HL';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = []; p.on('pageerror', e => errs.push(String(e).slice(0,200)));
await p.goto(URL, {waitUntil:'load', timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
await p.waitForTimeout(9000);
const shot = async n => { await p.waitForTimeout(1500);
  fs.writeFileSync(`${OUT}-${n}.png`, await p.screenshot()); console.log('shot', n); };
const ev = (fn, a) => p.evaluate(fn, a);

/* what is painted, before anything renders it */
console.log('painted coverage', JSON.stringify(await ev(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const read = (tex, name) => {
    const cv = tex?.image; if (!cv || !cv.getContext) return [name, null];
    const g = cv.getContext('2d', {willReadFrequently: true});
    const d = g.getImageData(0, 0, cv.width, cv.height).data;
    let n = 0, hi = 0, mid = 0;
    for (let i = 3; i < d.length; i += 4) {
      n++; if (d[i] > 127) hi++; if (d[i] > 20 && d[i] < 235) mid++; }
    return [name, {cover: +(hi / n).toFixed(3), partial: +(mid / n).toFixed(3)}];
  };
  return Object.fromEntries([read(v.atlas, 'atlas'), read(v.canopy, 'canopy')]);
})));

await shot('base');

/* 1. the grove dissolve off: every drawn clump at full opacity */
await ev(() => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  window.__pin = () => { for (const gv of v.groves || []) {
    const a = gv.mesh.geometry.getAttribute('aVegAlpha');
    if (a) { a.array.fill(1); a.needsUpdate = true; } } };
  window.__pinT = setInterval(window.__pin, 60); window.__pin();
});
await shot('nofade');
await ev(() => { clearInterval(window.__pinT); });

/* 2. the two far tiers separately */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of v.meshes) if (m.material === v.matGrove) m.visible = false; });
await shot('nogrove');
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of v.meshes) if (m.material === v.matGrove) m.visible = true;
  for (const e of v.trees) if (e.far) e.far.visible = false; });
await shot('nofarcard');
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const e of v.trees) if (e.far) e.far.visible = true; });

/* 3. the coverage curve out, i.e. this round's change reverted live */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of v.materials) { const u = m.userData?.lem?.uVegCover;
    if (u) { u.__old = u.value.slice(); u.value = [1,1,1,1,1,1,1,1]; } } });
await shot('nocurve');
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of v.materials) { const u = m.userData?.lem?.uVegCover;
    if (u && u.__old) u.value = u.__old; } });

/* 4. the cutout made impossible, for the ceiling */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of [v.matFar, v.matGrove]) if (m) m.userData.lem.uVegAlphaBias.value = 4.0; });
await shot('solid');
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  v.matFar.userData.lem.uVegAlphaBias.value = 0.34;
  if (v.matGrove) v.matGrove.userData.lem.uVegAlphaBias.value = -0.10; });

/* 5. nofade AND solid grove bias positive */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  window.__pinT = setInterval(window.__pin, 60); window.__pin();
  if (v.matGrove) v.matGrove.userData.lem.uVegAlphaBias.value = 0.30; });
await shot('nofade-gb30');
await ev(() => { clearInterval(window.__pinT); });

await ev(() => { window.__lemWorld.subsystems.get('vegetation').group.visible = false; });
await shot('noveg');

console.log('errors', JSON.stringify(errs.slice(0,4)));
await b.close();
