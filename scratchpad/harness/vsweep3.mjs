/* vsweep3.mjs — far-card alpha coverage sweep on the real frame. */
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
    const f = v.matFar.userData.lem;
    f.uVegAlphaBias.value = o.ab ?? 0.20;
    f.uVegDither.value = o.dit ?? 0.16;
    f.uVegGain.value = o.gain ?? 0.95;
    f.uVegWrap.value = o.wrap ?? 4.60;
  }, s);
  await p.waitForTimeout(900);
  await p.screenshot({path: `${dir}/a-${s.tag}.png`});
  console.log(s.tag);
}
await b.close();
