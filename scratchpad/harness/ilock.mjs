/* ilock.mjs — is the interlocking actually connected, and does anything move?
 *
 * The soak says whether trains touch. This says whether the block table is
 * being written by consists at all, which is the thing that was dead. */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) args[a.slice(2)] = process.argv[i + 1];
}
const LAYOUT = args.layout || 'real';
const POS = {
  real: [[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]],
  rank: [[0,0],[2.05,0],[4.1,0],[6.15,0],[8.2,0],[10.25,0],[12.3,0]],
  file: [[0,0],[0,2.05],[0,4.1],[0,6.15],[0,8.2],[0,10.25],[0,12.3]],
}[LAYOUT];

const b = await chromium.launch({headless: true, channel: 'chromium',
                                 args: ['--use-angle=metal']});
const p = await b.newPage({viewport: {width: 900, height: 600}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 300)); });
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains&cam=yard&time=15&hud=0',
             {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.evaluate(pos => {
  const F = [['multitek-ns','Multitek NS'],['multitek-s','Multitek S'],
             ['optimpp-1','OptiMPP 1'],['optimpp-2','OptiMPP 2'],
             ['pac-flash-1','PAC Flash 1'],['pac-flash-2','PAC Flash 2'],
             ['koehler-cp','Koehler CP']];
  window.__lemWorld.setMachines(F.map(([uid, title], i) => ({
    machine_uid: uid, title, status: 'GREEN', pos: pos[i], reason: 'probe',
    sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
    module_running: true, module_state: 'running',
    effective_specs: [], qc_targets: [], maintenance: [],
  })));
}, POS);
await p.waitForTimeout(3000);

const snap = () => p.evaluate(() => {
  const W = window.__lemWorld;
  const R = W.subsystems.get('rail'), T = W.subsystems.get('trains');
  const held = R._held || new Map();
  const byTrain = new Map();
  for (const [blk, who] of held) {
    if (!byTrain.has(who)) byTrain.set(who, []);
    byTrain.get(who).push(blk);
  }
  return {
    sections: [...(R._sections || new Map())].map(([k, v]) => k + ':' + v.length),
    runs: [...(R._runBlocks || new Map())].map(([k, v]) => k + '=' + v.length),
    heldTotal: held.size,
    heldBy: [...byTrain].map(([k, v]) => k + '×' + v.length),
    consists: T.consists.filter(c => !c.shunt && c.group.visible).map(c => ({
      slot: c.slot, uid: c.uid, st: c.state, line: c.line,
      s: +c.s.toFixed(1), len: +c.length.toFixed(1), v: +c.v.toFixed(2),
      spans: c.spans ? c.spans.length : null, holds: c.holds ? c.holds.size : 0,
      dock: +(c.parkS || 0).toFixed(1), roadEnd: +(c.roadEnd || 0).toFixed(1),
      wait: !!c.waiting,
    })),
    backlog: [...T.backlog].filter(([, n]) => n > 0).map(([u, n]) => u + ':' + n),
  };
});

console.log('=== at rest ===');
console.log(JSON.stringify(await snap(), null, 1));

const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
for (let i = 0; i < 40; i++) {
  await p.evaluate(u => window.__lemWorld.parse(u, 'L'), FLEET[i % 7]);
  await p.waitForTimeout(150);
}
await p.waitForTimeout(6000);
console.log('=== after 40 parses + 6s ===');
console.log(JSON.stringify(await snap(), null, 1));
await p.waitForTimeout(25000);
console.log('=== +25s ===');
console.log(JSON.stringify(await snap(), null, 1));
console.log('errors:', JSON.stringify(errs.slice(0, 8), null, 1));
await b.close();
