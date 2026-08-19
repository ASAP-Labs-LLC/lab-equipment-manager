/* ab.mjs — screenshot with one lighting term switched off.
 *   node ab.mjs URL OUT.png [noshadow|noao|nocsm|none] */
import {chromium} from 'playwright';
const MODE = process.argv[4] || 'none';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(5000);
await p.evaluate(m => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  if (m === 'noshadow') {
    gi.sun.castShadow = false;
    Object.defineProperty(gi.sun, 'castShadow', {get: () => false, set: () => {}});
    w.engine.shadowNeedsUpdate = true;
  }
  if (m === 'noao') { gi.uniforms.lemAOStrength.value = 0; gi.uniforms.lemAOContact.value = 0; }
  if (m === 'nocsm') {
    gi._renderCascade = () => {};
    for (let i = 0; i < 2; i++) {
      gi.uniforms['lemCsmReady' + i].value = 0;
      Object.defineProperty(gi.uniforms['lemCsmReady' + i], 'value',
        {get: () => 0, set: () => {}});
    }
  }
}, MODE);
await p.waitForTimeout(2500);
await p.screenshot({path: process.argv[3]});
await b.close();
