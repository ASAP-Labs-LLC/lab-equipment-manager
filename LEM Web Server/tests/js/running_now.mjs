// static/js/running_now.js: the "Running now" line in the sidebar foot and
// its list, from /api/ui/live's `jobs`. Ported from GC hub's
// tests/js/running_now.test.js: the task fixtures and every assertion on the
// indicator, the outcome line, who/when and per-browser dismissal are GC's
// (the jobs registry serves GC's task shape on purpose). Not ported: GC's
// agent strip and processing-paused banner, which LEM does not have (the
// fleet line and status.js's banner are LEM's; see tests/js/status.mjs).
// LEM adds `pct` to the summary, which the strip's progress bar uses.
//
// Why: an ended job must read as ONE outcome line ("Log copy filled · 41,903
// rows"), not as the title plus a frozen last phase; and a dismissal must not
// outlive a server restart that reuses the job id.
import { createRequire } from 'module';
import assert from 'assert';
const require = createRequire(import.meta.url);
const R = require('../../static/js/running_now.js');
let n = 0;
const t = { eq(a, b) { assert.deepStrictEqual(a, b); n++; } };


const T = (over) => Object.assign({ id: 'import-history:3', kind: 'import-history',
    title: 'Importing GC-2 history', instrument: 'gc2', state: 'running',
    progress: { done: 3000, total: 12000, text: 'Classifying samples' }, by: 'Ryan C',
    started_at: '2026-09-29T14:02:00+00:00', ended_at: null, open_url: '/admin/hub#import-history',
    download_url: null, mine: true, outcome: null }, over || {});
