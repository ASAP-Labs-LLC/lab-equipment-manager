// The round editor's rules, in node (static/js/round_edit.js, LEMRoundEditLogic).
//
// P2 (baseline lem-ui.md §P2): three items typed with Enter on an empty slot
// made three checklists, because every Add posted a new one. The editor now
// keeps the rows in the page and posts ONE payload with every item, under a
// uid minted once. What is tested here is the part of that which is logic:
//
// * Enter from a row with a label adds the next row; from an empty row it
//   stays put, and an empty row already below is reused, so a run of Enters
//   never stacks blank rows.
// * The payload drops rows with nothing in them, keeps the fields the row
//   does not show (heading, subtask, weekdays) untouched, sends limits as
//   typed text, and sends NO limits for a tracked reading whose limits could
//   not be read, so a save cannot blank limits it never showed.
// * Limits: blank is no limit (never 0), "1,000" and words are refused, and
//   reversed limits are refused, each placed on its row and field.
// * Reorder: ported from the old editor's reorder.mjs. A subtask dragged
//   above its parent detaches rather than pointing downward.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/round_edit.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const L = root.LEMRoundEditLogic;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

// ── Enter adds the next row, and only when there is something to follow ────
check('Enter on a labelled last row adds a row after it', L.enterFrom(['Lights'], 0), { add: true, at: 1 });
check('Enter on an empty row keeps the caret there', L.enterFrom(['Lights', ''], 1), { focus: 1 });
check('an empty row below is reused, not stacked', L.enterFrom(['Lights', '', 'Gas'], 0), { focus: 1 });
check('Enter mid-list inserts right below', L.enterFrom(['A', 'B', 'C'], 0), { add: true, at: 1 });

// ── the payload: one round, every item ──────────────────────────────────────
const def = {
  uid: 'a1b2c3d4e5f6', name: '  Opening round ', slot: 'opening', due_time: '7:30',
  items: [
    { uid: 'i1', text: 'Check nitrogen generator', item_type: 'item', entry_type: 'none', units: 'x', min: '1', max: '2' },
    { uid: 'i2', text: 'Helium cylinder pressure ', item_type: 'item', entry_type: 'number', units: ' PSI', min: '', max: '3000', track: true },
    { uid: 'blank', text: '', item_type: 'item', entry_type: 'none', units: '', min: '', max: '' },
    { uid: 'h', text: 'Gas', item_type: 'header', entry_type: 'number', units: 'PSI', min: '1', max: '2' },
    { uid: 's', text: 'Helium off', item_type: 'subtask', parent_uid: 'h', days_active: [5], entry_type: 'text' },
    { uid: 'u', text: 'Argon', item_type: 'item', entry_type: 'number', units: 'PSI', min: '', max: '', track: true, limits_unknown: true, track_uid: 't9' },
  ],
};
const p = L.payload(def);
check('one round, its uid kept', [p.uid, p.name, p.slot, p.due_time], ['a1b2c3d4e5f6', 'Opening round', 'opening', '07:30']);
check('the empty row is dropped, the rest kept in order', p.items.map(i => i.uid), ['i1', 'i2', 'h', 's', 'u']);
check('a tick carries no units, no limits and no tracking',
  [p.items[0].units, 'min' in p.items[0], p.items[0].track], ['', false, false]);
check('a number carries its limits as typed text, and the switch',
  [p.items[1].text, p.items[1].units, p.items[1].min, p.items[1].max, p.items[1].track],
  ['Helium cylinder pressure', 'PSI', '', '3000', true]);
check('a heading is never a reading', [p.items[2].entry_type, p.items[2].item_type, 'min' in p.items[2]], ['none', 'header', false]);
check('a subtask keeps its parent and its days', [p.items[3].parent_uid, p.items[3].days_active, p.items[3].entry_type],
  ['h', [5], 'text']);
check('unread limits are not sent, so the server keeps them',
  ['min' in p.items[4], 'max' in p.items[4], p.items[4].track, p.items[4].track_uid], [false, false, true, 't9']);
check('the count says items and readings, not headings', L.countWords(def.items), '4 items · 2 readings');

