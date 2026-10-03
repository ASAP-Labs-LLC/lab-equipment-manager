// The machine log's pure logic (static/js/log_logic.js), shared by the
// record's Log section and the /logs page.
//
// Why these are node tests and not only a browser walk:
//
// * THE ADDRESS BAR IS THE LINK (ia-final §1 URL rules). Every /logs filter
//   lives in the query string, and a reload must restore exactly what was on
//   screen. parse and serialise are pinned to round-trip, including the
//   record's `equipment=` spelling and the old page's `machine=`.
// * THE TRANSFER GUARD'S ROWS READ AS ENGLISH. A `result_conflict` is what
//   the guard did when an analyst had changed the LabCore cell: "LabCore has
//   151.6, changed by dana 09:10; the bench's 151.9 was not sent again". A
//   `reread` is "re-read 43 rows; all 43 already on record, nothing re-sent".
//   Printed as kind codes they would be the jargon the judges cut.
// * THE SHEET NEVER INVENTS A NUMBER. A raw value, a correction, an operator
//   or a calibration id the row did not record reads "Not recorded", never a
//   blank (which reads as "nothing") and never 0 (which reads as "no
//   correction was applied"). Both detail shapes are read: v3.9's flat
//   `raw_value`/`correction` and v4's per-test `raw{}`/`corrections{}`.
// * NO DEAD END. Every sheet links to /instruments/<uid>#log; a lab-wide row
//   (no instrument) links to the full log of its kind instead.
import fs from 'fs';

