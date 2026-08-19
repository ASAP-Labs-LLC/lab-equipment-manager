/* lblquad10.mjs — is the plate dark because the albedo TEXTURE is dark, or
 * because the standard shading path loses light on it? Compare the same mesh
 * with (a) the shipped material, (b) a flat colour equal to the texture's mean,
 * (c) the texture, (d) white. And print the texture's actual mean. */
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
  console.log(label.padEnd(46), avg.join(','));
};

const mean = await page.evaluate(() => {
  const scene = window.__lemWorld.scene || window.__lemWorld.ctx.scene;
  let m = null; scene.traverse(o => { if (o.name === 'multitek-s:rust') m = o.material; });
  window.__M = m;
  const im = m.map.image;
  const c = document.createElement('canvas');
  c.width = im.width; c.height = im.height;
  const g = c.getContext('2d'); g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, im.width, im.height).data;
  let r = 0, gg = 0, b = 0, n = 0;
  for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; n++; }
  return {size: `${im.width}x${im.height}`, mean: [r / n, gg / n, b / n].map(Math.round)};
});
console.log('rust albedo texture', JSON.stringify(mean));

const run = (src) => page.evaluate(async (s) => {
  const THREE = await import('three');
  const scene = window.__lemWorld.scene || window.__lemWorld.ctx.scene;
  scene.traverse(o => { if (o.name === 'multitek-s:rust') eval(`(${s})`)(o, THREE); });
}, src.toString());

await read('a shipped material');
await run((o, T) => { o.material = new T.MeshStandardMaterial({map: window.__M.map, metalness: 0, roughness: 0.8}); });
await page.waitForTimeout(900); await read('b fresh Standard, same map');
const c = mean.mean;
await page.evaluate(c => { window.__C = c; }, c);
await run((o, T) => { o.material = new T.MeshStandardMaterial({color: new T.Color(window.__C[0] / 255, window.__C[1] / 255, window.__C[2] / 255).convertSRGBToLinear(), metalness: 0, roughness: 0.8}); });
await page.waitForTimeout(900); await read(`c flat colour = texture mean ${c.join(',')}`);
await run((o, T) => { o.material = new T.MeshStandardMaterial({color: 0xffffff, metalness: 0, roughness: 0.8}); });
await page.waitForTimeout(900); await read('d flat white');
await run((o, T) => { o.material = new T.MeshBasicMaterial({map: window.__M.map}); });
await page.waitForTimeout(900); await read('e Basic + same map (albedo only)');

await browser.close();
