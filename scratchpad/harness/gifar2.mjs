/* gifar2.mjs — is the coarse cascade producing pixels at all?
 *
 *   node gifar2.mjs "<solo url>" <outprefix>
 *
 * Shot A: as-is. Shot B: lemNearRadius forced to 0 so the coarse cascades apply
 * over the WHOLE frame including the near field. Shot C: the cascade map itself
 * blitted to screen.
 */
import {chromium} from 'playwright';

const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low&time=13&weather=clear&hud=0';
const pre = process.argv[3] || '/Users/rynatical/LAB-lem/scratchpad/shots/gifar2';

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(4500);
await page.screenshot({path: pre + '-a.png'});

await page.evaluate(() => {
  const gi = window.__lemWorld.subsystems.get('gi');
  gi._pinNear = true;
  gi.uniforms.lemNearRadius.value = 0.001;
  const f = gi._fitShadow.bind(gi);
  gi._fitShadow = (...a) => { f(...a); gi.uniforms.lemNearRadius.value = 0.001; };
});
await page.waitForTimeout(2500);
await page.screenshot({path: pre + '-b.png'});

/* Blit cascade 0's packed depth to the screen so we can see what it holds. */
const stats = await page.evaluate(async () => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE;
  const c = gi._csm[0];
  const rn = w.engine.renderer;
  const buf = new Uint8Array(c.rt.width * c.rt.height * 4);
  rn.readRenderTargetPixels(c.rt, 0, 0, c.rt.width, c.rt.height, buf);
  let white = 0, occ = 0, min = 1;
  for (let i = 0; i < buf.length; i += 4) {
    const d = buf[i] / 255 + buf[i + 1] / 65025;
    if (d > 0.999) white++; else { occ++; if (d < min) min = d; }
  }
  void THREE;
  return {texels: buf.length / 4, white, occ, minDepth: min,
          occPct: +(100 * occ / (buf.length / 4)).toFixed(2)};
});
console.log(JSON.stringify(stats, null, 2));
await browser.close();
