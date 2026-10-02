// static/js/notifications_panel.js: the bell's panel. Ported from GC hub's
// tests/js/notifications_panel.test.js: level words and glyphs, "when",
// newest-first ordering, the title, the badge and the bell's label are GC's
// assertions unchanged. What changed, because LEM's items carry their own
// link and are dismissed per browser: link() reads the item's href (and
// refuses anything that leaves the site), and visible/prune replace GC's
// needsReload (the items arrive in the live answer itself).
//
// Why: each item is a condition that is TRUE now. Its link has to land on
// the record that explains it (no dead ends), its level must be a word as
// well as a colour, and dismissing it on one tablet must neither hide it on
// every other screen nor survive the condition clearing and coming back.
import { createRequire } from 'module';
import assert from 'assert';
const require = createRequire(import.meta.url);
const N = require('../../static/js/notifications_panel.js');
let n = 0;
const t = { eq(a, b) { assert.deepStrictEqual(a, b); n++; } };

t.eq(N.level('error'), { glyph: 'error', word: 'Error' });
t.eq(N.level('warning'), { glyph: 'held', word: 'Warning' });
t.eq(N.level('success'), { glyph: 'final', word: 'Resolved' });
t.eq(N.level('info'), { glyph: 'never', word: 'Note' });
t.eq(N.level('whatever'), { glyph: 'never', word: 'Note' });

// the server's own link, same-site only
t.eq(N.link({ href: '/settings#diagnostics', link: 'Open Diagnostics' }), { href: '/settings#diagnostics', text: 'Open Diagnostics' });
t.eq(N.link({ href: '/floor' }), { href: '/floor', text: 'Open' });
t.eq(N.link({ href: '//evil.example/x', link: 'Open' }), null);
t.eq(N.link({ href: 'https://evil.example', link: 'Open' }), null);
t.eq(N.link({ href: '/\\evil' }), null);
t.eq(N.link({}), null);
t.eq(N.link(null), null);

const now = Date.parse('2026-09-30T15:00:00');
t.eq(N.when('2026-09-30T14:59:30', now), '30 s ago');
t.eq(N.when('2026-09-30T14:10:00', now), '50 min ago');
t.eq(N.when('2026-09-30T09:12:00', now), '09:12');
t.eq(N.when('2026-09-28T09:12:00', now), 'Sep 28 09:12');
t.eq(N.when(null, now), '');

const list = [
    { id: 'a', ts: '2026-09-30T10:00:00', level: 'info', message: 'A' },
    { id: 'b', ts: '2026-09-30T12:00:00', level: 'error', message: 'B' },
    { id: 'c', level: 'warning', message: 'C' },
];
t.eq(N.sorted(list).map((x) => x.id), ['b', 'a', 'c']);
t.eq(N.sorted(null), []);
t.eq(N.title(0), 'Notifications');
t.eq(N.title(3), 'Notifications · 3');
t.eq(N.badge(0), '');
t.eq(N.badge(7), '7');
t.eq(N.badge(150), '99+');
t.eq(N.bellLabel(2), 'Notifications: 2');
t.eq(N.bellLabel(0), 'Notifications');
t.eq(N.without(list, 'a').map((x) => x.id), ['b', 'c']);

// per-browser dismissal: hidden here, still a fact
t.eq(N.visible(list, new Set(['b'])).map((x) => x.id), ['a', 'c']);
t.eq(N.visible(list, new Set()).length, 3);
// the condition cleared and came back: a new id, so it shows again
const back = [{ id: 'notok:gc1:1790000999', ts: '2026-09-30T14:00:00', level: 'error', message: 'GC-1 is not OK to run' }];
t.eq(N.visible(back, new Set(['notok:gc1:1790000000'])).length, 1);
// dismissals of items that are gone are forgotten (the stored list stays small)
t.eq([...N.prune(new Set(['a', 'gone', 'b']), list)].sort(), ['a', 'b']);
const mem = { v: {}, getItem(k) { return this.v[k] === undefined ? null : this.v[k]; }, setItem(k, v) { this.v[k] = String(v); } };
t.eq([...N.loadDismissed(mem)], []);
N.saveDismissed(new Set(['x:1']), mem);
t.eq([...N.loadDismissed(mem)], ['x:1']);
mem.v[N.DISMISS_KEY] = '{junk';
t.eq([...N.loadDismissed(mem)], []);
const broken = { getItem() { throw new Error('no'); }, setItem() { throw new Error('no'); } };
t.eq([...N.loadDismissed(broken)], []);
N.saveDismissed(new Set(['y']), broken);                       // no throw
t.eq(N.DISMISS_KEY, 'lem.notes-dismissed');

// A page that lists instruments' problems on its own rows folds the bell's
// instrument lines (round 3's critic: on Instruments, "the bell lists the
// row problems a second time, by name and cause"). Folded lines are neither
// listed nor counted in the badge; the panel says, without a name or a
// number, that they are on the page. Other pages fold nothing.
const mixed = [
  { id: 'notok:a:1', level: 'error', message: 'A is not OK to run.', ts: '2026-09-30T14:00:00', about: 'instruments' },
  { id: 'caldue:a,b:1', level: 'warning', message: '2 instruments are overdue for calibration: A and B.', ts: '2026-09-30T13:00:00', about: 'instruments' },
  { id: 'audit:1', level: 'warning', message: '2 audit rows waiting.', ts: '2026-09-30T12:00:00', about: null },
  { id: 'round:1', level: 'warning', message: 'The morning round is overdue.', ts: '2026-09-30T11:00:00' },
];
t.eq(N.visible(mixed, new Set(), 'instruments').map(x => x.id), ['audit:1', 'round:1']);
t.eq(N.visible(mixed, new Set()).map(x => x.id).length, 4);
t.eq(N.visible(mixed, new Set(), '').length, 4);
t.eq(N.folded(mixed, new Set(), 'instruments'), true);
t.eq(N.folded(mixed, new Set(['notok:a:1', 'caldue:a,b:1']), 'instruments'), false);
t.eq(N.folded(mixed, new Set(), ''), false);
t.eq(N.foldNote('instruments'), 'Instrument problems are on this page, each on its own row.');
// the empty sentence on a folding page does not claim "no notifications"
t.eq(N.emptyText({ known: true, any: true, folded: true }), 'Nothing else. Instrument problems are on this page, each on its own row.');
t.eq(N.emptyText({ known: true, any: false, folded: false }), 'No notifications. Instruments that stop being OK to run, overdue rounds and LabCore trouble will show here.');
t.eq(N.emptyText({ known: false }), 'Checking for notifications…');
t.eq(N.emptyText({ known: true, any: true, folded: false }), 'Nothing new. Everything here was dismissed on this computer.');

console.log('notifications_panel.mjs: ' + n + ' checks passed');
