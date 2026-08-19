/* zz-queue.mjs — does a train boxed in behind a standing one ever get out?
 *
 *   node zz-queue.mjs [--secs 180] [--every 4000]
 *
 * The operator: "There's no way for a train to get out (without clipping
 * through) if the station in front of it doesn't move."
 *
 * Two different questions hide in that, and they have different answers:
 *
 *   TRAFFIC   does every bench's booked work eventually get carried? If a
 *             bench's backlog grows without bound, the railway is starving it.
 *   VEHICLES  does every locomotive eventually leave its stand, or do the ones
 *             at the back of a loading road stand for ever?
 *
 * Parses are fired at ONE bench only by default — the one deepest in the queue,
 * i.e. furthest from its road's exit turnout — because that is the operator's
 * exact case: the station in front of it is not moving, so does its work move?
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const SECS = parseInt(args.secs || '180', 10);
const REPORT = parseInt(args.every || '4000', 10);
const MODE = args.mode || 'deepest';   // deepest | all

const url = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
  '?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&hud=0' +
  '&time=16&quality=ultra';

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(4500);

const setup = await page.evaluate((mode) => {
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains');
  /* The bench deepest in its road's queue: smallest dockS, i.e. the most
   * railway in front of it before the exit turnout. */
  const rows = [];
  for (const c of T.consists) {
    if (!c.uid || !c.docks?.length) continue;
    rows.push({slot: c.slot, uid: c.uid, s: +c.s.toFixed(1),
               lastDock: +c.lastDock.toFixed(1),
               road: c.roadTrack,
               behind: +(c.lastDock - c.s).toFixed(1)});
  }
  rows.sort((a, b) => b.behind - a.behind);
  const target = rows.length ? rows[0] : null;
  window.__q = {
    departures: new Map(), target,
    firstMove: new Map(), t0: performance.now(),
  };
  const uids = mode === 'all'
    ? w.plan.stations.map(s => s.uid)
    : [target ? target.uid : w.plan.stations[0].uid];
  let i = 0;
  window.__qParse = setInterval(() => {
    w.parse(uids[i++ % uids.length], 'L-QUEUE');
  }, 2500);
  /* count a departure each time a consist leaves idle */
  const prev = new Map();
  window.__qWatch = setInterval(() => {
    for (const c of T.consists) {
      const was = prev.get(c.slot);
      if (was === 'idle' && c.state !== 'idle') {
        window.__q.departures.set(c.slot,
          (window.__q.departures.get(c.slot) || 0) + 1);
        if (!window.__q.firstMove.has(c.slot)) {
          window.__q.firstMove.set(c.slot,
            Math.round(performance.now() - window.__q.t0) / 1000);
        }
      }
      prev.set(c.slot, c.state);
    }
  }, 60);
  return {rows, target, uids};
}, MODE);

console.log('road queue at t=0 (behind = metres from the exit-end stand):');
for (const r of setup.rows) {
  console.log(`  slot ${r.slot} uid=${r.uid} road=${r.road} s=${r.s} ` +
              `lastDock=${r.lastDock} behind=${r.behind}`);
}
console.log(`\nfiring parses at: ${setup.uids.join(', ')}  (mode=${MODE})\n`);

const t0 = Date.now();
while (Date.now() - t0 < SECS * 1000) {
  await page.waitForTimeout(REPORT);
  const s = await page.evaluate(() => {
    const w = window.__lemWorld;
    const T = w.subsystems.get('trains');
    return {
      t: Math.round((performance.now() - window.__q.t0) / 1000),
      backlog: [...T.backlog.entries()].map(([u, n]) => `${u}:${n}`).join(' '),
      maxActive: T.maxActive, roads: T.roads,
      active: T.consists.filter(c => c.state !== 'idle').length,
      departures: [...window.__q.departures.entries()]
        .sort((a, b) => a[0] - b[0]).map(([k, v]) => `${k}:${v}`).join(' '),
      states: T.consists.map(c => `${c.slot}${c.state[0]}`).join(' '),
    };
  });
  console.log(`t=${String(s.t).padStart(3)}s active=${s.active}/${s.maxActive} ` +
    `roads=${s.roads} | backlog ${s.backlog || '(empty)'} | departures ${s.departures || '(none)'}`);
}

const fin = await page.evaluate(() => {
  clearInterval(window.__qParse); clearInterval(window.__qWatch);
  const T = window.__lemWorld.subsystems.get('trains');
  return {
    firstMove: [...window.__q.firstMove.entries()].sort((a, b) => a[0] - b[0]),
    departures: [...window.__q.departures.entries()].sort((a, b) => a[0] - b[0]),
    backlog: [...T.backlog.entries()],
    seated: T.consists.filter(c => c.uid).length,
    never: T.consists.filter(c => c.uid && !window.__q.departures.has(c.slot))
                     .map(c => c.slot),
    target: window.__q.target,
  };
});
console.log('\n=== result ===');
console.log('target bench (deepest in its queue):', JSON.stringify(fin.target));
console.log('seated consists:', fin.seated);
console.log('departures per slot:', JSON.stringify(fin.departures));
console.log('first departure at (s):', JSON.stringify(fin.firstMove));
console.log('NEVER departed:', JSON.stringify(fin.never));
console.log('final backlog:', JSON.stringify(fin.backlog));
if (errors.length) console.log('ERRORS:', errors.slice(0, 4));
await browser.close();
