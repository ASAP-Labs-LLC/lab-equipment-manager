/* transfer_logic.js: what people see of the data transfer (transfer §14),
   without a DOM. window.LEMTransfer; module.exports under node, where
   tests/js/transfer.mjs drives it with a fake fetch and fake timers.

   Two rules every controller here is built on, and the harness checks:

   * EVERY SAVE CHECKS r.ok. All writes go through `send()`, which returns
     {ok, status, body, text}; `ok` is true only for a 2xx answer. A 409, a
     503, an HTML error page, a dropped connection: each comes back ok:false
     with the server's own sentence (or one that says what is unknown), and
     no caller paints a decision, a reset or an import that did not land.
   * NO POST ON A TIMER. The pollers here only ever GET (the idle rule:
     /healthz idle_seconds must keep rising while a page is open, or the
     unattended updater can never fire). A write happens only inside a
     function a person's click calls. createPoller refuses any method but GET.

   Pages: the instrument's Data transfer section (transfer_section.js),
   /results/conflicts (conflicts.js), Settings › Transfer
   (settings_transfer.js), and the shell foot's Data line (status.js). */
(function (root) {
    'use strict';

    const SECTION_EVERY_MS = 15000;
    const CONFLICTS_EVERY_MS = 15000;
    const OVERVIEW_EVERY_MS = 30000;
    const NOT_REPORTED = "Not reported by this bench's module version";

    /** "4 s", "12 min", "3 h", "2 d" (the server's ui_transfer.ago). */
    function ago(seconds) {
        if (typeof seconds !== 'number' || !isFinite(seconds)) return null;
        const s = Math.max(0, Math.round(seconds));
        if (s < 60) return s + ' s';
        if (s < 3600) return Math.floor(s / 60) + ' min';
        if (s < 86400) return Math.floor(s / 3600) + ' h';
        return Math.floor(s / 86400) + ' d';
    }

    function plural(n, one, many) { return n === 1 ? one : many; }

    // ── the one door every request goes through ───────────────────────────

    /** {ok, status, body, text}. `ok` only on r.ok. Never throws. `text` is
        the sentence to show when it is not ok. */
    async function send(fetchFn, method, url, payload, what) {
        let r;
        try {
            const opts = { method, credentials: 'same-origin', headers: {} };
            if (payload instanceof (root && root.FormData ? root.FormData : function Never() {})) {
                opts.body = payload;
            } else if (payload !== undefined) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(payload);
            }
            r = await fetchFn(url, opts);
        } catch (_e) {
            return { ok: false, status: 0, body: null,
                     text: 'LEM did not answer, so ' + (what || 'nothing changed') + '. Try again.' };
        }
        let body = null;
        try { body = await r.json(); } catch (_e) { body = null; }
        if (r.ok) return { ok: true, status: r.status, body, text: '' };
        return { ok: false, status: r.status, body, text: why({ status: r.status, body }, what) };
    }

    /** The sentence for a refused request. */
    function why(res, what) {
        const w = what || 'nothing changed';
        if (!res || !res.status) return 'LEM did not answer, so ' + w + '. Try again.';
        if (res.status === 401) return 'You are signed out, so ' + w + '. Sign in and try again.';
        const e = res.body && typeof res.body.error === 'string' ? res.body.error.trim() : '';
        const cap = w.charAt(0).toUpperCase() + w.slice(1);
        return (e ? e.replace(/\.?$/, '. ') : 'The server answered ' + res.status + '. ') + cap + '.';
    }

    /** setInterval for GETs only. `timers` is {setInterval, clearInterval}
        (the page's own, or the harness's fake). `isVisible()` false skips a
        tick: a hidden tab costs nothing. */
    function createPoller(opts) {
        const method = opts.method || 'GET';
        if (method !== 'GET') throw new Error('a poller only ever GETs (the idle rule)');
        const t = opts.timers;
        let id = null;
        let busy = false;
        async function tick() {
            if (busy || (opts.isVisible && !opts.isVisible())) return;
            busy = true;
            try { await opts.run(); } finally { busy = false; }
        }
        return {
            start() { if (id === null) id = t.setInterval(tick, opts.every); return this; },
            stop() { if (id !== null) { t.clearInterval(id); id = null; } },
            tick,
        };
    }

    // ── the instrument's Data transfer section ─────────────────────────────

    /** The rows to draw, with "Last delivered" aged by `elapsedS` seconds
        since the answer was read. Unknown stays unknown. */
    function sectionRows(data, elapsedS) {
        if (!data || !Array.isArray(data.rows)) return [];
        return data.rows.map((r) => {
            const out = Object.assign({}, r);
            if (r.key === 'delivered' && typeof r.age_s === 'number') {
                const age = r.age_s + Math.max(0, elapsedS || 0);
                out.value = ago(age) + ' ago';
                if (r.glyph === 'final' && age > 120) out.glyph = 'held';
            }
            return out;
        });
    }

    function createSection(opts) {
        const url = '/api/ui/transfer/' + encodeURIComponent(opts.uid);
        let readAt = null;
        let last = null;
        async function load() {
            const res = await send(opts.fetch, 'GET', url, undefined, 'this section could not be refreshed');
            if (res.ok) { last = res.body; readAt = opts.now(); opts.render({ data: last, error: null }); }
            else opts.render({ data: last, error: res.text, status: res.status });
            return res;
        }
        const poller = createPoller({ timers: opts.timers, every: SECTION_EVERY_MS, run: load,
                                      isVisible: opts.isVisible });
        return {
            load, start() { poller.start(); }, stop() { poller.stop(); },
            rows() { return sectionRows(last, readAt === null ? 0 : (opts.now() - readAt) / 1000); },
            seed(data) { last = data; readAt = opts.now(); },
        };
    }

    // ── /results/conflicts ─────────────────────────────────────────────────

    function createConflicts(opts) {
        const q = opts.machine ? '?machine=' + encodeURIComponent(opts.machine) : '';
        let view = opts.initial || null;
        async function load() {
            const res = await send(opts.fetch, 'GET', '/api/results/conflicts' + q, undefined,
                                   'the list could not be refreshed');
            if (res.ok) { view = res.body; opts.render({ view, error: null }); }
            else opts.render({ view, error: res.text, status: res.status });
            return res;
        }
        /** One person's decision. Painted as decided only after r.ok, and
            then re-read so the row moves to Decided from the server's word. */
        async function decide(ref, choice, seen) {
            if (choice !== 'keep' && choice !== 'send') return { ok: false, text: 'Keep or Send.' };
            const res = await send(opts.fetch, 'POST', '/api/results/conflicts/decide',
                                   { ref, choice, seen }, 'nothing was decided');
            if (!res.ok) return res;
            await load();
            return Object.assign(res, { text: choice === 'keep'
                ? "Kept LabCore's value. The bench is told on its next sync."
                : "The instrument's value goes to the bench, which files it on its next sync." });
        }
        const poller = createPoller({ timers: opts.timers, every: CONFLICTS_EVERY_MS, run: load,
                                      isVisible: opts.isVisible });
        return { load, decide, start() { poller.start(); }, stop() { poller.stop(); },
                 view() { return view; } };
    }

    /** The page-head pill, from the same list the page draws. */
    function conflictPill(view) {
        if (!view) return { tone: 'error', glyph: 'error', text: 'Could not be read' };
        const n = (view.open || []).length;
        return n ? { tone: 'held', glyph: 'error', text: n + ' ' + plural(n, 'result needs', 'results need') + ' a decision' }
                 : { tone: 'final', glyph: 'final', text: 'Nothing waiting' };
    }

    // ── Settings › Transfer ────────────────────────────────────────────────

    /** One row of the benches table: words, and the one action it offers. */
    function benchRow(b, elapsedS) {
        const age = typeof b.delivered_s === 'number' ? b.delivered_s + Math.max(0, elapsedS || 0) : null;
        let delivered;
        if (b.mode === 'legacy') delivered = { text: 'Through LabCore', glyph: b.checking_in ? 'final' : 'never' };
        else if (b.mode === 'enrolling') delivered = { text: 'Not yet', glyph: 'never' };
        else if (age === null) delivered = { text: 'Never', glyph: 'never' };
        else delivered = { text: ago(age) + ' ago', glyph: age <= 120 ? 'final' : age > 1800 ? 'error' : 'held' };
        const waiting = (b.mode !== 'v2' || typeof b.waiting !== 'number') ? '—'
            : b.waiting === 0 ? '0' : String(b.waiting);
        let action = null;
        if (b.enrolment === 'pending') action = { kind: 'approve', label: 'Approve enrolment' };
        else if (b.mode === 'v2' && b.enrolment === 'enrolled') action = { kind: 'reset', label: 'Reset…' };
        else if (b.mode === 'legacy' && !b.checking_in) action = { kind: 'retire', label: 'Retire…' };
        return { title: b.title, uid: b.machine_uid, href: b.href, module: b.module,
                 road: b.road, delivered, waiting, action,
                 note: b.enrolment === 'pending' ? 'asks to enrol' : b.enrolment === 'revoked' ? 'token reset' : '' };
    }

    /** The benches in three groups, so the page says each thing once:
        module 4 (a table: road, last delivered, waiting, one action each);
        the older module still running (one line naming them); stopped on
        the older module (each with Retire…). */
    function benchGroups(o) {
        const out = { v4: [], older: [], stopped: [] };
        for (const b of (o && o.benches) || []) {
            if (b.mode === 'legacy') (b.checking_in ? out.older : out.stopped).push(b);
            else out.v4.push(b);
        }
        return out;
    }

    function names(list, limit) {
        const t = list.map(b => b.title);
        const n = limit || 4;
        if (t.length <= 1) return t.join('');
        if (t.length <= n) return t.slice(0, -1).join(', ') + ' and ' + t[t.length - 1];
        return t.slice(0, n - 1).join(', ') + ' and ' + (t.length - n + 1) + ' more';
    }

    /** The bridge's words: state, and the reasons it may not be turned off. */
    function bridgeView(o) {
        const br = (o && o.bridge) || {};
        if (br.on === null || br.on === undefined) {
            return { state: 'No bridge on this server', why: 'This server and LabCore are one database, so there is nothing to copy.',
                     reasons: [], canChange: false, label: null };
        }
        const reasons = Array.isArray(br.refusals) ? br.refusals : [];
        if (br.on) {
            return { state: 'On: LEM copies its record into LabCore for benches on the older module',
                     why: reasons.length ? 'It cannot be turned off yet:' : 'Every condition is met. Turning it off freezes LabCore\'s copy of LEM\'s tables.',
                     reasons, canChange: !!br.can_change, label: reasons.length ? null : 'Turn the bridge off…' };
        }
        return { state: 'Off', why: 'LabCore\'s copy of LEM\'s tables is frozen. Turning it back on re-copies them; nothing is lost.',
                 reasons: [], canChange: !!br.can_change, label: 'Turn the bridge back on' };
    }

    /** The journal import's preview: lines to show, and the go button's
        label (null: nothing to import). */
    function journalPreview(ans) {
        const lines = [];
        let total = 0;
        for (const b of (ans && ans.benches) || []) {
            const name = b.title || b.machine_uid || '?';
            const parts = [b.new + ' new', b.held + ' already in LEM'];
            if (b.damaged) parts.push(b.damaged + ' damaged ' + plural(b.damaged, 'line', 'lines') + ' skipped');
            lines.push({ name, epoch: b.epoch, text: parts.join(' · '), note: b.note || '' });
            if (!b.note || !/^LEM has no instrument|could not/.test(b.note)) total += b.new || 0;
        }
        if (ans && ans.unreadable) lines.push({ name: 'Unreadable', epoch: '', text: ans.unreadable + ' lines were not journal records', note: '' });
        return { lines, label: total ? 'Import ' + total + ' ' + plural(total, 'record', 'records') : null, total };
    }

    function journalOutcome(ans) {
        const bs = (ans && ans.benches) || [];
        const got = bs.reduce((n, b) => n + (b.imported || 0), 0);
        const notes = bs.filter(b => b.note).map(b => (b.title || b.machine_uid) + ': ' + b.note);
        return { tone: notes.length ? 'held' : 'done',
                 text: got ? got + ' ' + plural(got, 'record', 'records') + ' imported into LEM.'
                           : 'Nothing imported: LEM already held every record in the folder.',
                 notes };
    }

    function createSettingsTransfer(opts) {
        let overview = null;
        async function load() {
            const res = await send(opts.fetch, 'GET', '/api/transfer/overview', undefined,
                                   'the benches could not be read');
            if (res.ok) { overview = res.body; opts.render({ overview, error: null }); }
            else opts.render({ overview, error: res.text, status: res.status });
            return res;
        }
        async function after(res) { if (res.ok) await load(); return res; }
        const poller = createPoller({ timers: opts.timers, every: OVERVIEW_EVERY_MS, run: load,
                                      isVisible: opts.isVisible });
        return {
            load, start() { poller.start(); }, stop() { poller.stop(); },
            overview() { return overview; },
            approve: async (uid) => after(await send(opts.fetch, 'POST',
                '/api/transfer/benches/' + encodeURIComponent(uid) + '/approve', {}, 'the bench was not approved')),
            reset: async (uid) => after(await send(opts.fetch, 'POST',
                '/api/transfer/benches/' + encodeURIComponent(uid) + '/reset', {}, 'the token still works')),
            retire: async (uid) => after(await send(opts.fetch, 'DELETE',
                '/api/machines/' + encodeURIComponent(uid), { purge_history: true, confirm: true },
                'the instrument was not retired')),
            bridge: async (on) => after(await send(opts.fetch, 'POST', '/api/transfer/bridge', { on },
                'the bridge was not changed')),
            journal: async (form, dry) => {
                const res = await send(opts.fetch, 'POST', '/api/transfer/journal-import' + (dry ? '?dry=1' : ''),
                                       form, dry ? 'the folder could not be read' : 'nothing was imported');
                if (!dry) await after(res);
                return res;
            },
            dedupeRun: async () => send(opts.fetch, 'GET', '/api/dedupe/dry-run', undefined, 'the dry run did not finish'),
            approvals: async () => send(opts.fetch, 'GET', '/api/dedupe/approvals', undefined, 'the approvals could not be read'),
            dedupeApprove: async (b) => send(opts.fetch, 'POST', '/api/dedupe/approve',
                { machine_uid: b.machine_uid, label: b.unit, run_id: b.run_id, password: b.password,
                  decision: b.decision || 'approved' }, 'nothing was approved'),
            dedupeApply: async (id) => send(opts.fetch, 'POST', '/api/dedupe/apply', { approval_id: id },
                'nothing was hidden'),
        };
    }

    /** A dedupe dry run, as rows to draw: one per (bench, set of candidates). */
    function dedupeRows(report, titles) {
        const out = [];
        for (const b of (report && report.benches) || []) {
            const impact = (b.qc_impact || []).filter(s => s.last_moves || s.spread_changes);
            for (const u of b.units || []) {
                if (!u.candidates) continue;
                out.push({ machine_uid: b.machine_uid, title: (titles && titles[b.machine_uid]) || b.machine_uid,
                           unit: u.rule, run_id: u.run_id, label: u.label, storm: !!u.storm,
                           candidates: u.candidates,
                           text: u.candidates + ' ' + plural(u.candidates, 'row', 'rows') + ' ' +
                                 (u.label === 'import_leftover' ? 'left over from the August import' : 'written again by a restart') +
                                 (u.storm ? ' (one storm day)' : ''),
                           impact: impact.length ? impact.length + ' QC ' + plural(impact.length, 'series changes', 'series change') +
                               ' if hidden: ' + impact.slice(0, 3).map(s => s.test_name + (s.last_moves ? ' (last verdict moves earlier)' : ' (spread changes)')).join(', ')
                               : 'No QC verdict or spread changes if hidden.' });
            }
        }
        return out;
    }

    // ── the shell foot ─────────────────────────────────────────────────────

    /** How the page draws the section's rows (the server's
     *  ui_transfer.display_rows, in the same words): two or more rows that
     *  read NOT_REPORTED with no action and no note become ONE row, where the
     *  first of them was, naming every quantity it covers. One unknown, and
     *  any row with an action, stays as it is. */
    function displayRows(rows) {
        const list = Array.isArray(rows) ? rows : [];
        const fold = list.filter(r => r && r.value === NOT_REPORTED && !r.href && !r.action && !r.note);
        if (fold.length < 2) return list.slice();
        const names = fold.map(r => String(r.label || r.key));
        const named = [names[0], ...names.slice(1).map(n => n.charAt(0).toLowerCase() + n.slice(1))];
        const group = { key: 'unreported', label: 'Not reported',
            value: named.slice(0, -1).join(', ') + ' and ' + named[named.length - 1],
            note: "This bench's module version does not report these. They are unknown, not 0.",
            glyph: 'never', href: null, action: null, keys: fold.map(r => r.key) };
        const out = [];
        for (const r of list) {
            if (r === fold[0]) out.push(group);
            else if (!fold.includes(r)) out.push(r);
        }
        return out;
    }

    /** The foot's Data line from /api/ui/live's `transfer`, or null. */
    function footLine(t) {
        if (!t || typeof t.text !== 'string') return null;
        return { text: t.text, full: t.full || t.text, glyph: t.glyph || 'never', href: t.href || '/settings#transfer' };
    }

    const api = { ago, send, why, createPoller, sectionRows, createSection, createConflicts,
                  conflictPill, benchRow, benchGroups, names, bridgeView, journalPreview, journalOutcome,
                  createSettingsTransfer, dedupeRows, footLine, displayRows, NOT_REPORTED,
                  SECTION_EVERY_MS, CONFLICTS_EVERY_MS, OVERVIEW_EVERY_MS };
    if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
    if (root) root.LEMTransfer = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : null));
