// The round's tick, in a harness (static/js/round.js, LEMRoundLogic).
//
// P1, the bug this page exists to fix (baseline lem-ui.md §P1): the server
// recorded 3 of 3 and the screen showed 0 of 3, every box empty and
// "overdue". The item <div> took focus on a tap, `LEM.liveEdit` saw the caret
// inside `#lists` and held every repaint, and there was no optimistic tick.
// Somebody who sees nothing happen taps again, and the old second tap
// UNTICKED the item. So the rules tested here are the ones that close it:
//
// * The tick paints at once, before the POST has an answer, and the POST
//   carries the ABSOLUTE `checked: true`. A toggle that flips whatever the row
//   last said is how a double tap became an untick.
// * A tap on a ticked row sends nothing at all. Undo is the only way back,
//   and Undo is a separate, deliberate control.
// * A live merge is per row, and it never touches a row whose save is still
//   in flight, nor a row whose save settled AFTER the read being merged
//   began. Without the second rule a GET that left before the tick landed
//   and came back after it repaints the row unticked: P1 again, by a race.
// * A refused tick reverts the row and says why, in the row. A tick that
//   shows as done but is not on the server is the audit failure the whole
//   checklist store is built to prevent.
// * A reading that does not parse is refused here, before any request, with
//   the sentence the person needs ("Enter a number, like 2900"). The server
//   refuses it too; the point is that nothing ticks optimistically on a
//   value that can never be saved.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/round.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const L = root.LEMRoundLogic;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
const claim = (name, ok, note) => {
  if (ok) { console.log(`  ok   ${name}`); return; }
  fails++; console.log(`  FAIL ${name}${note ? `\n         ${note}` : ''}`);
};

if (!L) { console.log('  FAIL round.js does not define window.LEMRoundLogic'); process.exit(1); }

// ── a day, as /api/checklists answers it ────────────────────────────────────
function day(state) {
  return {
    day: '2026-10-01',
    checklists: [
      { uid: 'cl-open', name: 'Opening round', slot: 'opening', due_time: '09:30',
        items: [
          { uid: 'a', text: 'Lights and fume hoods on', item_type: 'item', entry_type: 'none' },
          { uid: 'b', text: 'Helium cylinder pressure', item_type: 'item', entry_type: 'number', units: 'PSI', track_uid: 't-he' },
          { uid: 'h', text: 'Gas', item_type: 'header', entry_type: 'none' },
          { uid: 'c', text: 'Nitrogen generator running', item_type: 'item', entry_type: 'none' },
          { uid: 'c1', text: 'No alarm on the panel', item_type: 'subtask', parent_uid: 'c', entry_type: 'none' },
        ] },
      { uid: 'cl-close', name: 'Closing round', slot: 'closing', due_time: '17:00',
        items: [{ uid: 'z', text: 'Lights off', item_type: 'item', entry_type: 'none' }] },
    ],
    state: state || {},
  };
}

// A fake server: every POST is held until the test resolves it, so "before
// the POST resolves" is a state the test can stand in.
function harness(opts) {
  const posts = [];
  const painted = [];
  let who = (opts && 'user' in opts) ? opts.user : 'Cody';
  const r = L.createRound({
    send: (kind, cl, body) => new Promise(res => posts.push({ kind, cl, body, res })),
    paint: (uid) => painted.push(uid),
    user: () => who,
    now: () => new Date('2026-10-01T08:02:00'),
  });
  r.load(day(opts && opts.state), 'opening');
  return { r, posts, painted, setUser: (u) => { who = u; } };
}
const flush = () => new Promise(res => setTimeout(res, 0));

// ── structure ───────────────────────────────────────────────────────────────
{
  const { r } = harness();
  check('rows are the slot\'s, in order, headers kept as headings',
        r.rows().map(x => x.uid + ':' + x.kind),
        ['a:tick', 'b:number', 'h:header', 'c:tick', 'c1:tick']);
  check('the closing round is not on the opening page', r.rows().some(x => x.uid === 'z'), false);
  check('a heading is not work: 4 to do, not 5', r.counts().total, 4);
}

