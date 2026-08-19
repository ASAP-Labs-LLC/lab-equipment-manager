/* Sweep the composite exposure at a fixed camera and grade each frame, so the
 * exposure that lands on the reference numbers is measured rather than guessed. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2];
const outDir = process.argv[3];
const vals = (process.argv[4] || '1.2,1.5,1.8,2.0').split(',').map(Number);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto(url, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(4500);
fs.mkdirSync(outDir, {recursive: true});
for (const v of vals) {
  await p.evaluate(x => {
    const w = window.__lemWorld;
    const gi = w.subsystems.get('gi');
    if (gi && !gi.__locked) { gi.__locked = true; gi._adapt = () => {}; }
    w.engine._passes.composite.material.uniforms.uExposure.value = x;
  }, v);
  await p.waitForTimeout(700);
  await p.screenshot({path: `${outDir}/exp-${v}.png`});
}
await b.close();
