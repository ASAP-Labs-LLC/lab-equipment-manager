/* shunt.mjs — the yard trip claims no blocks. Does it stand on any?
 *
 * Rule one says every consist claims what it occupies. The shunt is the one
 * that does not, because it has no circuit and therefore no block table. That
 * is only defensible if the rail it runs on is rail no working can be on, so
 * this measures it two ways: which track its route lies on, and how close it
 * ever comes to a working in world space. */
import {chromium} from 'playwright';

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--use-angle=metal']});
const p = await b.newPage({viewport: {width: 900, height: 600}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 300)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=15&hud=0',
             {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.evaluate(() => {
  const F = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
             'pac-flash-1','pac-flash-2','koehler-cp'];
  const pos = [[0,0],[2.05,0],[4.1,0],[6.15,0],[0,2.05],[2.05,2.05],[4.1,2.05]];
  window.__lemWorld.setMachines(F.map((uid, i) => ({
    machine_uid: uid, title: uid, status: 'GREEN', pos: pos[i], reason: 'shunt',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
});
await p.waitForTimeout(3000);
await p.evaluate(() => {
  const W = window.__lemWorld;
  window.__sh = {min: Infinity, at: null, samples: 0, shuntSeen: false};
  const pt = (c, s) => { const L = c.route.len || 1;
    const u = c.route.closed ? (((s % L) + L) % L) / L : Math.min(1, Math.max(0, s / L));
    return c.route.getPointAt ? c.route.getPointAt(u) : null; };
  const tick = () => {
    const T = W.subsystems.get('trains');
    const all = (T?.consists || []).filter(c => c && c.group.visible && c.route);
    const sh = all.filter(c => c.shunt), work = all.filter(c => !c.shunt);
    if (sh.length) window.__sh.shuntSeen = true;
    for (const s of sh) for (const w of work) {
      window.__sh.samples++;
      for (let u = 0; u <= 8; u++) {
        const pa = pt(s, s.s - s.length * (u / 8)); if (!pa) break;
        for (let v = 0; v <= 8; v++) {
          const pb = pt(w, w.s - w.length * (v / 8)); if (!pb) break;
          const d = Math.hypot(pa.x - pb.x, pa.y - pb.y, pa.z - pb.z);
          if (d < window.__sh.min) {
            window.__sh.min = +d.toFixed(2);
            window.__sh.at = `shunt slot ${s.slot} vs slot ${w.slot} (${w.state}) on ${w.line}`;
          }
        }
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
for (let i = 0; i < 250; i++) {
  await p.evaluate(u => window.__lemWorld.parse(u, 'L'), FLEET[i % 7]);
  await p.waitForTimeout(120);
}
await p.waitForTimeout(20000);
const out = await p.evaluate(() => ({
  ...window.__sh,
  yardRouteExists: (() => { try { return !!window.__lemWorld.subsystems.get('rail').yardRoute(); }
                            catch { return 'threw'; } })(),
  sections: [...(window.__lemWorld.subsystems.get('rail')._sections || new Map())]
              .map(([k, v]) => k + ':' + v.length),
  everHeld: [...(window.__lemWorld.subsystems.get('rail')._held || new Map())]
              .filter(([k]) => k.startsWith('yard.')).map(([k, v]) => k + '=' + v),
}));
console.log(JSON.stringify(out, null, 1));
console.log('errors:', errs.slice(0, 5));
await b.close();
