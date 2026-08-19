/* lblquad6.mjs — one property at a time on the black plate, reading the pixel
 * back each time, so the mechanism is measured rather than inferred. */
import {chromium} from 'playwright';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=16&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720}, deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3500);

const read = async (label) => {
  const buf = await page.screenshot({clip: {x: 462, y: 196, width: 24, height: 20}});
  /* Average the crop without decoding: ask the page instead. */
  const b64 = buf.toString('base64');
  const avg = await page.evaluate(async (b64) => {
    const img = new Image();
    await new Promise(r => { img.onload = r; img.src = 'data:image/png;base64,' + b64; });
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let r = 0, gg = 0, b = 0, n = 0;
    for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; n++; }
    return [Math.round(r / n), Math.round(gg / n), Math.round(b / n)];
  }, b64);
  console.log(label.padEnd(34), avg.join(','));
};

const tweak = (fn) => page.evaluate((src) => {
  const scene = window.__lemWorld.scene || window.__lemWorld.ctx.scene;
  scene.traverse(o => {
    if (o.name === 'multitek-s:rust' && o.material) eval(`(${src})`)(o, o.material);
  });
}, fn.toString());

await read('as-is');
await tweak((o, m) => { m.userData._m = m.metalness; m.metalness = 0; m.needsUpdate = false; });
await page.waitForTimeout(700); await read('metalness 1 -> 0');
await tweak((o, m) => { m.metalness = m.userData._m; });
await page.waitForTimeout(700); await read('metalness restored');
await tweak((o, m) => { m.userData._mm = m.metalnessMap; m.metalnessMap = null; m.needsUpdate = true; });
await page.waitForTimeout(900); await read('metalnessMap removed');
await tweak((o, m) => { m.metalnessMap = m.userData._mm; m.needsUpdate = true; });
await page.waitForTimeout(900); await read('metalnessMap restored');
await tweak((o, m) => { m.userData._n = m.normalMap; m.normalMap = null; m.needsUpdate = true; });
await page.waitForTimeout(900); await read('normalMap removed');
await tweak((o, m) => { m.normalMap = m.userData._n; m.needsUpdate = true; });
await page.waitForTimeout(900); await read('normalMap restored');
await tweak((o, m) => { m.userData._rm = m.roughnessMap; m.roughnessMap = null; m.roughness = 0.7; m.needsUpdate = true; });
await page.waitForTimeout(900); await read('roughness flat 0.7');
await tweak((o, m) => { m.roughnessMap = m.userData._rm; m.roughness = 1; m.needsUpdate = true; });
await page.waitForTimeout(900); await read('roughness restored');
await tweak((o, m) => { m.userData._e = m.emissive.getHex(); m.emissive.setHex(0x333333); });
await page.waitForTimeout(700); await read('emissive 0x333333 (sanity)');
await tweak((o, m) => { m.emissive.setHex(m.userData._e); });
await page.waitForTimeout(700); await read('emissive restored');
await tweak((o, m) => { o.receiveShadow = false; });
await page.waitForTimeout(900); await read('receiveShadow off');

await browser.close();
