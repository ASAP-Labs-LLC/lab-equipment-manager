// Readings (/checklists/trends), the logic half (static/js/readings.js).
//
// * `?item=<uid>` lands on the chart that item feeds. For a tracked item that
//   is its THING's chart, which no item uid names directly, so the card lists
//   the items that feed it. Landing nowhere is said, not silently ignored.
// * The text summary every chart carries (ia-final §9.1 rule 5) counts
//   readings outside the limits ONLY when someone set limits: without them it
//   says nothing about in or out, because LEM does not invent a band (A.6).
// * The y range always holds the limits, so a limit line is never drawn off
//   the chart, and a flat series still has height.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/readings.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const L = root.LEMReadingsLogic;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

const trends = [
  { tracked_uid: 't1', item_uid: '', item_uids: ['open-he', 'close-he'], text: 'Helium' },
  { item_uid: 'bath', item_uids: ['bath'], text: 'Bath' },
];
check('an item that feeds a tracked thing lands on the thing', L.target(trends, 'close-he'), 't1');
check('an untracked item lands on itself', L.target(trends, 'bath'), 'bath');
check('the thing uid works too', L.target(trends, 't1'), 't1');
check('an unknown item lands nowhere (and the page says so)', L.target(trends, 'gone'), '');

const pts = [{ day: '2026-09-29', value: 2900 }, { day: '2026-09-30', value: 450 }];
check('with limits, the summary counts what is outside',
  L.summary({ points: pts, units: 'PSI', min: 500, max: 3000 }),
  '2 readings, 1 outside the limits, last 450 PSI on 30 Sep.');
check('without limits it says nothing about in or out',
  L.summary({ points: pts, units: 'PSI' }), '2 readings, last 450 PSI on 30 Sep.');
check('never written is its own sentence', L.summary({ points: [] }), 'Never written.');

const [lo, hi] = L.yRange([2900, 2950], 500, 3000);
check('the range holds the limits', lo < 500 && hi > 3000, true);
const [a, b] = L.yRange([7, 7], null, null);
check('a flat series still has height', b > a, true);

console.log(fails ? `\n${fails} FAILED` : '\nall readings cases pass');
process.exit(fails ? 1 : 0);
