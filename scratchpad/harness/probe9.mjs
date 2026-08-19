/* probe9.mjs — more benches than consists, and cross-line fouling after a
 * relayout that lands on top of running trains. */
import {chromium} from 'playwright';
const N = parseInt(process.argv[2] || '10', 10);
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0`;
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
const errs = []; page.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3000);

const fleet = [];
for (let i = 0; i < N; i++) fleet.push([`bench-${i}`, `Bench ${i}`, 'GREEN', [(i % 5) * 2.05, Math.floor(i / 5) * 2.05]]);
await page.evaluate(f => window.__lemWorld.setMachines(f.map(([uid, title, status, pos]) => ({
  machine_uid: uid, title, status, pos, reason: 'probe',
  sub_statuses: {qc: status, pm: 'GREEN', calibration: 'GREEN'},
  module_running: true, module_state: 'running',
  effective_specs: [], qc_targets: [], maintenance: [],
}))), fleet);
await page.waitForTimeout(2500);

const slotMap = await page.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  const out = {};
  for (const [uid, slot] of T.slots) out[uid] = slot;
  return {slots: out, nStations: window.__lemWorld.plan.stations.length,
          nConsists: T.consists.length};
});
console.log('slot map:', JSON.stringify(slotMap.slots));
const rev = {};
for (const [u, s] of Object.entries(slotMap.slots)) (rev[s] ||= []).push(u);
const dupes = Object.entries(rev).filter(([, v]) => v.length > 1);
console.log(`stations=${slotMap.nStations} consists=${slotMap.nConsists}`);
console.log('slots shared by >1 bench:', JSON.stringify(dupes));

/* Parse hard at every bench and see whose book drains. */
for (let k = 0; k < 240; k++) {
  await page.evaluate(u => window.__lemWorld.parse(u, 'L-P'), fleet[k % N][0]);
  await page.waitForTimeout(80);
}
await page.waitForTimeout(6000);
const backlog = await page.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  const b = {}; for (const [k, v] of T.backlog) b[k] = v;
  return {backlog: b, consistUids: T.consists.map(c => ({slot: c.slot, uid: c.uid, state: c.state}))};
});
console.log('backlog after 240 parses:', JSON.stringify(backlog.backlog));
console.log('consists:', JSON.stringify(backlog.consistUids));
console.log('pageerrors:', errs.length, errs.slice(0, 3));
await browser.close();
