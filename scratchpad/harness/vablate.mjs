/* vablate.mjs — turn one far-card lighting term off at a time and shoot. */
import {chromium} from 'playwright';
const [url, dir] = process.argv.slice(2);
const steps = [
  ['00-base', () => {}],
  ['01-nowrap', v => { v.matFar.userData.lem.uVegWrap.value = 0; }],
  ['02-nosss', v => { v.matFar.userData.lem.uVegSSS.value = 0;
                      v.matFar.userData.lem.uVegWrap.value = 0; }],
  ['03-noenv', v => { v.matFar.userData.lemEnvU.value = 0;
                      v.matFar.envMapIntensity = 0; }],
  ['04-rough1', v => { v.matFar.roughness = 1.0; v.matFar.needsUpdate = true; }],
  ['05-nospec', v => { v.matFar.roughness = 1.0;
                       v.matFar.userData.lem.uVegGain.value = 0.0001; }],
];
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
for (const [tag, fn] of steps) {
  const p = await b.newPage({viewport: {width: 1920, height: 1080}});
  await p.goto(url, {waitUntil: 'load', timeout: 60000});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});
  await p.waitForTimeout(2800);
  await p.evaluate(`(${fn.toString()})(window.__lemWorld.subsystems.get('vegetation'))`);
  await p.waitForTimeout(1200);
  await p.screenshot({path: `${dir}/ab-${tag}.png`});
  await p.close();
  console.log(tag);
}
await b.close();
