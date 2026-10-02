// static/js/status.js: the words the shell says from one /api/ui/live answer.
//
// Three promises are checked here, each a judge's complaint about an earlier
// design:
// 1. Say it once (ia-final §0.2). The mock showed "Opening 3/5" in the nav
//    beside "4 of 5 done" on the page. The nav's count and a page's count are
//    handed out by ONE call from ONE payload field, and this test reads both
//    back and asserts the numbers are equal, for every case. The same cases
//    are run against the server's first paint (tests/test_ui_live.py reads
//    tests/fixtures/nav_meta_cases.json too), so a reload and the poll agree.
// 2. The banner never claims currency (§2). A degraded page cannot know it
//    is current, so no banner text may say "current", "up to date" or
//    "everything"; each one names the time the record is from.
// 3. The live row: Reconnecting within 15 s of the server dying, never Live
//    after 90 s.
import { createRequire } from 'module';
import assert from 'assert';
import fs from 'fs';
const require = createRequire(import.meta.url);
const S = require('../../static/js/status.js');
const L = require('../../static/js/live.js');
let n = 0;
const t = { eq(a, b, why) { (why === undefined ? assert.deepStrictEqual(a, b) : assert.deepStrictEqual(a, b, String(why))); n++; } };

// ── 1. one field, one count ─────────────────────────────────────────────────
const cases = JSON.parse(fs.readFileSync(new URL('../fixtures/nav_meta_cases.json', import.meta.url), 'utf8'));
for (const c of cases) t.eq(S.navMeta(c.payload), c.nav, c.why);

const num = (s) => (s && s.match(/\d+/g) || []).map(Number);
const PAYLOADS = cases.map(c => c.payload).concat([
    { needs_you: 12, round: { slot: 'opening', done: 4, total: 5, complete: false }, qc_out: 3 },
    { needs_you: 2, round: { slot: 'opening', done: 0, total: 6, complete: false }, qc_out: 0 },
]);
for (const p of PAYLOADS) {
    const nav = {};
    const page = {};
    S.counts(p, (k, m) => { nav[k] = m; }, (f, text) => { page[f] = text; });
    // every nav item was told something (null clears it), and every page field too
    t.eq(Object.keys(nav).sort(), ['checklists', 'instruments', 'qc']);
    t.eq(Object.keys(page).sort(), ['needs_you', 'qc_out', 'round']);
    // instruments: the badge and the page's count are the same number, from needs_you
    if (nav.instruments) t.eq(num(nav.instruments.badge), num(page.needs_you).slice(0, 1));
    else t.eq(p.needs_you === null ? page.needs_you : num(page.needs_you).length, p.needs_you === null ? null : 0);
    // the round: "Opening 3/5" in the nav, "3 of 5 done" on the page
    if (nav.checklists && nav.checklists.text !== 'Done') {
        t.eq(num(nav.checklists.text), num(page.round));
        t.eq(num(page.round), [p.round.done, p.round.total]);
    }
    if (nav.checklists && nav.checklists.text === 'Done') t.eq(page.round, 'Done');
    // QC
    if (nav.qc) t.eq(num(nav.qc.badge), num(page.qc_out));
    // unknown is unknown in BOTH places
    if (p.round === null) t.eq([nav.checklists, page.round], [null, null]);
    if (p.qc_out === null) t.eq([nav.qc, page.qc_out], [null, null]);
}
// one field moving moves both, and nothing else
{
    const a = { needs_you: 6, round: { slot: 'opening', done: 3, total: 5 }, qc_out: 1 };
    const b = Object.assign({}, a, { round: { slot: 'opening', done: 4, total: 5 } });
    const run = (p) => { const o = {}; S.counts(p, (k, m) => { o['nav.' + k] = m; }, (f, x) => { o['page.' + f] = x; }); return o; };
    const ra = run(a), rb = run(b);
    const moved = Object.keys(ra).filter(k => JSON.stringify(ra[k]) !== JSON.stringify(rb[k])).sort();
    t.eq(moved, ['nav.checklists', 'page.round']);
    t.eq(rb['nav.checklists'].text, 'Opening 4/5');
    t.eq(rb['page.round'], '4 of 5 done');
}

// ── 2. the banner ───────────────────────────────────────────────────────────
const NOW = Date.parse('2026-10-01T13:05:00-07:00');
const TZ = 'America/Los_Angeles';
const P = (over) => Object.assign({ snapshot_age: 4, read_at: NOW, snapshot_at: '2026-10-01T13:04:56-07:00',
    snapshot_stale: false, labcore_online: true, lab_tz: TZ,
    mirror: { state: 'filled', rows: 41903, complete_to: '2026-10-01T20:00:00+00:00' } }, over || {});
const ok = { connected: true, last_ok_at: NOW - 1000, error: null };

t.eq(S.bannerText(P(), ok, NOW, NOW - 60000), null, 'healthy data: no banner');
t.eq(S.bannerText(null, { connected: false, last_ok_at: 0, error: null }, NOW, NOW - 1000), null,
     'a page that just opened is not degraded');
