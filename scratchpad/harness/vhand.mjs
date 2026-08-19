/* vhand.mjs — the LOD hand-off on its own, with nothing else moving.
 *
 *   node vhand.mjs [--cam low] [--steps 16] [--metres 30] [--out ../shots/vhand]
 *
 * vpop.mjs dollies the camera, which is honest but useless as evidence: every
 * pixel in the frame moves, so a difference image is edges everywhere and a pop
 * hides inside it. This renders the *same view* repeatedly and moves only the
 * point the forest is partitioned from — the camera is put somewhere else, the
 * LOD sets are rebuilt for that place, and then it is put back before the frame
 * is drawn. Nothing in the world has moved between two consecutive frames
 * except which representation each tree is wearing.
 *
 * So any difference at all is the hand-off, and its shape says which kind:
 * a soft wash spread over the treeline is a fade, and a compact block of a few
 * hundred pixels that was not there before is a tree arriving whole.
 *
 * Writes the frames, a difference sheet amplified eight times, and the changed-
 * pixel and longest-run counts per step.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k);
                        return i > 0 ? process.argv[i + 1] : d; };
const cam = arg('cam', 'low');
const STEPS = parseInt(arg('steps', '16'), 10);
const METRES = parseFloat(arg('metres', '30'));
const OUT = path.resolve(arg('out', '../shots/vhand-' + cam));
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
  const v = w.subsystems.get('vegetation');
  /* Take the per-frame partition out of the loop entirely, or the module will
   * quietly put the sets back to the real camera between the rebuild and the
   * screenshot and every frame will be identical. */
  window.__vegPart = v._repartition.bind(v);
  v._repartition = () => {};
  window.__vegBase = w.camera.position.clone();
  /* The wind is what else moves in a forest. Frozen for the take: a leaf that
   * has swayed three centimetres between two frames is a difference, and it
   * would be counted as one. */
  v.shared.uVegWind.value = 0;
  v._wind = 0;
});
if (process.argv.includes('--nogrove')) {
  await p.evaluate(() => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    for (const g of v.groves || []) g.mesh.visible = false;
  });
}

const shots = [];
for (let i = 0; i < STEPS; i++) {
  await p.evaluate(({i, m}) => {
    const w = window.__lemWorld, c = w.camera, base = window.__vegBase;
    const dir = new (c.position.constructor)();
    c.getWorldDirection(dir); dir.y = 0; dir.normalize();
    c.position.copy(base).addScaledVector(dir, -i * m);
    c.updateMatrixWorld(true);
    c.matrixWorldInverse.copy(c.matrixWorld).invert();
    window.__vegPart(true);
    c.position.copy(base);
    c.updateMatrixWorld(true);
    c.matrixWorldInverse.copy(c.matrixWorld).invert();
  }, {i, m: METRES});
  await p.waitForTimeout(500);
  const f = path.join(OUT, `h${String(i).padStart(2, '0')}.png`);
  await p.screenshot({path: f});
  shots.push(f);
}
await b.close();

const b2 = await chromium.launch({headless: true, channel: 'chromium'});
const p2 = await b2.newPage({viewport: {width: 1280, height: 720}});
const res = await p2.evaluate(async srcs => {
  const load = s => new Promise(r => { const im = new Image();
    im.onload = () => r(im); im.src = s; });
  const cv = document.createElement('canvas');
  cv.width = 1280; cv.height = 720;
  const g = cv.getContext('2d', {willReadFrequently: true});
  const pix = async s => { const im = await load(s); g.clearRect(0, 0, 1280, 720);
    g.drawImage(im, 0, 0); return g.getImageData(0, 0, 1280, 720).data; };
  const out = [], sheets = [];
  let prev = await pix(srcs[0]);
  for (let k = 1; k < srcs.length; k++) {
    const cur = await pix(srcs[k]);
    let changed = 0, maxRun = 0;
    const img = g.createImageData(1280, 720);
    for (let y = 0; y < 720; y++) {
      let run = 0;
      for (let x = 0; x < 1280; x++) {
        const o = (y * 1280 + x) * 4;
        const d = Math.abs(cur[o] - prev[o]) + Math.abs(cur[o + 1] - prev[o + 1]) +
                  Math.abs(cur[o + 2] - prev[o + 2]);
        const a = Math.min(255, d * 8);
        img.data[o] = img.data[o + 1] = img.data[o + 2] = a;
        img.data[o + 3] = 255;
        if (d > 96) { changed++; run++; if (run > maxRun) maxRun = run; }
        else run = 0;
      }
    }
    g.putImageData(img, 0, 0);
    sheets.push(cv.toDataURL('image/png'));
    out.push({step: k, changed, maxRun});
    prev = cur;
  }
  return {out, sheets};
}, shots.map(f => 'data:image/png;base64,' + fs.readFileSync(f).toString('base64')));

const sheet = await b2.newPage();
const cols = 4, rows = Math.ceil(res.sheets.length / cols);
await sheet.setViewportSize({width: cols * 420, height: rows * 240});
await sheet.setContent(`<style>body{margin:0;background:#000;display:grid;
  grid-template-columns:repeat(${cols},1fr);gap:2px}figure{margin:0;position:relative}
  img{width:100%;display:block}figcaption{position:absolute;left:5px;top:4px;
  color:#0f0;font:700 14px ui-monospace,monospace}</style>` +
  res.sheets.map((s, i) => `<figure><img src="${s}">` +
    `<figcaption>${i + 1}: ${res.out[i].changed} px, run ${res.out[i].maxRun}` +
    `</figcaption></figure>`).join(''));
await sheet.waitForTimeout(500);
await sheet.screenshot({path: OUT + '-diff.png', fullPage: true});
await b2.close();

console.log(JSON.stringify({cam, metres: METRES, diff: OUT + '-diff.png',
                            steps: res.out}));
