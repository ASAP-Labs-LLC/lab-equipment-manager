// The Instruments home's pure logic (static/js/instruments_logic.js).
//
// Why these are tested in node and not only in a browser:
//
// * The view filters live in the address bar (§1 URL rules): a link the bell
//   sends ("/?filter=needs"), the sidebar's fleet line ("/?filter=quiet") and a
//   merged Needs-you tile ("/?cause=ok_but-cal") all have to land on a list
//   that shows exactly what they promised. A filter that silently shows
//   everything is a dead end that looks like an answer.
// * The chips are VIEWS, NOT COUNTS. "Out of spec 1", "QC due 4" beside the
//   card that already listed them was the "three times" defect (§12, J1). A
//   chip label with a digit in it fails here.
// * The find box says whether it searched the whole record. "No match" from a
//   clipped corpus, or from a server still warming up, is a different
//   sentence from "no such thing", and only one of them is a statement about
//   the lab (the rule this codebase is built around).
// * The 180 ms loading rule: a fast answer never flashes "Searching…", a slow
//   one always says it is working.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/instruments_logic.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const L = root.LEMInstruments;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
if (!L) { console.log('  FAIL instruments_logic.js does not define window.LEMInstruments'); process.exit(1); }

const row = (uid, state, extra) => Object.assign({
  uid, title: uid.toUpperCase(), href: '/instruments/' + uid, level_uid: 'L1',
  readiness: { state, word: '', glyph: '', reason: '', detail: '', next: null, tiles: [] },
  needs_you: ['not_ok', 'ok_but', 'cant_tell'].includes(state),
  cause: null, bench: { state: 'in' }, maintenance: 0, last_qc: {}, where: {},
}, extra || {});

const data = {
  state: 'ready',
  instruments: [
    row('a', 'not_ok', { cause: { key: 'not_ok-qc' },
                         problems: [{ key: 'not_ok-qc', words: 'QC out of spec' }, { key: 'ok_but-cal', words: 'Calibration overdue' }] }),
    row('a2', 'ok_but', { cause: { key: 'ok_but-cal' },
                          problems: [{ key: 'ok_but-cal', words: 'Calibration overdue' }, { key: 'ok_but-pm', words: 'PM overdue' }] }),
    row('b', 'ok_but', { cause: { key: 'ok_but-qc' }, level_uid: 'L2', problems: [{ key: 'ok_but-qc', words: 'QC due' }] }),
    row('c', 'cant_tell', { cause: { key: 'cant_tell-stopped' }, bench: { state: 'stopped' },
                            problems: [{ key: 'cant_tell-stopped', words: 'Bench stopped' }] }),
    row('d', 'no_qc', { maintenance: 2, last_qc: { word: 'No QC assigned', assigned: false } }),
    row('e', 'ok', { level_uid: 'L2' }),
    row('f', 'off_line', { bench: { state: 'never' }, last_qc: { word: 'No QC assigned', assigned: false } }),
  ],
  levels: [{ uid: 'L1', name: 'Ground Floor' }, { uid: 'L2', name: 'Upper Lab' }],
  has_maintenance: true,
};
const uids = (rows) => rows.map(r => r.uid);

// ── the address bar is the view ────────────────────────────────────────────
check('parse: nothing', L.parseView(''), { filter: '', level: '', cause: '' });
check('parse: all three', L.parseView('?filter=needs&level=L2&cause=ok_but-qc'),
  { filter: 'needs', level: 'L2', cause: 'ok_but-qc' });
check('parse: junk filter is ignored, not obeyed', L.parseView('?filter=drop%20table').filter, '');
// Round 2's critic: "?filter=offline silently shows All with the All chip
// pressed, and says nothing". A view the page does not know is said, in
// words, so nobody reads the whole list as the answer to what they asked.
check('an unknown view is named, so the page can say it does not have it',
      L.unknownView('?filter=bogus'), 'bogus');
check('a known view is not unknown', L.unknownView('?filter=needs'), '');
check('no view is not unknown', L.unknownView(''), '');
check('an unknown view is clipped, never a paragraph', L.unknownView('?filter=' + 'x'.repeat(200)).length <= 41, true);
check('the sentence for it', L.unknownViewText('bogus'), 'There is no “bogus” view, so this is every instrument.');
check('round trip', L.viewQuery({ filter: 'noqc', level: 'L1', cause: '' }), '?filter=noqc&level=L1');
check('round trip: all', L.viewQuery({ filter: '', level: '', cause: '' }), '');

