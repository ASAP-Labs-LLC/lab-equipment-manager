/* tv-film.mjs — film.mjs, but able to see the thing this round is about.
 *
 * film.mjs records only consists whose state is not `idle`. An overtake is a
 * working passing a train that is NOT moving, so the half of it that matters is
 * exactly the half film.mjs filters out. This records EVERY consist each frame —
 * arc length, line, state — and the true minimum body-to-body distance between
 * every pair, sampled every two metres of both bodies rather than at three
 * points, so a frame can be quoted against soak.mjs's 5.00 m fouling threshold
 * rather than merely looked at.
 *
 *   node tv-film.mjs --out /tmp/pass-rank [--layout 0|1] [--frames 14]
 *                    [--every 900] [--cam yard]
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
const OUT = path.resolve(args.out || '/tmp/tv-film');
const FRAMES = parseInt(args.frames || '14', 10);
const EVERY = parseInt(args.every || '900', 10);
const LAYOUT = parseInt(args.layout || '1', 10);
const W = 1280, H = 720;
fs.mkdirSync(OUT, {recursive: true});

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
const ONE_RANK = FLEET.map((_, i) => [i * 2.05, 0]);

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}${args.at ? '&at=' + args.at : ''}` +
  `&time=${args.time || 16}&weather=clear&hud=0&quality=ultra`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: W, height: H}});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 160)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3500);
if (LAYOUT === 1) {
  await page.evaluate(([fleet, pos]) => window.__lemWorld.setMachines(
    fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'tvfilm',
      sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }))), [FLEET, ONE_RANK]);
  await page.waitForTimeout(4000);
}

await page.evaluate(() => {
  const w = window.__lemWorld;
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__f = setInterval(() => w.parse(uids[i++ % uids.length], 'L-TVFILM'), 800);
});
/* let the rank get busy before the take starts */
await page.waitForTimeout(parseInt(args.warm || '20000', 10));

const SAMPLE = () => {
  const T = window.__lemWorld.subsystems.get('trains');
  const pt = (c, s) => {
    const r = c.route;
    if (!r) return null;
    const len = r.totalLength || r.len || 0;
    if (!len) return null;
    let u = s / len;
    if (r.closed) u -= Math.floor(u);
    return r.getPointAt(Math.min(1, Math.max(0, u)));
  };
  const live = T.consists.filter(c => c && c.group && c.group.visible && c.route);
  const rows = live.map(c => ({
    slot: c.slot, uid: c.uid, state: c.state, s: +c.s.toFixed(1),
    v: +(c.v || 0).toFixed(2), line: c.line, len: +c.length.toFixed(1),
  }));
  const pairs = [];
  for (let i = 0; i < live.length; i++) {
    for (let j = i + 1; j < live.length; j++) {
      const a = live[i], b = live[j];
      if (a.state === 'idle' && b.state === 'idle') continue;
      let d = Infinity;
      for (let x = 0; x <= a.length; x += 2) {
        const pa = pt(a, a.s - x);
        if (!pa) continue;
        for (let y = 0; y <= b.length; y += 2) {
          const pb = pt(b, b.s - y);
          if (!pb) continue;
          const dd = Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z);
          if (dd < d) d = dd;
        }
      }
      if (isFinite(d)) pairs.push({a: a.slot, b: b.slot, m: +d.toFixed(2)});
    }
  }
  pairs.sort((x, y) => x.m - y.m);
  return {t: Math.round(performance.now()), rows, closest: pairs.slice(0, 4)};
};

const track = [];
for (let f = 0; f < FRAMES; f++) {
  await page.waitForTimeout(EVERY);
  const name = `frame-${String(f).padStart(2, '0')}.png`;
  await page.screenshot({path: path.join(OUT, name)});
  const st = await page.evaluate(SAMPLE);
  track.push({frame: f, name, ...st});
  console.log(`${name}  ` + st.rows.map(r =>
    `${r.slot}:${r.state}@${r.s}${r.line && r.line !== 'branch0' ? '[' + r.line + ']' : ''}`).join(' '));
  console.log(`        closest: ` + st.closest.map(p => `${p.a}/${p.b}=${p.m}m`).join('  '));
}
await page.evaluate(() => clearInterval(window.__f));

const cols = Math.ceil(Math.sqrt(FRAMES));
const rows = Math.ceil(FRAMES / cols);
const sheet = await ctx.newPage();
await sheet.setViewportSize({width: cols * 480, height: rows * 285});
const imgs = track.map((t, i) => {
  const b64 = fs.readFileSync(path.join(OUT, t.name)).toString('base64');
  return `<figure><img src="data:image/png;base64,${b64}">` +
         `<figcaption>${i + 1}</figcaption></figure>`;
}).join('');
await sheet.setContent(`<style>body{margin:0;background:#111;display:grid;
  grid-template-columns:repeat(${cols},1fr);gap:2px}figure{margin:0;position:relative}
  img{width:100%;display:block}figcaption{position:absolute;left:5px;top:4px;color:#fff;
  font:700 15px ui-monospace,monospace;text-shadow:0 0 5px #000}</style>${imgs}`);
await sheet.waitForTimeout(600);
await sheet.screenshot({path: OUT + '-sheet.png', fullPage: true});
await sheet.close();
fs.writeFileSync(OUT + '-track.json', JSON.stringify({url, LAYOUT, errors, track}, null, 2));
console.log(`\ncontact sheet: ${OUT}-sheet.png\nper-frame state: ${OUT}-track.json`);
if (errors.length) console.log('ERRORS:', errors.slice(0, 3));
await ctx.close();
await browser.close();
