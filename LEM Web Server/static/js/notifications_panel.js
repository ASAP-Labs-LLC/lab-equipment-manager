/* notifications_panel.js: the bell's panel, ported from GC hub's
   static/js/notifications_panel.js (v5.0 lane R).

   It lists LEM's notifications newest first, each with its level as a glyph
   and a word (never colour alone), when it began, and a "Go to" link to where
   it is dealt with; Dismiss one, or Dismiss all. It follows LEMLive: an
   instrument that goes to Stop shows without a reload.

   What changed for LEM, and why:
   * The items come IN the live answer (`notifications`, only when they
     changed), not from a second GET. One request per poll, and the bell can
     never disagree with the count beside it.
   * Each item carries its own link (`href`, `link`). GC guessed the link from
     the message's words; LEM's server knows which record a condition is about.
   * Dismissing is per browser (localStorage `lem.notes-dismissed`), not a
     POST. An item is a condition that is TRUE right now ("GC-1 is not OK to
     run"); dismissing it here hides it on this computer and does not pretend
     it is fixed. When the condition clears and comes back, it is a new item
     (a new id) and shows again. It also keeps every open page free of POSTs:
     only a person's click could make one, and here not even that.

   Pure helpers are node-tested (tests/js/notifications_panel.mjs). */
(function (root) {
    'use strict';
    const req = (typeof require === 'function') ? require : null;
    const U = (root && root.LEMUi) || (req ? req('./ui_logic.js') : null);
    const DISMISS_KEY = 'lem.notes-dismissed';
    const DISMISS_MAX = 300;

    const LEVELS = {
        error: { glyph: 'error', word: 'Error' },
        warning: { glyph: 'held', word: 'Warning' },
        success: { glyph: 'final', word: 'Resolved' },
    };
    function level(l) { return Object.assign({}, LEVELS[l] || { glyph: 'never', word: 'Note' }); }

    /** Where the item is dealt with: the server's own link, same-site only. */
    function link(n) {
        const href = n && typeof n.href === 'string' ? n.href : '';
        if (!/^\/(?![\/\\])/.test(href)) return null;
        return { href, text: (n.link && String(n.link)) || 'Open' };
    }

    /** "30 s ago" within the hour, else the clock time ("09:12", "Sep 28 09:12"). */
    function when(ts, nowMs) {
        if (!ts) return '';
        const at = Date.parse(ts);
        if (isNaN(at)) return '';
        return nowMs - at < 3600 * 1000 ? (U.relTime(ts, nowMs) || '') : U.clockTime(ts, nowMs);
    }

    function sorted(list) {
        const t = (n) => { const v = Date.parse(n && n.ts); return isNaN(v) ? -Infinity : v; };
        return (Array.isArray(list) ? list : []).map((n, i) => [n, i])
            .sort((a, b) => (t(b[0]) - t(a[0])) || (a[1] - b[1])).map((x) => x[0]);
    }
    function title(n) { return n ? 'Notifications · ' + n : 'Notifications'; }
    function badge(n) { return !n ? '' : n > 99 ? '99+' : String(n); }
    function bellLabel(n) { return n ? 'Notifications: ' + n : 'Notifications'; }
    function without(list, id) { return (list || []).filter((n) => n.id !== id); }
    /** The items this browser has not dismissed, less the ones this page
        folds: a page that names each instrument's problems on its own rows
        (Instruments) folds the lines `about` instruments, which would be
        their second telling there. They are not listed and not counted. */
    function visible(list, dismissed, fold) {
        return sorted(list).filter((n) => !dismissed.has(n.id) && !(fold && n.about === fold));
    }
    /** Did this page fold anything the person has not dismissed? */
    function folded(list, dismissed, fold) {
        return !!fold && (list || []).some((n) => n.about === fold && !dismissed.has(n.id));
    }
    const FOLD_NOTES = { instruments: 'Instrument problems are on this page, each on its own row.' };
    function foldNote(fold) { return FOLD_NOTES[fold] || ''; }
    /** The sentence when nothing is listed. Not answered yet is not "no
        notifications", and neither is "all of them are on this page". */
    function emptyText(o) {
        if (!o || !o.known) return 'Checking for notifications…';
        if (o.folded) return 'Nothing else. ' + foldNote(o.fold || 'instruments');
        if (o.any) return 'Nothing new. Everything here was dismissed on this computer.';
        return 'No notifications. Instruments that go to Stop, overdue rounds and LabCore trouble will show here.';
    }
    /** Dismissed ids that still name a live item (the rest are forgotten, so
        the stored list cannot grow without end). */
    function prune(dismissed, list) {
        const ids = new Set((list || []).map((n) => n.id));
        return new Set([...dismissed].filter((id) => ids.has(id)));
    }

    function _storage(s) {
        if (s !== undefined) return s;
        try { return root.localStorage || null; } catch (_) { return null; }
    }
    function loadDismissed(storage) {
        try {
            const st = _storage(storage);
            const raw = st ? st.getItem(DISMISS_KEY) : null;
            const list = raw ? JSON.parse(raw) : [];
            return new Set(Array.isArray(list) ? list.filter((x) => typeof x === 'string') : []);
        } catch (_) { return new Set(); }
    }
    function saveDismissed(set, storage) {
        try {
            const st = _storage(storage);
            if (st) st.setItem(DISMISS_KEY, JSON.stringify([...set].slice(-DISMISS_MAX)));
        } catch (_) { /* not remembered: private window, blocked storage */ }
    }

    const pure = { DISMISS_KEY, level, link, when, sorted, title, badge, bellLabel, without,
                   visible, folded, foldNote, emptyText, prune, loadDismissed, saveDismissed };
    if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }
    root.LEMNotesLogic = pure;
    if (typeof document === 'undefined') return;

    // ── the panel ─────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    let notes = [];
    let known = false;            // has the live feed answered yet?
    let dismissed = loadDismissed();
    // what this page already says on its own (Instruments: each row's problems)
    let fold = '';
    function readFold() {
        const at = document.querySelector('[data-bell-folds]');
        fold = at ? at.getAttribute('data-bell-folds') || '' : '';
    }

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined && text !== null) e.textContent = String(text);
        return e;
    }
    function toast(msg) { if (root.LEMShell && root.LEMShell.toast) root.LEMShell.toast(msg); }

    function render() {
        const list = visible(notes, dismissed, fold);
        const count = list.length;
        const isFolded = folded(notes, dismissed, fold);
        const c = $('bell-count');
        if (c) { c.hidden = !count; c.textContent = badge(count); }
        const bell = $('bell');
        if (bell) bell.setAttribute('aria-label', bellLabel(count));
        const head = $('bell-title');
        if (head) head.textContent = title(count);
        const ul = $('bell-list');
        if (!ul) return;
        const now = Date.now();
        ul.replaceChildren(...list.map((n) => {
            const lv = level(n.level);
            const li = el('li', 'note note-' + lv.glyph);
            li.dataset.id = n.id;
            li.setAttribute('data-testid', 'note');
            const g = el('span', 'glyph ' + lv.glyph);
            g.setAttribute('aria-hidden', 'true');
            const body = el('div', 'note-body');
            const meta = el('div', 'note-meta');
            meta.append(el('span', 'note-level', lv.word));
            const w = when(n.ts, now);
            if (w) { meta.append(el('span', 'note-sep', '·')); meta.append(el('span', 'note-when', w)); }
            body.append(meta, el('p', 'note-msg', n.message || ''));
            const go = link(n);
            if (go) {
                const a = el('a', 'note-go', go.text);
                a.href = go.href;
                body.append(a);
            }
            const x = el('button', 'icon-btn note-dismiss');
            x.type = 'button';
            x.setAttribute('aria-label', 'Dismiss on this computer: ' + String(n.message || '').slice(0, 80));
            x.title = 'Dismiss on this computer';
            x.append(el('span', 'ico ico-x'));
            x.addEventListener('click', (ev) => { ev.stopPropagation(); dismiss(n.id); });
            li.append(g, body, x);
            return li;
        }));
        const empty = $('bell-empty');
        if (empty) {
            empty.hidden = count > 0;
            empty.textContent = emptyText({ known, any: notes.length > 0, folded: isFolded, fold });
        }
        // folded lines are said to be here, with no name and no number
        const note = $('bell-fold');
        if (note) {
            note.hidden = !(isFolded && count > 0);
            note.textContent = foldNote(fold);
        }
        const clear = $('bell-clear');
        if (clear) clear.hidden = !count;
    }

    function dismiss(id) {
        dismissed.add(id);
        saveDismissed(dismissed);
        render();
        const bell = $('bell');
        if (!visible(notes, dismissed, fold).length && bell) bell.focus();
    }

    function dismissAll() {
        const list = visible(notes, dismissed, fold);
        for (const n of list) dismissed.add(n.id);
        saveDismissed(dismissed);
        render();
        toast(list.length === 1 ? 'Dismissed 1 notification on this computer.'
            : 'Dismissed ' + list.length + ' notifications on this computer.');
        const bell = $('bell');
        if (bell) bell.focus();
    }

    function onUpdate(u) {
        if (!Array.isArray(u.notifications)) return;
        known = true;
        notes = u.notifications;
        // forget dismissals of items that are gone, so the list stays small
        const kept = prune(dismissed, notes);
        if (kept.size !== dismissed.size) { dismissed = kept; saveDismissed(dismissed); }
        render();
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (!$('bell-panel')) return;
        readFold();
        $('bell-clear').addEventListener('click', (ev) => { ev.stopPropagation(); dismissAll(); });
        render();
        if (root.LEMLive) { root.LEMLive.subscribe(onUpdate); root.LEMLive.start(); }
        // another tab dismissed something
        root.addEventListener('storage', (ev) => {
            if (ev.key === DISMISS_KEY) { dismissed = loadDismissed(); render(); }
        });
        // "12 s ago" keeps counting while the panel is open (no request: a redraw)
        setInterval(() => { if (!$('bell-panel').hidden) render(); }, 15000);
    });

    root.LEMNotes = Object.assign({}, pure, { dismiss, dismissAll, list: () => notes.slice() });
})(typeof window !== 'undefined' ? window : null);