// ── the tick paints before the POST resolves ─────────────────────────────────
{
  const { r, posts, painted } = harness();
  const what = r.tap('a');
  check('a tap on an unticked tick row is a tick', what, 'tick');
  claim('the row is painted ticked synchronously, before any answer',
        r.row('a').st.checked === true && painted.includes('a'));
  check('...drawn as saving, by the person ticking', [r.row('a').saving, r.row('a').st.user], [true, 'Cody']);
  check('one POST, to the toggle, with the ABSOLUTE checked value',
        posts.map(p => [p.kind, p.cl, p.body]),
        [['toggle', 'cl-open', { item_uid: 'a', checked: true, day: '2026-10-01' }]]);
  check('counts include the tick already (the pill moves with the tap)', r.counts().done, 1);
  check('the bench bar says one tick is saving', r.counts().saving, 1);

  // ── a second tap sends nothing ────────────────────────────────────────────
  check('a second tap while it saves does nothing', r.tap('a'), 'none');
  check('...and sends no request', posts.length, 1);

  // ── a live merge that started before the tick landed ──────────────────────
  const seq = r.beginFetch();
  r.merge(day({}), seq);                       // the server has not got it yet
  check('a merge while saving leaves the row ticked', r.row('a').st.checked, true);
  posts[0].res({ ok: true, body: { ok: true, touched: ['a'] } });
  await flush();
  check('on ok the row is done, not saving', [r.row('a').saving, r.row('a').st.checked], [false, true]);
  r.merge(day({}), seq);                       // that same stale read, landing late
  check('a read that began before the tick settled cannot untick it', r.row('a').st.checked, true);
  const seq2 = r.beginFetch();
  r.merge(day({ 'cl-open': { a: { checked: true, user: 'Cody', at: '2026-10-01T08:02:05', value: '' } } }), seq2);
  check('a fresh read takes the server\'s own time', r.row('a').st.at, '2026-10-01T08:02:05');

  check('a tap on a ticked row does nothing', r.tap('a'), 'none');
  check('...and sends nothing: Undo is the only way back', posts.length, 1);
  const s3 = r.beginFetch();
  r.merge(day({}), s3);
  check('a read that began after the tick settled IS believed (someone undid it elsewhere)',
        r.row('a').st.checked, false);
}

// ── a refusal reverts and says why ──────────────────────────────────────────
{
  const { r, posts } = harness();
  r.tap('a');
  posts[0].res({ ok: false, error: 'LabCore is busy. Try again in 30s.', body: {} });
  await flush();
  check('a refused tick reverts the row', r.row('a').st.checked, false);
  check('...and says so in the row', r.row('a').error, 'Not saved: LabCore is busy. Try again in 30s.');
  check('...and in the bench bar', r.counts().failed, 1);
  check('tapping again is allowed (and is a fresh absolute tick)', r.tap('a'), 'tick');
  check('...whose POST is checked:true again', posts[1].body.checked, true);
  check('the error clears while it retries', r.row('a').error, '');
}

// ── a parent ticks its children, optimistically too ─────────────────────────
{
  const { r, posts } = harness();
  r.tap('c');
  check('the subtask paints with its parent', r.row('c1').st.checked, true);
  posts[0].res({ ok: true, body: { ok: true, touched: ['c', 'c1'] } });
  await flush();
  check('both done after the answer', [r.row('c').st.checked, r.row('c1').st.checked], [true, true]);
}

// ── Undo ────────────────────────────────────────────────────────────────────
{
  const state = { 'cl-open': { a: { checked: true, user: 'Ana', at: '2026-10-01T07:58:00', value: '' },
                               b: { checked: true, user: 'Ana', at: '2026-10-01T08:01:00', value: '2900' } } };
  const { r, posts } = harness({ state });
  check('Undo on a ticked row is an untick', r.undo('a'), 'untick');
  check('...painted at once', r.row('a').st.checked, false);
  check('...posting the absolute checked:false', posts[0].body, { item_uid: 'a', checked: false, day: '2026-10-01' });
  check('Undo on an unticked row does nothing', r.undo('c'), 'none');
  check('Undo on a reading clears the reading (the reading is what ticked it)', r.undo('b'), 'untick');
  check('...through the value route, with an empty value',
        [posts[1].kind, posts[1].body], ['value', { item_uid: 'b', value: '', day: '2026-10-01' }]);
}

// ── a reading ──────────────────────────────────────────────────────────────
{
  const { r, posts } = harness();
  check('tapping a reading row goes to its field, it does not tick', r.tap('b'), 'focus');
  check('...and sends nothing', posts.length, 0);
  check('a reading that does not parse is refused here', r.save('b', 'about half'), 'refused');
  check('...with the sentence the person needs', r.row('b').error, 'Enter a number, like 2900');
  check('...and nothing was sent or ticked', [posts.length, r.row('b').st.checked], [0, false]);
  check('a refused value is not a tick that failed to save (the bench bar stays honest)',
        r.counts().failed, 0);
  check('an empty Save is refused the same way', r.save('b', '  '), 'refused');
  check('a good reading saves', r.save('b', ' 2900 '), 'saving');
  check('...and ticks at once', [r.row('b').st.checked, r.row('b').st.value], [true, '2900']);
  check('...posting the trimmed value', posts[0].body, { item_uid: 'b', value: '2900', day: '2026-10-01' });
}

