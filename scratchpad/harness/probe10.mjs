import {chromium} from 'playwright';
const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=yard&time=15&hud=0`;
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const page = await browser.newPage({viewport: {width: 1280, height: 720}});
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(3500);

const dump = async (tag) => {
  const d = await page.evaluate(() => {
    const T = window.__lemWorld.subsystems.get('trains');
    const blocks = []; for (const [k, v] of (T.blocks || [])) blocks.push(k + '->slot' + v.slot);
    const live = new Set((window.__lemWorld.plan?.stations || []).map(s => s.uid));
    return {
      maxActive: T.maxActive, active: T.consists.filter(c => !c.shunt && c.state !== 'idle').length,
      blocks,
      consists: T.consists.map(c => ({
        slot: c.slot, uid: c.uid, onFloor: c.uid ? live.has(c.uid) : null,
        state: c.state, s: +(c.s || 0).toFixed(1), v: +(c.v || 0).toFixed(2),
        laden: +(c.laden ?? -1).toFixed(3), cooldown: +(c.cooldown ?? -1).toFixed(2),
        waiting: !!c.waiting, line: c.line, blockLine: c.blockLine,
        holds: c.holds ? [...c.holds] : null,
      })),
      backlog: Object.fromEntries(T.backlog || []),
    };
  });
  console.log('---', tag, '---');
  console.log('maxActive', d.maxActive, 'active', d.active, 'blocks', JSON.stringify(d.blocks));
  for (const c of d.consists) console.log('  ', JSON.stringify(c));
  console.log('  backlog', JSON.stringify(d.backlog));
};

await dump('boot');
const N = 10, fleet = [];
for (let i = 0; i < N; i++) fleet.push([`bench-${i}`, `Bench ${i}`, [(i % 5) * 2.05, Math.floor(i / 5) * 2.05]]);
/* Parse the boot fleet first so trains are OUT when the relayout lands. */
const boot = await page.evaluate(() => (window.__lemWorld.plan?.stations || []).map(s => s.uid));
for (const u of boot.slice(0, 4)) await page.evaluate(x => window.__lemWorld.parse(x, 'L'), u);
await page.waitForTimeout(1500);
await dump('boot fleet running');
await page.evaluate(f => window.__lemWorld.setMachines(f.map(([uid, title, pos]) => ({
  machine_uid: uid, title, status: 'GREEN', pos, reason: 'probe',
  sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
  module_running: true, module_state: 'running',
  effective_specs: [], qc_targets: [], maintenance: [],
}))), fleet);
await page.waitForTimeout(2500);
await dump('immediately after hot relayout');
for (let k = 0; k < 120; k++) {
  await page.evaluate(u => window.__lemWorld.parse(u, 'L'), fleet[k % N][0]);
  await page.waitForTimeout(80);
}
await page.waitForTimeout(20000);
await dump('after 120 parses + 20s settle');
await browser.close();
