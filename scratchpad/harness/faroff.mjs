/* faroff.mjs — same frame with the coarse cascade forced off, to see what it
 * is actually painting.  node faroff.mjs URL OUT.png [on|off] */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(5000);
if ((process.argv[4] || 'off') === 'off') {
  await p.evaluate(() => {
    const gi = window.__lemWorld.subsystems.get('gi');
    gi._renderFar = () => {};
    gi.uniforms.lemFarAmount.value = 0;
    Object.defineProperty(gi.uniforms.lemFarAmount, 'value',
      {get: () => 0, set: () => {}});
  });
  await p.waitForTimeout(1500);
}
await p.screenshot({path: process.argv[3]});
await b.close();
