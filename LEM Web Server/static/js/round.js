/* round.js: today's opening or closing round (ia-final §3.3, piece 9).

   P1, the bug this page was rebuilt for: the server recorded 3 of 3 and the
   screen showed 0 of 3, every box empty and "overdue". The row <div> took
   focus on a tap, LEM.liveEdit saw the caret inside #lists and held every
   repaint, there was no optimistic tick, and a second tap (the natural thing
   to do when nothing happened) UNTICKED the item. So, here:

   * The tick is a <button aria-pressed>. It paints on the tap, before the
     POST is answered, and the POST carries the ABSOLUTE `checked: true`.
   * A tap on a ticked row sends nothing. Undo is the only way back.
   * A live merge is per row. It skips a row whose save is in flight, and a
     row whose save settled after the read being merged began (otherwise a
     GET that left before the tick landed repaints it unticked).
   * LEM.liveEdit guards one thing: the reading input that is focused or
     holds unsaved text (LEM.liveEdit.holds). Never the round.
   * A reading is saved by Save or Enter, never by blur; a value that does
     not parse is refused in the row before anything is sent.

   The pure half (LEMRoundLogic: the parse, the words and the round's state
   machine over injected send/paint) is node-tested in tests/js/round.mjs.
   Every server string goes in through textContent. GETs only on a timer:
   an open round never POSTs by itself (constraints A.7). */
