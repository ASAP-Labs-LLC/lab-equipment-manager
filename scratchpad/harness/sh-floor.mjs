/* sh-floor.mjs — sn-floor.mjs with a `--fog` init override, so the fog curve
 * A/B can be run on the REAL page in one protocol. Everything else, including
 * the per-capture box recompute and the frozen rig, is sn-floor's verbatim.
 *
 * ORIGINAL HEADER FOLLOWS.
 *
 * sn-floor.mjs — THE ACCEPTANCE TEST. Not the dev harness: the real Flask page
 * at :5612/floor, nine subsystems including `labels`, seven seeded instruments
 * each carrying a floating status board.
 *
 * The product's job is that an operator glances at the wall and sees which
 * instrument is RED. Four critiques have said the frame's compressed histogram
 * is *why* the boards read. So any change to the key-to-fill has to be measured
 * here before it is allowed to ship, and the question is not "is the aerial
 * prettier" but "is the RED board harder to find".
 *
 * Measures, per board, in ONE page session with the stop PINNED to one value
 * and gi's fill ablated in place (`setFillTrim`, which survives `onTime`):
 *
 *   BOARD L        median luminance of the card's own pixels
 *   BG L           median luminance of an annulus of PAGE behind/around it
 *   WEBER          |board - bg| / bg — how far the card stands off its ground
 *   TEXT           p95 - p05 within the card (the white type against the chip)
 *   NEAREST        how close the background gets to the card's own value; a
 *                  board sitting over a value close to its own is the failure
 *                  mode being guarded against
 *   CHROME         left panel and legend strips, which are DOM over the canvas
 *
 *   node sn-floor.mjs [--trims 1,1.1698] [--out DIR]
 *
 * The two trims are the A/B: 1 is what the file now ships, and the second is
 * the multiplier that reproduces the PREVIOUS fill exactly at this hour, so the
 * pair is a true in-session ablation rather than a cross-session comparison.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (!process.argv[i].startsWith('--')) continue;
  const k = process.argv[i].slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
}
const trims = String(a.trims || '1').split(',').map(Number);
const dir = a.out || '/tmp/';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
if (a.fog) await p.addInitScript(`window.__lemFog = ${a.fog};`);
await p.goto('http://127.0.0.1:5612/floor', {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => !!window.__lemWorld, null, {timeout: 120000});
await p.waitForTimeout(16000);

if (a.time) {
  await p.evaluate(h => window.__lemWorld.setTimeOfDay(h), Number(a.time));
  await p.waitForTimeout(6000);
}
const setup = await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  /* The camera on /floor drifts. The first version of this instrument computed
   * each card's screen box once and reused it across the pair, so by the second
   * capture the box was no longer over the card — and the A/A noise on `weber`
   * came back at 0.34 against an A/B effect of 0.04. Freeze the rig AND
   * recompute the boxes per capture. */
  if (w.rig) { w.rig.idleDrift = false; w.rig.autoOrbit = false; w.rig.apply(1); }
  w.camera.updateMatrixWorld(true);
  gi.setExposureLocked(true);
  return {pinned: +gi._expNow.toFixed(4), locked: gi.exposureLocked,
          hasTrim: typeof gi.setFillTrim === 'function',
          time: w.ctx.world?.timeOfDay, tier: w.engine.tier?.name,
          fillE: +gi._fillE.toFixed(4), keyE: +gi._keyE.toFixed(4),
          ratio: +(gi._fillE / gi._keyE).toFixed(4),
          subsystems: [...w.subsystems.keys()]};
});

/* Where each card is on screen. Projected from the labels entry's own
 * anchor, not guessed from colour, so a board that moves between the two
 * halves of the pair cannot be silently compared against a different patch. */
const BOXFN = () => {
  const w = window.__lemWorld, lab = w.subsystems.get('labels');
  const THREE = w.ctx.THREE, cam = w.camera;
  cam.updateMatrixWorld(true);
  const out = [];
  for (const e of (lab.entries.values ? [...lab.entries.values()] : [...lab.entries])) {
    if (!Number.isFinite(e.x) || !Number.isFinite(e.z)) continue;
    const y = (e.ground ?? 0) + (e.cardY ?? 0);
    const v = new THREE.Vector3(e.x, y, e.z).project(cam);
    const sx = (v.x * 0.5 + 0.5) * innerWidth, sy = (-v.y * 0.5 + 0.5) * innerHeight;
    if (!Number.isFinite(sx) || v.z > 1) continue;
    /* card half-size in world metres -> screen, via a second projected point */
    const cw = (e.card && e.card.w) || 26, ch = (e.card && e.card.h) || 12;
    const r = new THREE.Vector3(e.x, y + ch * 0.5, e.z).project(cam);
    const halfH = Math.abs((-r.y * 0.5 + 0.5) * innerHeight - sy);
    const halfW = halfH * (cw / Math.max(1e-3, ch));
    out.push({uid: e.uid, title: e.title, state: e.state ?? e.phase ?? null,
              sx: Math.round(sx), sy: Math.round(sy),
              hw: Math.round(halfW), hh: Math.round(halfH)});
  }
  return out;
};
const boxes = await p.evaluate(BOXFN);

