/* lblquad4.mjs — prove the cause. Photograph the plate as it ships, then with
 * metalness dropped to 0, then with metalness restored and an environment
 * present. A fully-metallic surface has no diffuse term at all: with no IBL it
 * can only be black, whatever the albedo map holds. */
import {chromium} from 'playwright';
import fs from 'node:fs';

const OUT = '/Users/rynatical/LAB-lem/scratchpad/shots/lblquad';
fs.mkdirSync(OUT, {recursive: true});

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

const env = await page.evaluate(() => {
  const w = window.__lemWorld;
  const scene = w.scene || w.ctx?.scene;
  const mats = new Map();
  scene.traverse(o => {
    if (!o.material) return;
    for (const m of [].concat(o.material)) {
      if (!m || !m.isMeshStandardMaterial) continue;
      const k = m.uuid;
      if (!mats.has(k)) mats.set(k, {name: o.name, metal: m.metalness, rough: m.roughness,
                                     envI: m.envMapIntensity, hasEnv: !!m.envMap});
    }
  });
  const hot = [...mats.values()].filter(m => m.metal >= 0.9);
  return {sceneEnv: !!scene.environment, envType: scene.environment?.type || null,
          envIntensity: scene.environmentIntensity,
          totalStd: mats.size, fullyMetal: hot.length, sample: hot.slice(0, 25)};
});
console.log('ENV', JSON.stringify(env, null, 1));

await page.screenshot({path: OUT + '/a-asis.png'});

await page.evaluate(() => {
  const scene = (window.__lemWorld.scene || window.__lemWorld.ctx.scene);
  scene.traverse(o => {
    if (o.name && o.name.endsWith(':rust') && o.material) o.material.metalness = 0.0;
  });
});
await page.waitForTimeout(900);
await page.screenshot({path: OUT + '/b-metal0.png'});

console.log('wrote', OUT);
await browser.close();
