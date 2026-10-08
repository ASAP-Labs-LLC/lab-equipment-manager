// QC's pure logic (static/js/quality_logic.js): the New standard sheet and
// the standard page's Check it on.
//
// Why these are node tests and not only a browser walk:
//
// * "Check it on" puts the instruments that already report the test first
//   (§3.5, from B). The point is the T5 walk: the lab adds a Flash Point lot
//   and the four flash testers are under the thumb, not alphabetised among
//   seventeen. The order is a rule, so it is checked as one.
// * The test comes from LabCore's catalogue, the only legal test names
//   (CLAUDE.md). The type-ahead ranks "Flash" so the Flash Point methods
//   come first, and a name typed but not PICKED is not a test: Save stays
//   refused until one is picked, in words.
// * A standard's band is expected ± k·s. The sheet says the band it will
//   judge by before Save, in fmtQC's decimals (§4.2), so a typo in s shows as
//   an absurd band on screen, not as a month of false passes.
// * Every refusal is a sentence that says what to do.
import fs from 'fs';

const load = (f) => fs.readFileSync(new URL('../../static/js/' + f, import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', load('instruments_logic.js'))(root, undefined);
new Function('window', 'module', load('record_logic.js'))(root, undefined);
new Function('window', 'module', load('quality_logic.js'))(root, undefined);
const Q = root.LEMQuality;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
if (!Q) { console.log('  FAIL quality_logic.js does not define window.LEMQuality'); process.exit(1); }

// ── Check it on: who reports the test comes first ──────────────────────────
const fleet = [
  { uid: 'g1', title: 'Agilent GC 1' }, { uid: 'g2', title: 'Agilent GC 2' },
  { uid: 'p2', title: 'PAC Flash 2' }, { uid: 'ag', title: 'Auto Grabner' },
  { uid: 'p1', title: 'PAC Flash 1' }, { uid: 'mg', title: 'Mini Grabner' },
];
const reporting = { 'astm d7236/d7094 - flash point closed cup (small scale)': ['ag', 'mg', 'p1', 'p2'] };
const fp = 'ASTM D7236/D7094 - Flash Point Closed cup (small scale)';
const o = Q.chipOrder(fleet, reporting, fp);
check('the instruments that report the test come first, by name',
  o.first.map(m => m.title), ['Auto Grabner', 'Mini Grabner', 'PAC Flash 1', 'PAC Flash 2']);
check('then the rest, by name', o.rest.map(m => m.title), ['Agilent GC 1', 'Agilent GC 2']);
check('a test nobody reports yet lists everyone once, by name',
  Q.chipOrder(fleet, reporting, 'Sulfur').first.length + '/' + Q.chipOrder(fleet, reporting, 'Sulfur').rest.map(m => m.title).join(','),
  '0/Agilent GC 1,Agilent GC 2,Auto Grabner,Mini Grabner,PAC Flash 1,PAC Flash 2');
check('no reporting answer (the log could not be read) is not "nobody reports it"',
  Q.chipOrder(fleet, null, fp).unknown, true);
check('the match ignores spacing and case, as LabCore\'s names vary',
  Q.chipOrder(fleet, reporting, '  astm D7236/D7094 -  Flash Point Closed cup (small scale)').first.length, 4);

// ── the test type-ahead ────────────────────────────────────────────────────
const cat = ['ASTM D2887/D86 - Distillation in Petroleum Products, 10% Recovery', 'ASTM D5453 - Sulfur',
  fp, 'Flash Point', 'ASTM D93 - Flash Point by Pensky-Martens', 'ASTM D6304 - Water, by Karl Fischer'];
check('"Flash" finds the flash point methods: the name itself, then the shorter (plainer) names',
  Q.matchTests(cat, 'Flash'), ['Flash Point', 'ASTM D93 - Flash Point by Pensky-Martens', 'ASTM D7236/D7094 - Flash Point Closed cup (small scale)']);
check('a method code finds its test', Q.matchTests(cat, 'd5453'), ['ASTM D5453 - Sulfur']);
check('nothing typed offers nothing (the list is long)', Q.matchTests(cat, ' '), []);
check('at most eight', Q.matchTests(Array.from({ length: 20 }, (_, i) => 'Test ' + i), 'test').length, 8);

// ── the band it will judge by ──────────────────────────────────────────────
check('expected ± k·s in the certificate\'s decimals',
  Q.bandPreview({ expected: '60.0', sd: '1.0', k: '2', units: 'C' }), 'Passes 58.0 – 60.0 – 62.0 °C');
check('a sulfur band keeps its places', Q.bandPreview({ expected: '0.0015', sd: '0.0002', k: '2', units: '%m/m' }),
  'Passes 0.0011 – 0.0015 – 0.0019 %m/m');
check('no band until both numbers are there', Q.bandPreview({ expected: '60', sd: '' }), '');

// ── what Save refuses, in words ────────────────────────────────────────────
const good = { name: 'Flash CRM lot 7', labId: 'FCRM7', test: fp, picked: true, expected: '60', sd: '1', k: '2', hours: '24' };
const lib = [{ name: 'Diesel - AO25', sample_id_val: 'STD-1' }];
check('a whole standard can be saved', Q.newProblem(good, lib), '');
check('a name is needed', Q.newProblem({ ...good, name: ' ' }, lib), 'Give the standard a name.');
check('a Lab ID is needed', Q.newProblem({ ...good, labId: '' }, lib), 'Enter the Lab ID it runs under.');
check('a test typed but not picked is not a test',
  Q.newProblem({ ...good, test: 'Flash', picked: false }, lib), 'Pick the test from LabCore\'s list.');
check('expected is a number', Q.newProblem({ ...good, expected: 'sixty' }, lib), 'The expected value has to be a number, like 63.7.');
check('s is a number', Q.newProblem({ ...good, sd: '' }, lib), 'Enter the standard deviation.');
check('s cannot be negative', Q.newProblem({ ...good, sd: '-1' }, lib), 'The standard deviation cannot be negative.');
check('k is above zero', Q.newProblem({ ...good, k: '0' }, lib), 'k has to be greater than zero.');
check('a name already in the library, in any case',
  Q.newProblem({ ...good, name: 'diesel - ao25' }, lib), 'There is already a standard called Diesel - AO25.');
check('a Lab ID already in the library',
  Q.newProblem({ ...good, labId: 'std-1' }, lib), 'STD-1 is already the Lab ID of Diesel - AO25.');

// ── the line above Save ────────────────────────────────────────────────────
check('it says what Save will do', Q.newSentence(good, ['Agilent GC 2']),
  'Saves Flash CRM lot 7 and checks Flash Point Closed cup (small scale) on Agilent GC 2.');
check('two instruments, joined in words', Q.newSentence(good, ['Agilent GC 2', 'PAC Flash 1']),
  'Saves Flash CRM lot 7 and checks Flash Point Closed cup (small scale) on Agilent GC 2 and PAC Flash 1.');
check('none picked says it will not be checked yet', Q.newSentence(good, []),
  'Saves Flash CRM lot 7. Nothing is checked on it until you pick an instrument here or on its page.');

// ── the standard page's Check it on sheet ──────────────────────────────────
check('changed: who is added and who is taken off',
  Q.assignChange(['a', 'b'], ['b', 'c'], { a: 'GC 1', b: 'GC 2', c: 'PAC 1' }),
  'Adds PAC 1. Takes GC 1 off it.');
check('unchanged is said, and Save refuses it', Q.assignChange(['a'], ['a'], { a: 'GC 1' }), 'Nothing has changed.');

// ── the certificate column ─────────────────────────────────────────────────
check('a certificate row says its dates and who filed it',
  Q.certLine({ issued_at: '2026-01-15', expires_at: '2027-06-30', uploaded_by: 'ryan', uploaded_at: '2026-01-20T10:00:00' }),
  'Issued 15 Jan 2026 · valid to 30 Jun 2027 · filed by ryan');
check('an undated certificate says so', Q.certLine({ uploaded_by: 'ryan' }), 'No expiry stated · filed by ryan');

// ── Trends (2026-10-08, Ryan: "a page with all of them … arrange them
// together by machine and have them show dynamically in one page") ────────
const card = (uid, title, rank, check, key, pts) => ({ uid, title, rank, check, test: check, href: '/instruments/' + uid + '#qc',
    verdict: { key, word: key, glyph: 'g' }, points: pts || [] });
const groups = Q.trendGroups([
    card('gc1', 'Agilent GC 1', 0, '90% Recovery', 'out'),
    card('gc2', 'Agilent GC 2', 2, '10% Recovery', 'never'),
    card('gc1', 'Agilent GC 1', 3, 'FBP', 'in'),
    card('gc1', 'Agilent GC 1', 3, 'IBP', 'in'),
    card('gc1', 'Agilent GC 1', 3, '10% Recovery', 'in'),
    card('vis', 'Viscosity', 2, 'Viscosity 40C', 'never'),
]);
check('trends: one group per instrument', groups.map(g => g.title), ['Agilent GC 1', 'Agilent GC 2', 'Viscosity']);
check('trends: an instrument is as bad as its worst check', groups[0].worst, 'out');
check('trends: a GC reads in distillation order', groups[0].cards.map(c => c.check), ['IBP', '10% Recovery', '90% Recovery', 'FBP']);
check('trends: the group links to its record', groups[0].href, '/instruments/gc1');
check('trends: the group counts its verdicts', groups[0].counts, { out: 1, in: 3 });
check('trends: nothing in, nothing out', Q.trendGroups(null), []);

const DAY = 86400000, to = Date.UTC(2026, 9, 8), from = to - 90 * DAY;
const pts = [
    { z: 0, in_spec: true, at: new Date(to - 100 * DAY).toISOString() },   // before the window
    { z: 0.5, in_spec: true, at: new Date(from).toISOString() },
    { z: 2, in_spec: false, at: new Date(to - 45 * DAY).toISOString() },
    { z: null, in_spec: true, at: new Date(to - 10 * DAY).toISOString() }, // no band: not drawable
    { z: -1, in_spec: true, at: null },                                    // no time: not placeable
    { z: 0.1, in_spec: true, at: new Date(to).toISOString() },
];
check('trends: points sit on the time axis, the window only', Q.trendX(pts, from, to).map(p => [p.t, p.z, p.in_spec]),
    [[0, 0.5, true], [0.5, 2, false], [1, 0.1, true]]);
check('trends: the range is 30, 90 or 180 days', [Q.trendRange('30'), Q.trendRange('180'), Q.trendRange('7'), Q.trendRange(null)], [30, 180, 90, 90]);

// fit: the biggest tiles that still put every chart on one screen
const fit4k = Q.trendFit({ width: 3600, height: 2000, gap: 16, sizes: [2, 1, 1, 1, 1, 1, 1, 2, 1, 1] });
check('fit: a 4K desk gets tiles bigger than the minimum', fit4k.tileW > 400, true);
check('fit: and everything fits the height', fit4k.total <= 2000, true);
const fitSmall = Q.trendFit({ width: 1100, height: 700, gap: 16, sizes: [5, 5, 5, 5, 5, 5] });
check('fit: too many for one screen packs at the minimum and scrolls', fitSmall.cols, 3);
check('fit: a phone is one column', Q.trendFit({ width: 360, height: 700, gap: 12, sizes: [2, 1] }).cols, 1);
check('fit: the chart grows with the tile', Q.trendChartH(300) < Q.trendChartH(700), true);

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall passed');
