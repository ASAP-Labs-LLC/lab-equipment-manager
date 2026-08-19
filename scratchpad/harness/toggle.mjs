/* toggle.mjs <url> <out-prefix> — screenshot the same frame with individual
 * gi terms switched off, so "what is painting that" is measured not guessed. */
import {chromium} from 'playwright';
const url = process.argv[2], prefix = process.argv[3];
const b = await chromium.launch({headless:true, channel:'chromium'});
const p = await b.newPage({viewport:{width:1920,height:1080}, deviceScaleFactor:1});
await p.goto(url, {waitUntil:'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
await p.waitForTimeout(4000);

const variants = {
  base: () => {},
  nofar: () => { const g = window.__lemWorld.subsystems.get('gi'); g.uniforms.lemFarAmount.value = 0; g._farRT = null; },
  nosunshadow: () => { const g = window.__lemWorld.subsystems.get('gi'); g.sun.castShadow = false; window.__lemWorld.engine.shadowNeedsUpdate = true; },
  noao: () => { const g = window.__lemWorld.subsystems.get('gi'); g.uniforms.lemAOStrength.value = 0;
                const c = window.__lemWorld.engine._passes.composite.material.uniforms; if (c.uAOStrength) c.uAOStrength.value = 0; },
  oldenv: () => { const w = window.__lemWorld; w.scene.environmentIntensity = 1; const g = w.subsystems.get('gi'); g._refreshEnvIntensity = () => {}; },
  noenv: () => { window.__lemWorld.scene.environmentIntensity = 0; },
  nogi: () => { const g = window.__lemWorld.subsystems.get('gi'); g.uniforms.lemGIStrength.value = 0; },
  onlygi: () => { const g = window.__lemWorld.subsystems.get('gi'); g.sun.intensity = 0; window.__lemWorld.scene.environmentIntensity = 0; },
};
for (const name of process.argv.slice(4)) {
  await p.evaluate(v => { location.hash = ''; }, null);
  await p.reload({waitUntil:'load'});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout:60000});
  await p.waitForTimeout(3500);
  await p.evaluate(variants[name] ? String(variants[name]) : '()=>{}').catch(()=>{});
  await p.evaluate(fn => { (0, eval)('(' + fn + ')')(); }, String(variants[name] || (()=>{})));
  await p.waitForTimeout(1200);
  await p.screenshot({path: `${prefix}-${name}.png`});
  console.log('wrote', `${prefix}-${name}.png`);
}
await b.close();
