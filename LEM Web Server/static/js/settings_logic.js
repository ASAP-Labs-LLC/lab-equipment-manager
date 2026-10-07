// Settings' pure logic (ia-final §3.7, piece 11): the sentences the page
// says, with no DOM. Node-tested in tests/js/settings_logic.mjs; the page
// (static/js/settings.js) only places what these return.
//
// The rule it is built around: an import's outcome is said from `landed` /
// `not_landed` — what LabCore acknowledged — and nothing short of "2xx,
// nothing not landed, not incomplete" reads as success. notes.md records an
// import that "reported 'imported 3094' while nothing landed"; twice.
(function (root) {
    'use strict';

    const num = (n) => Number(n).toLocaleString('en-US');
    const isCount = (n) => typeof n === 'number' && isFinite(n) && n >= 0;
    const of = (n, unit) => num(n) + ' ' + (n === 1 ? unit.one : unit.many);
    const AGAIN = 'Run the same file again to bring the rest in; nothing is duplicated.';

    // LabCore's own sentence. The route appends " — <what happened to this
    // import>" (refusal_response), which this page says itself from the
    // counts, so only the part before the dash is kept.
    function reason(body) {
        const e = body && typeof body.error === 'string' ? body.error.split(' — ')[0].trim() : '';
        return e ? (/[.!?…]$/.test(e) ? e : e + '.') : '';
    }

    /** What an import did, as {tone, glyph, title, detail}.
        tone: done | partial | failed | unknown. `unit` = {one, many}. */
    function importOutcome(status, body, unit) {
        const glyph = { done: 'final', partial: 'held', failed: 'error', unknown: 'held' };
        const out = (tone, title, detail) => ({ tone, glyph: glyph[tone], title, detail: detail || '' });
        if (!status) {
            return out('unknown', 'Could not tell what landed: LEM did not answer.',
                'The import may or may not have reached LabCore. Check the history, then run the same file again; nothing is duplicated.');
        }
        if (status === 401) return out('failed', 'Not imported: you are signed out. Nothing was sent.', 'Sign in and import again.');
        const ok2xx = status >= 200 && status < 300;
        if (!body || typeof body !== 'object' || !isCount(body.landed) || !isCount(body.not_landed)) {
            if (status === 400 && body && body.error) return out('failed', 'Not imported: ' + reason(body), '');
            return out('unknown', 'Could not tell what landed (the answer was HTTP ' + status + ').',
                (reason(body) ? reason(body) + ' ' : '') + 'Check the history before running it again; nothing is duplicated if you do.');
        }
        const L = body.landed, N = body.not_landed, total = L + N;
        const histL = body.history_landed, histN = body.history_not_landed;
        const hasHist = isCount(histL) && isCount(histN) && histL + histN > 0;
        const clean = ok2xx && N === 0 && !body.incomplete && (!hasHist || histN === 0);
        if (clean) {
            if (L === 0) return out('done', 'Nothing new to import: every ' + unit.one + ' in the file is already on record.');
            const all = L === 1 ? 'The 1 ' + unit.one : (L === 2 ? 'Both ' + unit.many : 'All ' + of(L, unit));
            const hist = hasHist ? ', with ' + num(histL) + ' ticks of history' : '';
            return out('done', all + ' landed in LabCore' + hist + '.');
        }
        if (L > 0 && N === 0 && hasHist && histN > 0) {
            const all = L === 1 ? 'The 1 ' + unit.one : (L === 2 ? 'Both ' + unit.many : 'All ' + of(L, unit));
            return out('partial', all + ' landed; ' + num(histL) + ' of ' + num(histL + histN) +
                ' ticks of history did, ' + num(histN) + ' did not.', [reason(body), AGAIN].filter(Boolean).join(' '));
        }
        if (L === 0 && N > 0) {
            return out('failed', 'None landed: 0 of ' + of(total, unit) + ' are in LabCore.',
                [reason(body), AGAIN.replace('bring the rest in', 'try again')].filter(Boolean).join(' '));
        }
        if (N > 0) {
            return out('partial', 'Stopped part-way: ' + num(L) + ' of ' + of(total, unit) + ' landed, ' + num(N) + ' did not.',
                [reason(body), AGAIN].filter(Boolean).join(' '));
        }
        // 2xx-or-not, nothing reported missing, but the server says incomplete
        return out('partial', 'Not finished: ' + of(L, unit) + ' landed, and the server says the import stopped.',
            [reason(body), AGAIN].filter(Boolean).join(' '));
    }

    /** The PM dry run, as sentences, and the label of the button that does it ('' = none). */
    function pmPreview(p) {
        const errors = (p && p.errors) || [];
        const whole = errors.find((e) => !e.line);
        if (whole) return { lines: ['The file could not be read: ' + whole.error], go: '' };
        const U = { one: 'completion', many: 'completions' };
        const n = (p && p.create_count) || 0, skipped = (p && p.skipped) || 0;
        const lines = [];
        if (n) lines.push(of(n, U) + ' to add');
        else lines.push(skipped ? 'Nothing new: all ' + of(skipped, U) + ' in the file are already on record'
            : 'Nothing to add: the file has no completions LEM can match');
        if (n && skipped) lines.push(num(skipped) + ' already on record, skipped');
        const moves = ((p && p.reschedule) || []).length;
        if (moves) lines.push(num(moves) + (moves === 1 ? ' schedule moves' : ' schedules move') + ' to their latest completion');
        const un = (p && p.unmatched) || [];
        if (un.length) {
            lines.push(num(un.length) + (un.length === 1 ? ' row names' : ' rows name') + ' equipment LEM does not have: ' +
                un.slice(0, 4).map((r) => r.equipment + ' (row ' + r.line + ')').join(', ') + (un.length > 4 ? ', …' : ''));
        }
        if (errors.length) {
            lines.push(num(errors.length) + (errors.length === 1 ? ' row' : ' rows') + ' could not be read: ' +
                errors.slice(0, 3).map((e) => 'row ' + e.line + ', ' + e.error).join('; ') + (errors.length > 3 ? '; …' : ''));
        }
        return { lines, go: n ? 'Import ' + of(n, U) : '' };
    }

    /** The ladder, top floor first, with the count standing on each. A
        machine on no level, or on one that is gone, stands on the ground
        (levels.placements). `machines` null = the fleet was not read. */
    function levelRows(resp, machines) {
        const levels = ((resp && resp.levels) || []).slice()
            .sort((a, b) => (a.rank - b.rank) || String(a.name).localeCompare(String(b.name)) || String(a.uid).localeCompare(String(b.uid)));
        const ground = (resp && resp.ground_level) || (levels[0] && levels[0].uid) || '';
        const known = new Set(levels.map((l) => l.uid));
        const counts = {};
        if (Array.isArray(machines)) {
            for (const m of machines) {
                const at = known.has(m && m.level_uid) ? m.level_uid : ground;
                counts[at] = (counts[at] || 0) + 1;
            }
        }
        return levels.reverse().map((l) => ({
            uid: l.uid, name: l.name, rank: l.rank,
            isGround: l.uid === ground, isDefault: l.uid === (resp && resp.default_level),
            n: Array.isArray(machines) ? (counts[l.uid] || 0) : null,
            count: !Array.isArray(machines) ? 'Not counted yet'
                : !counts[l.uid] ? 'None' : of(counts[l.uid], { one: 'instrument', many: 'instruments' }),
        }));
    }

    function ageText(seconds) {
        if (seconds === null || seconds === undefined || !isFinite(seconds)) return 'not read yet';
        const s = Math.max(0, Math.round(seconds));
        if (s < 5) return 'just now';
        if (s < 120) return s + ' s ago';
        if (s < 7200) return Math.round(s / 60) + ' min ago';
        return Math.round(s / 3600) + ' h ago';
    }

    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    function daysText(days) {
        const d = (days || []).slice().sort((a, b) => a - b);
        if (d.length === 7) return 'Every day';
        if (!d.length) return 'No days';
        const run = d.every((v, i) => i === 0 || v === d[i - 1] + 1);
        if (run && d.length >= 3) return DAYS[d[0]] + '–' + DAYS[d[d.length - 1]];
        return d.map((i) => DAYS[i]).join(', ');
    }

    function hoursProblem(opens, closes, days) {
        if (!opens || !closes) return 'Give an opening and a closing time.';
        if (!(days || []).length) return 'Pick at least one open day.';
        if (opens >= closes) return 'The lab has to open before it closes.';
        return '';
    }

    const api = { importOutcome, pmPreview, levelRows, ageText, daysText, hoursProblem, DAYS };
    root.LEMSettingsLogic = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
