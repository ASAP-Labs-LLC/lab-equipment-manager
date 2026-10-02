// The Instruments home's pure logic: no DOM, node-tested (tests/js/instruments.mjs).
// Window global LEMInstruments; module.exports for node. The page
// (static/js/instruments.js) sets every string it gets from here with
// textContent.
//
//   parseView(search) / viewQuery(view)   the address bar is the view (§1)
//   filterRows(rows, view)                which instruments a view shows
//   chips(data, view, causeWords)         the view chips: places and views, never counts
//   tileWords(tile, view)                 what a Needs-you tile draws: cause, next step, link
//   needsCaption(needsYou, failedAt)      the card's caption: never a count
//   when(iso, nowMs)                      "09:05", "Wed 15:04", "3 Aug"
//   searchNote / searchEmpty / searchFailed   what the find box searched, in words
//   searchRows(answer, hrefFor, opts)     /api/search results as links
//   showLoading(ms) / isFindKey(ev)       the 180 ms rule and Ctrl K
(function (root) {
    'use strict';

    const FILTERS = ['needs', 'noqc', 'maintenance', 'quiet'];
    const SAFE_KEY = /^[A-Za-z0-9_-]{1,64}$/;

    function parseView(search) {
        let p;
        try { p = new URLSearchParams(search || ''); } catch (_e) { p = new URLSearchParams(''); }
        const filter = p.get('filter') || '';
        const level = p.get('level') || '';
        const cause = p.get('cause') || '';
        return {
            filter: FILTERS.includes(filter) ? filter : '',
            level: SAFE_KEY.test(level) ? level : '',
            cause: SAFE_KEY.test(cause) ? cause : '',
        };
    }

    function viewQuery(view) {
        const p = new URLSearchParams();
        if (view && view.filter) p.set('filter', view.filter);
        if (view && view.level) p.set('level', view.level);
        if (view && view.cause) p.set('cause', view.cause);
        const s = p.toString();
        return s ? '?' + s : '';
    }

    function _matches(r, view) {
        const st = r.readiness && r.readiness.state;
        switch (view.filter) {
            case 'needs': if (!r.needs_you) return false; break;
            case 'noqc': if (st !== 'no_qc') return false; break;
            case 'maintenance': if (!(r.maintenance > 0)) return false; break;
            case 'quiet': if (!r.bench || r.bench.state === 'in') return false; break;
            default: break;
        }
        if (view.level && r.level_uid !== view.level) return false;
        if (view.cause && !(r.cause && r.cause.key === view.cause)) return false;
        return true;
    }

    function filterRows(rows, view) {
        const v = Object.assign({ filter: '', level: '', cause: '' }, view || {});
        return (rows || []).filter(r => _matches(r, v));
    }

    const VIEW_LABELS = { '': 'All', needs: 'Needs you', noqc: 'No QC assigned', maintenance: 'Maintenance' };
    const HIDDEN_LABELS = { quiet: 'Not checking in' };

    /** The chip row. Each: {group, label, pressed, next (the view a click
        goes to), clears (a removable view with no chip of its own)}.
        Labels are words only; a count here was the "three times" defect. */
    function chips(data, view, causeWords) {
        const v = Object.assign({ filter: '', level: '', cause: '' }, view || {});
        const out = [];
        const views = ['', 'needs', 'noqc'];
        if (data && data.has_maintenance) views.push('maintenance');
        // a view reached by a link (the fleet line, a merged tile) shows as a
        // pressed chip that clears, so the list never filters invisibly
        if (v.cause) {
            out.push({ group: 'view', label: (causeWords && causeWords[v.cause]) || 'This cause',
                       pressed: true, clears: true, next: { filter: '', level: v.level, cause: '' } });
        } else if (HIDDEN_LABELS[v.filter]) {
            out.push({ group: 'view', label: HIDDEN_LABELS[v.filter], pressed: true, clears: true,
                       next: { filter: '', level: v.level, cause: '' } });
        }
        for (const f of views) {
            out.push({ group: 'view', label: VIEW_LABELS[f],
                       pressed: !v.cause && v.filter === f && !(f === '' && HIDDEN_LABELS[v.filter]),
                       clears: false, next: { filter: f, level: v.level, cause: '' } });
        }
        // "All" means the whole list: it clears the level too
        const all = out.find(c => c.label === 'All' && !c.clears);
        if (all) {
            all.next = { filter: '', level: '', cause: '' };
            all.pressed = !v.filter && !v.level && !v.cause;
        }
        for (const lv of (data && data.levels) || []) {
            const on = v.level === lv.uid;
            out.push({ group: 'level', label: lv.name, pressed: on, clears: false,
                       next: { filter: v.filter, level: on ? '' : lv.uid, cause: v.cause } });
        }
        return out;
    }

    const lowerFirst = (s) => { s = String(s || ''); return s ? s.charAt(0).toLowerCase() + s.slice(1) : s; };

    /** A Needs-you tile's words. Its cause, the next step for that cause and
        a link that filters the table to its rows; never a member's name and
        never a count (the rows name them, the pill counts the fleet). The
        tile whose cause is the current view is pressed, and its link is the
        way back to the whole list. */
    function tileWords(t, view) {
        const active = !!(view && view.cause && t && t.key === view.cause);
        if (t && t.key === 'more') {
            return { head: t.cause, next: (t.next && t.next.text) || '', link: t.link, active: false };
        }
        return { head: (t && t.cause) || '',
                 next: t && t.next && t.next.text ? 'Next: ' + lowerFirst(t.next.text) : '',
                 link: active ? 'Show all' : ((t && t.link) || 'Show them'), active };
    }

    /** The card's caption. No count: "10 need you" has its one home in the
        nav. A failed background refresh is said, never hidden. */
    function needsCaption(ny, failedAt, nowMs) {
        if (failedAt) return 'Couldn’t refresh since ' + when(failedAt, nowMs) + ' · showing the list as last read';
        return ny && ny.count ? 'Worst first · updates by itself' : '';
    }

    const pad = (n) => String(n).padStart(2, '0');
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    /** Local time, §4.3: "09:05" today, "Wed 15:04" this week, "3 Aug" before. */
    function when(iso, nowMs) {
        if (!iso || typeof iso !== 'string') return '';
        const at = Date.parse(iso);
        if (isNaN(at)) return '';
        const d = new Date(at);
        const n = new Date(nowMs === undefined ? Date.now() : nowMs);
        const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
        const day0 = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
        const days = Math.round((day0(n) - day0(d)) / 86400000);
        if (days === 0) return hm;
        if (days > 0 && days < 7) return DAYS[d.getDay()] + ' ' + hm;
        return d.getDate() + ' ' + MONTHS[d.getMonth()];
    }

    const fmtInt = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

    /** One sentence under the results: what was searched. */
    function searchNote(a) {
        if (!a || a.warming) return 'LEM has not read the instruments yet, so nothing was searched. Try again in a moment.';
        if (a.searched_all_time) return 'Searched the whole record, back to the first entry.';
        const c = a.corpus || {};
        if (c.partial) return 'The log is still being read; samples may be missing until it is.';
        if (c.stale) return 'The last log read failed; this searched the copy from before it.';
        if (c.truncated) return 'Searched the newest ' + fmtInt(c.rows || 0) + ' log entries; an exact Lab ID is looked up further back.';
        return 'Searched the whole record.';
    }

    function searchEmpty(a, q) {
        if (!a || a.warming) return searchNote(a);
        if (a.state === 'short') return 'Type two or more letters.';
        const c = a.corpus || {};
        if (c.truncated && !a.searched_all_time) {
            return 'Nothing matches “' + q + '” in the newest ' + fmtInt(c.rows || 0) + ' log entries.';
        }
        return 'Nothing matches “' + q + '”.';
    }

    function searchFailed(why) {
        return 'Couldn’t search: ' + (why || 'LEM did not answer') + '. This is not “no match”.';
    }

    const KIND = { equipment: 'Instrument', sample: 'Lab ID', standard: 'Standard', method: 'Method',
                   operator: 'Person', level: 'Level' };
    const MAX_PER_RESULT = 3;
    const ORDER = [KIND.equipment, KIND.sample, KIND.method, KIND.standard, KIND.level, KIND.operator];

    /** /api/search results as rows {kind, label, detail, href}. A result
        that spans several instruments becomes one row per instrument (up to
        three), so every row lands on a record, never on a chooser. */
    function searchRows(a, hrefFor, opts) {
        const out = [];
        const hasQuality = !!(opts && opts.hasQuality);
        for (const r of (a && a.results) || []) {
            const kind = r.kind;
            const label = String(r.label || r.lab_id || r.id || '');
            const ms = Array.isArray(r.machines) && r.machines.length ? r.machines
                : (r.machine_uid ? [{ machine_uid: r.machine_uid, title: r.title || r.machine_title || r.machine_uid }] : []);
            if (kind === 'equipment') {
                out.push({ kind: KIND.equipment, label, detail: '', href: hrefFor(r.machine_uid || r.id, '') });
            } else if (kind === 'level') {
                out.push({ kind: KIND.level, label, detail: '', href: '/?level=' + encodeURIComponent(r.id || r.level_uid || '') });
            } else if (kind === 'standard') {
                out.push({ kind: KIND.standard, label,
                           detail: ms.length ? 'used on ' + ms.length + (ms.length === 1 ? ' instrument' : ' instruments') : '',
                           href: hasQuality ? '/quality/standards/' + encodeURIComponent(label) : '/qc' });
            } else if (kind === 'operator') {
                out.push({ kind: KIND.operator, label, detail: 'in the log', href: '/logs' });
            } else if (kind === 'sample' || kind === 'method') {
                const sec = kind === 'sample' ? 'log' : 'qc';
                for (const m of ms.slice(0, MAX_PER_RESULT)) {
                    const title = m.title || m.machine_uid;
                    const extra = (kind === 'sample' && r.test_name) ? ' · ' + r.test_name : '';
                    out.push({ kind: KIND[kind], label, detail: 'on ' + title + extra, href: hrefFor(m.machine_uid, sec) });
                }
            }
        }
        // instruments first, then Lab IDs: what this box is named for. Stable
        // inside a kind, so the server's best match stays on top of its kind.
        return out.filter(r => typeof r.href === 'string' && r.href.charAt(0) === '/')
            .map((r, i) => [ORDER.indexOf(r.kind), i, r]).sort((a, b) => a[0] - b[0] || a[1] - b[1])
            .map(x => x[2]);
    }

    const LOADING_AFTER_MS = 180;
    function showLoading(elapsedMs) { return elapsedMs >= LOADING_AFTER_MS; }

    function isFindKey(ev) {
        return !!ev && !!(ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey &&
            String(ev.key || '').toLowerCase() === 'k';
    }

    const api = { parseView, viewQuery, filterRows, chips, tileWords, needsCaption, when, searchNote, searchEmpty, searchFailed,
                  searchRows, showLoading, isFindKey, LOADING_AFTER_MS };
    root.LEMInstruments = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