check('all', uids(L.filterRows(data.instruments, L.parseView(''))), ['a', 'a2', 'b', 'c', 'd', 'e', 'f']);
check('needs you = the card (not_ok, ok_but, cant_tell)', uids(L.filterRows(data.instruments, { filter: 'needs' })), ['a', 'a2', 'b', 'c']);
// Round 2's critic: "No QC assigned" showed "No instrument matches this
// view" while three rows said "No QC assigned" in their Last QC column. The
// filter keyed on the readiness STATE, which is the worst thing about an
// instrument: an off-line instrument, or one whose bench never checked in,
// has no QC assigned too, but its state says Off line / Can't tell. A chip
// is a fact, and its view is every row of which the fact is true, the same
// fact the Last QC column draws (last_qc.assigned).
check('no QC assigned: every row whose Last QC says so, whatever its verdict',
      uids(L.filterRows(data.instruments, { filter: 'noqc' })), ['d', 'f']);
check('an older answer without last_qc.assigned falls back to the state',
      uids(L.filterRows([row('y', 'no_qc'), row('x', 'ok')], { filter: 'noqc' })), ['y']);
check('off line: its own view (an address the bell and people type)',
      uids(L.filterRows(data.instruments, { filter: 'offline' })), ['f']);
check('maintenance: instruments with a schedule', uids(L.filterRows(data.instruments, { filter: 'maintenance' })), ['d']);
check('quiet: the fleet line\'s link, benches not checking in', uids(L.filterRows(data.instruments, { filter: 'quiet' })), ['c', 'f']);
check('a merged tile\'s link shows exactly its members', uids(L.filterRows(data.instruments, { cause: 'ok_but-qc' })), ['b']);
// Round 3: a tile's filter is about EVERY instrument with its problem, not
// only those for which it is the worst. "a" is not OK to run (QC) and its
// calibration is overdue too; "a2"'s PM hides behind its calibration. The
// PM view that showed 2 of 3 overdue PMs was this filter keying on the cause.
check('a calibration tile\'s link: everyone with an overdue calibration',
      uids(L.filterRows(data.instruments, { cause: 'ok_but-cal' })), ['a', 'a2']);
check('a PM behind a calibration is in the PM view', uids(L.filterRows(data.instruments, { cause: 'ok_but-pm' })), ['a2']);
check('a row from an older answer (no problems) still filters by its cause',
      uids(L.filterRows([row('z', 'ok_but', { cause: { key: 'ok_but-cal' } })], { cause: 'ok_but-cal' })), ['z']);
check('the chip for a cause view is named from any row\'s problems',
      L.problemWords(data.instruments), { 'not_ok-qc': 'QC out of spec', 'ok_but-cal': 'Calibration overdue',
                                          'ok_but-pm': 'PM overdue', 'ok_but-qc': 'QC due', 'cant_tell-stopped': 'Bench stopped' });
check('level', uids(L.filterRows(data.instruments, { level: 'L2' })), ['b', 'e']);
check('level and filter together', uids(L.filterRows(data.instruments, { filter: 'needs', level: 'L2' })), ['b']);

// ── chips: views, not counts ───────────────────────────────────────────────
const chips = L.chips(data, { filter: '', level: '', cause: '' });
check('chip labels', chips.map(c => c.label), ['All', 'Needs you', 'No QC assigned', 'Maintenance', 'Ground Floor', 'Upper Lab']);
check('no chip carries a count', chips.filter(c => /\d/.test(c.label)).length, 0);
check('All is pressed on the plain list', chips.filter(c => c.pressed).map(c => c.label), ['All']);
check('Maintenance only when a task exists',
  L.chips(Object.assign({}, data, { has_maintenance: false }), {}).map(c => c.label).includes('Maintenance'), false);
check('levels only when the server sent more than one',
  L.chips(Object.assign({}, data, { levels: [] }), {}).map(c => c.group), ['view', 'view', 'view', 'view']);