// ── limits ──────────────────────────────────────────────────────────────────
check('blank is no limit, never 0', L.parseLimit('  '), { ok: true, value: null });
check('a number is that number', L.parseLimit('2900.5'), { ok: true, value: 2900.5 });
check('a comma is refused, not guessed at', L.parseLimit('1,000').ok, false);
check('words are refused', L.parseLimit('high').ok, false);
check('Infinity is refused', L.parseLimit('Infinity').ok, false);

const bad = (over, at = 0) => L.problems({ name: 'R', due_time: '', items: [Object.assign(
  { uid: 'x', text: 'Helium', item_type: 'item', entry_type: 'number', units: 'PSI', min: '', max: '' }, over)] });
check('reversed limits are refused on the minimum', bad({ min: '3000', max: '500' }).map(e => [e.index, e.field]), [[0, 'min']]);
check('the refusal says both numbers', /3000.*500/.test(bad({ min: '3000', max: '500' })[0].error), true);
check('a word in the maximum is refused there', bad({ max: 'lots' }).map(e => e.field), ['max']);
check('limits on a tick are not judged', bad({ entry_type: 'none', min: 'x', max: 'y' }), []);
check('unread limits are not judged', bad({ min: 'x', limits_unknown: true }), []);
check('a row with units but no label needs a label', bad({ text: '' }).map(e => e.field), ['text']);
check('a nameless round is refused', L.problems({ name: ' ', due_time: '', items: [] }).map(e => e.field), ['name']);
check('a due time that is not HH:MM is refused', L.problems({ name: 'R', due_time: '9.30', items: [] }).map(e => e.field), ['due']);
check('7:05 is 07:05', L.parseDue('7:05'), '07:05');
check('24:00 is not a time', L.parseDue('24:00'), null);

// ── the uid: minted once, the server's shape ────────────────────────────────
check('twelve hex characters', /^[0-9a-f]{12}$/.test(L.mintUid()), true);
check('from the random source it is given', L.mintUid(b => { for (let i = 0; i < b.length; i++) b[i] = i * 17; }), '00112233445566'.slice(0, 12));

// ── reorder (ported from reorder.mjs) ───────────────────────────────────────
const items = (...t) => t.map((x, i) => ({ uid: 'u' + i, text: x, item_type: 'item', parent_uid: null }));
check('drag first to third', L.moveItem(items('a', 'b', 'c', 'd'), 0, 2).map(i => i.text), ['b', 'c', 'a', 'd']);
check('drag last to first', L.moveItem(items('a', 'b', 'c', 'd'), 3, 0).map(i => i.text), ['d', 'a', 'b', 'c']);
check('drop on itself is a no-op', L.moveItem(items('a', 'b', 'c'), 1, 1).map(i => i.text), ['a', 'b', 'c']);
check('past the end clamps', L.moveItem(items('a', 'b', 'c'), 0, 99).map(i => i.text), ['b', 'c', 'a']);
const withSub = [
  { uid: 'p', text: 'Parent', item_type: 'item', parent_uid: null },
  { uid: 's', text: 'Child', item_type: 'subtask', parent_uid: 'p' },
];
check('subtask above its parent detaches', L.moveItem(withSub, 1, 0).map(i => i.parent_uid), [null, null]);
check('parent moved below detaches the child', L.moveItem(withSub, 0, 1).find(i => i.uid === 's').parent_uid, null);
const three = [{ uid: 'x', text: 'X', item_type: 'item', parent_uid: null }].concat(withSub);
check('a still-valid parent link survives', L.moveItem(three, 0, 2).find(i => i.uid === 's').parent_uid, 'p');
check('the input is not mutated', withSub[1].parent_uid, 'p');

// ── the caption says what the row cannot ────────────────────────────────────
check('days in week order', L.caption({ days_active: [5, 0] }), 'Only on Mon, Sat.');
check('every day says nothing', L.caption({ days_active: [] }), '');
check('a subtask names its parent', L.caption({ item_type: 'subtask', parent_uid: 'p' }, 'Power down'),
  'Under “Power down”; ticking it ticks this.');

console.log(fails ? `\n${fails} FAILED` : '\nall round editor cases pass');
process.exit(fails ? 1 : 0);
