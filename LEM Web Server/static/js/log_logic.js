/* log_logic.js: the machine log's pure half (ia-final §3.1 #6, §3.6, §7).
   No DOM. Node-tested in tests/js/log_logic.mjs; drawn by log_view.js on the
   record (#log) and on /logs.

   What lives here, because both surfaces must say it the same way:
     kinds      the chips (All · Results · QC · Status · Setup) and the word
                for each kind; `result_conflict` reads "Not re-sent" and
                `reread` reads "Re-read" (the transfer guard at work, §7)
     sentences  what a Not re-sent or a Re-read row means, in English
     the sheet  raw value, correction applied, operator, calibration id and
                the rest of the detail as key/value; anything the row did not
                record says "Not recorded", never a blank and never 0
     the URL    /logs filters live in the query string (§1): parse and
                serialise round-trip, so a reload restores the view
     the count  "385 events · Kept by LEM · complete to 13:20" (§3.6) */
(function (root) {
    'use strict';

    // ── kinds (ui_log.GROUPS says the same on the server) ─────────────────
    const GROUPS = [
        { key: 'all', label: 'All', kinds: [] },
        { key: 'results', label: 'Results', kinds: ['run', 'held_expired', 'result_conflict', 'reread'] },
        { key: 'qc', label: 'QC', kinds: ['qc'] },
        { key: 'status', label: 'Status', kinds: ['status_change', 'override', 'comment'] },
        { key: 'setup', label: 'Setup', kinds: ['config', 'pm', 'calibration'] },
    ];
    const WORDS = {
        run: 'Result', qc: 'QC', status_change: 'Status', override: 'Off line / on line',
        comment: 'Comment', pm: 'PM', calibration: 'Calibration', config: 'Setup',
        held_expired: 'Gave up waiting', result_conflict: 'Not re-sent', reread: 'Re-read',
    };
    function kindWord(kind) {
        const k = String(kind || '');
        if (WORDS[k]) return WORDS[k];
        const t = k.replace(/_/g, ' ').trim();
        return t ? t.charAt(0).toUpperCase() + t.slice(1) : 'Entry';
    }
    function groupOf(kind) {
        const g = GROUPS.find(x => x.kinds.indexOf(kind) >= 0);
        return g ? g.key : null;
    }
    const isMeasure = (kind) => kind === 'qc' || kind === 'run' || kind === 'held_expired';

    // ── times ─────────────────────────────────────────────────────────────
    const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const pad = (n) => String(n).padStart(2, '0');
    // A stored ts is the bench's local wall time without an offset; one with
    // an offset (the server's own stamps) is converted to this browser's.
    function parts(iso) {
        const s = String(iso || '');
        if (/[zZ]|[+-]\d\d:?\d\d$/.test(s)) {
            const d = new Date(s);
            if (!isNaN(d)) return { y: d.getFullYear(), mo: d.getMonth(), d: d.getDate(), h: d.getHours(), mi: d.getMinutes(), s: d.getSeconds(), wd: d.getDay() };
        }
        const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(s);
        if (!m) return null;
        const y = +m[1], mo = +m[2] - 1, d = +m[3];
        return { y, mo, d, h: +(m[4] || 0), mi: +(m[5] || 0), s: +(m[6] || 0), wd: new Date(y, mo, d).getDay() };
    }
    function whenText(iso, nowMs) {
        const p = parts(iso);
        if (!p) return String(iso || '');
        const year = new Date(nowMs === undefined ? Date.now() : nowMs).getFullYear();
        return MON[p.mo] + ' ' + p.d + (p.y !== year ? ' ' + p.y : '') + ' ' + pad(p.h) + ':' + pad(p.mi);
    }
    function zoneName() {
        try {
            const z = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' }).formatToParts(new Date())
                .find(x => x.type === 'timeZoneName');
            return z ? z.value : '';
        } catch (_e) { return ''; }
    }
    function whenFull(iso) {
        const p = parts(iso);
        if (!p) return String(iso || '');
        const z = zoneName();
        return DAY[p.wd] + ' ' + p.d + ' ' + MON[p.mo] + ' ' + p.y + ' · ' + pad(p.h) + ':' + pad(p.mi) + ':' + pad(p.s) + (z ? ' ' + z : '');
    }
    function hm(iso, rowIso) {
        const p = parts(iso);
        if (!p) return String(iso || '');
        const r = parts(rowIso);
        if (!r || (r.y === p.y && r.mo === p.mo && r.d === p.d)) return pad(p.h) + ':' + pad(p.mi);
        return MON[p.mo] + ' ' + p.d + ' ' + pad(p.h) + ':' + pad(p.mi);
    }

    // ── numbers ───────────────────────────────────────────────────────────
    function num(v) {
        if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
        const n = Number(v);
        return isFinite(n) ? n : null;
    }
    function decimals(x) {
        for (let d = 0; d <= 6; d++) if (Math.abs(Number(x.toFixed(d)) - x) < 1e-9 * Math.max(1, Math.abs(x))) return d;
        return 6;
    }
    function band(lo, mid, hi) {
        const ns = [lo, mid, hi].map(num);
        if (ns.every(n => n === null)) return null;
        const d = Math.max(1, ...ns.filter(n => n !== null).map(decimals));
        return ns.map(n => n === null ? '—' : n.toFixed(d)).join(' – ');
    }
    function signed(n) { return n > 0 ? '+' + n : String(n); }

    // ── the sentences ─────────────────────────────────────────────────────
    function summary(e) {
        const d = (e && e.detail) || {};
        if (e.kind === 'result_conflict') {
            const theirs = d.theirs !== undefined && d.theirs !== '' ? String(d.theirs) : 'another value';
            const ours = d.ours !== undefined && d.ours !== '' ? String(d.ours) : String(e.value || 'its value');
            const who = d.their_operator ? String(d.their_operator) : 'an analyst';
            const at = d.their_updated_at ? ' ' + hm(d.their_updated_at, e.ts) : '';
            return 'LabCore has ' + theirs + ', changed by ' + who + at + '; the bench\'s ' + ours + ' was not sent again.';
        }
        if (e.kind === 'reread') {
            const rows = num(d.rows), already = num(d.already);
            if (rows === null) return 'Re-read its file; the bench did not say how many rows.';
            const notResent = num(d.not_resent);
            if (already === rows && !notResent) return 'Re-read ' + rows + ' rows; all ' + rows + ' already on record, nothing re-sent.';
            const bits = [];
            if (already !== null) bits.push(already + ' already on record');
            if (notResent) bits.push(notResent + ' not sent again');
            if (num(d.resent)) bits.push(num(d.resent) + ' sent');
            return 'Re-read ' + rows + ' rows' + (bits.length ? '; ' + bits.join(', ') : '') + '.';
        }
        // A measurement and a status change say everything in their own
        // fields (the sheet's rows); the server's fallback for those is the
        // blob as "key: value" pairs, which would say it all a second time.
        if (isMeasure(e.kind) || e.kind === 'status_change') return '';
        const t = String(e.detail_text || '');
        return /^[\w ]+: [^·]*( · [\w ]+: .*)?$/.test(t) && !/[.!?]$/.test(t) ? '' : t;
    }

    // ── the table cells ───────────────────────────────────────────────────
    const cap = (s) => { s = String(s || ''); return s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : ''; };
    function rowWhat(e) {
        if (e.kind === 'config') return String(e.action_label || e.action || e.test_name || '');
        if (e.kind === 'reread') return 'Re-read';
        // a status change's from → to is its Value; its Test cell carries
        // the reason the bench gave, when it gave one
        const d = e.detail || {};
        if (e.kind === 'status_change') return String(d.reason || '');
        // an override or a comment is about what somebody wrote
        if ((e.kind === 'override' || e.kind === 'comment') && !e.test_name) {
            return String(d.comment || d.text || d.note || summary(e) || '');
        }
        return String(e.test_name || '');
    }
    function rowValue(e) {
        const d = e.detail || {};
        if (e.kind === 'status_change' && (d.from || d.to)) return [cap(d.from) || '—', cap(d.to) || '—'].join(' → ');
        return String(e.value || '');
    }
    function rowWho(e) { return String(e.by || (e.detail && e.detail.operator) || ''); }

    // ── the sheet ─────────────────────────────────────────────────────────
    const NR = 'Not recorded';
    function perTest(obj, fmt) {
        if (!obj || typeof obj !== 'object') return null;
        const items = Object.entries(obj).filter(([, v]) => v !== null && v !== undefined && v !== '');
        if (!items.length) return null;
        return items.map(([k, v]) => k + ' ' + fmt(v)).join(' · ');
    }
    function corrText(v) {
        const n = num(v);
        if (n === null) return String(v);
        return n === 0 ? 'None (0)' : signed(n);
    }
    function verdictWord(d) {
        if (d.in_spec === true) return 'In spec';
        if (d.in_spec === false) return 'Out of spec';
        const v = String(d.verdict || '').toLowerCase();
        if (['pass', 'in', 'in_spec', 'green', 'ok'].indexOf(v) >= 0) return 'In spec';
        if (['fail', 'out', 'out_of_spec', 'red'].indexOf(v) >= 0) return 'Out of spec';
        return d.verdict ? String(d.verdict) : null;
    }
    const keyWords = (k) => { const t = String(k).replace(/_/g, ' ').trim(); return t.charAt(0).toUpperCase() + t.slice(1); };
    function flat(v) {
        if (v === null || v === undefined || v === '') return '';
        if (typeof v === 'boolean') return v ? 'Yes' : 'No';
        if (Array.isArray(v)) return v.map(flat).filter(Boolean).join(', ');
        if (typeof v === 'object') return Object.entries(v).map(([k, x]) => { const f = flat(x); return f ? k + ': ' + f : ''; }).filter(Boolean).join(' · ');
        return String(v);
    }
    // keys a row's own fields already say, per kind
    const ALWAYS = ['by', 'action'];
    const CONSUMED = {
        qc: ['raw_value', 'raw', 'correction', 'corrections', 'operator', 'calibration_id', 'low', 'high', 'expected', 'in_spec', 'verdict', 'spec', 'values'],
        run: ['raw_value', 'raw', 'correction', 'corrections', 'operator', 'calibration_id', 'values'],
        held_expired: ['raw_value', 'raw', 'correction', 'corrections', 'operator', 'calibration_id', 'values'],
        status_change: ['from', 'to'],
        result_conflict: ['ours', 'theirs', 'their_operator', 'their_updated_at', 'lab_id', 'test_name', 'of'],
        reread: ['rows', 'already', 'resent', 'not_resent'],
    };
    function linkFor(e, opts) {
        const uid = String(e.machine_uid || '');
        if (opts && opts.onRecord && uid) return { href: '/logs?equipment=' + encodeURIComponent(uid), text: 'Open in the full log →' };
        if (!uid) {
            const g = GROUPS.find(x => x.key === groupOf(e.kind));
            return g ? { href: '/logs?kind=' + g.key, text: 'Open the ' + g.label.toLowerCase().replace(/^qc$/, 'QC') + ' log →' }
                : { href: '/logs', text: 'Open the full log →' };
        }
        return { href: '/instruments/' + encodeURIComponent(uid) + '#log', text: 'Open ' + (e.machine_title || uid) + ' →' };
    }
    function sheet(e, opts) {
        const d = (e && e.detail && typeof e.detail === 'object') ? e.detail : {};
        const rows = [];
        const add = (k, v, muted) => rows.push(muted ? { k, v, muted: true } : { k, v });
        const test = String(e.test_name || '');
        if (e.lab_id) add('Lab ID', String(e.lab_id));
        if (e.kind === 'result_conflict') {
            if (test) add('Test', test);
            add('The bench read', d.ours !== undefined && d.ours !== '' ? String(d.ours) : String(e.value || NR), !(d.ours || e.value));
            add('LabCore has', d.theirs !== undefined && d.theirs !== '' ? String(d.theirs) : NR, !d.theirs);
            add('Changed by', d.their_operator ? String(d.their_operator) : NR, !d.their_operator);
            add('Changed at', d.their_updated_at ? whenFull(d.their_updated_at) : NR, !d.their_updated_at);
        } else if (isMeasure(e.kind)) {
            if (test) add('Test', test);
            const vals = perTest(d.values, String);
            const value = e.value !== undefined && e.value !== '' ? String(e.value) : vals;
            add('Value', value || NR, !value);
            let raw = d.raw_value !== undefined && d.raw_value !== null && d.raw_value !== '' ? String(d.raw_value) : null;
            if (raw === null && d.raw && typeof d.raw === 'object') raw = test && d.raw[test] !== undefined ? String(d.raw[test]) : perTest(d.raw, String);
            else if (raw === null && d.raw !== undefined && typeof d.raw !== 'object') raw = String(d.raw);
            add('Raw value', raw || NR, !raw);
            let corr = d.correction !== undefined && d.correction !== null && d.correction !== '' ? corrText(d.correction) : null;
            if (corr === null && d.corrections && typeof d.corrections === 'object') {
                corr = test && d.corrections[test] !== undefined ? corrText(d.corrections[test]) : perTest(d.corrections, corrText);
            }
            add('Correction applied', corr || NR, !corr);
            add('Operator', d.operator ? String(d.operator) : NR, !d.operator);
            add('Calibration id', d.calibration_id ? String(d.calibration_id) : NR, !d.calibration_id);
            if (e.kind === 'qc') {
                const v = verdictWord(d);
                if (v) add('Verdict', v);
                const sp = (d.spec && typeof d.spec === 'object') ? d.spec : null;
                let b = band(d.low, d.expected, d.high);
                if (!b && sp && num(sp.expected) !== null && num(sp.std_dev) !== null) {
                    const k = num(sp.k) === null ? 2 : num(sp.k), w = k * num(sp.std_dev);
                    b = band(num(sp.expected) - w, sp.expected, num(sp.expected) + w);
                }
                if (b) add('Band', b);
            }
        } else {
            if (e.kind === 'status_change') add('Status', rowValue(e) || NR);
            else if (rowWhat(e)) add(e.kind === 'config' ? 'Change' : 'Test', rowWhat(e));
            if (e.value && e.kind !== 'status_change') add('Value', String(e.value));
        }
        const who = rowWho(e);
        if (who && !(isMeasure(e.kind) && who === d.operator)) add('By', who);
        const skip = ALWAYS.concat(CONSUMED[e.kind] || []);
        const extra = Object.entries(d).filter(([k]) => skip.indexOf(k) < 0)
            .map(([k, v]) => ({ k: keyWords(k), v: flat(v) })).filter(r => r.v !== '');
        const title = kindWord(e.kind) + ((test || e.lab_id || rowWhat(e)) ? ' · ' + (e.kind === 'config' ? rowWhat(e) : (test || e.lab_id || rowWhat(e))) : '');
        return {
            title, where: e.machine_uid ? String(e.machine_title || e.machine_uid) : 'Lab-wide',
            when: whenFull(e.ts), summary: summary(e), rows, extra, link: linkFor(e, opts),
            id: (e.id === undefined ? null : e.id),
        };
    }

    // ── the address bar (§1: the address bar is the link) ─────────────────
    const DATE = /^\d{4}-\d{2}-\d{2}$/;
    const KEYS = ['equipment', 'kind', 'since', 'until', 'q'];
    function parseQuery(search) {
        const p = new URLSearchParams(String(search || '').replace(/^\?/, ''));
        const kind = (p.get('kind') || '').trim().toLowerCase();
        const date = (k) => { const v = (p.get(k) || '').trim(); return DATE.test(v) ? v : ''; };
        return {
            equipment: (p.get('equipment') || p.get('machine') || '').trim(),
            kind: /^[a-z_,]+$/.test(kind) && kind !== 'all' ? kind : '',
            since: date('since'), until: date('until'),
            q: (p.get('q') || '').trim(),
        };
    }
    function toQuery(st) {
        const p = new URLSearchParams();
        for (const k of KEYS) if (st && st[k]) p.set(k, st[k]);
        const s = p.toString();
        return s ? '?' + s : '';
    }
    function apiQuery(st, extra) {
        const p = new URLSearchParams();
        if (st.equipment) p.set('machine', st.equipment);
        for (const k of ['kind', 'since', 'until', 'q']) if (st[k]) p.set(k, st[k]);
        for (const [k, v] of Object.entries(extra || {})) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
        return p.toString();
    }
    function shiftDay(iso, days) {
        const [y, m, d] = iso.split('-').map(Number);
        const t = new Date(y, m - 1, d + days);
        return t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate());
    }
    function rangeFor(preset, today) {
        if (preset === 'today') return { since: today, until: today };
        if (preset === '7d') return { since: shiftDay(today, -6), until: '' };
        if (preset === '30d') return { since: shiftDay(today, -29), until: '' };
        return { since: '', until: '' };
    }
    function presetFor(since, until, today) {
        if (!since && !until) return 'any';
        for (const p of ['today', '7d', '30d']) {
            const r = rangeFor(p, today);
            if (r.since === since && r.until === until) return p;
        }
        return 'custom';
    }
    function localToday(nowMs) {
        const t = new Date(nowMs === undefined ? Date.now() : nowMs);
        return t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate());
    }

    // ── the count header (§3.6) ───────────────────────────────────────────
    function countLine(o) {
        const n = o.total, shown = o.shown || 0;
        let count;
        if (o.searched) count = shown + ' ' + (shown === 1 ? 'match' : 'matches') + ' in the whole record';
        else if (n === null || n === undefined) count = 'Newest ' + shown + ' ' + (shown === 1 ? 'event' : 'events');
        else count = n.toLocaleString('en-US') + ' ' + (n === 1 ? 'event' : 'events');
        const s = o.source || {};
        const kept = 'Kept by ' + (s.kept_by || 'LEM');
        const p = parts(s.complete_to);
        const tail = s.incomplete ? 'incomplete: ' + s.incomplete
            : (p ? 'complete to ' + pad(p.h) + ':' + pad(p.mi) : '');
        return { count, source: tail ? kept + ' · ' + tail : kept };
    }

    const api = { GROUPS, kindWord, groupOf, whenText, whenFull, zoneName, summary, rowWhat, rowValue, rowWho, sheet, parseQuery, toQuery, apiQuery, rangeFor, presetFor, localToday, countLine };
    root.LEMLog = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