const off = L.chips(data, { filter: 'offline', level: '', cause: '' });
check('the off-line view shows as a chip that clears',
  off.filter(c => c.pressed).map(c => [c.label, c.clears]), [['Off line', true]]);
const quiet = L.chips(data, { filter: 'quiet', level: '', cause: '' });
check('a filter with no chip of its own still shows, and can be cleared',
  quiet.filter(c => c.pressed).map(c => [c.label, c.clears]), [['Not checking in', true]]);
const cause = L.chips(data, { filter: '', level: '', cause: 'ok_but-cal' }, { 'ok_but-cal': 'Calibration overdue' });
check('a merged tile\'s view is named by its cause',
  cause.filter(c => c.pressed).map(c => c.label), ['Calibration overdue']);
check('clicking a pressed level clears it',
  L.chips(data, { filter: '', level: 'L2', cause: '' }).find(c => c.label === 'Upper Lab').next,
  { filter: '', level: '', cause: '' });
check('clicking a view keeps the level',
  L.chips(data, { filter: '', level: 'L2', cause: '' }).find(c => c.label === 'Needs you').next,
  { filter: 'needs', level: 'L2', cause: '' });

// ── when ───────────────────────────────────────────────────────────────────
const now = new Date(2026, 9, 1, 13, 30).getTime();      // Thu 1 Oct 2026 13:30 local
check('today', L.when('2026-10-01T09:05:00', now), '09:05');
check('this week', L.when('2026-09-30T15:04:00', now), 'Wed 15:04');
check('older', L.when('2026-08-03T10:00:00', now), '3 Aug');
check('nothing', L.when('', now), '');
check('junk', L.when('not a time', now), '');

// ── find: what the search said, in a sentence ──────────────────────────────
const ans = (o) => Object.assign({ state: 'ok', matched: 1, results: [], corpus: { rows: 385, truncated: false, partial: false, stale: false } }, o);
check('whole record', L.searchNote(ans({})), 'Searched the whole record.');
check('all time via the log copy', L.searchNote(ans({ searched_all_time: true, corpus: { truncated: true } })), 'Searched the whole record, back to the first entry.');
check('clipped corpus', L.searchNote(ans({ corpus: { rows: 20000, truncated: true } })),
  'Searched the newest 20,000 log entries; an exact Lab ID is looked up further back.');
check('corpus not read yet', L.searchNote(ans({ corpus: { partial: true } })),
  'The log is still being read; samples may be missing until it is.');
check('stale corpus', L.searchNote(ans({ corpus: { stale: true, rows: 385 } })),
  'The last log read failed; this searched the copy from before it.');
check('warming is not "no match"', L.searchNote({ state: 'idle', warming: true, results: [] }),
  'LEM has not read the instruments yet, so nothing was searched. Try again in a moment.');
check('no match from a whole record', L.searchEmpty(ans({ state: 'no_match', matched: 0 }), 'zz9'),
  'Nothing matches “zz9”.');
check('no match from a clipped corpus says so',
  L.searchEmpty(ans({ state: 'no_match', matched: 0, corpus: { truncated: true, rows: 20000 } }), 'zz9'),
  'Nothing matches “zz9” in the newest 20,000 log entries.');
check('too short', L.searchEmpty({ state: 'short', results: [] }, 'a'), 'Type two or more letters.');
check('failed', L.searchFailed('HTTP 503'), 'Couldn’t search: HTTP 503. This is not “no match”.');

