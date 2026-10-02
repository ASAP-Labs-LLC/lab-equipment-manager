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
check('QC stop: what, against what, when, next',
  R.captionText({ lead: 'QC out of spec on 10% Recovery and 50% Recovery', std: 'AF26',
    at: '2026-09-30T15:04:24', too: '', next: 'Fix, then rerun AF26' }, NOW),
  'QC out of spec on 10% Recovery and 50% Recovery (AF26, Wed 15:04). Next: fix, then rerun AF26.');
check('a warning behind it is in the same sentence',
  R.captionText({ lead: 'QC out of spec on Flash Point', std: 'AF26', at: '2026-10-01T09:00:00',
    too: 'calibration overdue since 21 Jun too', next: 'Fix, then rerun AF26' }, NOW),
  'QC out of spec on Flash Point (AF26, 09:00); calibration overdue since 21 Jun too. Next: fix, then rerun AF26.');
check('nothing to do: no "Next:"',
  R.captionText({ lead: 'Its check is in spec', std: 'AF26', at: '2026-10-01T08:38:39', too: '', next: null }, NOW),
  'Its check is in spec (AF26, 08:38).');
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

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall passed');
