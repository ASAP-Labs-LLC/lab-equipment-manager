/* lblquad9.mjs — separate albedo from irradiance. Paint the plate white and
 * read the pure lighting term; then raise the environment and see whether the
 * cylinder ever shows any cross-section shading at all. */
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
  console.log(label.padEnd(44), avg.join(','));
};

const run = (src) => page.evaluate(async (s) => {
  const THREE = await import('three');
  const w = window.__lemWorld;
  eval(`(${s})`)(w.scene || w.ctx.scene, THREE, w);
}, src.toString());

await read('plate as-is');
await read('sunlit white tank left of it', {x: 405, y: 215, width: 16, height: 30});
await read('grey clad wall right of it', {x: 545, y: 200, width: 20, height: 20});

await run((scene, T) => {
  scene.traverse(o => {
    if (o.name !== 'multitek-s:rust') return;
    window.__M = o.material;
    o.material = new T.MeshStandardMaterial({color: 0xffffff, roughness: 0.8, metalness: 0});
  });
});
await page.waitForTimeout(1000); await read('plate as plain white PBR');
await page.screenshot({path: OUT + '/c-white.png'});

await run((scene) => { scene.environmentIntensity = 1.0; });
await page.waitForTimeout(1000); await read('white plate, env intensity 1.0');
await page.screenshot({path: OUT + '/d-white-env1.png'});

await run((scene, T) => {
  scene.environmentIntensity = 0.15171149045242321;
  scene.traverse(o => { if (o.name === 'multitek-s:rust') o.material = window.__M; });
});
await page.waitForTimeout(1000); await read('restored');

/* Same tank on the other sites: is every rust vessel this dark? */
const others = await page.evaluate(async () => {
  const THREE = await import('three');
  const w = window.__lemWorld, cam = w.camera || w.ctx.camera, scene = w.scene || w.ctx.scene;
  const out = [];
  scene.traverse(o => {
    if (!o.name || !o.name.endsWith(':rust')) return;
    o.geometry.computeBoundingBox();
    const b = o.geometry.boundingBox;
    out.push({name: o.name, tris: (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3,
              box: [b.min.toArray().map(n => +n.toFixed(1)), b.max.toArray().map(n => +n.toFixed(1))]});
  });
  return out;
});
console.log('RUST MESHES', JSON.stringify(others));
await browser.close();
