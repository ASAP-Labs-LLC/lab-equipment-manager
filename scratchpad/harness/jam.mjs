/* jam.mjs — when a working has stood still for a while, ask why.
 *
 * Dumps, for every consist, the block immediately in front of its head and who
 * holds it. A deadlock is a cycle in that relation; a queue is a chain that
 * ends at something which is moving. */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) args[a.slice(2)] = process.argv[i + 1];
}
const LAYOUTS = {
  real: [[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]],
  rank: [[0,0],[2.05,0],[4.1,0],[6.15,0],[8.2,0],[10.25,0],[12.3,0]],
  'two-row': [[0,0],[2.05,0],[4.1,0],[6.15,0],[0,2.05],[2.05,2.05],[4.1,2.05]],
};
const POS = LAYOUTS[args.layout || 'two-row'];

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
await p.evaluate(pos => {
  const F = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
             'pac-flash-1','pac-flash-2','koehler-cp'];
  window.__lemWorld.setMachines(F.map((uid, i) => ({
    machine_uid: uid, title: uid, status: 'GREEN', pos: pos[i], reason: 'jam',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
}, POS);
await p.waitForTimeout(3000);

const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
for (let i = 0; i < parseInt(args.parses || '90', 10); i++) {
  await p.evaluate(u => window.__lemWorld.parse(u, 'L'), FLEET[i % 7]);
  await p.waitForTimeout(140);
}
await p.waitForTimeout(parseInt(args.settle || '12000', 10));

const dump = await p.evaluate(() => {
  const W = window.__lemWorld;
  const T = W.subsystems.get('trains'), R = W.subsystems.get('rail');
  const held = R._held || new Map();
  const headArc = c => { const L = c.L || 1;
    return c.route.closed ? c.s - Math.floor(c.s / L) * L + L : c.s; };
  const rows = (T.consists || []).filter(c => c && c.group.visible && c.route && !c.shunt)
    .map(c => {
      const h = headArc(c);
      const ahead = (c.spanIdx || []).filter(sp => sp.a >= h - 0.01)
                                     .sort((x, y) => x.a - y.a).slice(0, 4)
        .map(sp => `${sp.id}${sp.junction ? 'J' : ''}@${sp.a.toFixed(0)}→${held.get(sp.id) || 'free'}`);
      const mine = [];
      for (const [id, who] of held) if (who === 'train' + c.slot) mine.push(id);
      return {slot: c.slot, uid: c.uid, state: c.state, line: c.line,
              s: +c.s.toFixed(1), v: +c.v.toFixed(2), waiting: !!c.waiting,
              headArc: +h.toFixed(1), terminal: +(c.terminal || 0).toFixed(1),
              roadEnd: +(c.roadEnd || 0).toFixed(1), carried: c.carried || 0,
              holds: mine.sort(), ahead};
    });
  const backlog = [...T.backlog].filter(([, n]) => n > 0).map(([u, n]) => u + ':' + n);
  return {rows, backlog, activeCount: rows.filter(r => r.state !== 'idle').length,
          maxActive: T.maxActive,
          runs: [...(R._runBlocks || new Map())].map(([k, v]) => k + '=' + v.join(',')),
          sections: [...(R._sections || new Map())].map(([k, v]) => k + ':' + v.length)};
});
console.log(JSON.stringify(dump, null, 1));
console.log('errors:', errs.slice(0, 5));
await b.close();
