/* zz-shunt.mjs — find the consist that never goes anywhere and flips around.
 *
 *   node zz-shunt.mjs [--secs 45]
 *
 * Polls every consist at 10Hz and reports, per slot: livery colour, route
 * length, the arc-length range it actually visited, how many times its world
 * heading reversed by more than 90 degrees between samples, and how far it is
 * from the LabCore hub. */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const SECS = parseInt(args.secs || '45', 10);

const url = 'http://127.0.0.1:5601/static/world/dev/solo.html' +
  '?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=16&hud=0' +
  '&quality=ultra';

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(4000);

await page.evaluate(() => {
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains');
  window.__probe = {rows: [], hub: w.plan.hub};
  window.__probeTimer = setInterval(() => {
    for (const c of T.consists) {
      if (!c) continue;
      const h = c.headPos;
      const d = c.loco && c.loco.dirV;
      window.__probe.rows.push({
        slot: c.slot, shunt: !!c.shunt, state: c.state,
        s: +(c.s || 0).toFixed(2), v: +(c.v || 0).toFixed(2),
        vis: !!(c.group && c.group.visible),
        len: c.route ? +c.route.len.toFixed(1) : null,
        clen: +(c.length || 0).toFixed(1),
        uid: c.uid,
        x: h ? +h.x.toFixed(1) : null, z: h ? +h.z.toFixed(1) : null,
        dx: d ? +d.x.toFixed(3) : null, dz: d ? +d.z.toFixed(3) : null,
      });
    }
  }, 100);
  // keep the real railway busy so the shunt is judged against working trains
  const uids = w.plan.stations.map(s => s.uid);
  let i = 0;
  window.__probeParse = setInterval(() => {
    w.parse(uids[i++ % uids.length], 'L-PROBE');
  }, 1500);
});

await page.waitForTimeout(SECS * 1000);

const out = await page.evaluate(() => {
  clearInterval(window.__probeTimer);
  clearInterval(window.__probeParse);
  const w = window.__lemWorld;
  const T = w.subsystems.get('trains');
  const rows = window.__probe.rows;
  const hub = window.__probe.hub;
  const by = new Map();
  for (const r of rows) {
    if (!by.has(r.slot)) by.set(r.slot, []);
    by.get(r.slot).push(r);
  }
  const report = [];
  for (const [slot, list] of by) {
    const c = T.consists.find(x => x.slot === slot);
    const sv = list.filter(r => r.vis).map(r => r.s);
    let flips = 0, jumps = 0, maxJump = 0;
    for (let i = 1; i < list.length; i++) {
      const a = list[i - 1], b = list[i];
      if (a.dx === null || b.dx === null || !a.vis || !b.vis) continue;
      if (a.dx * b.dx + a.dz * b.dz < 0) flips++;
      const ds = b.s - a.s;
      if (ds < -0.25) { jumps++; maxJump = Math.max(maxJump, -ds); }
    }
    const hd = list.filter(r => r.vis && r.x !== null);
    const dhub = hd.length
      ? Math.min(...hd.map(r => Math.hypot(r.x - hub.x, r.z - hub.z)))
      : null;
    report.push({
      slot, shunt: !!(c && c.shunt), states: [...new Set(list.map(r => r.state))],
      visible: list.some(r => r.vis),
      routeLen: list[list.length - 1].len, consistLen: list[0].clen,
      uid: list[list.length - 1].uid,
      sMin: sv.length ? +Math.min(...sv).toFixed(1) : null,
      sMax: sv.length ? +Math.max(...sv).toFixed(1) : null,
      sSpan: sv.length ? +(Math.max(...sv) - Math.min(...sv)).toFixed(1) : null,
      headingFlips: flips, arcJumps: jumps, maxJumpM: +maxJump.toFixed(1),
      minDistToHub: dhub === null ? null : +dhub.toFixed(1),
      locoBody: c && c.loco && c.loco.mesh && c.loco.mesh.material
        ? '#' + c.loco.mesh.material.color.getHexString() : null,
      locoMat: c && c.loco && c.loco.mesh ? c.loco.mesh.material.name || '' : '',
    });
  }
  return {report: report.sort((a, b) => a.slot - b.slot),
          hub: {x: +hub.x.toFixed(1), z: +hub.z.toFixed(1)},
          soakBackwards: (window.__soakStats || {}).backwardsFrames};
});

console.log('hub (LABCORE):', JSON.stringify(out.hub));
console.log('');
for (const r of out.report) {
  console.log(
    `slot ${String(r.slot).padStart(2)}${r.shunt ? ' SHUNT' : '      '}` +
    ` vis=${r.visible ? 'Y' : 'n'}` +
    ` states=${r.states.join(',')}` +
    ` uid=${r.uid || '-'}`);
  console.log(
    `        routeLen=${r.routeLen} consistLen=${r.consistLen}` +
    ` s=[${r.sMin}..${r.sMax}] span=${r.sSpan}` +
    ` headingFlips=${r.headingFlips} arcJumps=${r.arcJumps}` +
    ` maxJump=${r.maxJumpM}m distToHub=${r.minDistToHub}`);
}
console.log('');
if (errors.length) console.log('ERRORS:', errors.slice(0, 4));
await browser.close();