const texts = [
    S.bannerText(P({ labcore_online: false, snapshot_age: 180, snapshot_at: '2026-10-01T13:02:00-07:00' }), ok, NOW),
    S.bannerText(P({ labcore_online: false, snapshot_age: null, snapshot_at: null }), ok, NOW),
    S.bannerText(P({ snapshot_stale: true, snapshot_age: 300, snapshot_at: '2026-10-01T13:00:00-07:00' }), ok, NOW),
    S.bannerText(P({ mirror: { state: 'filling', rows: 40000, held_to: '2026-09-02T10:00:00-07:00' } }), ok, NOW),
    S.bannerText(P({ mirror: { state: 'partial', rows: 12, held_to: null } }), ok, NOW),
    S.bannerText(P({ mirror: { state: 'behind', rows: 9, complete_to: '2026-10-01T12:00:00-07:00', reason: 'x' } }), ok, NOW),
    S.bannerText(P(), { connected: false, last_ok_at: NOW - 40000, error: 'Failed to fetch' }, NOW),
    S.bannerText(null, { connected: false, last_ok_at: 0, error: 'Failed to fetch' }, NOW, NOW - 20000),
];
t.eq(texts[0], "Showing the record as of 13:02. LabCore has not answered for 3 min; changes can't be saved until it does.");
t.eq(texts[1], "Nothing has been read from LabCore yet, and it is not answering. Changes can't be saved until it does.");
t.eq(texts[2], 'Showing the record as of 13:00. LabCore is slow to answer; the record has not refreshed for 5 min.');
t.eq(texts[3], 'The log copy is filling (40,000 rows so far, up to 2 Sep 10:00). History after that is not in the copy yet.');
t.eq(texts[4], 'The log copy is filling (12 rows so far). Searches of the whole log are incomplete until it finishes.');
t.eq(texts[5], 'The log copy has not refreshed since 12:00; searches of the whole log may miss the newest events.');
t.eq(texts[6], "LEM has not answered for 40 s. This page shows the record as of 13:04; changes can't be saved until LEM answers.");
t.eq(texts[7], "LEM is not answering. Nothing on this page is updating, and changes can't be saved.");
for (const x of texts) {
    t.eq(typeof x === 'string' && x.length > 0, true);
    t.eq(/\bcurrent|up to date|up-to-date|everything|all good|in sync/i.test(x), false, 'banner claims currency: ' + x);
}
// worst first: LEM itself not answering outranks LabCore
t.eq(S.bannerText(P({ labcore_online: false }), { connected: false, last_ok_at: NOW - 20000 }, NOW).startsWith('LEM has not answered'), true);
// the age keeps growing between answers (read_at + the server's age)
t.eq(S.recordAge(P({ snapshot_age: 10, read_at: NOW - 5000 }), NOW), 15);
t.eq(S.recordAge(P({ snapshot_age: null }), NOW), null);
// the clock is the lab's, not the browser's: 20:02 UTC is 13:02 in the lab
t.eq(S.clock('2026-10-01T20:02:00+00:00', NOW, TZ), '13:02');
t.eq(S.clock('2026-09-30T20:02:00+00:00', NOW, TZ), '30 Sep 13:02');
t.eq(S.clock('garbage', NOW, TZ), null);

// ── 3. the live row ─────────────────────────────────────────────────────────
t.eq(S.liveWords({ connected: true, last_ok_at: NOW - 8000 }, NOW), { state: 'fresh', text: 'Live · updated 8 s ago' });
t.eq(S.liveWords({ connected: false, last_ok_at: NOW - 12000 }, NOW), { state: 'error', text: 'Reconnecting… · last update 12 s ago' });
t.eq(S.liveWords({ connected: false, last_ok_at: 0 }, NOW), { state: 'connecting', text: 'Connecting…' });
for (let s = 91; s < 600; s += 7) {
    t.eq(S.liveWords({ connected: true, last_ok_at: NOW - s * 1000 }, NOW).state, 'error', 'Live after ' + s + ' s');
}
// "Live · updated just now" with a green dot over a record LabCore stopped
// refreshing told the opposite of the banner beside it (round 3's critic).
// LEM answering is live; the RECORD it answers with is not, so the dot goes
// hollow and the words say how old the record is, in the lab's clock.
{
    const stale = { labcore_online: false, snapshot_at: '2026-10-01T20:02:00+00:00', lab_tz: TZ };
    t.eq(S.liveWords({ connected: true, last_ok_at: NOW - 2000 }, NOW, stale),
         { state: 'stale', text: 'Live · record as of 13:02' });
    t.eq(S.liveWords({ connected: true, last_ok_at: NOW - 2000 }, NOW,
                     Object.assign({}, stale, { labcore_online: true, snapshot_stale: true })).state, 'stale');
    t.eq(S.liveWords({ connected: true, last_ok_at: NOW - 8000 }, NOW,
                     { labcore_online: true, snapshot_stale: false, snapshot_at: stale.snapshot_at }),
         { state: 'fresh', text: 'Live · updated 8 s ago' });
    // nothing read yet: no time to give, so the words stay LEM's own
    t.eq(S.liveWords({ connected: true, last_ok_at: NOW - 2000 }, NOW, { labcore_online: false }).state, 'fresh');
    // LEM not answering still outranks an old record
    t.eq(S.liveWords({ connected: false, last_ok_at: NOW - 12000 }, NOW, stale).state, 'error');
}
t.eq(S.fleet({ fleet: { checking_in: 15, total: 17, live_road: 0 } }), { checking: 15, total: 17 });
t.eq(S.fleet({ fleet: null }), null);
t.eq(S.dur(59), '59 s');
t.eq(S.dur(61), '1 min');
t.eq(S.dur(7300), '2 h');
t.eq(L.FIELDS.includes('nav_meta'), true);

console.log('status.mjs: ' + n + ' checks passed');