// ── the header moves with the row, in the same task ─────────────────────────
// Round 1's critic, at 4 s of latency: the row said ticked and "Saving…" while
// the pill said "0 of 4 done" and the bench bar "Nothing ticked yet today".
// The row was painted on the tap but the header only after the answer, so
// for as long as the network took the page contradicted itself. The round
// now calls deps.head() after every batch of row paints: the tap, the
// answer, a merge that changed anything, and a refused value.
{
  const heads = [];
  const posts = [];
  const r = L.createRound({
    send: (kind, cl, body) => new Promise(res => posts.push({ res })),
    paint: () => {},
    head: () => heads.push(L.saveWords(r.counts(), r.lastSaved()).text + ' | ' + r.counts().done),
    user: () => 'Cody',
    now: () => new Date('2026-10-01T08:02:00'),
  });
  r.load(day(), 'opening');
  r.tap('a');
  check('the tap repaints the header at once, before any answer',
        heads, ['1 tick saving… | 1']);
  posts[0].res({ ok: true, body: { ok: true } });
  await flush();
  check('...and again when the answer lands', heads[heads.length - 1], 'Saved 08:02 · all ticks on the server | 1');
  const n = heads.length;
  const seq = r.beginFetch();
  r.merge(day({ 'cl-open': { a: { checked: true, user: 'Cody', at: '2026-10-01T08:02:00', value: '' },
                             c: { checked: true, user: 'Ryan', at: '2026-10-01T08:03:00', value: '' } } }), seq);
  check('a merge that changed a row repaints the header', [heads.length, heads[heads.length - 1].split(' | ')[1]], [n + 1, '2']);
  r.merge(day({ 'cl-open': { a: { checked: true, user: 'Cody', at: '2026-10-01T08:02:00', value: '' },
                             c: { checked: true, user: 'Ryan', at: '2026-10-01T08:03:00', value: '' } } }), r.beginFetch());
  check('a merge that changed nothing does not', heads.length, n + 1);
}

// ── a session that expired on the server asks for sign-in, in place ─────────
// Round 1: the server had dropped the session, the page still thought Cody
// was signed in, and a tap ended in "Not saved: Authentication required" with
// no way forward but a reload. A 401 now reverts the row, says why in words
// a person can act on, and hands deps.expired() the act to run again after
// sign-in, so the person signs in and the tick lands without a second tap.
{
  const posts = [];
  const expired = [];
  const r = L.createRound({
    send: (kind, cl, body) => new Promise(res => posts.push({ body, res })),
    paint: () => {},
    expired: (act, again) => expired.push([act, again]),
    user: () => 'Cody',
    now: () => new Date('2026-10-01T08:02:00'),
  });
  r.load(day(), 'opening');
  r.tap('a');
  posts[0].res({ ok: false, status: 401, error: 'Authentication required', body: { error: 'Authentication required' } });
  await flush();
  check('a 401 reverts the row', r.row('a').st.checked, false);
  check('...says what to do', r.row('a').error, 'Not saved: you were signed out. Sign in and it saves.');
  check('...and asks for sign-in with the act to run again', expired.map(e => e[0]), ['tick']);
  expired[0][1]();
  check('running it again is a fresh absolute tick', [posts.length, posts[1].body.checked], [2, true]);
  posts[1].res({ ok: false, status: 503, error: 'LabCore is busy.', body: {} });
  await flush();
  check('a refusal that is not a 401 does not ask for sign-in', expired.length, 1);
  r.save('b', '2900');
  posts[2].res({ ok: false, status: 401, error: 'Authentication required', body: {} });
  await flush();
  check('a reading signed out asks to save this reading', expired.map(e => e[0]), ['tick', 'save this reading']);
}

// ── signed out, nothing is sent ─────────────────────────────────────────────
{
  const { r, posts } = harness({ user: '' });
  check('signed out, a tap asks for sign-in and sends nothing', r.tap('a'), 'signin');
  check('...nothing painted ticked', r.row('a').st.checked, false);
  check('...no request', posts.length, 0);
}

