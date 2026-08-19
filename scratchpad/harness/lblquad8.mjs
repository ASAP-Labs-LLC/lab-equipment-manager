/* lblquad8.mjs — the previous receiveShadow test was void: three only rebuilds
 * the program when the material is marked dirty. Redo it properly, and try the
 * shadow map off entirely. */
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

const read = async (label, box = {x: 462, y: 196, width: 24, height: 20}) => {
  const buf = await page.screenshot({clip: box});
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
  }, buf.toString('base64'));
  console.log(label.padEnd(40), avg.join(','));
};

const run = (src) => page.evaluate(async (s) => {
  const THREE = await import('three');
  const w = window.__lemWorld;
  eval(`(${s})`)(w.scene || w.ctx.scene, THREE, w);
}, src.toString());

await read('plate as-is');
await read('brick wall beside it (control)', {x: 520, y: 250, width: 24, height: 20});

await run((scene) => {
  scene.traverse(o => {
    if (o.name === 'multitek-s:rust') {
      o.receiveShadow = false;
      [].concat(o.material).forEach(m => m && (m.needsUpdate = true));
    }
  });
});
await page.waitForTimeout(1000); await read('receiveShadow off (properly)');

await run((scene, T, w) => {
  window.__lights = [];
  scene.traverse(o => {
    if (o.isLight && o.castShadow) { window.__lights.push(o); o.castShadow = false; }
  });
  scene.traverse(o => { if (o.material) [].concat(o.material).forEach(m => m && (m.needsUpdate = true)); });
});
await page.waitForTimeout(1400); await read('all shadow casting off');

await run((scene, T, w) => {
  window.__lights.forEach(l => l.castShadow = true);
  scene.traverse(o => { if (o.material) [].concat(o.material).forEach(m => m && (m.needsUpdate = true)); });
});
await page.waitForTimeout(1400); await read('shadows back on');

/* What lights are there at all, and where is the sun? */
const lights = await page.evaluate(() => {
  const scene = window.__lemWorld.scene || window.__lemWorld.ctx.scene;
  const out = [];
  scene.traverse(o => {
    if (!o.isLight) return;
    out.push({type: o.type, name: o.name, intensity: o.intensity,
              colour: '#' + o.color.getHexString(),
              pos: o.position.toArray().map(n => +n.toFixed(1)),
              target: o.target?.position?.toArray().map(n => +n.toFixed(1)),
              castShadow: o.castShadow,
              cam: o.shadow ? {near: o.shadow.camera.near, far: o.shadow.camera.far,
                               l: o.shadow.camera.left, r: o.shadow.camera.right,
                               t: o.shadow.camera.top, b: o.shadow.camera.bottom,
                               bias: o.shadow.bias, nBias: o.shadow.normalBias,
                               map: o.shadow.mapSize.toArray()} : null});
  });
  const s = window.__lemWorld.scene || window.__lemWorld.ctx.scene;
  return {lights: out, envIntensity: s.environmentIntensity, hasEnv: !!s.environment,
          bg: s.background ? s.background.type || 'colour' : null};
});
console.log('LIGHTS', JSON.stringify(lights, null, 1));

await browser.close();
