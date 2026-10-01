// LEM's shell (templates/_layout.html, _shell.html), ported from GC hub's
// static/js/shell.js: theme, the sidebar rail, the phone drawer, the user
// menu, Recent, the bell's panel, the record-age words, the in-place sign-in
// and small DOM helpers the pages share (LEMShell). Loaded in <head> right
// after ui_logic.js so the theme is set before the page paints. Every server
// or browser-stored string is set with textContent.
//
// Storage keys are LEM's own (lem.theme, lem.sidebar, lem.recent): GC hub can
// be open on the same machine, and one app's choice must not flip the other.
(function () {
    'use strict';
    const U = window.LEMUi;
    const THEME_KEY = 'lem.theme';
    const SIDEBAR_KEY = 'lem.sidebar';
    const RECENT_KEY = 'lem.recent';

    function load(key) {
        try { return window.localStorage.getItem(key); } catch (_e) { return null; }
    }
    function save(key, value) {
        try {
            if (value === null) window.localStorage.removeItem(key);
            else window.localStorage.setItem(key, value);
        } catch (_e) { /* private mode, blocked storage: the default applies */ }
    }

    // ── theme, before first paint ───────────────────────────────────────────
    // System (the default: follows prefers-color-scheme, live, no reload),
    // Light or Dark, kept in localStorage 'lem.theme'. <html data-theme> is set
    // here, in <head>, before <body> exists. window.LEMTheme:
    //   LEMTheme.get()        -> { choice: 'system'|'light'|'dark', mode: 'light'|'dark' }
    //   LEMTheme.set(choice)  -> stores it and applies it; returns get()
    // and a 'lem:theme' event on document (detail = get()) whenever the mode
    // or the choice changes, so a page's charts can re-theme.
    const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    function themePref() { return U.themeChoice(load(THEME_KEY)); }
    let themeNow = null;
    function themeGet() {
        return { choice: themePref(), mode: document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light' };
    }
    function syncThemeSwitches() {
        const pref = themePref();
        document.querySelectorAll('[data-theme-choice]').forEach(b =>
            b.setAttribute('aria-checked', b.dataset.themeChoice === pref ? 'true' : 'false'));
    }
    function applyTheme() {
        document.documentElement.setAttribute('data-theme', U.resolveTheme(themePref(), !!(media && media.matches)));
        const now = themeGet();
        const changed = themeNow && (themeNow.choice !== now.choice || themeNow.mode !== now.mode);
        themeNow = now;
        if (changed) {
            syncThemeSwitches();
            document.dispatchEvent(new CustomEvent('lem:theme', { detail: now }));
        }
    }
    function themeSet(choice) {
        if (!U.THEME_CHOICES.includes(choice)) throw new Error('LEMTheme.set: system, light or dark, not ' + choice);
        save(THEME_KEY, choice);
        applyTheme();
        syncThemeSwitches();
        return themeGet();
    }
    window.LEMTheme = { get: themeGet, set: themeSet };
    applyTheme();
    if (media && media.addEventListener) media.addEventListener('change', applyTheme);
    else if (media && media.addListener) media.addListener(applyTheme);
    // another tab changed the choice
    window.addEventListener('storage', (ev) => {
        if (ev.key === THEME_KEY) applyTheme();
        if (ev.key === SIDEBAR_KEY) applySidebar();
    });

    // ── sidebar: Automatic, Names (full) or Icons (rail) ────────────────────
    function sidebarPref() { return U.sidebarChoice(load(SIDEBAR_KEY)); }
    function applySidebar() {
        const pref = sidebarPref();
        if (pref === 'auto') document.documentElement.removeAttribute('data-sidebar');
        else document.documentElement.setAttribute('data-sidebar', pref);
        document.dispatchEvent(new CustomEvent('lem:sidebar', { detail: { choice: pref } }));
    }
    function sidebarSet(choice) {
        if (!U.SIDEBAR_CHOICES.includes(choice)) throw new Error('LEMSidebar.set: auto, full or rail, not ' + choice);
        save(SIDEBAR_KEY, choice === 'auto' ? null : choice);
        applySidebar();
        return sidebarPref();
    }
    window.LEMSidebar = { get: sidebarPref, set: sidebarSet };
    applySidebar();

    // ── DOM helpers (text only) ─────────────────────────────────────────────
    function h(tag, props, ...children) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(props || {})) {
            if (v === null || v === undefined || v === false) continue;
            if (k === 'className') el.className = v;
            else if (k === 'text') el.textContent = String(v);
            else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
            else if (k === 'value') el.value = v;
            else if (k === 'checked') el.checked = !!v;
            else el.setAttribute(k, v === true ? '' : String(v));
        }
        for (const c of children.flat()) {
            if (c === null || c === undefined || c === false) continue;
            el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
        }
        return el;
    }
    const $ = (id) => document.getElementById(id);
    function icon(name) { return h('span', { className: 'ico ico-' + name, 'aria-hidden': 'true' }); }
    function glyph(kind, label) {
        return h('span', { className: 'glyph ' + kind, role: label ? 'img' : null, 'aria-label': label || null,
                           'aria-hidden': label ? null : 'true' });
    }

    let toastTimer = null;
    function toast(message, kind) {
        const t = $('toast');
        if (!t) return;
        t.textContent = message || '';
        t.className = 'toast' + (message ? ' show' : '') + (kind === 'err' ? ' err' : '');
        clearTimeout(toastTimer);
        if (message) toastTimer = setTimeout(() => { t.className = 'toast'; }, kind === 'err' ? 9000 : 5000);
    }

    // ── Recent, on this computer ────────────────────────────────────────────
    function recents() {
        try { return U.recentClean(JSON.parse(load(RECENT_KEY) || '[]')); } catch (_e) { return []; }
    }
    function addRecent(item) {
        save(RECENT_KEY, JSON.stringify(U.recentAdd(recents(), item, 6, Date.now())));
        renderRecent();
    }
    function renderRecent() {
        const ul = $('recent-list');
        if (!ul) return;
        const list = recents();
        ul.replaceChildren(...list.map(r => h('li', {}, h('a', { href: r.href },
            h('span', { text: r.label }), h('span', { className: 'when', text: U.clockTime(new Date(r.at || 0).toISOString()) })))));
        $('recent-empty').hidden = list.length > 0;
    }

    // ── nav meta and rail badges ────────────────────────────────────────────
    // One call sets all three places a page's count lives: the meta beside
    // the name, the badge on the rail icon, and the item's accessible name.
    // `meta` is { text: '6 need you', badge: '6' } or null to clear.
    function setNavMeta(key, meta) {
        const item = document.querySelector('.nav-item[data-nav="' + key + '"]');
        if (!item) return;
        const name = item.querySelector('.sb-label').textContent.trim();
        const text = meta && meta.text ? String(meta.text) : '';
        item.querySelector('.nav-meta').textContent = text;
        item.querySelector('.rail-badge').textContent = text && meta.badge !== undefined ? String(meta.badge) : '';
        item.setAttribute('aria-label', U.railLabel(name, text));
    }

    // ── the record's age, the benches: the foot and the rail strip ─────────
    // Both places say the same words from one state. The live feed
    // (status.js) calls setStatus every second once it has started; before
    // its first answer the page's own render ages on screen.
    const record = { at: null, labcore: 'unknown' };
    let statusOverride = null;          // { state, text } from the live feed
    function renderRecord() {
        const st = statusOverride || U.recordStatus(record, Date.now());
        for (const [dotId, textId] of [['live-dot', 'live-text'], ['rs-dot', 'rs-record-text']]) {
            const dot = $(dotId);
            const text = $(textId);
            if (dot) dot.dataset.state = st.state;
            if (text && text.textContent !== st.text) { text.textContent = st.text; text.title = st.text; }
        }
    }
    function setFleet(textValue, all) {
        const show = !!textValue;
        const fleet = $('fleet');
        if (fleet) {
            fleet.hidden = !show;
            $('fleet-text').textContent = textValue || '';
            if (all === undefined) fleet.removeAttribute('data-all');
            else fleet.dataset.all = all ? 'true' : 'false';
        }
        const rs = $('rs-fleet');
        if (rs) {
            rs.hidden = !show;
            rs.textContent = textValue || '';
            const sep = rs.previousElementSibling;
            if (sep && sep.classList.contains('rs-sep')) sep.hidden = !show;
        }
    }
    // what is running, in the strip (the foot has #running-now for it)
    function setJob(textValue) {
        const job = $('rs-job');
        if (!job) return;
        const show = !!textValue;
        job.hidden = !show;
        if (job.textContent !== (textValue || '')) job.textContent = textValue || '';
        const sep = job.previousElementSibling;
        if (sep && sep.classList.contains('rs-sep')) sep.hidden = !show;
    }
    // status: { live?: {state, text}, fleet?: {checking, total} | null, job?: text | null }
    // The live feed (status.js, running_now.js) calls this; the words in the
    // foot and in the rail strip are always the same words.
    function setStatus(status) {
        if (!status) return;
        if (status.live) statusOverride = { state: status.live.state, text: String(status.live.text) };
        if ('fleet' in status) {
            const f = status.fleet;
            setFleet(f ? U.fleetText(f.checking, f.total) : null, f ? f.checking === f.total : undefined);
        }
        if ('job' in status) setJob(status.job);
        renderRecord();
    }

    // ── menus and the drawer ───────────────────────────────────────────────
    function toggle(panel, button, open) {
        if (!panel || !button) return;
        const show = open === undefined ? panel.hidden : open;
        panel.hidden = !show;
        button.setAttribute('aria-expanded', show ? 'true' : 'false');
    }
    let catcher = null;
    function drawer(open, byKeyboard) {
        const btn = $('drawer-open');
        if (open) {
            document.documentElement.setAttribute('data-drawer', 'open');
            if (!catcher) {
                catcher = h('button', { type: 'button', className: 'drawer-catch', 'aria-label': 'Close the menu',
                                        onclick: () => drawer(false) });
                document.body.appendChild(catcher);
            }
            // a keyboard opener lands in the menu; a tap does not draw a focus ring
            const first = document.querySelector('#sidebar .nav-item');
            if (first && byKeyboard) first.focus();
        } else {
            if (!document.documentElement.hasAttribute('data-drawer')) return;
            document.documentElement.removeAttribute('data-drawer');
            if (catcher) { catcher.remove(); catcher = null; }
            if (btn) btn.focus();
        }
        if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    // ── sign in, out, switch person (in place: same page, same scroll) ─────
    let reloadOnClose = false;
    function signIn(title) {
        const dlg = $('signin-sheet');
        if (!dlg) return;
        // Switch person has already signed the last person out; however the
        // sheet closes (Cancel, Escape), the page must redraw signed out.
        reloadOnClose = title === 'Switch person';
        $('signin-title').textContent = title || 'Sign in';
        $('signin-error').hidden = true;
        $('signin-pass').value = '';
        if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
        $('signin-user').focus();
    }
    async function submitSignIn(ev) {
        ev.preventDefault();
        const ok = $('signin-ok');
        const err = $('signin-error');
        ok.disabled = true;
        err.hidden = true;
        let res = null;
        try {
            res = await fetch('/api/login', {
                method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ username: $('signin-user').value, password: $('signin-pass').value }),
            });
        } catch (_e) { res = null; }
        let body = null;
        try { body = res ? await res.json() : null; } catch (_e) { body = null; }
        ok.disabled = false;
        if (res && res.ok && body && body.ok) {
            location.reload();          // the shell and the page redraw with the name; the URL and scroll stay
            return;
        }
        // a refusal is not a network failure: say which one it was
        err.textContent = !res ? 'Not signed in: LEM did not answer. Check the connection and try again.'
            : res.status === 401 ? ((body && body.error) || 'That user name and password were not accepted.')
            : 'Not signed in: LEM answered ' + res.status + '. Try again in a moment.';
        err.hidden = false;
        $('signin-pass').select();
    }
    async function signOut(then) {
        let ok = false;
        try { ok = (await fetch('/api/logout', { method: 'POST', headers: { Accept: 'application/json' } })).ok; }
        catch (_e) { ok = false; }
        if (!ok) { toast('Not signed out: LEM did not answer. Try again.', 'err'); return false; }
        if (then) then(); else location.reload();
        return true;
    }

    // ── the version badge's width, for gutters that must clear it ──────────
    function trackBadgeWidth() {
        const b = $('app-version');
        if (!b) return;
        const set = () => {
            const w = b.getBoundingClientRect().width;
            if (w > 0) document.documentElement.style.setProperty('--badge-w', Math.ceil(w) + 'px');
        };
        set();
        if (typeof ResizeObserver === 'function') new ResizeObserver(set).observe(b);
    }

    // ── wiring ──────────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {
        trackBadgeWidth();
        syncThemeSwitches();
        if (!$('sidebar')) return;
        const signedIn = $('user-name').dataset.signedIn === 'true';
        if (signedIn) $('user-initials').textContent = U.initials($('user-name').textContent);

        const rec = $('rs-record');
        if (rec) { record.at = rec.dataset.at || null; record.labcore = rec.dataset.labcore || 'unknown'; }
        renderRecord();
        setInterval(renderRecord, 1000);

        $('sb-toggle').addEventListener('click', () => {
            const railNow = $('sidebar').getBoundingClientRect().width < 120;
            sidebarSet(railNow ? 'full' : 'rail');
        });
        const opener = $('drawer-open');
        if (opener) opener.addEventListener('click', (ev) => {
            ev.stopPropagation();
            drawer(!document.documentElement.hasAttribute('data-drawer'), ev.detail === 0);
        });

        const chip = $('user-chip');
        const menu = $('user-menu');
        const bell = $('bell');
        const panel = $('bell-panel');
        chip.addEventListener('click', (ev) => { ev.stopPropagation(); toggle(panel, bell, false); toggle(menu, chip); syncThemeSwitches(); });
        bell.addEventListener('click', (ev) => { ev.stopPropagation(); toggle(menu, chip, false); toggle(panel, bell); });
        document.addEventListener('click', (ev) => {
            if (!menu.hidden && !menu.contains(ev.target)) toggle(menu, chip, false);
            if (!panel.hidden && !panel.contains(ev.target)) toggle(panel, bell, false);
        });
        document.addEventListener('keydown', (ev) => {
            if (ev.key !== 'Escape') return;
            const wasOpen = !menu.hidden || !panel.hidden;
            toggle(menu, chip, false); toggle(panel, bell, false);
            if (wasOpen) chip.focus();
            drawer(false);
        });
        document.querySelectorAll('[data-theme-choice]').forEach(b => b.addEventListener('click', (ev) => {
            ev.stopPropagation();
            themeSet(b.dataset.themeChoice);
        }));
        if ($('menu-signout')) $('menu-signout').addEventListener('click', () => signOut());
        if ($('menu-switch')) $('menu-switch').addEventListener('click', () => {
            toggle(menu, chip, false);
            signOut(() => signIn('Switch person'));
        });
        if ($('menu-signin')) $('menu-signin').addEventListener('click', () => { toggle(menu, chip, false); signIn(); });
        const form = $('signin-form');
        if (form) {
            form.addEventListener('submit', submitSignIn);
            const dlg = $('signin-sheet');
            $('signin-cancel').addEventListener('click', () => { if (dlg.open) dlg.close(); });
            dlg.addEventListener('close', () => { if (reloadOnClose) location.reload(); });
        }

        renderRecent();
    });

    window.LEMShell = {
        h, icon, glyph, toast, addRecent, applyTheme, setNavMeta, setStatus, signIn, signOut, drawer,
    };
})();
