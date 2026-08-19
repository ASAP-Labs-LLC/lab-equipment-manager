/* rr-look.mjs — frame a named piece of the railway and photograph it.
 *
 * The five solo.html presets cannot look at a tunnel mouth or a bridge pier:
 * they frame the site. This one asks rail.js where its own structures are and
 * puts the camera on them, so a portal can be judged at eye level and from the
 * air rather than as four pixels in a plan view.
 *
 *   node rr-look.mjs --what portal0 --dist 40 --pitch 0.12 --out /tmp/p0.png
 *   node rr-look.mjs --list
 *
 * `--settle` waits for draws+triangles to stop changing, like shot.mjs, and
 * prints `settled`; a frame with settled:false proves nothing.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';

const a = {};
for (let i = 2; i < process.argv.length; i++) {
  if (process.argv[i].startsWith('--')) {
    const k = process.argv[i].slice(2);
    const n = process.argv[i + 1];
    if (!n || n.startsWith('--')) a[k] = true; else { a[k] = n; i++; }
  }
}
const mods = a.mods || 'sky,gi,terrain,rail';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}` +
            `&hud=0&quality=ultra&time=${a.time || 13}`;
const W = parseInt(a.w || '1280', 10), H = parseInt(a.h || '760', 10);

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: W, height: H}});
const errors = [];
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 200)); });
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});

/* Wait for the world to stop populating — draws and triangles both still. */
const settle = await page.evaluate(async () => {
  const w = window.__lemWorld;
  const info = () => { const s = w.stats ? w.stats() : {}; return (s.draws ?? -1) + ':' + (s.triangles ?? -1); };
  let last = '', same = 0, t0 = performance.now();
  while (performance.now() - t0 < 25000) {
    await new Promise(r => setTimeout(r, 400));
    const n = info();
    if (n === last) { same++; if (same >= 6) return {settled: true, ms: Math.round(performance.now() - t0), state: n}; }
    else { same = 0; last = n; }
  }
  return {settled: false, ms: Math.round(performance.now() - t0), state: last};
});

const feats = await page.evaluate(() => {
  const rail = window.__lemWorld.subsystems.get('rail');
  const out = [];
  const at = (t, s) => {
    const f = t.frames;
    const i = Math.max(0, Math.min(f.count - 1, Math.round(s / f.step)));
    return {x: f.pos[i * 3], y: f.pos[i * 3 + 1], z: f.pos[i * 3 + 2],
            tx: f.tan[i * 3], tz: f.tan[i * 3 + 2]};
  };
  for (const t of rail.tracks) {
    if (!t.frames) continue;
    try { t.earthworks(); } catch { /* ignore */ }
    for (const [i, [s0, s1]] of (t.bores || []).entries()) {
      out.push({name: `portal:${t.name}:${i}a`, ...at(t, s0), s: s0,
                inDrawn: s0 >= (t.renderFrom || 0) && s0 <= Math.min(t.renderTo, t.length)});
      out.push({name: `portal:${t.name}:${i}b`, ...at(t, s1), s: s1,
                inDrawn: s1 >= (t.renderFrom || 0) && s1 <= Math.min(t.renderTo, t.length)});
    }
    for (const [i, [s0, s1]] of (t.decks || []).entries()) {
      out.push({name: `deck:${t.name}:${i}`, ...at(t, (s0 + s1) / 2), s: (s0 + s1) / 2,
                len: s1 - s0, inDrawn: true});
    }
  }
  /* the two ends of the trunk — the headshunts */
  if (rail.trunk?.frames) {
    const t = rail.trunk;
    out.push({name: 'headshunt:start', ...at(t, t.renderFrom || 0), s: 0, inDrawn: true});
    out.push({name: 'headshunt:end', ...at(t, Math.min(t.renderTo, t.length)), s: t.length, inDrawn: true});
  }
  return out;
});

if (a.list) {
  for (const f of feats) console.log(f.name.padEnd(26),
    `(${f.x.toFixed(0)}, ${f.y.toFixed(1)}, ${f.z.toFixed(0)}) s=${f.s.toFixed(1)}` +
    (f.len ? ` len=${f.len.toFixed(1)}` : '') + (f.inDrawn ? '' : '   [OUTSIDE DRAWN TRACK]'));
  console.log(JSON.stringify({settle, errors}));
  await browser.close();
  process.exit(0);
}

const want = String(a.what || '');
const f = feats.find(q => q.name === want) || feats.find(q => q.name.includes(want));
if (!f) { console.error('no such feature; --list to see them'); await browser.close(); process.exit(2); }

/* Look along the track by default — a portal photographed from the side shows
 * nothing about whether the bore reads as a hole. */
const yaw = a.yaw !== undefined ? parseFloat(a.yaw)
          : Math.atan2(-f.tx, -f.tz) + (a.back ? Math.PI : 0);
const pitch = parseFloat(a.pitch ?? '0.14');
const dist = parseFloat(a.dist ?? '46');
const lift = parseFloat(a.lift ?? '2');

await page.evaluate(({x, y, z, yaw, pitch, dist, lift}) => {
  const w = window.__lemWorld;
  w.rig.idleDrift = false;
  w.rig.goalTarget.set(x, y + lift, z);
  w.rig.target.set(x, y + lift, z);
  w.rig.goalYaw = yaw; w.rig.goalPitch = pitch; w.rig.goalDistance = dist;
  w.rig.minDistance = 2;
  w.rig.apply(1);
}, {x: f.x, y: f.y, z: f.z, yaw, pitch, dist, lift});
await page.waitForTimeout(1400);

const out = a.out || '/tmp/rr-look.png';
fs.mkdirSync(out.replace(/\/[^/]*$/, ''), {recursive: true});
await page.screenshot({path: out});
console.log(JSON.stringify({feature: f.name, at: [+f.x.toFixed(1), +f.y.toFixed(1), +f.z.toFixed(1)],
                            inDrawn: f.inDrawn, yaw: +yaw.toFixed(3), pitch, dist,
                            settled: settle.settled, settledMs: settle.ms,
                            errors, out}));
await browser.close();
