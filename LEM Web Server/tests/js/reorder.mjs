import fs from 'fs';
const html = fs.readFileSync(new URL('../../templates/checklists.html', import.meta.url), 'utf8');

// Pull moveItem straight out of the page so the test cannot drift from the code.
const at = html.indexOf('function moveItem(');
const body = html.slice(at, html.indexOf('\n}', at) + 2);

let EDIT = {items: []}, EDROW = null, renders = 0;
const renderEditor = () => { renders++; };
// moveItem closes over EDIT / EDROW / renderEditor, so it is evaluated with those
// names in scope rather than reimplemented here — the test reads the shipped code.
const make = new Function('__deps', `
  let {EDIT, EDROW, renderEditor} = __deps;
  ${body}
  return {run: (from, to) => { moveItem(from, to); return {items: EDIT.items, EDROW}; }};
`);

function items(...texts) {
  return texts.map((t, i) => ({uid: 'u' + i, text: t, item_type: 'item',
                               parent_uid: null}));
}
let fails = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
}

// 1. plain reorder
let st = {EDIT: {items: items('a','b','c','d')}, EDROW: null, renderEditor};
let r = make(st).run(0, 2);
check('drag first to third', r.items.map(i => i.text), ['b','c','a','d']);

st = {EDIT: {items: items('a','b','c','d')}, EDROW: null, renderEditor};
r = make(st).run(3, 0);
check('drag last to first', r.items.map(i => i.text), ['d','a','b','c']);

st = {EDIT: {items: items('a','b','c')}, EDROW: null, renderEditor};
r = make(st).run(1, 1);
check('drop on itself is a no-op', r.items.map(i => i.text), ['a','b','c']);

st = {EDIT: {items: items('a','b','c')}, EDROW: null, renderEditor};
r = make(st).run(0, 99);
check('past the end clamps', r.items.map(i => i.text), ['b','c','a']);

// 2. the selected row follows its item
st = {EDIT: {items: items('a','b','c','d')}, EDROW: 0, renderEditor};
r = make(st).run(0, 2);
check('selection follows the moved row', r.EDROW, 2);

st = {EDIT: {items: items('a','b','c','d')}, EDROW: 2, renderEditor};
r = make(st).run(0, 3);
check('selection shifts when passed over', r.EDROW, 1);

// 3. a subtask dragged above its parent must detach, not point downward
const withSub = [
  {uid: 'p', text: 'Parent', item_type: 'item', parent_uid: null},
  {uid: 's', text: 'Child', item_type: 'subtask', parent_uid: 'p'},
];
st = {EDIT: {items: JSON.parse(JSON.stringify(withSub))}, EDROW: null, renderEditor};
r = make(st).run(1, 0);
check('subtask above its parent detaches', r.items.map(i => i.parent_uid), [null, null]);

st = {EDIT: {items: JSON.parse(JSON.stringify(withSub))}, EDROW: null, renderEditor};
r = make(st).run(0, 1);   // parent moved below the child
check('parent moved below detaches the child', r.items.find(i => i.uid === 's').parent_uid, null);

// a valid arrangement keeps the link
const three = [
  {uid: 'x', text: 'X', item_type: 'item', parent_uid: null},
  {uid: 'p', text: 'Parent', item_type: 'item', parent_uid: null},
  {uid: 's', text: 'Child', item_type: 'subtask', parent_uid: 'p'},
];
st = {EDIT: {items: JSON.parse(JSON.stringify(three))}, EDROW: null, renderEditor};
r = make(st).run(0, 2);   // move X to the end; parent still above child
check('a still-valid parent link survives', r.items.find(i => i.uid === 's').parent_uid, 'p');

console.log(fails ? `\n${fails} FAILED` : '\nall reorder cases pass');
process.exit(fails ? 1 : 0);