async function measure(tag) {
  const buf = await p.screenshot({type: 'png'});
  fs.writeFileSync(`${dir}floor-${tag}.png`, buf);
  /* recomputed per capture — see the note in `setup` */
  const boxes = await p.evaluate(BOXFN);
  return await p.evaluate(async ({src, boxes}) => {
    const im = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; });
    const cv = document.createElement('canvas'); cv.width = im.width; cv.height = im.height;
    const g = cv.getContext('2d', {willReadFrequently: true}); g.drawImage(im, 0, 0);
    const D = g.getImageData(0, 0, im.width, im.height).data;
    const W = im.width, H = im.height, sc = W / innerWidth;
    const L = (x, y) => { if (x < 0 || y < 0 || x >= W || y >= H) return null;
      const o = (y * W + x) * 4; return 0.2126 * D[o] + 0.7152 * D[o + 1] + 0.0722 * D[o + 2]; };
    const q = (v, t) => { v.sort((x, y) => x - y);
      return +v[Math.min(v.length - 1, Math.floor(v.length * t))].toFixed(1); };
    const boards = boxes.map(bx => {
      const cx = Math.round(bx.sx * sc), cy = Math.round(bx.sy * sc);
      const hw = Math.max(4, Math.round(bx.hw * sc)), hh = Math.max(3, Math.round(bx.hh * sc));
      const inner = [], ring = [];
      for (let y = cy - hh; y <= cy + hh; y++) for (let x = cx - hw; x <= cx + hw; x++) {
        const v = L(x, y); if (v !== null) inner.push(v);
      }
      /* annulus: 1.35x..2.0x the card box, excluding the card box */
      const ow = Math.round(hw * 2.0), oh = Math.round(hh * 2.6);
      const iw = Math.round(hw * 1.15), ih = Math.round(hh * 1.35);
      for (let y = cy - oh; y <= cy + oh; y++) for (let x = cx - ow; x <= cx + ow; x++) {
        if (Math.abs(x - cx) <= iw && Math.abs(y - cy) <= ih) continue;
        const v = L(x, y); if (v !== null) ring.push(v);
      }
      if (!inner.length || !ring.length) return {uid: bx.uid, skipped: true};
      const bL = q([...inner], 0.5), gL = q([...ring], 0.5);
      const bP05 = q([...inner], 0.05), bP95 = q([...inner], 0.95);
      /* "does the board now sit over a value close to its own" — the fraction
       * of the background annulus within 6 L of the card's median. A minimum
       * over the annulus is useless: some pixel always matches. */
      const near = 100 * ring.filter(v => Math.abs(v - bL) < 6).length / ring.length;
      const ringHi = q([...ring], 0.95), ringLo = q([...ring], 0.05);
      return {uid: bx.uid, title: bx.title, state: bx.state,
              n: inner.length, nRing: ring.length,
              boardL: bL, bgL: gL, bgP05: ringLo, bgP95: ringHi,
              weber: +(Math.abs(bL - gL) / Math.max(1, gL)).toFixed(3),
              michelson: +((Math.abs(bL - gL)) / Math.max(1, bL + gL)).toFixed(3),
              textRange: +(bP95 - bP05).toFixed(1),
              nearestBg: +near.toFixed(1)};
    });
    /* chrome: DOM over the canvas edge. Sample the left eighth and the bottom
     * strip, which is where the panel and the legend live. */
    const strip = (x0, y0, x1, y1) => { const v = [];
      for (let y = y0; y < y1; y += 2) for (let x = x0; x < x1; x += 2) { const l = L(x, y); if (l !== null) v.push(l); }
      return {p05: q([...v], 0.05), p50: q([...v], 0.5), p95: q([...v], 0.95)}; };
    return {boards,
      chrome: {leftPanel: strip(0, Math.round(H * 0.15), Math.round(W * 0.13), Math.round(H * 0.85)),
               topBar: strip(0, 0, W, Math.round(H * 0.06)),
               legend: strip(0, Math.round(H * 0.92), W, H)},
      frame: (() => { const v = [];
        for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) v.push(L(x, y));
        return {p01: q([...v], 0.01), p05: q([...v], 0.05), p50: q([...v], 0.5),
                mean: +(v.reduce((s, u) => s + u, 0) / v.length).toFixed(1), p95: q([...v], 0.95)}; })()};
  }, {src: 'data:image/png;base64,' + buf.toString('base64'), boxes});
}

