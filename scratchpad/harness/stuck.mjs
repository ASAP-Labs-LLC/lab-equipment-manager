/* stuck.mjs — full precision on whichever working has stopped and should not
 * have. Prints the exact numbers `_stepRun` is comparing. */
import {chromium} from 'playwright';

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--use-angle=metal']});
const p = await b.newPage({viewport: {width: 900, height: 600}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text()))
  errs.push(m.text().slice(0, 300)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=15&hud=0',
             {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.evaluate(() => {
  const F = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
             'pac-flash-1','pac-flash-2','koehler-cp'];
  const pos = [[0,0],[2.05,0],[4.1,0],[6.15,0],[0,2.05],[2.05,2.05],[4.1,2.05]];
  window.__lemWorld.setMachines(F.map((uid, i) => ({
    machine_uid: uid, title: uid, status: 'GREEN', pos: pos[i], reason: 'stuck',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
});
await p.waitForTimeout(3000);
const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
for (let i = 0; i < 200; i++) {
  await p.evaluate(u => window.__lemWorld.parse(u, 'L'), FLEET[i % 7]);
  await p.waitForTimeout(120);
}
await p.waitForTimeout(90000);

const out = await p.evaluate(() => {
  const T = window.__lemWorld.subsystems.get('trains');
  const stuckList = (T.consists || []).filter(c => c && !c.shunt && c.group.visible &&
                                                   c.state !== 'idle' && c.v < 0.05);
  return stuckList.map(c => {
    const h = T._headArc(c);
    const target = c.state === 'back' || c.state === 'hold'
                 ? (c.homeS ?? c.route.len) : c.terminal;
    const a = T._authority(c, target);
    const look = (c.v * c.v) / (2 * 2.8) + 46;
    const want = Math.min(target, c.s + look);
    const permitted = Math.min(want, c.s + (T._authority(c, want).limit - h));
    const goal = Math.min(want, Math.max(c.s, permitted));
    return {
      slot: c.slot, state: c.state, dwell: c.dwell, v: c.v,
      target, onRoad: T._onRoad(c), roadTrack: c.roadTrack,
      tokens: c.tokenIds ? [...c.tokenIds].sort() : null,
      bodySpans: (c.spanIdx || []).filter(sp => sp.b > h - c.length && sp.a < h)
                                  .map(sp => `${sp.id}[${sp.a.toFixed(1)},${sp.b.toFixed(1)}]${sp.junction ? 'J' : ''}`),
      nextHolder: (() => { const R = window.__lemWorld.subsystems.get('rail');
        const nx = (c.spanIdx || []).filter(sp => sp.a >= h)[0];
        return nx ? nx.id + '→' + (R.heldBy(nx.id) || 'free') : null; })(),
      s: c.s, terminal: c.terminal, homeS: c.homeS, L: c.L, len: c.length,
      headArc: h, authLimitToTerminal: a.limit,
      look, want, permitted, goal,
      'goal>=target': goal >= target,
      'target-goal': target - goal,
      driveWouldReturnTrue: c.v <= 0.06 && (goal - c.s) * c.dir < 2.5,
      headSpan: (c.spanIdx || []).filter(sp => sp.a <= h && sp.b > h)
                                 .map(sp => `${sp.id}[${sp.a},${sp.b}]${sp.junction ? 'J' : ''}`),
      nextSpans: (c.spanIdx || []).filter(sp => sp.a > h).slice(0, 3)
                                 .map(sp => `${sp.id}[${sp.a},${sp.b}]${sp.junction ? 'J' : ''}`),
    };
  });
});
console.log(JSON.stringify(out, null, 1));
console.log('errors:', errs.slice(0, 5));
await b.close();
