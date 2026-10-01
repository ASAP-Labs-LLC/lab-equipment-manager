// The shell's pure logic (static/js/ui_logic.js), ported from GC hub's
// ui_logic.test.js and cut down to what LEM's shell uses: relative and clock
// times, initials, Recent, the theme and sidebar choices, and the words the
// status strip says when the sidebar is only icons.
//
// Why these are tested here and not only in a browser: every one of them is a
// sentence somebody reads off a wall or a tablet. "Updated 4 s ago" that says
// "NaN s ago" after a junk timestamp, or a Recent list that will follow a
// `//evil.example` link someone planted in localStorage, are both bugs a
// screenshot does not catch.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/ui_logic.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const U = root.LEMUi;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

if (!U) { console.log('  FAIL ui_logic.js does not define window.LEMUi'); process.exit(1); }

const now = Date.parse('2026-09-30T12:00:00Z');

// ── relative time (GC's wording, unchanged) ────────────────────────────────
check('just now', U.relTime('2026-09-30T11:59:58+00:00', now), 'just now');
check('seconds', U.relTime('2026-09-30T11:59:48+00:00', now), '12 s ago');
check('minutes', U.relTime('2026-09-30T11:56:00+00:00', now), '4 min ago');
check('hours', U.relTime('2026-09-30T09:00:00+00:00', now), '3 h ago');
check('days', U.relTime('2026-09-28T12:00:00+00:00', now), '2 d ago');
check('a clock a hair ahead is just now', U.relTime('2026-09-30T12:00:30+00:00', now), 'just now');
check('no time is no sentence, not "NaN"', U.relTime(null, now), null);
check('junk is no sentence either', U.relTime('garbage', now), null);
check('90 s is the one liveness rule', U.LIVE_SECONDS, 90);

// ── initials for the user chip ─────────────────────────────────────────────
check('two words', U.initials('Ryan Cunningham'), 'RC');
check('one word', U.initials('ann'), 'A');
check('nobody', U.initials(''), '?');

// ── Recent "on this computer" ──────────────────────────────────────────────
// Recent is read back out of localStorage, which anything on this origin can
// write. A stored href is followed on click, so only same-site paths survive.
const clean = U.recentClean([
  { href: '/instruments/abc', label: 'Agilent GC 1' },
  { href: '//evil.example/x', label: 'protocol-relative' },
  { href: 'https://evil.example', label: 'absolute' },
  { href: '/\\evil', label: 'backslash' },
  { href: 'javascript:alert(1)', label: 'script' },
  { href: '/logs', label: 42 },
  null, 'junk',
]);
check('only same-site paths with a text label survive', clean.map(r => r.href), ['/instruments/abc']);
check('not an array is an empty list', U.recentClean({}), []);
let list = [];
list = U.recentAdd(list, { href: '/a', label: 'A' }, 6, 1);
list = U.recentAdd(list, { href: '/b', label: 'B' }, 6, 2);
list = U.recentAdd(list, { href: '/a', label: 'A again' }, 6, 3);
check('re-opening moves it to the top, once', list.map(r => r.label), ['A again', 'B']);
for (let i = 0; i < 10; i++) list = U.recentAdd(list, { href: '/n' + i, label: 'N' + i }, 6, 10 + i);
check('at most six', list.length, 6);
check('labels are clipped, never trusted to be short',
      U.recentAdd([], { href: '/x', label: 'x'.repeat(200) }, 6, 1)[0].label.length, 60);

// ── theme: System (the default), Light, Dark ───────────────────────────────
check('nobody chose: System', U.themeChoice(null), 'system');
check('junk in storage: System', U.themeChoice('purple'), 'system');
check('a choice is kept', U.themeChoice('dark'), 'dark');
check('System follows a dark OS', U.resolveTheme('system', true), 'dark');
check('System follows a light OS', U.resolveTheme('system', false), 'light');
check('Light ignores the OS', U.resolveTheme('light', true), 'light');
check('Dark ignores the OS', U.resolveTheme('dark', false), 'dark');
check('three choices, in the order the control shows them', U.THEME_CHOICES, ['system', 'light', 'dark']);

// ── sidebar: Automatic (the default), Full, Icons only ─────────────────────
check('nobody chose: automatic', U.sidebarChoice(null), 'auto');
check('junk: automatic', U.sidebarChoice('wide'), 'auto');
check('rail kept', U.sidebarChoice('rail'), 'rail');
check('full kept', U.sidebarChoice('full'), 'full');

// ── rail items keep their words for a screen reader ────────────────────────
// In the rail the label and the nav-meta are hidden, so the aria-label is the
// only place "6 need you" still exists for someone not looking at the badge.
check('meta joins the name', U.railLabel('Instruments', '6 need you'), 'Instruments, 6 need you');
check('no meta, just the name', U.railLabel('Log', ''), 'Log');
check('whitespace meta is no meta', U.railLabel('Log', '   '), 'Log');

// ── the benches line ──────────────────────────────────────────────────────
check('some quiet', U.fleetText(15, 17), '15 of 17 benches checking in');
check('one bench, singular', U.fleetText(1, 1), '1 of 1 bench checking in');
check('none checking in is a real answer', U.fleetText(0, 4), '0 of 4 benches checking in');
check('no instruments: nothing to say', U.fleetText(0, 0), null);
check('unknown is not zero', U.fleetText(null, 17), null);

// ── the record's age, in words (before the live feed is wired) ─────────────
// This shell does not poll yet, so it must never say "Live". It says how old
// the record it was drawn from is, and that age keeps growing on screen.
const at = '2026-09-30T11:59:56+00:00';
check('fresh record', U.recordStatus({ at, labcore: 'reachable' }, now),
      { state: 'fresh', text: 'Updated just now' });
check('it ages on screen', U.recordStatus({ at, labcore: 'reachable' }, now + 64000),
      { state: 'fresh', text: 'Updated 1 min ago' });
check('past 90 s it is no longer fresh', U.recordStatus({ at, labcore: 'reachable' }, now + 120000).state, 'stale');
check('never says Live without a feed',
      /live/i.test(U.recordStatus({ at, labcore: 'reachable' }, now).text), false);
check('LabCore down: a failed read, said as one',
      U.recordStatus({ at, labcore: 'unreachable' }, now),
      { state: 'error', text: 'LabCore not answering · record from ' + U.clockTime(at, now) });
// A failed read is never an empty result: no record yet is its own sentence.
check('nothing read yet', U.recordStatus({ at: null, labcore: 'unknown' }, now),
      { state: 'never', text: 'Not read from LabCore yet' });
check('nothing read and LabCore down',
      U.recordStatus({ at: null, labcore: 'unreachable' }, now),
      { state: 'error', text: 'LabCore not answering · nothing read yet' });

if (fails) { console.log(`ui_logic: ${fails} failed`); process.exit(1); }
console.log('ui_logic: all passed');
