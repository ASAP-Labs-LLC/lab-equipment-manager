/* traincycle.mjs — watch a full train working, and cost what one train is.
 *
 * Four rounds have been lost to changes that were reported as done and were
 * not in the frame. This does not screenshot: it reads the running world's own
 * state every 250ms and prints the timeline, so "a train left, reached the
 * terminal and came back" is a measurement and not an impression.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[a.slice(2)] = true; else { args[a.slice(2)] = n; i++; }
}
const url = args.url;
const secs = parseFloat(args.seconds || '40');

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
const errors = [];
page.on('console', m => {
  if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 300));
});
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});
await page.waitForTimeout(800);

const layout = await page.evaluate(() => {
  const w = window.__lemWorld;
  const rail = w.subsystems.get('rail');
  const tr = w.subsystems.get('trains');
  const tracks = (rail?.tracks || []).map(t => ({
    n: t.name, len: Math.round(t.length),
    a: t.frames ? [Math.round(t.frames.pos[0]), Math.round(t.frames.pos[2])] : null,
    b: t.frames ? [Math.round(t.frames.pos[(t.frames.count - 1) * 3]),
                   Math.round(t.frames.pos[(t.frames.count - 1) * 3 + 2])] : null,
  }));
  const cycles = [];
  for (const s of w.plan.stations) {
    let c = null;
    try { c = rail?.cycle?.(s.uid) || null; } catch (e) { c = {err: String(e)}; }
    const r = rail?.route?.(s.uid);
    cycles.push({uid: s.uid, out: r ? Math.round(r.length) : null,
                 cycle: c && c.route ? Math.round(c.route.length) : null,
                 terminal: c && c.terminal != null ? Math.round(c.terminal) : null,
                 turned: c ? !!c.turned : null, line: c ? c.line : null});
  }
  return {tracks, cycles, consists: tr?.consists?.length ?? 0,
          slots: tr ? [...tr.slots.entries()] : []};
});

const samples = [];
const t0 = Date.now();
while (Date.now() - t0 < secs * 1000) {
  const s = await page.evaluate(() => {
    const w = window.__lemWorld;
    const tr = w.subsystems.get('trains');
    if (!tr) return null;
    return {
      t: +(performance.now() / 1000).toFixed(2),
      c: tr.consists.map(c => ({
        i: c.slot, st: c.state, s: Math.round(c.s || 0), v: +(c.v || 0).toFixed(1),
        vis: !!c.group.visible, uid: c.uid ? c.uid.slice(0, 9) : null,
        len: c.route ? Math.round(c.route.len) : null,
      })),
      stats: w.stats(),
    };
  });
  if (s) samples.push(s);
  await page.waitForTimeout(250);
}
await browser.close();

console.log(JSON.stringify(layout, null, 1));
/* Print only the consists that did anything, as a timeline. */
const seen = new Map();
for (const s of samples) {
  for (const c of s.c) {
    if (c.st === 'idle' && !c.vis) continue;
    if (!seen.has(c.i)) seen.set(c.i, []);
    const row = seen.get(c.i);
    const last = row[row.length - 1];
    if (!last || last.st !== c.st || Math.abs(last.s - c.s) > 25) {
      row.push({t: s.t, st: c.st, s: c.s, v: c.v, uid: c.uid, len: c.len});
    }
  }
}
for (const [i, row] of seen) {
  console.log('consist', i, JSON.stringify(row));
}
const last = samples[samples.length - 1];
console.log('stats', JSON.stringify(last?.stats));
console.log('errors', JSON.stringify(errors.slice(0, 8)));