// ── find: rows, each a link ────────────────────────────────────────────────
const hrefFor = (uid, sec) => '/instruments/' + uid + (sec ? '#' + sec : '');
const rows = L.searchRows(ans({ results: [
  { kind: 'equipment', label: 'GC-1', machine_uid: 'gc-1', machines: [{ machine_uid: 'gc-1', title: 'GC-1' }] },
  { kind: 'sample', label: '38214', machine_uid: 'p1', machines: [{ machine_uid: 'p1', title: 'PAC 1' }, { machine_uid: 'p2', title: 'PAC 2' }] },
  { kind: 'level', id: 'L2', label: 'Upper Lab', machines: [] },
  { kind: 'standard', label: 'STD-1', machines: [{ machine_uid: 'a', title: 'A' }] },
  { kind: 'method', label: 'Flash Point', machine_uid: 'p1', machines: [{ machine_uid: 'p1', title: 'PAC 1' }] },
  { kind: 'operator', label: 'ryan', machines: [{ machine_uid: 'a', title: 'A' }, { machine_uid: 'b', title: 'B' }] },
  { kind: 'sample', lab_id: '9901', machine_uid: 'p1', title: 'PAC 1', test_name: 'Flash Point', at: '2026-01-01T00:00:00' },
] }), hrefFor, { hasQuality: false });
check('every row is a link', rows.every(r => typeof r.href === 'string' && r.href.startsWith('/')), true);
check('rows: instruments first, then Lab IDs, the rest after', rows.map(r => [r.kind, r.label, r.href]), [
  ['Instrument', 'GC-1', '/instruments/gc-1'],
  ['Lab ID', '38214', '/instruments/p1#log'],
  ['Lab ID', '38214', '/instruments/p2#log'],
  ['Lab ID', '9901', '/instruments/p1#log'],
  ['Method', 'Flash Point', '/instruments/p1#qc'],
  ['Standard', 'STD-1', '/qc'],
  ['Level', 'Upper Lab', '/?level=L2'],
  ['Person', 'ryan', '/logs'],
]);
check('a sample row says where', rows[1].detail, 'on PAC 1');
check('a beyond-corpus Lab ID names the instrument and test', rows[3].detail, 'on PAC 1 · Flash Point');
check('standards go to the standards page once it exists',
  L.searchRows(ans({ results: [{ kind: 'standard', label: 'Diesel - AO25', machines: [] }] }), hrefFor, { hasQuality: true })[0].href,
  '/quality/standards/Diesel%20-%20AO25');

// ── the 180 ms loading rule ────────────────────────────────────────────────
check('fast answers never flash Searching', L.showLoading(120), false);
check('slow answers say so', L.showLoading(180), true);

// ── Ctrl K ──────────────────────────────────────────────────────────────────
check('Ctrl K', L.isFindKey({ key: 'k', ctrlKey: true }), true);
check('Cmd K', L.isFindKey({ key: 'K', metaKey: true }), true);
check('plain k types a k', L.isFindKey({ key: 'k' }), false);
check('Ctrl Shift K is the browser\'s', L.isFindKey({ key: 'K', ctrlKey: true, shiftKey: true }), false);

// ── Needs you says a cause once, and names nobody ─────────────────────────
// Round 2's critic: the tile "OptiMPP 1 and Pensky-Martens 1 · QC out of
// spec" sat above the two rows that say the same, under a pill that counted
// them. The tile now draws its cause, its next step and a link that filters
// the table. Nothing it draws is a name or a number; the caption carries no
// count either (the nav's "10 need you" is that number's one home on screen).
{
  const t = { key: 'ok_but-cal', state: 'ok_but', glyph: 'half', cause: 'Calibration overdue',
              next: { text: 'Calibrate, then mark the calibration done' }, link: 'Show them',
              href: '/?cause=ok_but-cal',
              members: [{ uid: 'dma', title: 'Anton Paar DMA 4500' }, { uid: 'gc2', title: 'GC-2' }] };
  const w = L.tileWords(t, { cause: '' });
  check('a tile draws its cause, next step and link', w,
        { head: 'Calibration overdue', next: 'Next: calibrate, then mark the calibration done',
          link: 'Show them', active: false });
  const said = Object.values(w).join(' ');
  check('a tile names no member', t.members.some(m => said.includes(m.title)), false);
  check('a tile draws no digit', /\d/.test(said), false);
  check('the tile whose cause is the view is the pressed one',
        L.tileWords(t, { cause: 'ok_but-cal' }).active, true);
  check('when its cause is the view, the link offers the way back',
        L.tileWords(t, { cause: 'ok_but-cal' }).link, 'Show all');
  const more = { key: 'more', cause: 'More causes', next: { text: 'PM overdue and Bench stopped' },
                 link: 'Show all that need you', members: [], more: 3 };
  check('the overflow tile lists the causes it holds, without a count',
        L.tileWords(more, { cause: '' }),
        { head: 'More causes', next: 'PM overdue and Bench stopped', link: 'Show all that need you', active: false });
}
// Round 3: with "All" the view, the first tile drew GC's ink "current"
// border and read as selected. A tile is current only while its cause IS
// the view; otherwise none is.
{
  const tiles = [{ key: 'not_ok-qc' }, { key: 'ok_but-cal' }];
  check('no tile is current on the whole list', L.currentTile(tiles, { cause: '' }), -1);
  check('the tile whose cause is the view is current', L.currentTile(tiles, { cause: 'ok_but-cal' }), 1);
  check('a cause no tile holds makes none current', L.currentTile(tiles, { cause: 'cant_tell-never' }), -1);
}
check('the caption carries no count', L.needsCaption({ count: 10 }, null), 'Worst first · updates by itself');
check('nothing needs you: no caption (the empty line says it)', L.needsCaption({ count: 0 }, null), '');
check('a failed refresh is said, not hidden',
      L.needsCaption({ count: 4 }, '2026-10-01T09:05:00', 0).startsWith('Couldn’t refresh since '), true);

