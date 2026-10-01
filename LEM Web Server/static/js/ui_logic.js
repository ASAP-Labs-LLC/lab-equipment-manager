// The shell's pure logic: no DOM, node-tested (tests/js/ui_logic.mjs).
// Ported from GC hub's static/js/ui_logic.js (GCUi) and cut to what LEM's
// shell uses; the functions kept are GC's, word for word, so the two apps say
// "4 min ago" the same way. LEM's additions are at the bottom.
// Window global LEMUi; module.exports for node. Everything a page shows from
// here is set with textContent by the caller.
(function (root) {
    'use strict';

    // The one liveness rule shared with GC hub (live.LIVE_SECONDS): past 90 s
    // without an answer nothing on screen may call itself current.
    const LIVE_SECONDS = 90;

    function parseMs(iso) {
        if (!iso || typeof iso !== 'string') return null;
        const ms = Date.parse(iso);
        return isNaN(ms) ? null : ms;
    }

    function relTime(iso, nowMs) {
        const at = parseMs(iso);
        if (at === null) return null;
        const s = Math.round((nowMs - at) / 1000);
        if (s < 5) return 'just now';
        if (s < 60) return s + ' s ago';
        const m = Math.floor(s / 60);
        if (m < 60) return m + ' min ago';
        const h = Math.floor(m / 60);
        if (h < 48) return h + ' h ago';
        return Math.floor(h / 24) + ' d ago';
    }

    const pad = (n) => String(n).padStart(2, '0');
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // "12:31" today, "Sep 29 12:31" otherwise (this browser's local time).
    function clockTime(iso, nowMs) {
        const at = parseMs(iso);
        if (at === null) return '';
        const d = new Date(at);
        const n = new Date(nowMs === undefined ? Date.now() : nowMs);
        const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
        if (d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()) return hm;
        return MONTHS[d.getMonth()] + ' ' + d.getDate() + ' ' + hm;
    }

    function initials(name) {
        const words = String(name || '').trim().split(/\s+/).filter(Boolean);
        if (!words.length) return '?';
        return words.slice(0, 2).map(w => w[0].toUpperCase()).join('');
    }

    // ── the admin password, kept in a closure for `ttlMs` (never storage) ──
    // Kept from GC so the admin unlock (a later piece) has one gate to use.
    function makeAdminGate(opts) {
        const ttl = (opts && opts.ttlMs) || 15 * 60 * 1000;
        const now = (opts && opts.now) || (() => Date.now());
        let secret = null;
        let until = 0;
        return {
            get() {
                if (secret !== null && now() > until) { secret = null; until = 0; }
                return secret;
            },
            set(pw) { secret = String(pw); until = now() + ttl; },
            clear() { secret = null; until = 0; },
            remainingMs() { return secret === null ? 0 : Math.max(0, until - now()); },
            toJSON() { return {}; },
        };
    }

    // ── recents "on this computer" (localStorage, via the shell) ───────────
    // A stored href is followed on click, and anything on this origin can
    // write localStorage: only a same-site path survives (no //host, no \).
    const SAFE_HREF = /^\/(?![\/\\])[^\s\\]*$/;
    function recentClean(list) {
        return (Array.isArray(list) ? list : []).filter(r => r && typeof r === 'object' &&
            typeof r.href === 'string' && SAFE_HREF.test(r.href) && typeof r.label === 'string');
    }
    function recentAdd(list, item, max, atMs) {
        const rest = recentClean(list).filter(r => r.href !== item.href);
        return [{ href: item.href, label: String(item.label || item.href).slice(0, 60), at: atMs }]
            .concat(rest).slice(0, max || 6);
    }

    // System (follow the OS's prefers-color-scheme, live), Light or Dark;
    // anyone who hasn't chosen (or junk in storage) gets System.
    const THEME_CHOICES = ['system', 'light', 'dark'];
    function themeChoice(stored) {
        return THEME_CHOICES.includes(stored) ? stored : 'system';
    }
    function resolveTheme(pref, prefersDark) {
        const choice = themeChoice(pref);
        if (choice === 'system') return prefersDark ? 'dark' : 'light';
        return choice;
    }

    // ── LEM ─────────────────────────────────────────────────────────────────

    // The sidebar: Automatic (full at 1400 px and wider, icons below), Full,
    // or Icons only. 'auto' is stored as no choice at all.
    const SIDEBAR_CHOICES = ['auto', 'full', 'rail'];
    function sidebarChoice(stored) {
        return stored === 'full' || stored === 'rail' ? stored : 'auto';
    }

    // A rail item's accessible name keeps the words the badge stands for.
    function railLabel(name, meta) {
        const m = String(meta || '').trim();
        return m ? name + ', ' + m : name;
    }

    // "15 of 17 benches checking in". null when there is nothing to count or
    // the count is unknown: an unknown count is never shown as 0.
    function fleetText(checking, total) {
        if (typeof checking !== 'number' || typeof total !== 'number' || !isFinite(checking) || !(total > 0)) return null;
        return checking + ' of ' + total + (total === 1 ? ' bench' : ' benches') + ' checking in';
    }

    // How old the record this page was drawn from is, in words. The shell
    // does not poll on its own, so this never says "Live": it says
    // "Updated 4 s ago" and lets the age grow, and after LIVE_SECONDS it is
    // marked stale (a hollow glyph) though the words stay true.
    //   rec: { at: ISO of the snapshot build | null, labcore: 'reachable'|'unreachable'|'unknown' }
    //   -> { state: 'fresh'|'stale'|'error'|'never', text }
    function recordStatus(rec, nowMs) {
        const at = rec && parseMs(rec.at);
        const down = rec && rec.labcore === 'unreachable';
        if (at === null || at === undefined) {
            return down ? { state: 'error', text: 'LabCore not answering · nothing read yet' }
                        : { state: 'never', text: 'Not read from LabCore yet' };
        }
        if (down) return { state: 'error', text: 'LabCore not answering · record from ' + clockTime(rec.at, nowMs) };
        const age = (nowMs - at) / 1000;
        return { state: age > LIVE_SECONDS ? 'stale' : 'fresh', text: 'Updated ' + relTime(rec.at, nowMs) };
    }

    const api = {
        LIVE_SECONDS, relTime, clockTime, initials, makeAdminGate,
        recentAdd, recentClean, resolveTheme, themeChoice, THEME_CHOICES,
        SIDEBAR_CHOICES, sidebarChoice, railLabel, fleetText, recordStatus,
    };
    root.LEMUi = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
