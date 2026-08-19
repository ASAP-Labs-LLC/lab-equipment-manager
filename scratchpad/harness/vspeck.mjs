/* vspeck.mjs — is the distant speckle the alpha cutout, or the atlas aliasing
 * under minification? Both are "only at distance" and they look identical at
 * 1:1, so they are told apart by making each impossible in turn, in one page
 * session on one frame. */
import {chromium} from 'playwright';
import fs from 'node:fs';
let URL = process.argv[2];
if (URL.includes('solo.html') && !/[?&]quality=/.test(URL)) URL += '&quality=ultra';
const OUT = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/SP';
const b = await chromium.launch({headless:true, channel:'chromium',
  args:['--use-angle=metal','--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1920,height:1080}});
const errs = []; p.on('pageerror', e => errs.push(String(e).slice(0,240)));
await p.goto(URL, {waitUntil:'load', timeout:60000});
await p.waitForFunction(()=>window.__worldReady===true, null, {timeout:60000});
await p.waitForTimeout(10000);
const shot = async n => { await p.waitForTimeout(1500);
  fs.writeFileSync(`${OUT}-${n}.png`, await p.screenshot()); console.log('shot', n); };
const ev = (fn, a) => p.evaluate(fn, a);

await shot('base');

/* 1. the cutout made impossible: bias the threshold below zero on the far tiers */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of [v.matFar, v.matGrove]) if (m) m.userData.lem.uVegAlphaBias.value = 4.0; });
await shot('solid');
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  v.matFar.userData.lem.uVegAlphaBias.value = 0.34;
  if (v.matGrove) v.matGrove.userData.lem.uVegAlphaBias.value = -0.10; });

/* 2. anisotropy down: fewer, blurrier taps on the same cutout */
for (const a of [4, 2, 1]) {
  await ev(n => { const v = window.__lemWorld.subsystems.get('vegetation');
    for (const t of [v.atlas, v.canopy, v.atlasNormal]) if (t) {
      t.anisotropy = n; t.needsUpdate = true; }
  }, a);
  await shot('aniso' + a);
}
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const t of [v.atlas, v.canopy, v.atlasNormal]) if (t) {
    t.anisotropy = 8; t.needsUpdate = true; } });

/* 3. anisotropy 1 AND solid: if anything is left it is neither */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const m of [v.matFar, v.matGrove]) if (m) m.userData.lem.uVegAlphaBias.value = 4.0;
  for (const t of [v.atlas, v.canopy]) if (t) { t.anisotropy = 1; t.needsUpdate = true; } });
await shot('solid-aniso1');

/* 4. and with the tint attribute flattened, so per-instance value spread is out */
await ev(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const mesh of v.meshes) {
    const a = mesh.geometry.getAttribute('aVegTint');
    if (a) { a.array.fill(1); a.needsUpdate = true; } } });
await shot('solid-aniso1-flat');

console.log('errors', JSON.stringify(errs.slice(0,4)));
await b.close();
