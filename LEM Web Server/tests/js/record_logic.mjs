// The instrument record's pure logic (static/js/record_logic.js).
//
// Why these are node tests and not only a browser walk:
//
// * ONE number format for QC (§4.2). A band printed "331.48 – 334.9 – 338.32"
//   reads as if the target were known to fewer places than its limits, and a
//   sulfur band printed "0.00 – 0.00 – 0.00" (what toFixed(2) did on the old
//   floor) is not a band at all. fmtQC takes its decimals from the standard's
//   own low / expected / high, at least 1, and the whole band shares them.
// * The band track is a picture of a fact: where the last result sits against
//   min, target and max. A result outside the band is pinned to the edge it
//   left by, as a triangle, not drawn off the track or silently clamped.
// * The card's one sentence carries what failed, against which standard,
//   when, anything else that is wrong, and the next step. Built here so the
//   words are checked, not eyeballed.
// * A statistical-control finding is a CAPTION marked provisional, never a
//   second verdict (§3.1 QC row, judge J3's "two verdict columns").
// * An uncertainty that could not be read is not "no approved estimate".
import fs from 'fs';

const load = (f) => fs.readFileSync(new URL('../../static/js/' + f, import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', load('instruments_logic.js'))(root, undefined);
new Function('window', 'module', load('record_logic.js'))(root, undefined);
const R = root.LEMRecord;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
if (!R) { console.log('  FAIL record_logic.js does not define window.LEMRecord'); process.exit(1); }

// ── fmtQC (§4.2) ────────────────────────────────────────────────────────────
const d90 = { low: 331.48, expected: 334.9, high: 338.32 };      // Agilent GC 1, 90% recovery
check('the whole band shares its decimals: 334.90, never 334.9',
  R.bandText(d90), '331.48 – 334.90 – 338.32');
check('a result takes the band\'s decimals', R.fmtQC(331.5, d90), '331.50');
const sulfur = { low: 0.0008, expected: 0.0011, high: 0.0014 };
check('sulfur keeps its small values: 0.0011 stays 0.0011', R.bandText(sulfur), '0.0008 – 0.0011 – 0.0014');
check('a sulfur result is not rounded to nothing', R.fmtQC(0.00113, sulfur), '0.0011');
const coarse = { low: 1, expected: 2, high: 3 };
check('at least one decimal', R.bandText(coarse), '1.0 – 2.0 – 3.0');
check('below 0.01 against a coarse band falls back to significant figures', R.fmtQC(0.0042, coarse), '0.0042');
check('a float artefact does not become 17 decimals',
  R.fmtQC(0.1 + 0.2, { low: 0.1, expected: 0.25, high: 0.4 }), '0.30');
check('negative temperatures', R.bandText({ low: -16, expected: -14, high: -12 }), '-16.0 – -14.0 – -12.0');
check('no value is a dash, never 0', R.fmtQC(null, d90), '—');
check('no band is said, not drawn as zeros', R.bandText({ low: null, expected: null, high: null }), '');
check('the old floor\'s toFixed(2) is not how this page formats', /toFixed\(2\)/.test(load('record_logic.js')) || /toFixed\(2\)/.test(load('record.js')), false);

// ── the band track ─────────────────────────────────────────────────────────
const p = R.bandPos({ low: 185.05, expected: 187.63, high: 190.21 }, 191.83);
check('a result above the band is pinned to the high edge, as outside', [p.outside, p.valPct], ['high', 100]);
const q = R.bandPos({ low: 331.48, expected: 334.9, high: 338.32 }, 331.51);
check('inside the band, between min and target', q.outside === null && q.valPct > q.lowPct && q.valPct < q.midPct, true);
check('min, target and max keep their order on the track', q.lowPct < q.midPct && q.midPct < q.highPct, true);
check('no value: no dot', R.bandPos(d90, null).valPct, null);
check('no band: no track', R.bandPos({ low: null, expected: null, high: null }, 3), null);

// ── the card's sentence ─────────────────────────────────────────────────────
const NOW = Date.parse('2026-10-01T13:30:00');
// the card's time names its day like every time on the record (round 4):
// "today 09:00", "yesterday 15:04", never a bare 09:00
check('QC stop: what, against what, when, next',
  R.captionText({ lead: 'QC out of spec on 10% Recovery and 50% Recovery', std: 'AF26',
    at: '2026-09-30T15:04:24', too: '', next: 'Fix, then rerun AF26' }, NOW),
  'QC out of spec on 10% Recovery and 50% Recovery (AF26, yesterday 15:04). Next: fix, then rerun AF26.');
check('a warning behind it is in the same sentence',
  R.captionText({ lead: 'QC out of spec on Flash Point', std: 'AF26', at: '2026-10-01T09:00:00',
    too: 'calibration overdue since 21 Jun too', next: 'Fix, then rerun AF26' }, NOW),
  'QC out of spec on Flash Point (AF26, today 09:00); calibration overdue since 21 Jun too. Next: fix, then rerun AF26.');
check('nothing to do: no "Next:"',
  R.captionText({ lead: 'Its check is in spec', std: 'AF26', at: '2026-10-01T08:38:39', too: '', next: null }, NOW),
  'Its check is in spec (AF26, today 08:38).');
check('a warning with no standard has no empty brackets',
  R.captionText({ lead: 'Calibration overdue since 11 Jul', std: '', at: null, too: '', next: 'Calibrate it, then mark the calibration done' }, NOW),
  'Calibration overdue since 11 Jul. Next: calibrate it, then mark the calibration done.');

// ── the chart's words ───────────────────────────────────────────────────────
const pts = [{ ts: '2026-08-04T10:00:00', value: 187, in_spec: true },
             { ts: '2026-09-30T15:04:24', value: 191.83, in_spec: false }];
check('the range caption says how many, since when, and how many outside',
  R.rangeCaption({ points: pts, failures: 1 }, '24', NOW), '2 runs since 4 Aug · 1 outside the limits');
check('All says all', R.rangeCaption({ points: pts, failures: 0 }, 'all', NOW), 'All 2 runs since 4 Aug · none outside the limits');
check('no runs in range is said as such', R.rangeCaption({ points: [], failures: 0 }, '90d', NOW), 'No runs in the last 90 days');
check('a control finding is a provisional caption, not a verdict',
  R.controlCaption({ violations: [{ rule: '2of3_2s', indices: [21, 23], side: 'above', provisional: true }], points: new Array(24) }),
  '2 of 3 in a row beyond 2s, above the mean · provisional');
check('the newest finding is said, and the rest counted',
  R.controlCaption({ violations: [
    { rule: '1_3s', indices: [3], side: 'below', provisional: false },
    { rule: 'shift', indices: [10, 11, 12, 13, 14, 15, 16, 17, 18], side: 'above', provisional: false }], points: new Array(24) }),
  '9 in a row above the mean · 1 more finding');
check('in control says nothing', R.controlCaption({ violations: [], points: new Array(24) }), '');

// ── U (one line) ────────────────────────────────────────────────────────────
check('an approved estimate', R.uLine({ ok: true, current: { u_expanded: 1.234, k: 2, approved_at: '2026-09-12T10:00:00' } },
  { low: 185.05, expected: 187.63, high: 190.21, units: '°C' }, NOW), 'U = ±1.23 °C (k = 2, approved 12 Sep)');
check('none approved', R.uLine({ ok: true, current: null }, d90, NOW), 'No approved uncertainty estimate yet');
check('could not read is not none', R.uLine({ ok: false, error: 'LabCore is busy' }, d90, NOW),
  'Couldn\'t read the uncertainty register: LabCore is busy');

// ── the chart geometry ──────────────────────────────────────────────────────
const g = R.chartModel({ points: pts, low: 185.05, high: 190.21, expected: 187.63 }, { w: 600, h: 200 });
check('every point is placed inside the plot', g.points.every(pt => pt.x >= g.plot.x0 && pt.x <= g.plot.x1 && pt.y >= g.plot.y0 && pt.y <= g.plot.y1), true);
check('the result outside the limits is marked outside', g.points.map(pt => pt.outside), [false, true]);
check('the limits are inside the y range', g.lines.high > g.plot.y0 && g.lines.low < g.plot.y1, true);

// ── a day that is not this year says its year ──────────────────────────────
// Round 2's shooter: dev-seed GC-1's annual calibration read "Last done 22 Jul,
// Due 22 Jul, Scheduled": the due date is a year on, but without the year it
// reads overdue-yet-"Scheduled".
const OCT2 = Date.parse('2026-10-02T12:00:00');
check('this year has no year', R.dayIn('2026-07-22T10:00:00', OCT2), '22 Jul');
check('next year says so', R.dayIn('2027-07-22', OCT2), '22 Jul 2027');
check('last year says so', R.dayIn('2025-07-22', OCT2), '22 Jul 2025');
check('no date is a dash', R.dayIn(null, OCT2), '—');

// ── how long a pass counts (§3.1 QC intro) ─────────────────────────────────
// Round 3's critic: the page dropped "A passing check counts for 24 h", the
// sentence that tells a reader why a pass from August is QC due today.
check('the lab default', R.windowSentence({ hours: 24, from: '' }), 'A passing check counts for 24 h.');
check('a standard\'s own window names the standard',
  R.windowSentence({ hours: 4, from: 'AF26 · Flash Point' }), 'A passing check counts for 4 h, as AF26 · Flash Point sets.');
check('a fraction of an hour is kept', R.windowSentence({ hours: 1.5, from: '' }), 'A passing check counts for 1.5 h.');
check('no window said is the default, not nothing', R.windowSentence(null), 'A passing check counts for 24 h.');

// ── a refresh that failed is said, never swallowed ─────────────────────────
// Round 3's critic: six 503s in a row left the old verdict on screen with no
// mark. The page keeps what it last read (a blank card answers nothing) but
// says, in words, that it is no longer current and why.
const T0 = Date.parse('2026-10-01T13:15:00');
const stale = R.staleText({ status: 503, error: 'LabCore did not answer the first read' }, T0, T0 + 120000);
check('the stale line says it could not read, why, and as of when',
  stale, 'Couldn\'t refresh this instrument (LabCore did not answer the first read). Shown as of ' + new Date(T0).getHours().toString().padStart(2, '0') + ':15; it may be out of date.');
check('a network failure is LEM not answering',
  R.staleText({ status: 0 }, T0, T0 + 1000).startsWith('Couldn\'t refresh this instrument (LEM did not answer).'), true);
check('an HTTP failure without words says its status',
  R.staleText({ status: 502 }, T0, T0 + 1000).startsWith('Couldn\'t refresh this instrument (HTTP 502).'), true);

// ── the chart never contradicts the row above it (round 4's critic) ────────
// The QC row's "Last" comes from the bench's status (the spec it publishes);
// the chart comes from LEM's QC log. They are two reads of the same runs and
// can disagree: the log lags, or (the dev seed, demo_floor) holds nothing yet.
// The old chart then said "No runs of this check on file yet" under a row
// showing "Last 0.0018 · 07:30": the page claimed no run while displaying
// one. withLatest() adds the row's own run to what is drawn when the log
// does not have it, and marks it, so the chart and the row tell one story.
const NOW4 = Date.parse('2026-10-02T12:00:00');
const row = { value: 0.0018, at: '2026-10-02T07:30:00', low: 0.0011, expected: 0.0015, high: 0.0019 };
const empty = R.withLatest({ points: [], failures: 0, violations: [] }, row, '24', NOW4);
check('an empty log under a row with a result draws that result',
  empty.points.map(p => [p.ts, p.value, p.from]), [['2026-10-02T07:30:00', 0.0018, 'status']]);
check('and says how many of the drawn runs are in the log', [empty.logged, empty.from_status], [0, true]);
check('no series at all is the same as an empty one',
  R.withLatest(null, row, 'all', NOW4).points.length, 1);
const logged = { points: [{ ts: '2026-10-01T07:30:00', value: 0.0016 }, { ts: '2026-10-02T07:30:00', value: 0.0018 }], failures: 0, violations: [] };
check('a log that already has the row\'s run is drawn as it is',
  [R.withLatest(logged, row, '24', NOW4).points.length, R.withLatest(logged, row, '24', NOW4).from_status], [2, false]);
const behind = { points: [{ ts: '2026-10-01T07:30:00', value: 0.0016 }], failures: 0, violations: [{ rule: 'shift', indices: [0] }] };
const b2 = R.withLatest(behind, row, '24', NOW4);
check('a log behind the row gets the row\'s run on the end, marked',
  b2.points.map(p => p.from || 'log'), ['log', 'status']);
check('the control findings keep their indices (the run is appended, never inserted)',
  b2.violations[0].indices, [0]);
const outRow = { value: 0.0021, at: '2026-10-02T07:30:00', low: 0.0011, expected: 0.0015, high: 0.0019 };
check('an appended run outside the limits is counted as outside',
  R.withLatest({ points: [], failures: 0, violations: [] }, outRow, '24', NOW4).failures, 1);
check('a row with no result adds nothing: the empty chart is honest then',
  R.withLatest({ points: [], failures: 0, violations: [] }, { value: null, at: null }, '24', NOW4).points.length, 0);
check('90 days does not draw a run older than 90 days',
  R.withLatest({ points: [] }, { value: 1, at: '2026-05-01T07:30:00', low: 0, high: 2 }, '90d', NOW4).points.length, 0);
check('the same run a few seconds apart in the two reads is one run',
  R.withLatest({ points: [{ ts: '2026-10-02T07:29:58', value: 0.0018 }] }, row, '24', NOW4).points.length, 1);
check('the chart model keeps which runs came from the status',
  R.chartModel(Object.assign({}, b2, { low: 0.0011, high: 0.0019, expected: 0.0015 }), { w: 600, h: 200 }).points.map(p => p.from), ['log', 'status']);
// Round 5's critic: on Multitek S the head said "Bench never checked in" and
// the caption said the run was "from the bench's status": a source the same
// page says never reported. The row's Last is what LabCore holds for the
// check (lem_machine_specs, the spec row the module writes), so the caption
// names LabCore, which is true whether or not the bench is checking in now.
check('the caption says where the newest run came from when the log is behind',
  R.rangeCaption(b2, '24'), '2 runs since 1 Oct · none outside the limits · newest from LabCore, not yet in LEM\'s QC log');
check('the caption for a log with nothing in it names LabCore as the run\'s source',
  R.rangeCaption(empty, '24'), '1 run on 2 Oct · LabCore\'s latest result for this check, none in LEM\'s QC log yet');
check('no caption names the bench as a source',
  [R.rangeCaption(b2, '24'), R.rangeCaption(empty, '24')].some(t => /bench/.test(t)), false);

// Round 5's critic: under "24 runs" a log of 24 plus the row's run drew 25
// ("25 runs since 5 Sep"). The selector is a promise about how many runs are
// drawn, so the oldest logged run makes way, and the control findings'
// indices move with it. A finding about only the dropped run goes; one that
// ran through it keeps saying how many runs it found ("3 in a row"), since
// that is what the control rules found in the log.
const full = { points: Array.from({ length: 24 }, (_, i) => ({ ts: new Date(Date.parse('2026-09-05T08:00:00') + i * 86400000).toISOString(), value: 0.0015 })),
  failures: 0, violations: [{ rule: 'shift', indices: [0, 1, 2] }, { rule: '1_3s', indices: [0] }] };
const capped = R.withLatest(full, row, '24', NOW4);
check('24 runs never draws 25', capped.points.length, 24);
check('the run that made way is the oldest, the row\'s run is the newest',
  [capped.points[0].ts, capped.points[23].from], [full.points[1].ts, 'status']);
check('the findings move with the points and a finding about only the dropped run goes',
  capped.violations, [{ rule: 'shift', indices: [0, 1], count: 3 }]);
check('and its caption still says 3', R.controlCaption(capped).includes('3 in a row'), true);
check('and the logged count says 23 of the 24 are from the log', capped.logged, 23);
check('All is not capped', R.withLatest(full, row, 'all', NOW4).points.length, 25);

// ── a time always carries its day on the record (round 4's critic) ─────────
// "When 07:30" next to "Last result Thu 07:49" read as if the older one were
// newer. On the record a time names its day.
check('today says today', R.stamp('2026-10-02T07:30:00', NOW4), 'Today 07:30');
check('yesterday says yesterday', R.stamp('2026-10-01T07:49:00', NOW4), 'Yesterday 07:49');
check('this week names the weekday', R.stamp('2026-09-29T15:04:00', NOW4), 'Tue 15:04');
check('older names the date', R.stamp('2026-08-03T15:04:00', NOW4), '3 Aug');
check('inside a sentence it is lower case', R.stamp('2026-10-02T07:30:00', NOW4, true), 'today 07:30');
check('a weekday keeps its capital mid-sentence', R.stamp('2026-09-29T15:04:00', NOW4, true), 'Tue 15:04');
check('no time is nothing, never a made-up one', R.stamp(null, NOW4), '');
// Round 5's critic: a result from 2025-10-02 read "2 Oct", the same as today.
check('another year names its year', R.stamp('2025-10-02T07:30:00', NOW4), '2 Oct 2025');
check('this year does not', R.stamp('2026-08-03T15:04:00', NOW4), '3 Aug');
// and stamp() returned '' when instruments_logic.js had not loaded, dropping
// the time from "In spec (STD-1, today 07:30)" without a word.
{
  const bare = {};
  new Function('window', 'module', load('record_logic.js'))(bare, undefined);
  check('stamp does not depend on another file having loaded',
    [bare.LEMRecord.stamp('2026-10-02T07:30:00', NOW4), bare.LEMRecord.stamp('2026-09-29T15:04:00', NOW4)],
    ['Today 07:30', 'Tue 15:04']);
}

// Round 6: an overdue calibration or PM gets the card's button, "Mark the
// calibration done…". The sheet records work done at the instrument, so its
// two rules are the ones an auditor reads the record by: a note is required
// (who did what; a bare "done" is what the old floor let through), and the
// day cannot be in the future (marking tomorrow's calibration done today
// moves the schedule past work nobody has done). The day defaults to today
// in the LAB's calendar, not UTC: at 21:00 in Houston UTC is already
// tomorrow, and toISOString() would have proposed a future date.
{
  const at21 = new Date(2026, 9, 2, 21, 0).getTime();
  check('today is the local calendar day', R.localDay(at21), '2026-10-02');
  check('a note is required', R.doneProblem('  ', '2026-10-02', at21),
    'Say what was done. A note is kept with every completion.');
  check('a day is required', R.doneProblem('Recalibrated with STD-1', '', at21), 'Say which day it was done.');
  check('not in the future', R.doneProblem('Recalibrated', '2026-10-03', at21),
    'It cannot be marked done on a day that has not come yet.');
  check('today is fine', R.doneProblem('Recalibrated', '2026-10-02', at21), '');
  check('an earlier day is fine', R.doneProblem('Recalibrated', '2026-09-30', at21), '');
  check('a non-date is refused, not sent', R.doneProblem('Recalibrated', '2026-13-40', at21), 'Say which day it was done.');
  check('the toast names the task, who and when',
    R.doneToast({ name: 'Annual calibration' }, 'ryan', '13:42'), 'Annual calibration marked done · ryan · 13:42');
}

// Round 8: at 820 wide the Bench tile's detail "Results folder · today 14:01"
// broke between "today" and "14:01", leaving the clock alone on a line. A
// tile's detail is a list of facts joined by " · "; each fact is kept whole
// (the page draws each as an unbreakable span), so a narrow tile breaks only
// at a separator. The Bench tile appends when it last checked in.
{
  const NOW = new Date(2026, 9, 2, 15, 0).getTime();
  check('the bench tile: source, then when, as two whole parts',
    R.tileParts({ key: 'bench', detail: 'Results folder', at: '2026-10-02T14:01:00' }, NOW),
    ['Results folder', 'today 14:01']);
  check('a detail already joined by " · " splits at its separators',
    R.tileParts({ key: 'qc', detail: 'Never run · bench stopped' }, NOW), ['Never run', 'bench stopped']);
  check('only the bench tile carries a time', R.tileParts({ key: 'qc', detail: 'In spec', at: '2026-10-02T14:01:00' }, NOW), ['In spec']);
  check('no detail, no parts', R.tileParts({ key: 'online', detail: '' }, NOW), []);
}

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall passed');
