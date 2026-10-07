// The record's action sections, their pure half (static/js/record_actions_logic.js).
//
// Why these are node tests and not only a browser walk:
//
// * THE SCHEDULE PRESETS ARE DAYS, NOT MONTHS. ia-final §8's precondition for
//   T6: a MINOR release has no calendar-month column, so "Monthly" is 30
//   days, "Quarterly" 91 and "Annual" 365, and the sheet says the days on
//   the chip so nobody believes a calendar is involved. One chip sets the
//   kind, the interval and the name; T6 is Row, Schedule…, chip, Save.
// * A NAME A PERSON TYPED IS NEVER OVERWRITTEN. The name follows the chip and
//   the kind only until somebody types one.
// * EVERY REFUSAL IS A SENTENCE BEFORE ANY REQUEST. A blank note, a reason
//   left out, a correction typed as "a bit", a date in the future: each is
//   refused in words in the sheet, because a request that the server refuses
//   for the same reason costs a round trip and reads as LEM's fault.
// * A CORRECTIVE ACTION OFFERS ITS NEXT STEP, AND ONLY STEPS IT CAN TAKE.
//   equipment_history.LIFECYCLE is the rule (open → actioned → verified →
//   closed, withdrawn from any unfinished state). A button the server will
//   refuse is a dead end with extra steps.
// * AN UNKNOWN COUNT IS NOT ZERO. "Change history (n)" with n unreadable is
//   "Change history", never "(0)".
import fs from 'fs';

