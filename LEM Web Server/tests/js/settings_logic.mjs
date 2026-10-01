// Settings' pure logic (static/js/settings_logic.js, LEMSettingsLogic).
//
// Why these are node-tested and not only clicked through:
//
// * `importOutcome` is the sentence a person reads after a bulk import, and
//   this codebase has twice told someone "imported 3094" while nothing landed
//   (notes.md). The server now answers `landed` / `not_landed` under both
//   refusal shapes; this is the half that turns that into words, and the rule
//   is that NO answer short of "2xx, nothing not landed, not incomplete" may
//   read as success. A 200 that somehow carries not_landed > 0, a 503 that
//   landed everything but the history, an HTML error page, a network failure:
//   each gets its own true sentence, and none says "landed" about work that
//   did not.
// * `levelRows` puts the building top floor first (how a directory board
//   reads) and counts the instruments standing on each from the same
//   /api/machines payload every screen uses, so the count cannot disagree with
//   the floor map. A level nobody stands on says "None", not a blank.
// * `ageText` is how old the instrument record is, ticking on the page. It
//   must never say "0 s ago" for a record that was never read.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/settings_logic.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const L = root.LEMSettingsLogic;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
const never = (name, text, word) => check(name + ' (never "' + word + '")', new RegExp(word, 'i').test(text), false);

if (!L) { console.log('  FAIL settings_logic.js does not define window.LEMSettingsLogic'); process.exit(1); }

const PM = { one: 'completion', many: 'completions' };

// ── importOutcome: success only when it is one ─────────────────────────────
{
  const o = L.importOutcome(200, { landed: 12, not_landed: 0 }, PM);
  check('all landed is done', o.tone, 'done');
  check('all landed says so', o.title, 'All 12 completions landed in LabCore.');
}
{
  const o = L.importOutcome(200, { landed: 1, not_landed: 0 }, PM);
  check('singular', o.title, 'The 1 completion landed in LabCore.');
}
{
  const o = L.importOutcome(200, { landed: 0, not_landed: 0 }, PM);
  check('nothing new is not a failure', o.tone, 'done');
  check('nothing new says so', o.title, 'Nothing new to import: every completion in the file is already on record.');
}
{
  // the evidenced busy refusal, all of it refused (503 from refusal_response)
  const o = L.importOutcome(503, { error: 'LabCore is busy — 0 of 3 completion(s) were imported…', busy: true,
    retryable: true, landed: 0, not_landed: 3, incomplete: true }, PM);
  check('wholly refused is failed', o.tone, 'failed');
  check('wholly refused title', o.title, 'None landed: 0 of 3 completions are in LabCore.');
  never('wholly refused', o.title + o.detail, 'imported');
  check('it says re-running is safe', /run the same file again/i.test(o.detail) && /nothing is duplicated/i.test(o.detail), true);
  check('it carries LabCore’s reason', /busy/.test(o.detail), true);
}
{
  // a permanent refusal (502), the synthetic no-error-key shape's route answer
  const o = L.importOutcome(502, { error: 'LabCore refused the write', busy: false, landed: 0, not_landed: 2, incomplete: true }, PM);
  check('502 is failed too', o.tone, 'failed');
}
{
  const o = L.importOutcome(503, { error: 'busy', landed: 1, not_landed: 2, incomplete: true }, PM);
  check('partial is partial', o.tone, 'partial');
  check('partial title', o.title, 'Stopped part-way: 1 of 3 completions landed, 2 did not.');
  never('partial', o.title, 'imported');
}
{
  // a 2xx that still reports not_landed: never success, whatever the status says
  const o = L.importOutcome(200, { landed: 2, not_landed: 1 }, PM);
  check('2xx with not_landed is not done', o.tone, 'partial');
}
{
  const o = L.importOutcome(200, { landed: 2, not_landed: 0, incomplete: true }, PM);
  check('2xx marked incomplete is not done', o.tone !== 'done', true);
}
{
  // checklists: every round landed, the history stopped (the route answers 503)
  const R = { one: 'round', many: 'rounds' };
  const o = L.importOutcome(503, { error: 'The rounds imported, but LabCore stopped accepting the historic ticks.',
    landed: 2, not_landed: 0, history_landed: 300, history_not_landed: 2794, incomplete: true }, R);
  check('rounds in, history stopped, is partial', o.tone, 'partial');
  check('it names both halves', o.title, 'Both rounds landed; 300 of 3,094 ticks of history did, 2,794 did not.');
}
{
  const R = { one: 'round', many: 'rounds' };
  const o = L.importOutcome(200, { landed: 2, not_landed: 0, history_landed: 3094, history_not_landed: 0 }, R);
  check('rounds and history', o.title, 'Both rounds landed in LabCore, with 3,094 ticks of history.');
}
{
  const o = L.importOutcome(503, null, PM);
  check('no body is unknown', o.tone, 'unknown');
  check('unknown says it cannot tell', /could not tell/i.test(o.title), true);
  never('unknown', o.title + o.detail, 'landed in');
}
{
  const o = L.importOutcome(200, { created: 3 }, PM);
  check('an answer without landed is unknown, even a 200', o.tone, 'unknown');
}
{
  const o = L.importOutcome(0, null, PM);
  check('a network failure is unknown', o.tone, 'unknown');
  check('network words', /did not answer/i.test(o.title), true);
}
{
  const o = L.importOutcome(401, { error: 'Authentication required' }, PM);
  check('signed out mid-way: nothing was sent', o.tone, 'failed');
  check('signed out words', o.title, 'Not imported: you are signed out. Nothing was sent.');
}
{
  const o = L.importOutcome(400, { error: 'No CSV supplied.' }, PM);
  check('a 400 is the file, nothing was sent', [o.tone, o.title], ['failed', 'Not imported: No CSV supplied.']);
}

