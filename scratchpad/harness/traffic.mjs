/* traffic.mjs — is the railway actually working, or just safely stopped?
 *
 * The soak proves nothing collides. It cannot tell a correct interlocking from
 * a deadlocked one, because a railway with every train standing still also has
 * no collisions. This drives parses at one layout and reports how many
 * workings completed, how deep the backlog got, and which blocks were held.
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}
const SECONDS = parseInt(args.seconds || '90', 10);

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
const POS = [[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]];

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=terrain,buildings,rail,trains&cam=yard&time=15&hud=0`;
const browser = await chromium.launch({headless: true, channel: 'chromium', args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
page.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.evaluate(([fleet, pos]) => {
  window.__lemWorld.setMachines(fleet.map(([uid, title, status], i) => ({
    machine_uid: uid, title, status, pos: pos[i], reason: 'traffic',
    sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
}, [FLEET, POS]);
await page.waitForTimeout(2500);

await page.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  window.__tr = {arrivals: 0, dispatches: 0, states: {}, maxLive: 0, blocks: new Set(),
                 lines: {}, commons: {}};
  const last = new Map();
  const tick = () => {
    let live = 0;
    for (const c of T.consists) {
      const prev = last.get(c.slot);
      if (prev && prev !== 'idle' && c.state === 'idle') window.__tr.arrivals++;
      if (prev === 'idle' && c.state === 'out') window.__tr.dispatches++;
      last.set(c.slot, c.state);
      window.__tr.states[c.state] = (window.__tr.states[c.state] || 0) + 1;
      if (!c.shunt && c.state !== 'idle') live++;
      if (c.cyc) window.__tr.commons[c.uid] =
        `lap ${c.route.len.toFixed(0)}m rack ${(c.terminal||0).toFixed(0)}m` +
        (c.waiting ? ' WAITING' : '');
      if (c.line) window.__tr.lines[c.uid] = c.line;
    }
    if (live > window.__tr.maxLive) window.__tr.maxLive = live;
    for (const c of T.consists) for (const k of (c.holds || [])) window.__tr.blocks.add(k);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const t0 = Date.now();
let n = 0;
while ((Date.now() - t0) / 1000 < SECONDS) {
  await page.evaluate(uid => window.__lemWorld.parse(uid, 'L-T'), FLEET[n % FLEET.length][0]);
  n++;
  await page.waitForTimeout(400);
}
await page.waitForTimeout(6000);
const out = await page.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  return {...window.__tr, blocks: [...window.__tr.blocks],
          backlog: [...T.backlog.entries()],
          held: T.consists.flatMap(c => [...(c.holds || [])].map(k => k + '=slot' + c.slot)),
          now: T.consists.map(c => ({slot: c.slot, uid: c.uid, st: c.state,
                                     s: +c.s.toFixed(1), v: +c.v.toFixed(2),
                                     line: c.line, holds: [...(c.holds || [])]}))};
});
console.log('parses sent:', n);
console.log(JSON.stringify(out, null, 1));
await browser.close();
