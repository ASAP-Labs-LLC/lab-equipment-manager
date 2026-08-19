/* Sweep the probe-fill strength (and re-expose for it) to find where deep
 * shade stops clipping to black without flattening the sun back out. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless:true, channel:'chromium', args:['--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport:{width:1600,height:900}});
await p.goto(process.argv[2], {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(3000);
console.log('BASE', JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld;
  const gi = [...(w.subsystems || new Map()).values()].find(s => s && s.uniforms && s.uniforms.lemGIStrength);
  window.__gi = gi;
  window.__gs0 = gi.uniforms.lemGIStrength.value;
  window.__fill0 = gi._fillE; window.__key0 = gi._keyE;
  gi._fitFill = () => {}; gi._adapt = () => {};
  return {gs: window.__gs0, fill: gi._fillE, key: gi._keyE, exp: gi.exposure};
})));
for (const m of (process.argv[4] || '1,1.4,1.8,2.4').split(',').map(Number)) {
  await p.evaluate(mult => {
    const w = window.__lemWorld, gi = window.__gi;
    gi.uniforms.lemGIStrength.value = window.__gs0 * mult;
    // re-expose analytically for the new fill, exactly as _adapt would
    const S = window.__key0 + window.__fill0 * mult;
    const REF = 4.00;
    const exp = Math.min(4, Math.max(0.15, Math.pow(REF / Math.max(S, 0.02), 0.62)));
    w.engine._passes.composite.material.uniforms.uExposure.value = exp;
  }, m);
  await p.waitForTimeout(500);
  await p.screenshot({path: `${process.argv[3]}-f${m}.png`});
}
await b.close();
