/* film.mjs — watch the world run, instead of photographing it once.
 *
 *   node film.mjs --out ../shots/film-yard [--frames 12] [--every 900]
 *                 [--cam yard] [--at optimpp-1] [--time 16] [--weather clear]
 *                 [--mods "sky,gi,..."] [--video]
 *
 * Every judgement in this project so far has been made from a single frame or
 * from a numeric soak. Both are blind to a whole class of fault: a train that
 * jerks, a consist that slides rather than rolls, a level-of-detail that pops,
 * a shadow that flickers as the cascade refits, two workings that visually
 * overlap for a moment the soak's sampling missed. None of that shows in a
 * still and none of it moves a counter.
 *
 * So this holds one page open and takes a timed sequence while the railway
 * works, then writes a contact sheet so a critic can see the motion in one
 * image. It also records, per frame, where every consist actually is — so a
 * fault seen in the film can be tied to a number.
 *
 * `--video` additionally records a .webm, which is worth having for a human
 * even though a critic reads the frames.
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

const OUT = path.resolve(args.out || '../shots/film');
const FRAMES = parseInt(args.frames || '12', 10);
const EVERY = parseInt(args.every || '900', 10);
const W = parseInt(args.w || '1280', 10);
const H = parseInt(args.h || '720', 10);
const MODS = args.mods ||
  'sky,gi,terrain,buildings,rail,trains,vegetation,weather';

fs.mkdirSync(OUT, {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=${encodeURIComponent(MODS)}&cam=${args.cam || 'yard'}` +
  (args.at ? `&at=${args.at}` : '') +
  `&time=${args.time || 16}&weather=${args.weather || 'clear'}&hud=0` +
  /* Pin the tier, for the same reason shot.mjs does: the adaptive ladder now
   * climbs from the floor tier, so an unpinned take films the renderer warming
   * up rather than the renderer working. This was missed when shot.mjs was
   * fixed — one instrument corrected, its twin left wrong, which is how a
   * whole contact sheet came back looking like a regression that was not one. */
  `&quality=${args.quality || 'ultra'}` +
  (args.season ? `&season=${args.season}` : '');

const browser = await chromium.launch({
  headless: !args.headed, channel: args.headed ? undefined : 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const ctx = await browser.newContext({
  viewport: {width: W, height: H}, deviceScaleFactor: 1,
  ...(args.video ? {recordVideo: {dir: OUT, size: {width: W, height: H}}} : {}),
});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 160)));

await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null,
                           {timeout: 60000});
await page.waitForTimeout(3500);

/* Keep the railway busy for the whole take, so the film shows work rather than
 * an empty yard. */
await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__filmParse = setInterval(() => {
    w.parse(uids[i++ % uids.length], 'L-FILM');
  }, 1200);
});

const track = [];
for (let f = 0; f < FRAMES; f++) {
  await page.waitForTimeout(EVERY);
  const name = `frame-${String(f).padStart(2, '0')}.png`;
  await page.screenshot({path: path.join(OUT, name)});
  const state = await page.evaluate(() => {
    const T = window.__lemWorld.subsystems.get('trains');
    const stats = window.__lemWorld.stats();
    return {
      t: Math.round(performance.now()),
      fps: stats.fps, draws: stats.drawCalls, tris: stats.triangles,
      consists: (T && T.consists ? T.consists : [])
        .filter(c => c && c.state !== 'idle')
        .map(c => ({slot: c.slot, state: c.state, s: +(c.s || 0).toFixed(1),
                    v: +(c.v || 0).toFixed(2), line: c.line})),
    };
  });
  track.push({frame: f, name, ...state});
  process.stdout.write(`  ${name}  ${state.fps}fps  ` +
    `${state.consists.length} working  ` +
    state.consists.map(c => `${c.slot}:${c.state}@${c.s}`).join(' ') + '\n');
}

await page.evaluate(() => clearInterval(window.__filmParse));

/* The contact sheet: every frame in one image, in order, so motion is legible
 * to something that can only look at a picture. */
const cols = Math.ceil(Math.sqrt(FRAMES));
const rows = Math.ceil(FRAMES / cols);
const sheet = await ctx.newPage();
await sheet.setViewportSize({width: cols * 480, height: rows * 285});
const imgs = track.map((t, i) => {
  const b64 = fs.readFileSync(path.join(OUT, t.name)).toString('base64');
  return `<figure><img src="data:image/png;base64,${b64}">` +
         `<figcaption>${i + 1}</figcaption></figure>`;
}).join('');
await sheet.setContent(`<style>
  body{margin:0;background:#111;display:grid;
       grid-template-columns:repeat(${cols},1fr);gap:2px}
  figure{margin:0;position:relative}
  img{width:100%;display:block}
  figcaption{position:absolute;left:5px;top:4px;color:#fff;font:700 15px
             ui-monospace,monospace;text-shadow:0 0 5px #000}
</style>${imgs}`);
await sheet.waitForTimeout(600);
await sheet.screenshot({path: OUT + '-sheet.png', fullPage: true});
await sheet.close();

fs.writeFileSync(OUT + '-track.json',
                 JSON.stringify({url, frames: FRAMES, every: EVERY,
                                 errors, track}, null, 2));
await ctx.close();
await browser.close();

console.log(`\ncontact sheet: ${OUT}-sheet.png`);
console.log(`per-frame state: ${OUT}-track.json`);
if (errors.length) console.log('ERRORS:', errors.slice(0, 3));