// ── the Maintenance view orders by what falls due, and says what comes next ──
// /maintenance became this view. With every seeded instrument scheduled it
// showed the same rows, in the same order, with the same Last QC column as
// All: a door into the room you were already in. In this view the rows sort
// by their earliest due date (overdue first, it is the earliest), and the
// Last QC column becomes "Next due": the next task that is NOT overdue. The
// overdue ones are on the row's Can it run? line already; saying them again
// here is the "repeats problems" defect.
//
// Round 2's critic: the view was sorted by a HIDDEN key (the earliest due
// date of any task, overdue ones included), so the one column a reader can
// see, Next due, read "16 Oct, 5 Oct, Nothing else due, 1 Oct, ... 22 Jul
// 2027, 21 Oct". A list that looks shuffled is a list nobody trusts. The
// view now sorts by exactly what it shows: Next due, soonest first, by the
// ISO day the server sends beside the words (`on`); rows with nothing else
// due go last, in the server's worst-first order. The overdue tasks are not
// lost: each row's Can it run? line says them.
{
  const sch = (next, tasks) => ({ schedule: { tasks: tasks || 2, next }, maintenance: tasks || 2 });
  const rows = [
    row('m1', 'ok', sch({ name: 'Annual calibration', due: '7 Nov', on: '2026-11-07', soon: false })),
    row('m2', 'not_ok', sch({ name: 'Monthly PM', due: '26 Oct', on: '2026-10-26', soon: false })),
    row('m3', 'ok_but', sch(null)),
    row('m4', 'ok_but', sch({ name: 'Monthly PM', due: '1 Oct', on: '2026-10-01', soon: true })),
    row('m5', 'ok', { maintenance: 0 }),
    row('m6', 'ok', sch({ name: 'Annual calibration', due: '22 Jul 2027', on: '2027-07-22', soon: false })),
    row('m7', 'not_ok', sch(null)),
  ];
  check('maintenance view: in the order of the Next due column it shows',
        uids(L.filterRows(rows, { filter: 'maintenance' })), ['m4', 'm2', 'm1', 'm6', 'm3', 'm7']);
  check('every other view keeps the server\'s worst-first order', uids(L.filterRows(rows, {})), ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7']);
  check('the Next due header says the view is sorted by it', L.sortedBy({ filter: 'maintenance' }), 'third');
  check('elsewhere the order is worst first, not by a column', L.sortedBy({ filter: '' }), '');
  check('the column is Last QC elsewhere', L.thirdColumn({ filter: '' }), 'Last QC');
  check('the column is Next due in the maintenance view', L.thirdColumn({ filter: 'maintenance' }), 'Next due');
  check('next due: the task and its day', L.nextDue(rows[0]), { main: 'Annual calibration', sub: '7 Nov', soon: false });
  check('due soon is said in words, not colour alone', L.nextDue(rows[3]), { main: 'Monthly PM', sub: 'Due soon · 1 Oct', soon: true });
  check('all overdue: nothing else is due, and "overdue" is not said again',
        L.nextDue(rows[2]), { main: 'Nothing else due', sub: '', soon: false });
}

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall passed');
