/* Whole-floor arrangements, run against the shipped function.
 *
 * `arrangement()` is what the Arrange bar's Grid / Compact / Row buttons write
 * to the server, one position per instrument through the same endpoint a drag
 * uses. It is pulled out of static/world/index.js by text rather than imported,
 * because that file imports three.js and node cannot resolve it — the same
 * approach `layout.mjs` takes with `claimBays`.
 *
 * The rule it has to keep is the one the floor has been bitten by before: a
 * layout must be a function of the INSTRUMENT, never of the order the payload
 * happened to arrive in. An arrangement that shuffles when a machine reports is
 * not a place an operator can put things.
 */
import fs from 'fs';

const src = fs.readFileSync(
  new URL('../../static/world/index.js', import.meta.url), 'utf8');
const at = src.indexOf('export function arrangement(');
if (at === -1) { console.log('FAIL: arrangement() not found'); process.exit(1); }
const body = src.slice(at, src.indexOf('\n}', at) + 2).replace('export ', '');
const arrangement = new Function(`${body}; return arrangement;`)();

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

const FLEET = [
  {machine_uid: 'b2ce', title: 'OptiMPP 1'},
  {machine_uid: '2a49', title: 'OptiMPP 2'},
  {machine_uid: '5fd0', title: 'PAC Flash 1'},
  {machine_uid: '7e83', title: 'PAC Flash 2'},
  {machine_uid: '8443', title: 'Multitek NS'},
  {machine_uid: '300f', title: 'Multitek S'},
  {machine_uid: 'aa11', title: 'Koehler CP'},
];
const clone = a => JSON.parse(JSON.stringify(a));

for (const kind of ['grid', 'compact', 'row']) {
  const a = arrangement(clone(FLEET), kind);

  // 1. deterministic
  check(`${kind}: same input, same output`, arrangement(clone(FLEET), kind), a);

  // 2. independent of payload order — the whole point
  const shuffled = [FLEET[3], FLEET[0], FLEET[6], FLEET[1], FLEET[5], FLEET[2], FLEET[4]];
  check(`${kind}: payload order does not change it`,
        arrangement(clone(shuffled), kind), a);

  // 3. every instrument gets a home, and no two share a bay
  const uids = Object.keys(a);
  check(`${kind}: every instrument placed`, uids.length, FLEET.length);
  const spots = Object.values(a).map(p => p.join(','));
  check(`${kind}: no two instruments on one bay`, new Set(spots).size, spots.length);

  // 4. positions are on the bay grid, not between bays
  const BAY = 2.05;
  const offGrid = Object.values(a).filter(
    ([x, y]) => Math.abs((x / BAY) - Math.round(x / BAY)) > 1e-9 ||
                Math.abs((y / BAY) - Math.round(y / BAY)) > 1e-9);
  check(`${kind}: every position lands on a bay`, offGrid, []);
}

// A row is a row: one rank, all on the same y.
const row = arrangement(clone(FLEET), 'row');
check('row is a single rank', new Set(Object.values(row).map(p => p[1])).size, 1);

// Compact should be tighter than grid — that is the reason it exists.
const span = a => {
  const xs = Object.values(a).map(p => p[0]), ys = Object.values(a).map(p => p[1]);
  return (Math.max(...xs) - Math.min(...xs)) + (Math.max(...ys) - Math.min(...ys));
};
check('compact is tighter than grid',
      span(arrangement(clone(FLEET), 'compact')) < span(arrangement(clone(FLEET), 'grid')),
      true);

// An empty lab must not throw — a fresh LabCore has no instruments yet.
check('an empty fleet arranges to nothing', arrangement([], 'grid'), {});

console.log(fails ? `\n${fails} FAILED` : '\nall arrangement cases pass');
process.exit(fails ? 1 : 0);
