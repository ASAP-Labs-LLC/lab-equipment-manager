/* vsweep2.mjs — the real frame (no LOD forcing), near and far foliage uniforms
 * swept together, one PNG per setting.
 *   node vsweep2.mjs <url> <outdir> '[{"tag":"a","fgain":1.0,"fwrap":4.5,"nwrap":2.2}]' */
import {chromium} from 'playwright';
const [url, dir, spec] = process.argv.slice(2);
const settings = JSON.parse(spec);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});
await p.waitForTimeout(3000);
for (const s of settings) {
  await p.evaluate(o => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    const f = v.matFar.userData.lem, n = v.matNear.userData.lem;
    f.uVegGain.value = o.fgain ?? 1.62;
    f.uVegWrap.value = o.fwrap ?? 2.20;
    f.uVegSSS.value = o.fsss ?? 0.45;
    n.uVegGain.value = o.ngain ?? 1.0;
    n.uVegWrap.value = o.nwrap ?? 2.20;
  }, s);
  await p.waitForTimeout(900);
  await p.screenshot({path: `${dir}/w-${s.tag}.png`});
  console.log(s.tag);
}
await b.close();