(function (root) {
    'use strict';

    // ── words ──────────────────────────────────────────────────────────────
    const NUM = /^[-+]?(\d+\.?\d*|\.\d+)$/;

    /** A reading, as typed, to what is sent: {ok, value} or {ok:false, error}.
        Commas are refused, not guessed at ("1,000" is a thousand to one person
        and one to another). Exponents, NaN and Infinity are not readings. */
    function parseReading(kind, raw) {
        const t = String(raw == null ? '' : raw).trim();
        if (kind === 'text') {
            return t ? { ok: true, value: t } : { ok: false, error: 'Write the note first, then Save' };
        }
        if (!NUM.test(t)) return { ok: false, error: 'Enter a number, like 2900' };
        // a signed zero is a slip, not a reading: say the fix, do not guess
        if (/^[-+]/.test(t) && Number(t) === 0) return { ok: false, error: 'Enter 0 without a sign' };
        return { ok: true, value: t };
    }

    function _num(v) { return v === null || v === undefined || v === '' ? null : Number(v); }
    function _fmt(n) { return String(n); }

    /** "Limits 500 – 3000 PSI", "Limit ≤ 10 µg/min", "Limit ≥ 500 PSI", or ''
        when nobody has set limits (A.6: LEM never invents one). */
    function limitsText(tr, units) {
        if (!tr) return '';
        const lo = _num(tr.min), hi = _num(tr.max);
        const u = units ? ' ' + units : '';
        if (lo !== null && hi !== null) return 'Limits ' + _fmt(lo) + ' – ' + _fmt(hi) + u;
        if (hi !== null) return 'Limit ≤ ' + _fmt(hi) + u;
        if (lo !== null) return 'Limit ≥ ' + _fmt(lo) + u;
        return '';
    }

    /** The verdict word, only where an operator set limits. */
    function judge(value, tr) {
        if (!tr) return '';
        const lo = _num(tr.min), hi = _num(tr.max);
        if (lo === null && hi === null) return '';
        const v = Number(String(value == null ? '' : value).trim());
        if (String(value == null ? '' : value).trim() === '' || !isFinite(v)) return '';
        if (lo !== null && v < lo) return 'Below the minimum';
        if (hi !== null && v > hi) return 'Above the maximum';
        return 'In range';
    }

    /** "Cody · 07:58" */
    function byline(st) {
        if (!st) return '';
        const hm = String(st.at || '').slice(11, 16);
        return [st.user || '', hm].filter(Boolean).join(' · ');
    }

    const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    /** "Only on Thu", "Only on Mon, Wed and Fri"; '' for every day. */
    function daysText(days) {
        const d = (Array.isArray(days) ? days : []).map(Number).filter(n => n >= 0 && n <= 6);
        if (!d.length || d.length === 7) return '';
        const names = d.sort((a, b) => a - b).map(n => DAY_NAMES[n]);
        const list = names.length === 1 ? names[0]
            : names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
        return 'Only on ' + list;
    }

    /** The page-head pill: {kind: part|overdue|done, text} or null. */
    function pill(c, due, nowHM, lastAt) {
        if (!c || !c.total) return null;
        if (c.done >= c.total) {
            const hm = String(lastAt || '').slice(11, 16);
            return { kind: 'done', text: 'Done · ' + c.done + ' of ' + c.total + (hm ? ' · ' + hm : '') };
        }
        if (due && nowHM && nowHM > due) {
            return { kind: 'overdue', text: 'Overdue · was due ' + due + ' · ' + c.done + ' of ' + c.total + ' done' };
        }
        return { kind: 'part', text: c.done + ' of ' + c.total + ' done' };
    }

    /** The bench bar's right-hand words. */
    function saveWords(c, lastSaved) {
        if (c.saving > 0) return { kind: 'saving', text: c.saving + (c.saving === 1 ? ' tick' : ' ticks') + ' saving…' };
        if (c.failed > 0) return { kind: 'failed', text: c.failed + (c.failed === 1 ? ' tick' : ' ticks') + ' not saved · see the row' };
        const hm = String(lastSaved || '').slice(11, 16);
        if (hm) return { kind: 'saved', text: 'Saved ' + hm + ' · all ticks on the server' };
        return { kind: 'idle', text: 'Nothing ticked yet today' };
    }

    function _iso(d) {
        const p = (n) => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T'
            + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    }

    function _st(s) {
        s = s || {};
        return { checked: !!s.checked, user: String(s.user || ''), at: String(s.at || ''), value: String(s.value || '') };
    }
    function _same(a, b) {
        return a.checked === b.checked && a.user === b.user && a.at === b.at && a.value === b.value;
    }

    // ── the round's state, over injected deps ──────────────────────────────
    /** deps: send(kind 'toggle'|'value', checklistUid, body) -> Promise<{ok,
        status, error, body}>, paint(uid), user() -> '' signed out, now() -> Date.
        Optional: head(), after every batch of row paints, so the pill and the
        bench bar never disagree with a row; expired(act, again), when a save
        came back 401 (the server's session is gone): ask for sign-in, then
        run again(); settled(), after any answer. */
    function createRound(deps) {
        let rows = [];
        let byUid = new Map();
        let lists = [];
        let day = '';
        let seq = 0;
        let pending = 0;
        let lastSaved = '';
        let shape = '';
        let slotOf = '';

        function shapeOf(ls) {
            return JSON.stringify(ls.map(cl => [cl.uid, cl.name, (cl.items || []).map(i =>
                [i.uid, i.text, i.item_type, i.entry_type, i.units || '', i.parent_uid || '', i.track_uid || '',
                 (i.days_active || []).join('')])]));
        }

        function kindOf(i) {
            if (i.item_type === 'header') return 'header';
            if (i.entry_type === 'number') return 'number';
            if (i.entry_type === 'text') return 'text';
            return 'tick';
        }

        function load(d, slot) {
            day = String((d && d.day) || '');
            slotOf = slot || '';
            lists = ((d && d.checklists) || []).filter(cl => !slotOf || cl.slot === slotOf);
            const state = (d && d.state) || {};
            rows = [];
            byUid = new Map();
            for (const cl of lists) {
                for (const i of (cl.items || [])) {
                    const r = {
                        uid: String(i.uid), cl: String(cl.uid), kind: kindOf(i), text: String(i.text || ''),
                        units: String(i.units || ''), sub: i.item_type === 'subtask', parent: i.parent_uid || '',
                        track: String(i.track_uid || ''), days: i.days_active || [],
                        st: _st(((state[cl.uid] || {})[i.uid])), saving: false, error: '', unsaved: false, stamp: 0,
                    };
                    rows.push(r);
                    byUid.set(r.uid, r);
                }
            }
            shape = shapeOf(lists);
            if (!lastSaved) {
                for (const r of rows) if (r.st.checked && r.st.at > lastSaved) lastSaved = r.st.at;
            }
        }

        function counts() {
            let done = 0, total = 0, failed = 0, lastAt = '';
            for (const r of rows) {
                if (r.kind === 'header') continue;
                total++;
                if (r.st.checked) { done++; if (r.st.at > lastAt) lastAt = r.st.at; }
                if (r.unsaved && !r.saving) failed++;
            }
            return { done, total, saving: pending, failed, lastAt };
        }

        function group(r) {
            const out = [r];
            for (const x of rows) if (x.parent && x.parent === r.uid) out.push(x);
            return out;
        }

        function head() { if (deps.head) deps.head(); }

        function write(r, members, kind, body, next, act, again) {
            const prev = members.map(x => Object.assign({}, x.st));
            const at = _iso(deps.now());
            const who = deps.user();
            members.forEach((x, i) => {
                x.st = Object.assign({}, x.st, next(x, i), { user: who, at });
                x.saving = true;
                x.error = '';
                x.unsaved = false;
            });
            pending++;
            members.forEach(x => deps.paint(x.uid));
            head();
            body.day = day;
            // sent now, in the same task as the paint: nothing can come between
            let sent;
            try { sent = Promise.resolve(deps.send(kind, r.cl, body)); } catch (e) { sent = Promise.reject(e); }
            return sent
                .catch(() => ({ ok: false, error: 'The server could not be reached, so nothing was saved.' }))
                .then(res => {
                    pending--;
                    seq++;
                    members.forEach((x, i) => {
                        x.saving = false;
                        x.stamp = seq;
                        if (!(res && res.ok)) x.st = prev[i];
                    });
                    const gone = !!(res && !res.ok && res.status === 401);
                    if (res && res.ok) lastSaved = at;
                    else {
                        r.error = gone ? 'Not saved: you were signed out. Sign in and it saves.'
                            : 'Not saved: ' + String((res && res.error) || 'LEM did not say why.');
                        r.unsaved = true;
                    }
                    members.forEach(x => deps.paint(x.uid));
                    head();
                    if (gone && deps.expired && again) deps.expired(act, again);
                    if (deps.settled) deps.settled();
                });
        }

        function tap(uid) {
            const r = byUid.get(uid);
            if (!r || r.kind === 'header') return 'none';
            if (r.saving || r.st.checked) return 'none';    // Undo is the only way back
            if (r.kind !== 'tick') return 'focus';
            if (!deps.user()) return 'signin';
            write(r, group(r), 'toggle', { item_uid: r.uid, checked: true }, () => ({ checked: true }),
                'tick', () => tap(uid));
            return 'tick';
        }

        function undo(uid) {
            const r = byUid.get(uid);
            if (!r || r.saving || !r.st.checked) return 'none';
            if (!deps.user()) return 'signin';
            if (r.kind === 'tick') {
                write(r, group(r), 'toggle', { item_uid: r.uid, checked: false }, () => ({ checked: false }),
                    'undo', () => undo(uid));
            } else {
                write(r, [r], 'value', { item_uid: r.uid, value: '' }, () => ({ checked: false, value: '' }),
                    'undo', () => undo(uid));
            }
            return 'untick';
        }

        function save(uid, raw) {
            const r = byUid.get(uid);
            if (!r || (r.kind !== 'number' && r.kind !== 'text')) return 'none';
            if (r.saving) return 'none';
            const p = parseReading(r.kind, raw);
            if (!p.ok) { r.error = p.error; deps.paint(r.uid); head(); return 'refused'; }
            if (!deps.user()) return 'signin';
            write(r, [r], 'value', { item_uid: r.uid, value: p.value }, () => ({ checked: true, value: p.value }),
                'save this reading', () => save(uid, raw));
            return 'saving';
        }

        function beginFetch() { return ++seq; }

        /** Fold one /api/checklists answer in, row by row. -> {restructure,
            changed:[uids]}. A changed definition is not merged at all: the
            caller rebuilds (when nothing is saving or being typed). */
        function merge(d, fetchSeq) {
            const ls = ((d && d.checklists) || []).filter(cl => !slotOf || cl.slot === slotOf);
            if (shapeOf(ls) !== shape) return { restructure: true, changed: [] };
            const state = (d && d.state) || {};
            const changed = [];
            for (const r of rows) {
                if (r.kind === 'header' || r.saving || r.stamp > fetchSeq) continue;
                const s = _st((state[r.cl] || {})[r.uid]);
                if (!_same(s, r.st)) {
                    r.st = s;
                    if (s.checked) { r.error = ''; r.unsaved = false; }
                    changed.push(r.uid);
                }
            }
            changed.forEach(u => deps.paint(u));
            if (changed.length) head();
            return { restructure: false, changed };
        }

        return {
            load, merge, beginFetch, tap, undo, save, counts,
            rows: () => rows, row: (u) => byUid.get(u), lists: () => lists,
            lastSaved: () => lastSaved, day: () => day,
        };
    }

    const logic = { parseReading, limitsText, judge, byline, daysText, pill, saveWords, createRound };
    root.LEMRoundLogic = logic;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = logic;
    if (typeof document === 'undefined') return;

    // ── the page ───────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const main = $('main');
    if (!main || !main.dataset.slot) return;
    const SLOT = main.dataset.slot;
    const DAY = main.dataset.day;
    const LV = root.LEMLive;
    const LEMjs = root.LEM;
    let loaded = false;
    let tracked = null;          // null: not asked yet; 'failed'; or {uid: tracked}

    const send = (kind, cl, body) => LEMjs.send(
        '/api/checklists/' + encodeURIComponent(cl) + '/' + (kind === 'value' ? 'value' : 'toggle'),
        { body, fallback: kind === 'value' ? 'That reading did not save.' : 'That tick did not save.' });

    const round = createRound({
        send,
        paint: (uid) => paintRow(uid),
        user: () => (root.LEMSignIn ? root.LEMSignIn.user() : (document.body.dataset.user || '')),
        now: () => new Date(),
        head: () => paintHead(),
        expired: (act, again) => signedOutHere(act, again),
        settled: () => { if (LV) LV.pollNow(); },
    });

    /** The server dropped the session while the page still showed a name:
        say so everywhere a name is shown (the same event signin.js sends),
        then ask for sign-in in place with the act waiting. */
    function signedOutHere(act, again) {
        document.body.dataset.user = '';
        document.body.classList.add('anon');
        document.dispatchEvent(new CustomEvent('lem:auth', { detail: { user: '' } }));
        gate(act, again);
    }

    function rowEl(uid) {
        return $('lists').querySelector('.rrow[data-item="' + (root.CSS && CSS.escape ? CSS.escape(uid) : uid) + '"]');
    }
    function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }

    function caption(r) {
        const parts = [];
        const tr = r.track && tracked && tracked !== 'failed' ? tracked[r.track] : null;
        const units = r.units || (tr && tr.units) || '';
        if (r.kind === 'number') {
            if (r.track && tracked === 'failed') parts.push('Limits could not be read');
            const lim = limitsText(tr, units);
            if (lim) parts.push(lim);
            const v = r.st.checked ? judge(r.st.value, tr) : '';
            if (v) parts.push(v);
        }
        const days = daysText(r.days);
        if (days) parts.push(days);
        return parts.join(' · ');
    }

    function paintRow(uid) {
        const r = round.row(uid);
        const el = r && rowEl(uid);
        if (!el) return;
        const state = r.saving ? 'saving' : r.error ? 'failed' : r.st.checked ? 'done' : 'todo';
        el.dataset.checked = r.st.checked ? '1' : '';
        el.dataset.state = state;
        const btn = el.querySelector('.tick');
        btn.setAttribute('aria-pressed', r.st.checked ? 'true' : 'false');
        setText(el.querySelector('.rby'), r.saving ? 'Saving…' : r.st.checked ? byline(r.st) : '');
        el.querySelector('.undo').hidden = !(r.st.checked && !r.saving);
        const err = el.querySelector('.rerr');
        setText(err, r.error || '');
        err.hidden = !r.error;
        const cap = el.querySelector('.rcap');
        const words = caption(r);
        cap.textContent = '';
        if (words) cap.appendChild(document.createTextNode(words));
        if (r.kind === 'number' && r.st.checked && r.st.value) {
            if (words) cap.appendChild(document.createTextNode(' · '));
            const a = document.createElement('a');
            a.className = 'link';
            a.href = '/checklists/trends?item=' + encodeURIComponent(r.uid);
            a.textContent = 'Past readings';
            cap.appendChild(a);
        }
        cap.hidden = !cap.firstChild;
        const input = el.querySelector('.rinput');
        if (input && !r.saving) {
            // the one field somebody is typing in is left alone (LEM.liveEdit)
            const held = LEMjs.liveEdit.holds ? LEMjs.liveEdit.holds(input) : document.activeElement === input;
            if (!held || (r.st.checked && input.value.trim() === r.st.value)) {
                input.setAttribute('value', r.st.value);
                if (!held) input.value = r.st.value;
            }
        }
        if (input) markDirty(el, r);
    }

    /** A saved reading hides its Save until the value is changed. */
    function markDirty(el, r) {
        const input = el.querySelector('.rinput');
        if (!input) return;
        el.dataset.dirty = input.value.trim() !== r.st.value ? '1' : '';
    }

    function paintHead() {
        const c = round.counts();
        const lists = round.lists();
        let due = '';
        for (const cl of lists) if (cl.due_time && (!due || cl.due_time < due)) due = cl.due_time;
        const now = new Date();
        const hm = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
        const isToday = DAY === _iso(now).slice(0, 10);
        const p = pill(c, due, isToday ? hm : '', c.lastAt);
        const pe = $('round-pill');
        pe.hidden = !p;
        if (pe.nextElementSibling) pe.nextElementSibling.hidden = !p;
        if (p) {
            pe.className = 'pill ' + (p.kind === 'done' ? 'final' : p.kind === 'overdue' ? 'held' : 'part');
            $('round-pill-glyph').className = 'glyph ' + (p.kind === 'done' ? 'final' : p.kind === 'overdue' ? 'error' : 'half');
            setText($('round-pill-text'), p.text);
        }
        setText($('round-due'), due ? 'Due ' + due : '');
        $('round-due').hidden = !due;
        $('round-due-sep').hidden = !due;
        // the next thing to do, marked the way GC marks "Now"
        let next = null;
        for (const r of round.rows()) if (r.kind !== 'header' && !r.st.checked && !r.saving) { next = r.uid; break; }
        $('lists').querySelectorAll('.rrow.next').forEach(el => { if (el.dataset.item !== next) el.classList.remove('next'); });
        if (next) { const el = rowEl(next); if (el) el.classList.add('next'); }
        const w = saveWords(c, round.lastSaved());
        // "Saved 08:02" always; " · all ticks on the server" where there is room
        const cut = w.text.indexOf(' · ');
        setText($('bb-saved-text'), cut > 0 ? w.text.slice(0, cut) : w.text);
        setText($('bb-saved-tail'), cut > 0 ? w.text.slice(cut) : '');
        $('bb-glyph').className = 'glyph ' + ({ saving: 'working', failed: 'error', saved: 'final', idle: 'never' }[w.kind]);
        $('bench-bar').dataset.save = w.kind;
        // "Edit this round": the one round, else the list of rounds
        const href = lists.length === 1 ? '/checklists/edit/' + encodeURIComponent(lists[0].uid)
            : lists.length === 0 && loaded ? '/checklists/edit/new?slot=' + SLOT : '/checklists/edit';
        $('round-edit').setAttribute('href', href);
        $('round-edit-foot').setAttribute('href', href);
    }

    function show(state) {
        main.dataset.state = state;
        $('lists').hidden = state !== 'ready';
        $('round-reading').hidden = state !== 'reading';
        // busy only while it is the thing on screen: a hidden spinner that
        // still says aria-busy reads as "loading" to anything that asks
        $('round-reading').setAttribute('aria-busy', state === 'reading' ? 'true' : 'false');
        $('round-empty').hidden = state !== 'empty';
        $('round-failed').hidden = state !== 'failed';
    }

    function build() {
        const box = $('lists');
        const title = $('round-items-title');
        box.textContent = '';
        box.appendChild(title);
        const lists = round.lists();
        for (const cl of lists) {
            if (lists.length > 1) {
                const h = document.createElement('h2');
                h.className = 'rgroup';
                h.textContent = cl.name || '';
                box.appendChild(h);
            }
            for (const i of (cl.items || [])) {
                const r = round.row(String(i.uid));
                if (!r) continue;
                if (r.kind === 'header') {
                    const h = document.createElement('h3');
                    h.className = 'rhead';
                    h.textContent = r.text;
                    box.appendChild(h);
                    continue;
                }
                const tpl = $(r.kind === 'tick' ? 'row-tpl' : 'reading-tpl');
                const el = tpl.content.firstElementChild.cloneNode(true);
                el.dataset.item = r.uid;
                el.dataset.cl = r.cl;
                el.dataset.kind = r.kind;
                el.classList.toggle('sub', r.sub);
                el.querySelector('.tick').setAttribute('aria-label', r.text);
                el.querySelector('.rlabel').textContent = r.text;
                const input = el.querySelector('.rinput');
                if (input) {
                    input.setAttribute('aria-label', r.text + (r.units ? ', in ' + r.units : ''));
                    if (r.kind === 'text') { input.removeAttribute('inputmode'); input.placeholder = 'Note'; }
                    const u = el.querySelector('.runits');
                    if (r.units) {
                        const span = u || document.createElement('span');
                        span.className = 'runits';
                        span.textContent = r.units;
                        if (!u) input.after(span);
                    } else if (u) u.remove();
                }
                box.appendChild(el);
            }
        }
        round.rows().forEach(r => { if (r.kind !== 'header') paintRow(r.uid); });
        paintHead();
    }

    function anyHeld() {
        return Array.from($('lists').querySelectorAll('.rinput')).some(i => LEMjs.liveEdit.holds(i));
    }

    let fetching = false;
    function fetchDay() {
        if (fetching) return Promise.resolve();
        fetching = true;
        const s = round.beginFetch();
        const url = '/api/checklists?day=' + encodeURIComponent(DAY);
        const get = LV ? LV.bgFetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' })
            : fetch(url, { cache: 'no-store' });
        return get.then(r => r.json().catch(() => ({})).then(b => ({ ok: r.ok, status: r.status, body: b })))
            .catch(() => ({ ok: false, status: 0, body: {} }))
            .then(res => {
                fetching = false;
                if (!res.ok || !res.body || !Array.isArray(res.body.checklists)) {
                    if (!loaded) {
                        setText($('round-failed-why'), (res.body && res.body.error)
                            ? String(res.body.error) : res.status ? 'LEM answered ' + res.status + '.'
                            : 'LEM did not answer.');
                        show('failed');
                    }
                    return;
                }
                if (!loaded) {
                    round.load(res.body, SLOT);
                    loaded = true;
                    build();
                    show(round.lists().length ? 'ready' : 'empty');
                    return;
                }
                const m = round.merge(res.body, s);
                if (!m.restructure) { paintHead(); return; }   // the clock moves "Overdue" too
                // someone edited the round: rebuild, but never under a save or a typed value
                if (round.counts().saving || anyHeld()) return;
                round.load(res.body, SLOT);
                build();
                show(round.lists().length ? 'ready' : 'empty');
            });
    }

    function readTracked() {
        const get = LV ? LV.bgFetch('/api/tracked', { headers: { Accept: 'application/json' } }) : fetch('/api/tracked');
        get.then(r => r.ok ? r.json() : Promise.reject(r.status))
            .then(b => {
                const out = {};
                for (const t of (b.tracked || [])) out[t.uid] = t;
                tracked = out;
            })
            .catch(() => { tracked = 'failed'; })
            .then(() => round.rows().forEach(r => { if (r.track) paintRow(r.uid); }));
    }

    // ── acts ──────────────────────────────────────────────────────────────
    function gate(act, again) {
        if (root.LEMSignIn) root.LEMSignIn.need(act, again);
    }

    function tapRow(uid) {
        const what = round.tap(uid);
        if (what === 'signin') gate('tick', () => tapRow(uid));
        else if (what === 'focus') {
            const el = rowEl(uid);
            const input = el && el.querySelector('.rinput');
            if (input) input.focus();
        }
    }
    function undoRow(uid) {
        if (round.undo(uid) === 'signin') gate('undo', () => undoRow(uid));
    }
    function saveRow(uid) {
        const el = rowEl(uid);
        const input = el && el.querySelector('.rinput');
        if (!input) return;
        const what = round.save(uid, input.value);
        if (what === 'signin') gate('save this reading', () => saveRow(uid));
        else if (what === 'saving') input.setAttribute('value', round.row(uid).st.value);
        else if (what === 'refused') input.focus();
    }

    $('lists').addEventListener('click', (ev) => {
        const t = ev.target;
        const el = t.closest && t.closest('.rrow');
        if (!el) return;
        const uid = el.dataset.item;
        if (t.closest('a')) return;                          // Past readings
        if (t.closest('.undo')) { undoRow(uid); return; }
        if (t.closest('.rsave')) { saveRow(uid); return; }
        if (t.closest('.rinput')) return;                    // typing is not a tick
        tapRow(uid);
    });
    $('lists').addEventListener('keydown', (ev) => {
        const input = ev.target.closest && ev.target.closest('.rinput');
        if (!input || ev.key !== 'Enter' || ev.isComposing) return;
        ev.preventDefault();
        saveRow(input.closest('.rrow').dataset.item);
    });
    $('lists').addEventListener('input', (ev) => {
        const el = ev.target.closest && ev.target.closest('.rrow');
        const r = el && round.row(el.dataset.item);
        if (!r) return;
        markDirty(el, r);
        // a refused value's sentence goes as soon as the person fixes it
        if (r.error && !r.saving && r.error.indexOf('Not saved') !== 0) { r.error = ''; paintRow(r.uid); }
    });
    $('round-retry').addEventListener('click', () => { show('reading'); fetchDay(); });

    // ── the bench bar ─────────────────────────────────────────────────────
    function paintWho(name) {
        name = name || '';
        $('bb-in').hidden = !name;
        $('bb-out').hidden = !!name;
        setText($('bb-name'), name);
        const ini = root.LEMUi && root.LEMUi.initials ? root.LEMUi.initials(name) : name.slice(0, 2).toUpperCase();
        setText($('bb-avatar'), name ? ini : '');
    }
    $('bb-switch').addEventListener('click', () => root.LEMSignIn && root.LEMSignIn.switchPerson());
    $('bb-signin').addEventListener('click', () => root.LEMSignIn && root.LEMSignIn.open({ act: 'tick' }));
    document.addEventListener('lem:auth', (ev) => paintWho(ev.detail && ev.detail.user));
    paintWho(document.body.dataset.user || '');

    function tickLive() {
        if (!LV || !root.LEMStatus) return;
        const w = root.LEMStatus.liveWords(LV.status(), Date.now());
        setText($('bb-live-text'), w.text);
        $('bb-dot').dataset.state = w.state;
    }
    setInterval(tickLive, 1000);

    // ── live: a changed round anywhere re-reads the day, row by row ───────
    let lastRound = null;
    if (LV) {
        LV.subscribe((u) => {
            const key = JSON.stringify(u && u.round);
            if (lastRound !== null && key !== lastRound && loaded) fetchDay();
            lastRound = key;
            tickLive();
        });
    }
    // and every 30 s while visible, a GET (never a POST: A.7)
    setInterval(() => { if (document.visibilityState !== 'hidden') fetchDay(); }, 30000);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && loaded) fetchDay();
    });

    // ── keep the tablet awake while the round is open (best effort) ───────
    let lock = null;
    function wake() {
        if (!('wakeLock' in navigator) || document.visibilityState !== 'visible' || lock) return;
        navigator.wakeLock.request('screen').then((l) => {
            lock = l;
            l.addEventListener('release', () => { lock = null; });
        }).catch(() => { lock = null; });
    }
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('pagehide', () => { if (lock) { lock.release().catch(() => {}); lock = null; } });
    wake();

    // ── first paint ───────────────────────────────────────────────────────
    let first = null;
    try { first = main.dataset.round ? JSON.parse(main.dataset.round) : null; } catch (_e) { first = null; }
    if (first) {
        round.load(first, SLOT);
        loaded = true;
        round.rows().forEach(r => { if (r.kind !== 'header') paintRow(r.uid); });
        paintHead();
        show(round.lists().length ? 'ready' : 'empty');
    } else {
        show('reading');
        fetchDay();
    }
    readTracked();
    tickLive();
    root.LEMRound = round;
})(typeof window !== 'undefined' ? window : globalThis);