// ── parsing ────────────────────────────────────────────────────────────────
check('2900', L.parseReading('number', '2900'), { ok: true, value: '2900' });
check('-1.5', L.parseReading('number', '-1.5'), { ok: true, value: '-1.5' });
check('.5', L.parseReading('number', '.5'), { ok: true, value: '.5' });
// A comma is refused, not guessed at: "1,000" is a thousand to one person and
// one to another, and a cylinder read as 1 PSI is a false alarm in the trend.
// "-0" is not a reading anybody took: a signed zero is a slip (round 1's
// critic saw it saved), so it is refused with the sentence that fixes it.
check('-0 says to drop the sign', L.parseReading('number', '-0'), { ok: false, error: 'Enter 0 without a sign' });
check('+0.0 too', L.parseReading('number', '+0.0').ok, false);
check('0 itself is a reading', L.parseReading('number', '0'), { ok: true, value: '0' });
for (const bad of ['2900psi', 'nan', 'inf', '1e3', '', '2.9.0', '1,000.5', '2,5', '1,000', '-0', '-.0']) {
  check(`refused: ${JSON.stringify(bad)}`, L.parseReading('number', bad).ok, false);
}
check('text takes anything that is not blank', L.parseReading('text', ' half full '), { ok: true, value: 'half full' });

// ── words ──────────────────────────────────────────────────────────────────
check('limits both ways', L.limitsText({ min: 500, max: 3000 }, 'PSI'), 'Limits 500 – 3000 PSI');
check('an upper limit only', L.limitsText({ min: null, max: 10 }, 'µg/min'), 'Limit ≤ 10 µg/min');
check('no limits, no words (A.6)', L.limitsText({ min: null, max: null }, 'PSI'), '');
check('in range', L.judge('2900', { min: 500, max: 3000 }), 'In range');
check('below', L.judge('400', { min: 500, max: 3000 }), 'Below the minimum');
check('no limits set judges nothing', L.judge('400', { min: null, max: null }), '');
check('byline', L.byline({ user: 'Cody', at: '2026-10-01T07:58:12' }), 'Cody · 07:58');
check('byline with no name still says when', L.byline({ user: '', at: '2026-10-01T07:58:12' }), '07:58');

check('pill: part done', L.pill({ done: 3, total: 6 }, '09:30', '08:02', ''),
      { kind: 'part', text: '3 of 6 done' });
check('pill: overdue', L.pill({ done: 3, total: 6 }, '09:30', '09:31', ''),
      { kind: 'overdue', text: 'Overdue · was due 09:30 · 3 of 6 done' });
check('pill: done says when', L.pill({ done: 6, total: 6 }, '09:30', '10:00', '2026-10-01T07:58:00'),
      { kind: 'done', text: 'Done · 6 of 6 · 07:58' });
check('pill: nothing to do', L.pill({ done: 0, total: 0 }, '', '08:00', ''), null);

check('bar: saving', L.saveWords({ saving: 2, failed: 0 }, ''), { kind: 'saving', text: '2 ticks saving…' });
check('bar: one saving', L.saveWords({ saving: 1, failed: 0 }, ''), { kind: 'saving', text: '1 tick saving…' });
check('bar: failed beats saved', L.saveWords({ saving: 0, failed: 1 }, '2026-10-01T08:02:00'),
      { kind: 'failed', text: '1 tick not saved · see the row' });
check('bar: saved', L.saveWords({ saving: 0, failed: 0 }, '2026-10-01T08:02:00'),
      { kind: 'saved', text: 'Saved 08:02 · all ticks on the server' });
check('bar: nothing yet', L.saveWords({ saving: 0, failed: 0 }, ''),
      { kind: 'idle', text: 'Nothing ticked yet today' });

// ── a section's own progress (2026-10-07) ─────────────────────────────────
// Ryan: the checklists did not feel consistent with the rest of the app. A
// heading like "Check and Record Gas Levels" now says how far its own rows
// are, so the progress is where the work is, not only in the pill at the top.
{
  const rows = [
    { uid: 'h1', kind: 'header', st: {} },
    { uid: 'a', kind: 'tick', st: { checked: true } },
    { uid: 'h2', kind: 'header', st: {} },
    { uid: 'o2', kind: 'number', st: { checked: true } },
    { uid: 'n2', kind: 'number', st: { checked: false } },
    { uid: 'h3', kind: 'header', st: {} },
  ];
  check('each heading counts the rows under it, to the next heading', L.sections(rows),
        [{ uid: 'h1', done: 1, total: 1 }, { uid: 'h2', done: 1, total: 2 }, { uid: 'h3', done: 0, total: 0 }]);
  check('rows before any heading belong to none', L.sections([{ uid: 'x', kind: 'tick', st: { checked: true } }]), []);
}


if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall round checks passed');
