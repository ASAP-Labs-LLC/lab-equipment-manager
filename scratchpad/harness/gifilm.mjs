/* gifilm.mjs — film.mjs, plus an arbitrary runtime patch applied after the
 * world is up, so a shadow hypothesis can be filmed rather than argued.
 *
 *   node gifilm.mjs --out ../shots/x --patch always   # shadow map every frame
 *   node gifilm.mjs --out ../shots/y --patch none     # exactly film.mjs
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const OUT = path.resolve(args.out || '../shots/gifilm');
const FRAMES = parseInt(args.frames || '12', 10);
const EVERY = parseInt(args.every || '1100', 10);
const W = 1280, H = 720;
fs.mkdirSync(OUT, {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}` +
  `&weather=${args.weather || 'clear'}&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: W, height: H},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 160)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3500);

await page.evaluate(p => {
  const w = window.__lemWorld;
  if (p === 'always') {
    const e = w.engine;
    const loop = () => { e.shadowNeedsUpdate = true; requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
}, args.patch || 'none');

await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__filmParse = setInterval(() => w.parse(uids[i++ % uids.length], 'L-GIF'), 1200);
});

const track = [];
for (let f = 0; f < FRAMES; f++) {
  await page.waitForTimeout(EVERY);
  const name = `frame-${String(f).padStart(2, '0')}.png`;
  await page.screenshot({path: path.join(OUT, name)});
  const st = await page.evaluate(() => {
    const s = window.__lemWorld.stats();
    return {fps: s.fps, draws: s.drawCalls, tris: s.triangles};
  });
  track.push({f, name, ...st});
  process.stdout.write(`  ${name} ${st.fps}fps ${st.draws} draws ${st.tris} tris\n`);
}
await page.evaluate(() => clearInterval(window.__filmParse));

const cols = Math.ceil(Math.sqrt(FRAMES)), rows = Math.ceil(FRAMES / cols);
const sheet = await ctx.newPage();
await sheet.setViewportSize({width: cols * 480, height: rows * 285});
const imgs = track.map((t, i) => {
  const b64 = fs.readFileSync(path.join(OUT, t.name)).toString('base64');
  return `<figure><img src="data:image/png;base64,${b64}"><figcaption>${i + 1}</figcaption></figure>`;
}).join('');
await sheet.setContent(`<style>body{margin:0;background:#111;display:grid;
  grid-template-columns:repeat(${cols},1fr);gap:2px}figure{margin:0;position:relative}
  img{width:100%;display:block}figcaption{position:absolute;left:5px;top:4px;color:#fff;
  font:700 15px ui-monospace,monospace;text-shadow:0 0 5px #000}</style>${imgs}`);
await sheet.waitForTimeout(600);
await sheet.screenshot({path: OUT + '-sheet.png', fullPage: true});
console.log('sheet', OUT + '-sheet.png', 'errors', errors.slice(0, 3));
await browser.close();
