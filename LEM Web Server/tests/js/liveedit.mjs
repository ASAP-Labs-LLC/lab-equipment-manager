// A poll must not type over somebody.
//
// Ryan, 31 Aug 2026: "if you are entering data via the UI its really slow, so
// like by the time the website polls and refreshes, it will like stutter and
// delete any other data that was written that hasn't saved yet it will also
// unselect text field."
//
// Every page in LEM refreshes on a timer — the checklists every 30s, the floor
// every 2s, maintenance every 60s — and each one repaints by replacing a
// container's innerHTML. That is fine for a page being READ and destructive for
// one being typed into: the DOM under the caret is thrown away, so the value
// goes, the focus goes, and any sibling field somebody had filled in but not
// yet saved goes with them. On a checklist round that is somebody's morning.
//
// The guard is deliberately not "re-render more cleverly". Diffing the DOM
// would be a large change to five pages and would still move the caret in the
// cases that matter. What a person actually needs is simpler: WHILE I AM
// TYPING, LEAVE THE PAGE ALONE — and catch up the moment I am done.
//
// These test `LEM.liveEdit`, in static/lem.js, because all five pages have the
// same problem and none of them should each grow their own answer.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/lem.js', import.meta.url), 'utf8');

let fails = 0;
const claim = (name, ok, note) => {
  if (ok) { console.log(`  ok   ${name}`); return; }
  fails++;
  console.log(`  FAIL ${name}${note ? `\n         ${note}` : ''}`);
};

/* A DOM small enough to reason about and real enough to be wrong in the same
 * ways: focus, an input whose value has drifted from what was rendered, and a
 * container that gets replaced wholesale. */
function makeDom() {
  const doc = {activeElement: null};
  const mk = (tag, attrs = {}) => {
    const el = {
      tagName: tag.toUpperCase(), children: [], parentNode: null,
      value: attrs.value ?? '', _rendered: attrs.value ?? '',
      isContentEditable: !!attrs.contentEditable,
      getAttribute: k => (k === 'value' ? el._rendered : (attrs[k] ?? null)),
      setAttribute: (k, v) => { attrs[k] = v; },
      hasAttribute: k => k in attrs,
      appendChild(c) { el.children.push(c); c.parentNode = el; return c; },
      contains(n) {
        for (let p = n; p; p = p.parentNode) if (p === el) return true;
        return false;
      },
      querySelectorAll(sel) {
        const want = sel.split(',').map(s => s.trim().toLowerCase());
        const out = [];
        (function walk(n) {
          n.children.forEach(c => {
            if (want.includes(c.tagName.toLowerCase())) out.push(c);
            walk(c);
          });
        })(el);
        return out;
      },
    };
    return el;
  };
  const root = mk('div');
  const a = mk('input', {value: 'clean'});
  const b = mk('input', {value: ''});
  root.appendChild(a); root.appendChild(b);
  return {doc, root, a, b, mk};
}

function load() {
  const at = src.indexOf('liveEdit');
  if (at === -1) return null;
  // The module is an IIFE assigning window.LEM; run it against a stub window.
  const win = {addEventListener() {}, setTimeout: (f) => f, clearTimeout() {}};
  const fn = new Function('window', 'document', 'navigator', 'fetch',
    `${src}; return window.LEM;`);
  try {
    return fn(win, {addEventListener() {}, activeElement: null,
                    querySelectorAll: () => []},
              {}, () => Promise.resolve({ok: true, json: () => ({})}));
  } catch (err) {
    console.log('  (lem.js did not evaluate: ' + err.message + ')');
    return null;
  }
}

console.log('a poll must not type over somebody');

const LEM = load();
claim('LEM.liveEdit exists', !!(LEM && LEM.liveEdit),
  'no shared guard — each page would need its own, and they would drift');

if (LEM && LEM.liveEdit) {
  const {root, a, b} = makeDom();
  const live = LEM.liveEdit;

  // ── focus ────────────────────────────────────────────────────────────
  claim('an untouched container may be repainted',
    live.busy(root, {activeElement: null}) === false);

  claim('a container holding the caret may NOT be repainted',
    live.busy(root, {activeElement: a}) === true,
    'the repaint would move the caret out of the box being typed into');

  const outside = makeDom();
  claim('focus somewhere else does not freeze this container',
    live.busy(root, {activeElement: outside.a}) === false);

  // ── unsaved values, with focus already gone ──────────────────────────
  //
  // This is the half that loses OTHER people's work: a field filled in,
  // tabbed out of, not yet saved. Focus is elsewhere, so a focus-only guard
  // repaints and the value is gone.
  b.value = 'typed but not saved';
  claim('a filled-in field that has not been saved holds the repaint',
    live.busy(root, {activeElement: null}) === true,
    'a value that differs from what was rendered was thrown away');

  b.value = '';
  claim('…and once it matches what was rendered, the repaint may run',
    live.busy(root, {activeElement: null}) === false);

  // ── the deferral must not be forever ─────────────────────────────────
  claim('a held repaint is remembered rather than dropped',
    typeof live.defer === 'function');

  let ran = 0;
  const paint = () => { ran++; };
  live.defer(root, paint, {activeElement: a});
  claim('a repaint during typing does not run', ran === 0);
  live.release(root, {activeElement: null});
  claim('…and runs as soon as the field is left', ran === 1);

  // Two polls landing while somebody types must not queue two repaints.
  ran = 0;
  live.defer(root, paint, {activeElement: a});
  live.defer(root, paint, {activeElement: a});
  live.defer(root, paint, {activeElement: a});
  live.release(root, {activeElement: null});
  claim('three held repaints collapse to one', ran === 1, `${ran} ran`);

  // Releasing with nothing held must not repaint out of nowhere.
  ran = 0;
  live.release(root, {activeElement: null});
  claim('releasing with nothing held does nothing', ran === 0);
}

console.log(fails ? `\n${fails} failed` : '\nall passed');
process.exit(fails ? 1 : 0);
