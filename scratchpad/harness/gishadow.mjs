/* gishadow.mjs — what is actually in a cast-shadow pixel? Knock out one term at
 * a time and measure the same 40x30 patch. */
import {chromium} from 'playwright';
const url = 'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';
const dir = '/Users/rynatical/LAB-lem/scratchpad/shots/';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);

const cases = {
  base: () => {},
  gi4: () => { const g = window.__lemWorld.subsystems.get('gi');
               g.uniforms.lemGIStrength.value *= 4; g._fitFill = () => {}; },
  noao: () => { const g = window.__lemWorld.subsystems.get('gi');
                g.uniforms.lemAOStrength.value = 0; g.uniforms.lemAOContact.value = 0;
                g.uniforms.lemAOFloor.value = 1; },
  env2: () => { const g = window.__lemWorld.subsystems.get('gi');
                window.__lemWorld.scene.environmentIntensity *= 4;
                g._refreshEnvIntensity = () => {}; },
  exp2: () => { const g = window.__lemWorld.subsystems.get('gi');
                g._applyGrade = () => {};
                const c = window.__lemWorld.engine?._passes?.composite?.material?.uniforms;
                if (c?.uExposure) c.uExposure.value *= 2; },
};
for (const [name, fn] of Object.entries(cases)) {
  await page.evaluate(fn);
  await page.waitForTimeout(1600);
  await page.screenshot({path: dir + 'gsh-' + name + '.png'});
  if (name !== 'base') await page.reload({waitUntil: 'load'});
  if (name !== 'base') {
    await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
    await page.waitForTimeout(4500);
  }
}
console.log('ok');
await browser.close();
