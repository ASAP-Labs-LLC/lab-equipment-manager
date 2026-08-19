/* cyclewatch.mjs — one working, timed end to end.
 *
 * Picks the consist that leaves first and follows only that one, printing every
 * state change with a clock. A round trip is a claim that has to be measured,
 * not eyeballed: departure, arrival at the rack, discharge, the loop, and the
 * moment it is standing back in the loop it started in.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[a.slice(2)] = true; else { args[a.slice(2)] = n; i++; }
}
const secs = parseFloat(args.seconds || '150');
const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({viewport: {width: 960, height: 540}});
const errors = [];
page.on('console', m => {
  if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 300));
});
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 300)));
await page.goto(args.url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 45000});

await page.evaluate(() => {
  const w = window.__lemWorld;
  const tr = w.subsystems.get('trains');
  window.__log = [];
  window.__prev = new Map();
  const t0 = performance.now();
  setInterval(() => {
    const now = (performance.now() - t0) / 1000;
    for (const c of tr.consists) {
      if (c.shunt) continue;
      const key = c.slot;
      const st = c.state + (c.waiting ? '/wait' : '');
      if (window.__prev.get(key) !== st) {
        window.__prev.set(key, st);
        window.__log.push({t: +now.toFixed(1), slot: key, uid: c.uid, st,
                           s: Math.round(c.s), len: c.route ? Math.round(c.route.len) : null,
                           laden: +(c.laden ?? 1).toFixed(2), pend: c.pending});
      }
    }
  }, 120);
});
await page.waitForTimeout(secs * 1000);
const out = await page.evaluate(() => {
  const w = window.__lemWorld;
  const tr = w.subsystems.get('trains');
  const rail = w.subsystems.get('rail');
  return {log: window.__log, stats: w.stats(),
          aspects: (rail?.signals || []).map(s => s.key + '=' + s.shown),
          standing: tr.consists.filter(c => !c.shunt).map(
            c => `${c.slot}:${c.uid}:${c.state}:${c.group.visible}`)};
});
await browser.close();
for (const r of out.log) console.log(JSON.stringify(r));
console.log('STATS', JSON.stringify(out.stats));
console.log('STANDING', JSON.stringify(out.standing));
console.log('ASPECTS', JSON.stringify(out.aspects));
console.log('ERRORS', JSON.stringify(errors.slice(0, 6)));