const load = (f) => fs.readFileSync(new URL('../../static/js/' + f, import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', load('log_logic.js'))(root, undefined);
const G = root.LEMLog;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
if (!G) { console.log('  FAIL log_logic.js does not define window.LEMLog'); process.exit(1); }

// ── kinds ───────────────────────────────────────────────────────────────────
check('a conflict reads Not re-sent', G.kindWord('result_conflict'), 'Not re-sent');
check('a re-read reads Re-read', G.kindWord('reread'), 'Re-read');
check('a run is a Result', G.kindWord('run'), 'Result');
check('status_change is Status', G.kindWord('status_change'), 'Status');
check('an unknown kind is shown, de-underscored, not hidden', G.kindWord('future_thing'), 'Future thing');
check('the chips, All first', G.GROUPS.map(g => g.label), ['All', 'Results', 'QC', 'Status', 'Setup']);
check('Not re-sent and Re-read are Results', [G.groupOf('result_conflict'), G.groupOf('reread'), G.groupOf('held_expired')], ['results', 'results', 'results']);

// ── the sentences ───────────────────────────────────────────────────────────
const conflict = { kind: 'result_conflict', lab_id: '38214', test_name: 'IBP', value: '151.9',
  detail: { ours: '151.9', theirs: '151.6', their_operator: 'dana', their_updated_at: '2026-10-02T09:10:00' } };
check('Not re-sent says both values, who changed it, and that ours was held',
  G.summary(conflict), "LabCore has 151.6, changed by dana 09:10; the bench's 151.9 was not sent again.");
check('an unnamed analyst is "an analyst"',
  G.summary({ ...conflict, detail: { ...conflict.detail, their_operator: '' } }),
  "LabCore has 151.6, changed by an analyst 09:10; the bench's 151.9 was not sent again.");
check('Re-read, all already on record',
  G.summary({ kind: 'reread', detail: { rows: 43, already: 43, resent: 0 } }),
  'Re-read 43 rows; all 43 already on record, nothing re-sent.');
check('Re-read, some differed',
  G.summary({ kind: 'reread', detail: { rows: 43, already: 41, resent: 0, not_resent: 2 } }),
  'Re-read 43 rows; 41 already on record, 2 not sent again.');
check('Re-read without counts says so instead of inventing them',
  G.summary({ kind: 'reread', detail: {} }), 'Re-read its file; the bench did not say how many rows.');
check('a QC row has no summary: its fields are the sheet, said once',
  G.summary({ kind: 'qc', detail_text: 'low: 44.4 · high: 50.0 · operator: dana' }), '');
check('a status change has none either (Status says from → to)',
  G.summary({ kind: 'status_change', detail_text: 'from: GREEN · to: RED' }), '');
check('key: value pairs are not a sentence and are not shown as one',
  G.summary({ kind: 'override', detail_text: 'status: SERVICE · comment: sensor' }), '');
check('other rows use the server sentence', G.summary({ kind: 'config', detail_text: 'Placed on Ground Floor.' }), 'Placed on Ground Floor.');

// ── the table cells ─────────────────────────────────────────────────────────
check('a status change reads from → to in words', G.rowValue({ kind: 'status_change', value: 'RED', detail: { from: 'GREEN', to: 'RED' } }), 'Green → Red');
check('a config row names its action', G.rowWhat({ kind: 'config', test_name: 'level_move', action_label: 'level moved' }), 'level moved');
check('a QC row names its test', G.rowWhat({ kind: 'qc', test_name: 'Cetane Index' }), 'Cetane Index');
check('an override names what was written', G.rowWhat({ kind: 'override', test_name: '', detail: { status: 'SERVICE', comment: 'sensor' } }), 'sensor');
check('who: the person, else the operator', [G.rowWho({ by: 'ryan', detail: {} }), G.rowWho({ by: '', detail: { operator: 'dana' } }), G.rowWho({ by: '', detail: {} })], ['ryan', 'dana', '']);

// ── the sheet ───────────────────────────────────────────────────────────────
const v39qc = { kind: 'qc', machine_uid: 'cetane-calc', machine_title: 'Cetane Bench', lab_id: 'STD-1', test_name: 'Cetane Index', value: '44.8',
  ts: '2026-10-02T19:40:17.806159',
  detail: { calibration_id: '2026-08-11', correction: 0.0, expected: 47.2, high: 50.0, in_spec: true, low: 44.4, operator: 'dana', raw_value: 44.8 } };
const s1 = G.sheet(v39qc);
const kv = (s) => Object.fromEntries(s.rows.map(r => [r.k, r.v]));
check('v3.9 QC: raw value', kv(s1)['Raw value'], '44.8');
check('v3.9 QC: a recorded correction of 0 says none was applied', kv(s1)['Correction applied'], 'None (0)');
check('v3.9 QC: operator', kv(s1)['Operator'], 'dana');
check('v3.9 QC: calibration id', kv(s1)['Calibration id'], '2026-08-11');
check('v3.9 QC: the verdict in words', kv(s1)['Verdict'], 'In spec');
check('v3.9 QC: the band, min – target – max', kv(s1)['Band'], '44.4 – 47.2 – 50.0');
check('consumed keys are not repeated in Detail', s1.extra.map(r => r.k), []);
check('the sheet links to the record\'s Log', s1.link, { href: '/instruments/cetane-calc#log', text: 'Open Cetane Bench →' });

const v4run = { kind: 'run', machine_uid: 'gc 1', machine_title: 'Agilent GC 1', lab_id: '40301', test_name: '', value: '',
  ts: '2026-10-02T09:00:00', detail: { values: { IBP: 152.2 }, raw: { IBP: 151.9 }, corrections: { IBP: 0.3 }, origin: 'recovered' } };
const s2 = G.sheet(v4run);
check('v4 run: raw values per test', kv(s2)['Raw value'], 'IBP 151.9');
check('v4 run: corrections per test, signed', kv(s2)['Correction applied'], 'IBP +0.3');
check('v4 run: values per test', kv(s2)['Value'], 'IBP 152.2');
check('v4 run: an operator nobody recorded is said, not blank', kv(s2)['Operator'], 'Not recorded');
check('the uid is encoded in the link', s2.link.href, '/instruments/gc%201#log');
check('what is left of the detail is a kv, keys in words', s2.extra, [{ k: 'Origin', v: 'recovered' }]);

const noRaw = G.sheet({ kind: 'qc', machine_uid: 'm', machine_title: 'M', test_name: 'X', value: '1', detail: {} });
check('no raw value recorded: words, not blank, not 0', [kv(noRaw)['Raw value'], kv(noRaw)['Correction applied']], ['Not recorded', 'Not recorded']);

const labwide = G.sheet({ kind: 'config', machine_uid: '', machine_title: '', test_name: 'level_create', action_label: 'level created', detail_text: 'Created the level Mezzanine.', detail: { by: 'ryan', action: 'level_create', name: 'Mezzanine' } });
check('a lab-wide row links to the full log of its kind', labwide.link, { href: '/logs?kind=setup', text: 'Open the setup log →' });
check('a lab-wide row says it is lab-wide', labwide.where, 'Lab-wide');

const onRecord = G.sheet(v39qc, { onRecord: true });
check('on the record itself the link goes to the full log, not back to the page', onRecord.link,
  { href: '/logs?equipment=cetane-calc', text: 'Open in the full log →' });

const nested = G.sheet({ kind: 'status_change', machine_uid: 'm', machine_title: 'M', detail: { from: 'GREEN', to: 'RED', sub: { qc: 'RED', pm: 'GREEN' }, reason: '' } });
check('nested detail is flattened, empties dropped', nested.extra, [{ k: 'Sub', v: 'qc: RED · pm: GREEN' }]);

// ── the address bar ─────────────────────────────────────────────────────────
const st = G.parseQuery('?equipment=gc-1&kind=results&since=2026-09-01&until=2026-09-30&q=40301');
check('parse', st, { equipment: 'gc-1', kind: 'results', since: '2026-09-01', until: '2026-09-30', q: '40301' });
check('serialise in a fixed order, empties dropped', G.toQuery(st), '?equipment=gc-1&kind=results&since=2026-09-01&until=2026-09-30&q=40301');
check('round trip', G.parseQuery(G.toQuery(st)), st);
check('the old page\'s machine= reads as equipment', G.parseQuery('?machine=m1').equipment, 'm1');
check('nothing set is an empty query', G.toQuery(G.parseQuery('')), '');
check('the API is asked with machine=, plus paging', G.apiQuery(st, { limit: 100 }),
  'machine=gc-1&kind=results&since=2026-09-01&until=2026-09-30&q=40301&limit=100');
check('a bad date in the URL is dropped, not sent', G.parseQuery('?since=yesterday').since, '');

// presets: the When select shows a preset when the dates are one
const today = '2026-10-02';
check('today', G.rangeFor('today', today), { since: '2026-10-02', until: '2026-10-02' });
check('last 7 days', G.rangeFor('7d', today), { since: '2026-09-26', until: '' });
check('last 30 days', G.rangeFor('30d', today), { since: '2026-09-03', until: '' });
check('any time', G.rangeFor('any', today), { since: '', until: '' });
check('dates that are a preset show as it', G.presetFor('2026-09-26', '', today), '7d');
check('other dates are Custom', G.presetFor('2026-09-01', '2026-09-30', today), 'custom');
check('no dates is Any time', G.presetFor('', '', today), 'any');

// ── the count header ────────────────────────────────────────────────────────
check('total, kept by, complete to',
  G.countLine({ total: 385, shown: 100, source: { kept_by: 'LEM', complete_to: '2026-10-02T13:20:00' } }),
  { count: '385 events', source: 'Kept by LEM · complete to 13:20' });
check('one event', G.countLine({ total: 1, shown: 1, source: { kept_by: 'LEM', complete_to: '2026-10-02T13:20:00' } }).count, '1 event');
check('incomplete is said instead of complete',
  G.countLine({ total: 385, shown: 100, source: { kept_by: 'LEM', complete_to: '2026-10-02T13:20:00', incomplete: 'the record is still moving from LabCore' } }).source,
  'Kept by LEM · incomplete: the record is still moving from LabCore');
check('a count that failed says how many are shown, never passes the page off as all',
  G.countLine({ total: null, shown: 100, source: { kept_by: 'LEM', complete_to: '2026-10-02T13:20:00' } }).count, 'Newest 100 events');
check('a search says it looked through the whole record',
  G.countLine({ total: null, shown: 12, searched: true, source: { kept_by: 'LEM', complete_to: '2026-10-02T13:20:00' } }).count, '12 matches in the whole record');

// ── times ───────────────────────────────────────────────────────────────────
const now = new Date(2026, 9, 2, 14, 0).getTime();
check('this year: month day time', G.whenText('2026-09-26T08:30:00', now), 'Sep 26 08:30');
check('another year names it', G.whenText('2025-12-31T23:59:00', now), 'Dec 31 2025 23:59');
check('a time that is not one is shown as stored', G.whenText('garbage', now), 'garbage');

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall log_logic checks passed');
