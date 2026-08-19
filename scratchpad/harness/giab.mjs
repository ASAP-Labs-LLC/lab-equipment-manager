/* A/B one gi feature. node giab.mjs URL OUTDIR — writes on.png and off.png with
 * the far cascade enabled and disabled, everything else identical. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2], outDir = process.argv[3];
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto(url, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(5000);
fs.mkdirSync(outDir, {recursive: true});
console.log(JSON.stringify(await p.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  return {casters: gi._farCasters.length, cost: gi._farCost,
          exposure: gi.exposure, bp: window.__lemWorld.engine._passes.composite
            .material.uniforms.uBlackPoint.value};
})));
await p.screenshot({path: `${outDir}/on.png`});
await p.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi.uniforms.lemNearRadius.value = 1e9;      // near cascade covers everything
});
await p.waitForTimeout(900);
await p.screenshot({path: `${outDir}/off.png`});
await b.close();
