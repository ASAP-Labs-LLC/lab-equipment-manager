/* sprobe.mjs — is arc length comparable across two consists on one line?
 *
 * soak.mjs's interval test compares a.s against b.s whenever a.line === b.line.
 * That is only meaningful if the two consists measure s from the same origin
 * along the same rail. This asks the world directly. */
import {chromium} from 'playwright';

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--use-angle=metal']});
const p = await b.newPage({viewport: {width: 900, height: 600}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=15&hud=0',
             {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.evaluate(() => {
  const F = [['multitek-ns','Multitek NS'],['multitek-s','Multitek S'],
             ['optimpp-1','OptiMPP 1'],['optimpp-2','OptiMPP 2'],
             ['pac-flash-1','PAC Flash 1'],['pac-flash-2','PAC Flash 2'],
             ['koehler-cp','Koehler CP']];
  const pos = [[0,0],[2.05,0],[4.1,0],[6.15,0],[8.2,0],[10.25,0],[12.3,0]];
  window.__lemWorld.setMachines(F.map(([uid, title], i) => ({
    machine_uid: uid, title, status: 'GREEN', pos: pos[i], reason: 'probe',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
});
await p.waitForTimeout(4000);

const out = await p.evaluate(() => {
  const W = window.__lemWorld;
  const T = W.subsystems.get('trains');
  const at = (r, s) => {
    const L = r.len || 1;
    const u = r.closed ? ((s % L) + L) % L / L : Math.min(1, Math.max(0, s / L));
    return r.getPointAt ? r.getPointAt(u) : null;
  };
  const live = T.consists.filter(c => c && c.group && c.group.visible && c.route);
  const rows = live.map(c => {
    const pt = at(c.route, c.s);
    return {slot: c.slot, uid: c.uid, line: c.line, state: c.state,
            s: +c.s.toFixed(2), len: +c.length.toFixed(1),
            routeLen: +c.route.len.toFixed(1), closed: !!c.route.closed,
            hasGetPointAt: typeof c.route.getPointAt === 'function',
            spans: c.spans ? c.spans.length : null,
            holds: c.holds ? [...c.holds].sort().join(' ') : null,
            x: pt ? +pt.x.toFixed(2) : null, z: pt ? +pt.z.toFixed(2) : null};
  });
  const pairs = [];
  for (let i = 0; i < live.length; i++)
    for (let j = i + 1; j < live.length; j++) {
      const a = live[i], bb = live[j];
      if (a.line !== bb.line) continue;
      const pa = at(a.route, a.s), pb = at(bb.route, bb.s);
      /* Sample both routes at the SAME arc length. If s is a shared coordinate
       * the two samples are the same place; if each consist measures from its
       * own dock they are not. */
      const qa = at(a.route, 300), qb = at(bb.route, 300);
      pairs.push({
        pair: a.slot + '/' + bb.slot, line: a.line,
        sameRouteObject: a.route === bb.route,
        sA: +a.s.toFixed(2), sB: +bb.s.toFixed(2),
        headGap: pa && pb ? +Math.hypot(pa.x - pb.x, pa.z - pb.z).toFixed(2) : null,
        gapAtSameS300: qa && qb ? +Math.hypot(qa.x - qb.x, qa.z - qb.z).toFixed(2) : null,
        lenA: +a.route.len.toFixed(1), lenB: +bb.route.len.toFixed(1),
      });
    }
  const R = W.subsystems.get('rail');
  const sections = [...(R._sections || new Map())].map(([k, v]) => k + ':' + v.length);
  return {rows, pairs, sections, held: [...(R._held || new Map())].length};
});
console.log(JSON.stringify(out, null, 1));
console.log('errors:', errs.slice(0, 5));
await b.close();