const ENDED = (over) => T(Object.assign({ state: 'done', ended_at: '2026-09-29T14:05:00+00:00',
    progress: { done: 1, total: 1, text: '1 of 1 re-processed' },
    title: 'Re-process · 1 sample', outcome: 'Re-processed 1 sample' }, over || {}));


    t.eq(R.fmtNum(3000), '3,000');
    t.eq(R.fmtNum(1234567), '1,234,567');
    t.eq(R.fmtNum(12), '12');

    // ── a running task: its title, then its progress in words ──
    t.eq(R.progressText(T()), 'Classifying samples · 3,000 of 12,000');
    t.eq(R.progressText(T({ progress: { done: 2, total: 5, text: null } })), '2 of 5');
    t.eq(R.progressText(T({ progress: { done: null, total: null, text: 'Scanning the folder' } })),
         'Scanning the folder');
    t.eq(R.progressText(T({ progress: null })), 'Starting…');

    // ── an ended task: ONE outcome line (the hub's, with counts), never the
    //    title plus the last phase ("Re-process · 1 sample … Finished · 1 of 1") ──
    t.eq(R.headline(T()), 'Importing GC-2 history');
    t.eq(R.headline(ENDED()), 'Re-processed 1 sample');
    t.eq(R.detailLine(ENDED()), null);
    t.eq(R.detailLine(T()), 'Classifying samples · 3,000 of 12,000');
    // an older hub without `outcome`: title + state, still one line
    t.eq(R.headline(ENDED({ outcome: undefined, title: 'Diagnostics bundle', state: 'failed' })),
         'Diagnostics bundle failed');
    t.eq(R.headline(ENDED({ outcome: undefined, title: 'Report ZIP', state: 'done' })),
         'Report ZIP finished');

    // who and when: started while running, finished once ended (local time)
    const hm = (iso) => { const d = new Date(iso); return String(d.getHours()).padStart(2, '0') + ':' +
                                                        String(d.getMinutes()).padStart(2, '0'); };
    t.eq(R.metaLine(T()), 'Ryan C · started ' + hm('2026-09-29T14:02:00+00:00'));
    t.eq(R.metaLine(ENDED()), 'Ryan C · finished ' + hm('2026-09-29T14:05:00+00:00'));
    t.eq(R.metaLine(ENDED({ state: 'failed', by: null })), 'ended ' + hm('2026-09-29T14:05:00+00:00'));

    // ── the popover's title ──
    t.eq(R.popoverTitle([T(), ENDED()]), 'Running now');
    t.eq(R.popoverTitle([ENDED()]), 'Recent work');

    // ── the collapsed indicator: most urgent first ──
    t.eq(R.summary([]), null);
    // the phase names its count, so a count restarting in the next phase reads right (lane E2 review)
    t.eq(R.summary([T({ progress: { done: 3000, total: 12000, text: null } })]).text,
        'Importing GC-2 history · 3,000/12,000');
    t.eq(R.summary([T({ progress: { done: 1500, total: 5000, text: 'Reading CDFs' } })]).text,
        'Importing GC-2 history · Reading CDFs 1,500/5,000');
    t.eq(R.summary([T()]), { text: 'Importing GC-2 history · Classifying samples 3,000/12,000', glyph: 'spinner',
                             state: 'running', count: 1, pct: 25 });
    t.eq(R.summary([T({ progress: { done: null, total: null, text: 'Scanning the folder' } })]).text,
         'Importing GC-2 history · Scanning the folder');
    t.eq(R.summary([T({ progress: null })]).text, 'Importing GC-2 history');
    t.eq(R.summary([T(), T({ id: 'reprocess:1' }), ENDED({ id: 'x' })]),
         { text: '2 running', glyph: 'spinner', state: 'running', count: 3, pct: null });
    t.eq(R.summary([ENDED({ outcome: 'GC-1 dry run finished · 40 classified' })]),
         { text: 'GC-1 dry run finished · 40 classified', glyph: 'dot', state: 'done', count: 1, pct: null });
    t.eq(R.summary([ENDED({ state: 'failed', outcome: 'Diagnostics bundle failed' })]).glyph, 'triangle');
    t.eq(R.summary([ENDED({ state: 'stopped' })]).glyph, 'ring');
    t.eq(R.summary([ENDED({ state: 'interrupted' })]).glyph, 'ring');

    // ── dismissing: per browser, keyed by the hub's boot id and the task id ──
    const tasks = [T(), ENDED({ id: 'reprocess:1' }), ENDED({ id: 'zip:2', state: 'failed' })];
    const dismissed = new Set(['b00t:reprocess:1', 'other:zip:2', 'b00t:import-history:3']);
    t.eq(R.visibleTasks(tasks, dismissed, 'b00t').map(x => x.id), ['import-history:3', 'zip:2']);
    t.eq(R.dismissKey('b00t', T()), 'b00t:import-history:3');
    const mem = { v: {}, getItem(k) { return this.v[k] === undefined ? null : this.v[k]; },
                  setItem(k, v) { this.v[k] = String(v); } };
    t.eq([...R.loadDismissed(mem)], []);
    R.saveDismissed(new Set(['a:1', 'b:2']), mem);
    t.eq([...R.loadDismissed(mem)].sort(), ['a:1', 'b:2']);
    mem.v[R.DISMISS_KEY] = '{not json';
    t.eq([...R.loadDismissed(mem)], []);
    const broken = { getItem() { throw new Error('no'); }, setItem() { throw new Error('no'); } };
    t.eq([...R.loadDismissed(broken)], []);
    R.saveDismissed(new Set(['x']), broken);                       // no throw
    const many = new Set(Array.from({ length: 500 }, (_, i) => 'b:' + i));
    R.saveDismissed(many, mem);
    t.eq(R.loadDismissed(mem).size, R.DISMISS_MAX);

// LEM: a job registered by jobs.py, exactly as /api/ui/live serves it
const fromServer = { id: 'log-copy:1', kind: 'log-copy', title: 'Filling the log copy', state: 'running',
    progress: { done: null, total: null, text: '40,000 rows so far' }, by: null,
    started_at: '2026-10-01T13:00:00-07:00', ended_at: null, open_url: '/logs', download_url: null, outcome: null };
t.eq(R.summary([fromServer]).text, 'Filling the log copy · 40,000 rows so far');
t.eq(R.summary([fromServer]).pct, null);          // no total: no invented percentage
t.eq(R.headline(Object.assign({}, fromServer, { state: 'done', outcome: 'Log copy filled · 41,903 rows' })),
     'Log copy filled · 41,903 rows');
t.eq(R.DISMISS_KEY, 'lem.running-dismissed');      // never GC's key: both apps open in one browser
console.log('running_now.mjs: ' + n + ' checks passed');
