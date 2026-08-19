/* pwjourney.mjs — how long a working takes from parse to discharge, on the
 * lab's own layout. The soak's liveness assertion gives each layout 52s and
 * counts transitions into `discharge`; if a journey no longer fits in that
 * window the gate reports a dead railway whatever the track is doing, so the
 * number has to be observable on its own. Owned by rail. */
import {chromium} from 'playwright';
const UIDS = ['multitek-ns', 'multitek-s', 'optimpp-1', 'optimpp-2',
              'pac-flash-1', 'pac-flash-2', 'koehler-cp'];
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 800, height: 500}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=rail,trains&time=13&hud=0',
             {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(2500);
await p.evaluate(() => {
  const w = window.__lemWorld, tr = w.subsystems.get('trains');
  window.__j = {t0: performance.now(), first: null, arrivals: 0, seen: new Map()};
  setInterval(() => {
    const j = window.__j;
    for (const c of tr.consists) {
      if (c.shunt) continue;
      const was = j.seen.get(c.slot);
      if (was !== c.state && /discharge/i.test(String(c.state))) {
        j.arrivals++;
        if (j.first === null) j.first = (performance.now() - j.t0) / 1000;
      }
      j.seen.set(c.slot, c.state);
    }
  }, 40);
});
await p.evaluate(us => us.forEach(u => window.__lemWorld.parse(u, 'J')), UIDS);
await p.waitForTimeout(75000);
const r = await p.evaluate(() => {
  const w = window.__lemWorld, rail = w.subsystems.get('rail');
  const lens = [];
  for (const s of w.plan.stations) {
    const c = rail.cycle(s.uid);
    if (c) lens.push({uid: s.uid, circuit: Math.round(c.route.length),
                      toStand: Math.round(c.terminal - (c.dockS || 0))});
  }
  return {...window.__j, seen: undefined, lens};
});
console.log('first discharge at', r.first, 's; arrivals in 75s:', r.arrivals);
console.log(JSON.stringify(r.lens));
await b.close();