// ── the PM preview, in sentences ───────────────────────────────────────────
{
  const p = L.pmPreview({ create_count: 12, skipped: 3, unmatched: [{ equipment: 'GC 9', line: 4 }],
    errors: [{ line: 7, error: 'bad date' }], reschedule: [{ uid: 'x' }, { uid: 'y' }] });
  check('preview lines', p.lines, [
    '12 completions to add',
    '3 already on record, skipped',
    '2 schedules move to their latest completion',
    '1 row names equipment LEM does not have: GC 9 (row 4)',
    '1 row could not be read: row 7, bad date',
  ]);
  check('preview button', p.go, 'Import 12 completions');
}
{
  const p = L.pmPreview({ create_count: 0, skipped: 5, unmatched: [], errors: [], reschedule: [] });
  check('nothing to add has no button', p.go, '');
  check('nothing to add says so', p.lines[0], 'Nothing new: all 5 completions in the file are already on record');
}
{
  const p = L.pmPreview({ create_count: 0, skipped: 0, unmatched: [], reschedule: [],
    errors: [{ line: 0, error: 'Missing column(s): kind. Start from the template.' }] });
  check('a file-level problem is one sentence and no button',
        [p.lines, p.go], [['The file could not be read: Missing column(s): kind. Start from the template.'], '']);
}

// ── levels: top floor first, counted from the fleet ────────────────────────
{
  const levels = { levels: [{ uid: 'g', name: 'Ground', rank: 0 }, { uid: 'm', name: 'Mezz', rank: 1 },
    { uid: 'u', name: 'Upper', rank: 2 }], default_level: 'm', ground_level: 'g' };
  const machines = [{ level_uid: 'g' }, { level_uid: 'g' }, { level_uid: 'u' }, { level_uid: '' }, { level_uid: 'gone' }];
  const rows = L.levelRows(levels, machines);
  check('top first', rows.map(r => r.name), ['Upper', 'Mezz', 'Ground']);
  check('counts: unplaced and dangling stand on the ground', rows.map(r => r.count), ['1 instrument', 'None', '4 instruments']);
  check('the default is marked', rows.map(r => r.isDefault), [false, true, false]);
  check('the ground is marked', rows.map(r => r.isGround), [false, false, true]);
}
{
  const rows = L.levelRows({ levels: [{ uid: 'g', name: 'Ground', rank: 0 }], default_level: 'g', ground_level: 'g' }, null);
  check('no fleet read yet: the count says so, never 0', rows[0].count, 'Not counted yet');
}

// ── the record's age ───────────────────────────────────────────────────────
check('never read', L.ageText(null), 'not read yet');
check('fresh', L.ageText(2), 'just now');
check('seconds', L.ageText(14), '14 s ago');
check('minutes', L.ageText(185), '3 min ago');
check('hours', L.ageText(7300), '2 h ago');

// ── lab hours ──────────────────────────────────────────────────────────────
check('opens before closes', L.hoursProblem('06:00', '22:00', [0, 1]), '');
check('closes before opens', L.hoursProblem('18:00', '06:00', [0]), 'The lab has to open before it closes.');
check('no days', L.hoursProblem('06:00', '22:00', []), 'Pick at least one open day.');
check('a missing time', L.hoursProblem('', '22:00', [1]), 'Give an opening and a closing time.');
check('days in words', L.daysText([0, 1, 2, 3, 4]), 'Mon–Fri');
check('days in words, odd set', L.daysText([0, 2, 5]), 'Mon, Wed, Sat');
check('every day', L.daysText([0, 1, 2, 3, 4, 5, 6]), 'Every day');

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall passed');
