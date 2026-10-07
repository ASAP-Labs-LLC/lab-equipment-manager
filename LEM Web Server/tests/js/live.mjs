// static/js/live.js: the cursor/reducer logic and the poll cadence, ported
// from GC hub's tests/js/live.test.js. The cadence, the back-off and the
// "Live / Reconnecting" words are GC's and are asserted with GC's numbers;
// the reducer is asserted over LEM's payload (/api/ui/live) instead of GC's
// samples, agents and hub.
//
// Why it matters: the live row is the one thing on every page that says
// whether anything else on the page can be believed. "Live" after the server
// died, or a count that silently stops moving, is how a lab ends up trusting
// a frozen screen.
import { createRequire } from 'module';
import assert from 'assert';
const require = createRequire(import.meta.url);
const L = require('../../static/js/live.js');

let n = 0;
const t = { eq(a, b, name) { assert.deepStrictEqual(a, b, name); n++; } };

const RESP = (over) => Object.assign({ cursor: 'b:1', reset: false, machines: [], kinds: [],
    needs_you: 2, fleet: { checking_in: 15, total: 17, live_road: 0 },
    round: { slot: 'opening', done: 3, total: 5, due: '09:30', overdue: false, complete: false },
    qc_out: 1, snapshot_age: 4.2, snapshot_at: '2026-10-01T13:02:00', snapshot_stale: false,
    labcore_online: true, mirror: { state: 'filled', rows: 10, complete_to: 'x' },
    notifications_unread: 1, notifications: [{ id: 'a:1', level: 'error', message: 'm', ts: 'x' }],
    jobs: [], version: 'v3.10.0', server_now: '2026-10-01T13:02:04-07:00', lab_tz: 'America/Los_Angeles',
    nav_meta: { instruments: { text: '2 need you', badge: '2' } } }, over || {});

// ── nextDelay: 3 s visible, 30 s hidden, backing off on failures (GC's numbers) ──
t.eq(L.nextDelay(true, 0), 3000);
t.eq(L.nextDelay(false, 0), 30000);
t.eq(L.nextDelay(true, 1), 6000);
t.eq(L.nextDelay(true, 2), 12000);
t.eq(L.nextDelay(true, 3), 12000);           // a visible tab retries at least every 12 s
t.eq(L.nextDelay(true, 10), 12000);
t.eq(L.nextDelay(false, 1), 60000);
t.eq(L.nextDelay(false, 9), 60000);
t.eq(L.nextDelay(true, -1), 3000);           // nonsense counts as none
t.eq(L.nextDelay(true, NaN), 3000);

// ── applyResponse: the first answer is a reset carrying the whole state ──
const s0 = L.initialState();
t.eq(s0.cursor, null);
let r = L.applyResponse(s0, RESP({ reset: true }));
t.eq(r.state.cursor, 'b:1');
t.eq(r.update.reset, true);
t.eq(r.update.needs_you, 2);
t.eq(r.update.round.done, 3);
t.eq(r.update.notifications.length, 1);
t.eq(r.update.boot, 'b');
t.eq(r.update.version_changed, false);
// the first answer is a reset even if the server did not say so
t.eq(L.applyResponse(s0, RESP()).update.reset, true);

// nothing changed: no update, cursor kept
const s1 = r.state;
r = L.applyResponse(s1, RESP());
t.eq(r.update, null);
t.eq(r.state.cursor, 'b:1');
// the record getting older is not a change (ages tick on their own)
t.eq(L.applyResponse(s1, RESP({ snapshot_age: 9.9, server_now: 'later' })).update, null);
// ... but the state remembers the new age
t.eq(L.applyResponse(s1, RESP({ snapshot_age: 9.9 })).state.snapshot_age, 9.9);

// changed machines: an update naming them
r = L.applyResponse(s1, RESP({ cursor: 'b:4', machines: ['gc-1'], kinds: ['machines'] }));
t.eq(r.update.reset, false);
t.eq(r.update.machines, ['gc-1']);
t.eq(r.update.kinds, ['machines']);
t.eq(r.state.cursor, 'b:4');

