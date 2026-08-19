/* catch.mjs — stop on the first real overlap and photograph the interlocking.
 *
 * The soak says two consists overlapped. This says what the block ledger
 * believed at that instant, which is the only way to tell a missing claim from
 * a wrong one. */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) args[a.slice(2)] = process.argv[i + 1];
}
const POS = {
  real: [[0,0],[2.05,0],[4.1,0],[0,2.05],[2.05,2.05],[4.1,2.05],[6.15,0]],
  rank: [[0,0],[2.05,0],[4.1,0],[6.15,0],[8.2,0],[10.25,0],[12.3,0]],
  file: [[0,0],[0,2.05],[0,4.1],[0,6.15],[0,8.2],[0,10.25],[0,12.3]],
}[args.layout || 'rank'];

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

await p.evaluate(() => {
  const W = window.__lemWorld;
  window.__caught = null;
  const snapOne = (c, R) => ({
    slot: c.slot, uid: c.uid, state: c.state, line: c.line,
    s: +c.s.toFixed(2), v: +c.v.toFixed(2), len: +c.length.toFixed(1),
    head: +c.s.toFixed(2), tail: +(c.s - c.length).toFixed(2),
    holds: c.holds ? [...c.holds].sort() : null,
    spansUnderBody: (c.spanIdx || []).filter(sp => {
      const L = c.L || 1;
      const h = c.route.closed ? c.s - Math.floor(c.s / L) * L + L : c.s;
      return !(sp.b <= h - c.length || sp.a >= h);
    }).map(sp => sp.id + '[' + sp.a.toFixed(1) + ',' + sp.b.toFixed(1) + ']' +
                 (sp.junction ? 'J' : '')),
    headArc: (() => { const L = c.L || 1;
      return +(c.route.closed ? c.s - Math.floor(c.s / L) * L + L : c.s).toFixed(2); })(),
  });
  const tick = () => {
    if (!window.__caught) {
      const T = W.subsystems.get('trains'), R = W.subsystems.get('rail');
      const live = (T.consists || []).filter(c => c && c.group && c.group.visible && c.route);
      outer:
      for (let i = 0; i < live.length; i++)
        for (let j = i + 1; j < live.length; j++) {
          const a = live[i], bb = live[j];
          if (!a.line || a.line !== bb.line) continue;
          const aS = a.s - a.length, aE = a.s, bS = bb.s - bb.length, bE = bb.s;
          if (aS < bE - 0.5 && bS < aE - 0.5) {
            /* Ground truth, independent of arc length: walk both bodies in world
             * space and take the closest approach. An interval overlap on a
             * shared route must show up here as metal inside metal; one on two
             * different route objects need not, and that difference is the whole
             * question. */
            const pt = (c, s) => { const L = c.route.len || 1;
              const u = c.route.closed ? (((s % L) + L) % L) / L
                                       : Math.min(1, Math.max(0, s / L));
              return c.route.getPointAt ? c.route.getPointAt(u) : null; };
            let worldMin = Infinity;
            for (let u = 0; u <= 10; u++) {
              const pA = pt(a, a.s - a.length * (u / 10));
              if (!pA) break;
              for (let v = 0; v <= 10; v++) {
                const pB = pt(bb, bb.s - bb.length * (v / 10));
                if (!pB) break;
                const d = Math.hypot(pA.x - pB.x, pA.y - pB.y, pA.z - pB.z);
                if (d < worldMin) worldMin = d;
              }
            }
            window.__caught = {
              sameRouteObject: a.route === bb.route,
              worldMinSeparation: +worldMin.toFixed(3),
              a: snapOne(a, R), b: snapOne(bb, R),
              ledger: [...(R._held || new Map())].sort(),
              runs: [...(R._runBlocks || new Map())].map(([k, v]) => k + '=' + v.join(',')),
            };
            break outer;
          }
        }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const FLEET = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
/* The soak's own layout sequence, because layout 0 was clean and every fault
 * appeared only after the first relayout. */
const BAY = 2.05;
let seed = 12345;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
const SEQ = [POS];
for (let L = 1; L < 10; L++) {
  const kind = L % 4, pos = [];
  for (let i = 0; i < 7; i++) {
    if (kind === 0) pos.push([Math.round(rnd() * 8) * BAY, Math.round(rnd() * 8) * BAY]);
    else if (kind === 1) pos.push([i * BAY, 0]);
    else if (kind === 2) pos.push([0, i * BAY]);
    else pos.push([Math.round(rnd() * 14) * BAY, Math.round(rnd() * 14) * BAY]);
  }
  if (kind === 3) pos[1] = pos[0].slice();
  SEQ.push(pos);
}
outer:
for (const pos of SEQ) {
  await p.evaluate(pp => {
    const F = ['multitek-ns','multitek-s','optimpp-1','optimpp-2',
               'pac-flash-1','pac-flash-2','koehler-cp'];
    window.__lemWorld.setMachines(F.map((uid, i) => ({
      machine_uid: uid, title: uid, status: 'GREEN', pos: pp[i], reason: 'probe',
      sub_statuses: {qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN'},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    })));
  }, pos);
  await p.waitForTimeout(2500);
  for (let i = 0; i < 50; i++) {
    await p.evaluate(u => window.__lemWorld.parse(u, 'L'), FLEET[i % 7]);
    await p.waitForTimeout(120);
    if (await p.evaluate(() => !!window.__caught)) break outer;
  }
  await p.waitForTimeout(4000);
  if (await p.evaluate(() => !!window.__caught)) break;
}
const caught = await p.evaluate(() => window.__caught);
console.log(caught ? JSON.stringify(caught, null, 1) : 'no overlap caught');
console.log('errors:', errs.slice(0, 6));
await b.close();
