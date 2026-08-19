/* zz-lamps.mjs — how many locomotive headlights actually light the world.
 *
 *   node zz-lamps.mjs [--time 21] [--quality ultra]
 *
 * Reports, per quality tier, gi's pool size, how many requests exist, how many
 * of them are trains, and how many train lamps won a slot — plus whether the
 * additive lens is up on the ones that did not. */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const TIME = args.time || '21';
const TIERS = (args.tiers || 'ultra,high,medium,low,floor').split(',');

const mkUrl = t => 'http://127.0.0.1:5601/static/world/dev/solo.html' +
  '?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&hud=0' +
  `&time=${TIME}&quality=${t}`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 160)); });
for (const tier of TIERS) {
  await page.goto(mkUrl(tier), {waitUntil: 'load', timeout: 60000});
  await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
  await page.waitForTimeout(4000);
  await page.evaluate(() => {
    const w = window.__lemWorld;
    const uids = w.plan.stations.map(s => s.uid);
    let i = 0;
    window.__lampParse = setInterval(() => w.parse(uids[i++ % uids.length], 'L-LAMP'), 1100);
  });
  await page.waitForTimeout(9000);
  const r = await page.evaluate(async (t) => {
    const w = window.__lemWorld;
    void t;
    const gi = w.subsystems.get('gi');
    const T = w.subsystems.get('trains');
    const pool = gi ? gi._pool.length : -1;
    const reqs = gi ? [...gi._lightRequests.values()] : [];
    const rows = T.consists.map(c => ({
      slot: c.slot, state: c.state, v: +(c.v || 0).toFixed(2),
      vis: !!c.group.visible,
      hasLamp: !!c.lamp,
      active: !!(c.lamp && c.lamp.active),
      pri: c.lampPri, int: +(c.lampInt || 0).toFixed(2),
      glow: !!(c.glow && c.glow.visible),
      glowOp: c.glow ? +c.glow.material.opacity.toFixed(3) : null,
    }));
    const trainReqs = T.consists.filter(c => c.lamp).map(c => c.lamp.id);
    return {
      tier: t, pool, requests: reqs.length,
      liveRequests: reqs.filter(x => x.live).length,
      trainRequests: trainReqs.length,
      trainLive: rows.filter(x => x.active).length,
      artificialFactor: gi ? +gi.artificialFactor.toFixed(3) : null,
      night: +T.night.toFixed(3),
      consists: T.consists.length,
      states: [...new Set(rows.map(x => x.state))],
      rows,
      draws: w.stats().drawCalls, tris: w.stats().triangles, fps: w.stats().fps,
    };
  }, tier);
  console.log(`\n--- ${r.tier} --- pool=${r.pool} requests=${r.requests} ` +
    `live=${r.liveRequests} trainReq=${r.trainRequests} trainLive=${r.trainLive} ` +
    `artificialFactor=${r.artificialFactor} night=${r.night} ` +
    `consists=${r.consists} states=${r.states.join(',')} ` +
    `draws=${r.draws} tris=${r.tris} fps=${r.fps}`);
  for (const x of r.rows) {
    console.log(`   slot ${x.slot} ${String(x.state).padEnd(9)} v=${String(x.v).padStart(6)}` +
      ` vis=${x.vis ? 'Y' : 'n'} lamp=${x.hasLamp ? 'Y' : 'n'}` +
      ` ACTIVE=${x.active ? 'YES' : ' no'} pri=${x.pri} int=${x.int}` +
      ` glow=${x.glow ? 'Y' : 'n'} op=${x.glowOp}`);
  }
}
console.log('');
if (errors.length) console.log('ERRORS:', errors.slice(0, 5));
else console.log('no console errors');
await browser.close();
