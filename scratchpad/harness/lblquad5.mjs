/* lblquad5.mjs — is the fully-metallic reading real, or is an ORM map meant to
 * override it? Report, per building material, whether metalness/roughness maps
 * exist and what they actually hold at a few texels. */
import {chromium} from 'playwright';

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings&cam=yard&time=16&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720}, deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);

const out = await page.evaluate(() => {
  const scene = window.__lemWorld.scene || window.__lemWorld.ctx.scene;
  const seen = new Set(), rows = [];
  const sample = (tex) => {
    if (!tex || !tex.image) return null;
    const im = tex.image;
    try {
      const c = document.createElement('canvas');
      c.width = im.width; c.height = im.height;
      const g = c.getContext('2d');
      g.drawImage(im, 0, 0);
      const d = g.getImageData(0, 0, im.width, im.height).data;
      const at = (u, v) => {
        const i = ((Math.floor(v * im.height) * im.width) + Math.floor(u * im.width)) * 4;
        return [d[i], d[i + 1], d[i + 2]];
      };
      return {size: `${im.width}x${im.height}`, s: [at(0.25, 0.25), at(0.5, 0.5), at(0.75, 0.75)]};
    } catch (e) { return {err: String(e).slice(0, 60)}; }
  };
  scene.traverse(o => {
    if (!o.material || !o.name || !o.name.startsWith('multitek-s:')) return;
    for (const m of [].concat(o.material)) {
      if (!m || seen.has(m.uuid)) continue;
      seen.add(m.uuid);
      rows.push({mesh: o.name, metalness: m.metalness, roughness: m.roughness,
                 metalnessMap: !!m.metalnessMap, roughnessMap: !!m.roughnessMap,
                 sameTex: m.metalnessMap === m.roughnessMap,
                 envMapIntensity: m.envMapIntensity,
                 orm: sample(m.metalnessMap)});
    }
  });
  return rows;
});
for (const r of out) console.log(JSON.stringify(r));
await browser.close();
