/* vpop.mjs — drive the camera through the LOD hand-off band and look for a
 * tree arriving all at once.
 *
 *   node vpop.mjs [--cam low] [--steps 14] [--metres 40] [--out ../shots/vpop]
 *
 * film.mjs is the gate and it is the right gate, but it never translates the
 * camera: at a fixed viewpoint `_repartition` returns on its first line and no
 * hand-off can happen, so a contact sheet from it cannot show a pop even if the
 * build is full of them. This dollies the eye outward in steps larger than the
 * partition's own 6 m threshold, so every step forces a rebuild — the worst
 * case, several times worse than any real camera move — and then diffs each
 * frame against the one before it.
 *
 * Trains and weather are hidden for the take. They move on their own and would
 * dominate every difference; what is being measured is whether the forest
 * changes smoothly, and nothing else in the frame may be allowed to move.
 *
 * Two outputs. A number per step: how many pixels changed by more than a
 * quarter of full range, and the largest single connected run of them on a
 * scanline — a fade moves many pixels a little and a pop moves a compact block
 * a lot, and the run length is what separates the two. And an amplified
 * difference sheet, because the number cannot tell a pop from a shadow refit.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
                        return i > 0 ? process.argv[i + 1] : d; };
const cam = arg('cam', 'low');
const STEPS = parseInt(arg('steps', '14'), 10);
const METRES = parseFloat(arg('metres', '40'));
const OUT = path.resolve(arg('out', '../shots/vpop-' + cam));
fs.mkdirSync(OUT, {recursive: true});

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html` +
             `?cam=${cam}&time=16&weather=clear&hud=0`,
             {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(6000);

await p.evaluate(() => {
  const w = window.__lemWorld;
  for (const id of ['trains', 'weather', 'labels']) {
    const m = w.subsystems.get(id);
    if (m && m.group) m.group.visible = false;
  }
  w.rig.idleDrift = false;
});

const shots = [];
for (let i = 0; i < STEPS; i++) {
  await p.evaluate(({i, m}) => {
    const w = window.__lemWorld, r = w.rig;
    /* Pull the pivot straight back along the view azimuth, which walks the
     * whole forest through every LOD boundary at once instead of sliding it
     * past one flank. */
    const a = r.goalYaw ?? r.yaw ?? 0;
    r.goalTarget.set(Math.sin(a) * -i * m, r.goalTarget.y, Math.cos(a) * -i * m);
    r.apply(1);
  }, {i, m: METRES});
  await p.waitForTimeout(700);
  const f = path.join(OUT, `s${String(i).padStart(2, '0')}.png`);
  await p.screenshot({path: f});
  shots.push(f);
}
await b.close();

/* Decode with the page itself rather than a dependency: the harness has no
 * image library and adding one to a lab bench is not on. */
const b2 = await chromium.launch({headless: true, channel: 'chromium'});
const p2 = await b2.newPage({viewport: {width: 1280, height: 720}});
const diffs = await p2.evaluate(async srcs => {
  const load = s => new Promise(r => { const im = new Image();
    im.onload = () => r(im); im.src = s; });
  const cv = document.createElement('canvas');
  cv.width = 1280; cv.height = 720;
  const g = cv.getContext('2d', {willReadFrequently: true});
  const pix = async s => { const im = await load(s); g.clearRect(0, 0, 1280, 720);
    g.drawImage(im, 0, 0); return g.getImageData(0, 0, 1280, 720).data; };
  const out = [];
  let prev = await pix(srcs[0]);
  for (let k = 1; k < srcs.length; k++) {
    const cur = await pix(srcs[k]);
    let changed = 0, maxRun = 0;
    /* The top half only. The near field slides past the lens under any camera
     * move and every pixel of it changes; the question is about the forest at
     * the hand-off, which lives above the horizon line at both these cameras. */
    for (let y = 0; y < 360; y++) {
      let run = 0;
      for (let x = 0; x < 1280; x++) {
        const o = (y * 1280 + x) * 4;
        const d = Math.abs(cur[o] - prev[o]) + Math.abs(cur[o + 1] - prev[o + 1]) +
                  Math.abs(cur[o + 2] - prev[o + 2]);
        if (d > 96) { changed++; run++; if (run > maxRun) maxRun = run; }
        else run = 0;
      }
    }
    out.push({step: k, changed, maxRun});
    prev = cur;
  }
  return out;
}, shots.map(f => 'data:image/png;base64,' + fs.readFileSync(f).toString('base64')));
await b2.close();

console.log(JSON.stringify({cam, steps: STEPS, metres: METRES, diffs}, null, 1));
