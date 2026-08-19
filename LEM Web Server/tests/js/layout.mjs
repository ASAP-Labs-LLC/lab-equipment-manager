import fs from 'fs';

// The floor is a rendered 3D world now, and the bay-claiming rule moved with it
// into static/world/index.js. It is still pulled out of the shipped file rather
// than reimplemented here — the whole value of this test is that it runs the
// code the lab actually loads. index.js imports three.js, which node cannot
// resolve, so the function is extracted by text rather than imported.
const src = fs.readFileSync(
  new URL('../../static/world/index.js', import.meta.url), 'utf8');
const at = src.indexOf('export function claimBays(');
if (at === -1) { console.log('FAIL: claimBays() not found'); process.exit(1); }
const body = src.slice(at, src.indexOf('\n}', at) + 2).replace('export ', '');
const layout = new Function(`${body}; return claimBays;`)();

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
const bays = out => out.map(p => `${p.m.title}@${p.gx},${p.gy}`);

// The real floor, including the two machines saved on the SAME bay.
const REAL = [
  {machine_uid: 'b2ce21612b3c', title: 'OptiMPP 1',   pos: [2.05, 0]},
  {machine_uid: '2a49a1320ca1', title: 'OptiMPP 2',   pos: [4.1, 0]},
  {machine_uid: '5fd04c0031f9', title: 'PAC Flash 1', pos: [0, 0]},
  {machine_uid: '7e8304c31983', title: 'PAC Flash 2', pos: [4.1, 0]},
  {machine_uid: '844337a2ba08', title: 'Multitek NS', pos: [4.1, 2.05]},
  {machine_uid: '300f71750e3e', title: 'Multitek S',  pos: [2.05, 2.05]},
];
const clone = a => JSON.parse(JSON.stringify(a));

// 1. the same input gives the same output, every time
const a = bays(layout(clone(REAL)));
for (let i = 0; i < 5; i++) check(`run ${i + 1} is identical`, bays(layout(clone(REAL))), a);

// 2. reordering the payload must not move anything on screen
const shuffles = [
  [...REAL].reverse(),
  [REAL[1], REAL[3], REAL[0], REAL[5], REAL[2], REAL[4]],
  [REAL[5], REAL[4], REAL[3], REAL[2], REAL[1], REAL[0]],
];
shuffles.forEach((order, i) => {
  const got = bays(layout(clone(order))).slice().sort();
  check(`payload order ${i + 1} yields the same bays`, got, a.slice().sort());
});

// 3. two machines on one bay: both must be visible, deterministically
const out = layout(clone(REAL));
const spots = out.map(p => `${p.gx},${p.gy}`);
check('no two machines share a bay', new Set(spots).size, spots.length);

// which one keeps the saved bay must not depend on payload order
const keeper = order => {
  const o = layout(clone(order));
  const at41 = o.find(p => p.gx === 4.1 && p.gy === 0);
  return at41 ? at41.m.title : null;
};
check('the same machine keeps the contested bay', keeper([...REAL].reverse()), keeper(REAL));

// 4. the returned order is fully determined, never a tie left to input order.
// The old SVG floor needed this for its painter's algorithm; the 3D world has a
// depth buffer and does not — but everything downstream still indexes off this
// order, so a wobble here is a wobble in the world.
const paint = o => layout(clone(o)).map(p => p.m.machine_uid);
check('order is stable across payload order', paint([...REAL].reverse()), paint(REAL));

// 5. unplaced machines still get a home, and the same one each time
const withNew = [...REAL, {machine_uid: 'zzz', title: 'Nova', pos: null}];
const n1 = bays(layout(clone(withNew))), n2 = bays(layout(clone([...withNew].reverse())));
check('a new instrument is placed identically regardless of order',
      n1.slice().sort(), n2.slice().sort());
const nova = layout(clone(withNew)).find(p => p.m.title === 'Nova');
check('the new instrument is on the floor', nova.gx !== null && nova.gy !== null, true);

// 6. an existing machine must never be displaced by a newcomer
const before = layout(clone(REAL)).find(p => p.m.title === 'Multitek NS');
const after = layout(clone(withNew)).find(p => p.m.title === 'Multitek NS');
check('a newcomer does not move an existing instrument',
      [after.gx, after.gy], [before.gx, before.gy]);

console.log(fails ? `\n${fails} FAILED` : '\nall layout cases pass');
process.exit(fails ? 1 : 0);
