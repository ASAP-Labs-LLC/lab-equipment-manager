/* Sweep the composite exposure and grade each step, to re-anchor gi's REF. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1600,height:900}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
const base = await p.evaluate(() => {
  const w = window.__lemWorld;
  const gi = [...(w.subsystems || new Map()).values()].find(s => s && s.uniforms && s.uniforms.lemGIStrength);
  window.__gi = gi;
  return {exposure: w.engine._passes.composite.material.uniforms.uExposure.value,
          giScale: gi?.giScale, fillE: gi?._fillE, keyE: gi?._keyE,
          sceneIrradiance: gi?.sceneIrradiance};
});
console.log('BASE', JSON.stringify(base));
const mults = (process.argv[4] || '1,1.3,1.6,2.0,2.4,2.9').split(',').map(Number);
for (const m of mults) {
  await p.evaluate(mult => {
    const w = window.__lemWorld, u = w.engine._passes.composite.material.uniforms;
    if (window.__gi) window.__gi._adapt = () => {};
    u.uExposure.value = window.__baseExp = (window.__baseExp ?? u.uExposure.value);
    u.uExposure.value = window.__baseExp * mult;
  }, m);
  await p.waitForTimeout(500);
  await p.screenshot({path: `${process.argv[3]}-x${m}.png`});
}
await b.close();