const load = (f) => fs.readFileSync(new URL('../../static/js/' + f, import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', load('record_actions_logic.js'))(root, undefined);
const A = root.LEMRecordActions;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
if (!A) { console.log('  FAIL record_actions_logic.js does not define window.LEMRecordActions'); process.exit(1); }

// fixed "now": Fri 2 Oct 2026, 13:42 local
const NOW = new Date(2026, 9, 2, 13, 42).getTime();

// ── schedule presets ────────────────────────────────────────────────────────
check('three presets, in days, said on the chip',
  A.PRESETS.map(p => [p.label, p.kind, p.days, A.presetChip(p)]),
  [['Monthly PM', 'pm', 30, 'Monthly PM · 30 days'],
   ['Quarterly PM', 'pm', 91, 'Quarterly PM · 91 days'],
   ['Annual calibration', 'calibration', 365, 'Annual calibration · 365 days']]);
check('a name follows the interval and the kind',
  [A.autoName(30, 'pm'), A.autoName(91, 'calibration'), A.autoName(365, 'calibration'), A.autoName(14, 'pm'), A.autoName(1, 'pm')],
  ['Monthly PM', 'Quarterly calibration', 'Annual calibration', 'PM every 14 days', 'PM every day']);
{
  let s = A.scheduleState();
  check('a new schedule starts as a monthly PM with nothing typed', [s.kind, s.days, s.name, s.touched], ['pm', 30, 'Monthly PM', false]);
  s = A.applyPreset(s, A.PRESETS[2]);
  check('one chip sets kind, interval and name (T6: one click)', [s.kind, s.days, s.name], ['calibration', 365, 'Annual calibration']);
  s = A.applyKind(s, 'pm');
  check('switching the kind renames an untouched name', s.name, 'Annual PM');
  s = A.typedName(s, 'Detector bake-out');
  s = A.applyPreset(s, A.PRESETS[0]);
  check('a typed name survives a chip', [s.name, s.days, s.kind], ['Detector bake-out', 30, 'pm']);
  s = A.typedName(s, '   ');
  s = A.applyKind(s, 'calibration');
  check('clearing the name hands it back to the chips', s.name, 'Monthly calibration');
  check('the pressed chip is the one that matches', A.pressedPreset(A.applyPreset(A.scheduleState(), A.PRESETS[1])), 'quarterly-pm');
  check('a hand-typed interval presses no chip', A.pressedPreset(Object.assign(A.scheduleState(), { days: 45 })), null);
  const edit = A.scheduleState({ uid: 'gc-1-pm', name: 'Monthly PM', kind: 'pm', every: 30, last_done: '2026-08-27' });
  check('editing starts from the task, its name counted as typed', [edit.uid, edit.name, edit.days, edit.lastDone, edit.touched],
    ['gc-1-pm', 'Monthly PM', 30, '2026-08-27', true]);
}
check('the schedule refuses in words',
  [A.scheduleProblem({ name: '', days: 30 }, NOW),
   A.scheduleProblem({ name: 'PM', days: 0 }, NOW),
   A.scheduleProblem({ name: 'PM', days: 2.5 }, NOW),
   A.scheduleProblem({ name: 'PM', days: 4000 }, NOW),
   A.scheduleProblem({ name: 'PM', days: 30, lastDone: '2026-10-03' }, NOW),
   A.scheduleProblem({ name: 'PM', days: 30, lastDone: '' }, NOW),
   A.scheduleProblem({ name: 'PM', days: 30, lastDone: '2026-10-02' }, NOW)],
  ['Give the task a name.',
   'Say how often, in whole days (1 to 3650).',
   'Say how often, in whole days (1 to 3650).',
   'Say how often, in whole days (1 to 3650).',
   'It cannot have been done on a day that has not come yet.',
   '', '']);
check('a second task with the same name is said before it is saved',
  A.duplicateNote([{ uid: 'k-cal', name: 'Annual calibration', every: 365 }], { name: 'annual calibration ', days: 365 }),
  'It already has an Annual calibration every 365 days; saving adds a second one.');
check('editing a task is not a duplicate of itself',
  A.duplicateNote([{ uid: 'k-cal', name: 'Annual calibration', every: 365 }], { uid: 'k-cal', name: 'Annual calibration', days: 365 }), '');
check('the save body is the route\'s',
  A.scheduleBody({ uid: '', name: ' Annual calibration ', kind: 'calibration', days: 365, lastDone: '' }),
  { uid: '', name: 'Annual calibration', kind: 'calibration', interval_days: 365, last_done: '' });

// ── a task row ──────────────────────────────────────────────────────────────
check('a task row says when, in words, never a bare date',
  [A.dueText({ next_due: '2026-09-26', word: 'Overdue' }, NOW),
   A.dueText({ next_due: '2026-10-02', word: 'Due soon' }, NOW),
   A.dueText({ next_due: '2026-10-07', word: 'Scheduled' }, NOW),
   A.dueText({ next_due: '2027-07-23', word: 'Scheduled' }, NOW),
   A.dueText({ next_due: null, last_done: null, word: 'Not done yet' }, NOW)],
  ['was due 26 Sep', 'due today', 'due in 5 days', 'due 23 Jul 2027', 'never done']);
check('how often, in words', [A.everyText(30), A.everyText(1), A.everyText(365), A.everyText(null)],
  ['every 30 days', 'every day', 'every 365 days', '—']);

// ── correction factors ──────────────────────────────────────────────────────
check('an offset carries its sign and units',
  [A.offsetText(-3, 'C'), A.offsetText(1.5, '°C'), A.offsetText(0.0004, '%m/m'), A.offsetText(0, '')],
  ['−3 °C', '+1.5 °C', '+0.0004 %m/m', '0']);
check('a correction is a number or it is refused',
  [A.correctionProblem('a bit', 'bias study'), A.correctionProblem('', 'x'), A.correctionProblem('-1,5', 'x'),
   A.correctionProblem('1.5', ''), A.correctionProblem('+0.25', 'calibration 2 Oct')],
  ['Enter the offset as a number, like -1.5.', 'Enter the offset as a number, like -1.5.', '',
   'Say why. The reason is kept with the change (17025 §7.8.2).', '']);
check('the change-history link counts only what it knows',
  [A.historyLabel(3), A.historyLabel(1), A.historyLabel(0), A.historyLabel(null), A.historyLabel(undefined)],
  ['Change history (3)', 'Change history (1)', 'Change history (0)', 'Change history', 'Change history']);

// ── corrective actions ──────────────────────────────────────────────────────
check('each state offers its next step and only the steps the lifecycle allows',
  ['open', 'actioned', 'verified', 'closed', 'withdrawn'].map(s => A.actionSteps(s)),
  [{ next: 'record', steps: ['record', 'assign', 'note', 'withdraw'] },
   { next: 'verify', steps: ['verify', 'record', 'assign', 'note', 'withdraw'] },
   { next: 'close', steps: ['close', 'assign', 'note', 'withdraw'] },
   { next: null, steps: ['note'] },
   { next: null, steps: ['note'] }]);
check('the next step reads as a button', ['record', 'verify', 'close'].map(A.stepButton),
  ['Record what was done…', 'Verify it worked…', 'Close it…']);
check('the progress line names where it is', ['open', 'actioned', 'verified', 'closed', 'withdrawn'].map(A.stateWord),
  ['Open', 'Recorded', 'Verified', 'Closed', 'Withdrawn']);
check('a step refuses in words before posting',
  [A.stepProblem('record', { text: '' }), A.stepProblem('verify', { text: ' ' }), A.stepProblem('withdraw', { text: '' }),
   A.stepProblem('note', { text: '' }), A.stepProblem('close', { text: '' }), A.stepProblem('assign', { who: '', due: '' }),
   A.stepProblem('assign', { who: 'cody', due: '' })],
  ['Say what was done.', 'Say how you checked that it worked.', 'Say why it is withdrawn.',
   'Write the note.', '', 'Name who owns it, or give a due date.', '']);
check('each step posts its own route and body',
  [A.stepRequest('record', 'a1', { text: ' Replaced septum ' }), A.stepRequest('verify', 'a1', { text: 'reran STD' }),
   A.stepRequest('close', 'a1', { text: '' }), A.stepRequest('withdraw', 'a1', { text: 'dup' }),
   A.stepRequest('assign', 'a1', { who: 'cody', due: '2026-10-09', priority: 'high' }), A.stepRequest('note', 'a1', { text: 'n' })],
  [['/api/equipment/actions/a1/record', { action_taken: 'Replaced septum' }],
   ['/api/equipment/actions/a1/verify', { note: 'reran STD' }],
   ['/api/equipment/actions/a1/close', { note: '' }],
   ['/api/equipment/actions/a1/withdraw', { reason: 'dup' }],
   ['/api/equipment/actions/a1/assign', { assigned_to: 'cody', due_at: '2026-10-09', priority: 'high' }],
   ['/api/equipment/actions/a1/note', { note: 'n' }]]);

// ── documents ───────────────────────────────────────────────────────────────
check('sizes read as a person says them', [A.sizeText(649), A.sizeText(2048), A.sizeText(5.5 * 1024 * 1024)], ['649 B', '2 KB', '5.5 MB']);
check('a file the store refuses is refused before uploading',
  [A.uploadProblem(null), A.uploadProblem({ name: 'cert.pdf', size: 0 }), A.uploadProblem({ name: 'cert.docx', size: 10 }),
   A.uploadProblem({ name: 'big.pdf', size: 26 * 1024 * 1024 }), A.uploadProblem({ name: 'Cert.PDF', size: 10 })],
  ['Choose a file.', 'That file is empty.', 'Only PDF, PNG or JPEG files are kept here.',
   'That file is over 25 MB, the most one document can be.', '']);

// ── remove ──────────────────────────────────────────────────────────────────
check('remove wants the exact name, and a password unless unlocked',
  [A.removeProblem('GC 1', 'GC-1', true, 'x'), A.removeProblem(' GC-1 ', 'GC-1', true, ''),
   A.removeProblem('GC-1', 'GC-1', true, 'pw'), A.removeProblem('GC-1', 'GC-1', false, '')],
  ['Type the name exactly as it is shown: GC-1.', 'Enter your password to unlock removing.', '', '']);

// ── toasts ──────────────────────────────────────────────────────────────────
check('a toast says what, who and when', A.toast('Monthly PM scheduled', 'Kaden Ortiz', '13:42'),
  'Monthly PM scheduled · Kaden Ortiz · 13:42');
check('who and when, together', [A.whoWhen('ryan', '2026-10-02T13:15:00', NOW), A.whoWhen('', '2026-09-30T08:01:00', NOW), A.whoWhen('ryan', null, NOW)],
  ['ryan · today 13:15', '30 Sep 08:01', 'ryan']);

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall passed');