// each count is an update when it moves
t.eq(L.applyResponse(s1, RESP({ needs_you: 3 })).update.needs_you, 3);
t.eq(L.applyResponse(s1, RESP({ qc_out: 0 })).update.qc_out, 0);
t.eq(L.applyResponse(s1, RESP({ round: { slot: 'opening', done: 4, total: 5 } })).update.round.done, 4);
t.eq(L.applyResponse(s1, RESP({ labcore_online: false })).update.labcore_online, false);
t.eq(L.applyResponse(s1, RESP({ fleet: { checking_in: 14, total: 17, live_road: 0 } })).update.fleet.checking_in, 14);
t.eq(L.applyResponse(s1, RESP({ jobs: [{ id: 'j:1', state: 'running' }] })).update.jobs[0].id, 'j:1');
// unknown is kept as unknown, not turned into 0
t.eq(L.applyResponse(s1, RESP({ needs_you: null })).update.needs_you, null);

// notifications ride along only when they changed: absent means "same as before"
r = L.applyResponse(s1, RESP({ notifications: undefined }));
t.eq(r.update, null);
t.eq(r.state.notifications.length, 1);
r = L.applyResponse(s1, RESP({ notifications: [], notifications_unread: 0, kinds: ['notifications'] }));
t.eq(r.update.notifications, []);

// a new version (the updater installed a release): flagged, once
r = L.applyResponse(s1, RESP({ version: 'v3.10.1' }));
t.eq(r.update.version, 'v3.10.1');
t.eq(r.update.version_changed, true);
t.eq(L.applyResponse(r.state, RESP({ version: 'v3.10.1' })).update, null);
t.eq(L.applyResponse(s1, RESP({ version: 'v3.10.1', reset: true, cursor: 'z:0' })).update.version_changed, true);

// a later reset (server restarted, cursor overflow)
r = L.applyResponse(s1, RESP({ cursor: 'c:0', reset: true, machines: ['ignored'] }));
t.eq(r.update.reset, true);
t.eq(r.update.machines, []);                  // a reset cannot say what changed: reload everything
t.eq(r.state.cursor, 'c:0');

// a garbled answer changes nothing
t.eq(L.applyResponse(s1, null), { state: s1, update: null });
t.eq(L.applyResponse(s1, { reset: false }), { state: s1, update: null });
t.eq(L.applyResponse(s1, 'x'), { state: s1, update: null });
// a field the answer lacks keeps its last value
r = L.applyResponse(s1, { cursor: 'b:2', reset: false });
t.eq(r.update, null);
t.eq(r.state.cursor, 'b:2');
t.eq(r.state.fields.needs_you, 2);

// applyResponse is pure: the old state is untouched
t.eq(s1.cursor, 'b:1');
t.eq(s1.fields.needs_you, 2);

// ── pollUrl: the one line GC's client changes ──
t.eq(L.pollUrl(null), '/api/ui/live');
t.eq(L.pollUrl('b:1'), '/api/ui/live?since=b%3A1');

// ── the "Live · updated Ns ago" text (GC's) ──
const now = 1_000_000;
t.eq(L.statusText({ connected: true, last_ok_at: now - 800, error: null }, now), 'Live · updated just now');
t.eq(L.statusText({ connected: true, last_ok_at: now - 12_000, error: null }, now), 'Live · updated 12 s ago');
t.eq(L.statusText({ connected: true, last_ok_at: now - 85_000, error: null }, now), 'Live · updated 1 min ago');
// no answer for longer than three hidden-tab polls (90 s) is not "Live", whatever the flag says
t.eq(L.statusText({ connected: true, last_ok_at: now - 90_001, error: null }, now),
     'Reconnecting… · last update 1 min ago');
t.eq(L.statusText({ connected: true, last_ok_at: now - 125_000, error: null }, now),
     'Reconnecting… · last update 2 min ago');
t.eq(L.statusText({ connected: false, last_ok_at: now - 40_000, error: 'HTTP 503' }, now),
     'Reconnecting… · last update 40 s ago');
t.eq(L.statusText({ connected: false, last_ok_at: 0, error: null }, now), 'Connecting…');
t.eq(L.statusText(null, now), 'Connecting…');
t.eq(L.STALE_MS, 90_000);

// ── agoText ──
t.eq(L.agoText(null, now), 'never');
t.eq(L.agoText(now - 3_000, now), 'just now');
t.eq(L.agoText(now - 42_000, now), '42 s ago');
t.eq(L.agoText(now - 60_000 * 5, now), '5 min ago');
t.eq(L.agoText(now - 3600_000 * 3, now), '3 h ago');
t.eq(L.agoText(now - 86400_000 * 2, now), '2 d ago');
t.eq(L.agoText(now + 5_000, now), 'just now');            // a clock a little ahead
t.eq(L.agoText(new Date(now - 42_000).toISOString(), now), '42 s ago');
t.eq(L.agoText('not a date', now), 'never');

console.log('live.mjs: ' + n + ' checks passed');
