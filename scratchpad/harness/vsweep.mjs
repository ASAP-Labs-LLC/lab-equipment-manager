/* vsweep.mjs — one page session: a near-LOD reference frame, then the same
 * trees drawn entirely as far billboards under each uniform setting.
 *   node vsweep.mjs <url> <outdir> '[{"tag":"g10","gain":1.0,...}, ...]' */
import {chromium} from 'playwright';
const [url, dir, spec] = process.argv.slice(2);
const settings = JSON.parse(spec);
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});
await p.waitForTimeout(3000);

/* Near reference: every tree that can be geometry is geometry, cards hidden. */
await p.evaluate(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const e of v.trees) e.far.visible = false; });
await p.waitForTimeout(1500);
await p.screenshot({path: `${dir}/sw-NEAR.png`});
console.log('NEAR');

await p.evaluate(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  for (const e of v.trees) { e.far.visible = true; e.near.visible = false;
                             if (e.trunk) e.trunk.visible = false; }
  v.quality = 0.44; v._treeBudget = 1 / 0.44; v._repartition(true); });
await p.waitForTimeout(1500);

for (const s of settings) {
  await p.evaluate(o => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    const u = v.matFar.userData.lem;
    u.uVegGain.value = o.gain ?? 1.62;
    u.uVegWrap.value = o.wrap ?? 2.20;
    u.uVegSSS.value = o.sss ?? 0.45;
  }, s);
  await p.waitForTimeout(900);
  await p.screenshot({path: `${dir}/sw-${s.tag}.png`});
  console.log(s.tag);
}
await b.close();
