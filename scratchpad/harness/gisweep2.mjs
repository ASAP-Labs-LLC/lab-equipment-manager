/* Sweep any set of composite uniforms. `node gisweep2.mjs URL OUTDIR
 * "uExposure=1.4,uBlackPoint=0.004,uLift=0.02" ";" ...` — one comma-separated
 * assignment list per output frame. uLift is a vec3 and takes one scalar. */
import {chromium} from 'playwright';
import fs from 'node:fs';
const url = process.argv[2];
const outDir = process.argv[3];
const sets = process.argv.slice(4);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto(url, {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(4500);
fs.mkdirSync(outDir, {recursive: true});
let n = 0;
for (const set of sets) {
  await p.evaluate(s => {
    const w = window.__lemWorld;
    const gi = w.subsystems.get('gi');
    if (gi && !gi.__locked) { gi.__locked = true; gi._adapt = () => {}; gi._applyGrade = () => {}; }
    const u = w.engine._passes.composite.material.uniforms;
    for (const kv of s.split(',')) {
      const [k, v] = kv.split('=');
      if (!u[k]) continue;
      if (u[k].value?.isVector3) u[k].value.setScalar(parseFloat(v));
      else u[k].value = parseFloat(v);
    }
  }, set);
  await p.waitForTimeout(700);
  await p.screenshot({path: `${outDir}/set-${n++}-${set.replace(/[=,.]/g, '_')}.png`});
}
await b.close();