/* The trim that reproduces the PREVIOUS model exactly at whatever hour this
 * page happens to be at — computed from gi's own live state rather than from a
 * remembered number, so the A/B is a true in-session ablation at 09:00, at
 * 16:00 and at the 23:40 the wall clock actually hands the operator.
 *
 *   old ratio  = 0.21 + 0.79 * t^1.5           (the constant that was here)
 *   old want   = max(oldRatio*keyE, (0.15 + cloud*0.30)*civil*day, night)
 *   trim       = old want / new want
 */
const oldTrim = await p.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const cloud = Math.max(0, Math.min(1, w.ctx.weather?.cloud ?? 0.2));
  const clear = gi._fillClearRatio, ratio = gi._fillRatio;
  const t15 = Math.max(0, (ratio - clear) / Math.max(1e-6, 1 - clear));
  const oldRatio = 0.21 + 0.79 * t15;
  const oldWant = Math.max(oldRatio * gi._keyE,
    (0.15 + cloud * 0.30) * gi.civil * gi.dayFactor,
    0.055 * (0.3 + 0.7 * gi.civil));
  return {oldRatio: +oldRatio.toFixed(4), oldWant: +oldWant.toFixed(4),
          newWant: +gi._fillE.toFixed(4), t15: +t15.toFixed(4),
          trim: +(oldWant / gi._fillE).toFixed(4)};
});
if (a.ab) trims.push(oldTrim.trim);

const runs = [];
for (const t of trims) {
  await p.evaluate(k => window.__lemWorld.subsystems.get('gi').setFillTrim(k), t);
  await p.waitForTimeout(4000);
  const st = await p.evaluate(() => { const gi = window.__lemWorld.subsystems.get('gi');
    return {trim: gi._fillTrim, fillE: +gi._fillE.toFixed(4), keyE: +gi._keyE.toFixed(4),
            ratio: +(gi._fillE / gi._keyE).toFixed(4), exp: +gi._expNow.toFixed(4)}; });
  /* /floor is a LIVE page — the cards pulse, the consists move, the hazard
   * strobes. A single capture per state puts more animation noise in the
   * number than the change under test. Repeat and take the median per field. */
  const reps = Math.max(1, parseInt(a.reps || '1', 10));
  const shots = [];
  for (let r = 0; r < reps; r++) { shots.push(await measure(`${t}-${r}`)); if (r < reps - 1) await p.waitForTimeout(900); }
  const pick = (path) => { const v = shots.map(path).filter(x => Number.isFinite(x)).sort((x, y) => x - y);
    return v.length ? +v[v.length >> 1].toFixed(2) : null; };
  const merged = {
    boards: shots[0].boards.map((bd, i) => bd.skipped ? bd : ({
      uid: bd.uid, title: bd.title, state: bd.state,
      boardL: pick(s => s.boards[i].boardL), bgL: pick(s => s.boards[i].bgL),
      bgP05: pick(s => s.boards[i].bgP05), bgP95: pick(s => s.boards[i].bgP95),
      weber: pick(s => s.boards[i].weber), michelson: pick(s => s.boards[i].michelson),
      textRange: pick(s => s.boards[i].textRange), nearestBg: pick(s => s.boards[i].nearestBg),
      spreadWeber: +(Math.max(...shots.map(s => s.boards[i].weber)) -
                     Math.min(...shots.map(s => s.boards[i].weber))).toFixed(3),
    })),
    chrome: shots[0].chrome,
    frame: {p01: pick(s => s.frame.p01), p05: pick(s => s.frame.p05), p50: pick(s => s.frame.p50),
            mean: pick(s => s.frame.mean), p95: pick(s => s.frame.p95)},
    reps,
  };
  runs.push({trim: t, state: st, ...merged});
}
await p.evaluate(() => window.__lemWorld.subsystems.get('gi').setFillTrim(1));
console.log(JSON.stringify({setup, oldTrim,
  boxes: boxes.map(b => ({uid: b.uid, sx: b.sx, sy: b.sy, hw: b.hw, hh: b.hh})),
  runs, errs: errs.slice(0, 4)}, null, 1));
await b.close();
