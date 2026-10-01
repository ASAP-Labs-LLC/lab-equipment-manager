/* status.js: the shell's global status, wired to LEMLive (ia-final §2, §5).

   Every shell page shows, from ONE /api/ui/live answer:
     nav-meta + rail badges   "6 need you", "Opening 3/5", "1 out of spec"
     the fleet line           "15 of 17 benches checking in"
     the live row             "Live · updated 4 s ago" / "Reconnecting… · last update 40 s ago"
     the .rail-status strip   the same words, when the sidebar is only icons
     #paused-banner           one line, only while the data is degraded
     [data-live-count]        any count a page shows ("3 of 5 done")
   (the bell is notifications_panel.js; running-now is running_now.js.)

   Say it once (§0.2): the nav's count and a page's count are both read here
   from the same payload field by `counts()`, and nowhere else. The server
   builds the first paint with the same rule (ui_live.nav_meta), and
   tests/js/status.mjs runs both over one set of cases.

   The banner says only what is true (§2). It names the time the record is
   from and what is wrong; it never claims the page is current, because the
   one thing a degraded page cannot know is whether it is.

   Ages tick every second from the answer's own numbers (no request); the
   poller stays at 3 s visible / 30 s hidden, GETs only. Every string is set
   with textContent. Pure parts are module.exports for node. */
(function (root) {
    'use strict';
    const req = (typeof require === 'function') ? require : null;
    const LV = (root && root.LEMLive) || (req ? req('./live.js') : null);
    const NO_ANSWER_MS = 15000;           // the banner speaks after this long without LEM

    function plural(n, one, many) { return n === 1 ? one : many; }

    /** "40 s", "3 min", "2 h", "3 d". */
    function dur(seconds) {
        const s = Math.max(0, Math.round(seconds));
        if (s < 60) return s + ' s';
        if (s < 3600) return Math.floor(s / 60) + ' min';
        if (s < 86400) return Math.floor(s / 3600) + ' h';
        return Math.floor(s / 86400) + ' d';
    }

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    /** "13:02" today, "2 Sep 13:02" another day, in the lab's zone when known. */
    function clock(iso, nowMs, tz) {
        const ms = Date.parse(iso || '');
        if (!isFinite(ms)) return null;
        const parts = (t) => {
            if (tz && typeof Intl !== 'undefined') {
                try {
                    const f = new Intl.DateTimeFormat('en-GB', { timeZone: tz, year: 'numeric', month: 'numeric',
                        day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
                    const o = {};
                    for (const p of f.formatToParts(new Date(t))) o[p.type] = p.value;
                    return { y: +o.year, mo: +o.month - 1, d: +o.day, hm: o.hour + ':' + o.minute };
                } catch (_e) { /* unknown zone: the browser's clock */ }
            }
            const d = new Date(t);
            return { y: d.getFullYear(), mo: d.getMonth(), d: d.getDate(),
                     hm: String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') };
        };
        const a = parts(ms);
        const n = parts(nowMs);
        return a.y === n.y && a.mo === n.mo && a.d === n.d ? a.hm : a.d + ' ' + MONTHS[a.mo] + ' ' + a.hm;
    }

    // ── the counts: one function for the nav AND the page ──────────────────

    /** The nav's words for one payload (the same rule as ui_live.nav_meta).
        Unknown (null) is no words at all, never 0. */
    function navMeta(p) {
        const out = {};
        if (!p) return out;
        const n = p.needs_you;
        if (Number.isInteger(n) && n > 0) out.instruments = { text: n + ' ' + plural(n, 'needs you', 'need you'), badge: String(n) };
        const r = p.round;
        if (r && typeof r === 'object' && r.slot && r.total) {
            if (r.complete) out.checklists = { text: 'Done', badge: '✓' };
            else {
                const frac = r.done + '/' + r.total;
                out.checklists = { text: r.slot.charAt(0).toUpperCase() + r.slot.slice(1) + ' ' + frac, badge: frac };
            }
        }
        const q = p.qc_out;
        if (Number.isInteger(q) && q > 0) out.qc = { text: q + ' out of spec', badge: String(q) };
        return out;
    }

    /** What a page's own count says, from the SAME fields. `field` is
        needs_you | round | qc_out. null when unknown. */
    function pageCount(p, field) {
        if (!p) return null;
        if (field === 'round') {
            const r = p.round;
            if (!r || typeof r !== 'object') return null;
            if (!r.slot || !r.total) return 'No round set up';
            if (r.complete) return 'Done';
            return r.done + ' of ' + r.total + ' done';
        }
        if (field === 'needs_you') {
            const n = p.needs_you;
            if (!Number.isInteger(n)) return null;
            return n === 0 ? 'Nothing needs you' : n + ' ' + plural(n, 'instrument needs', 'instruments need') + ' you';
        }
        if (field === 'qc_out') {
            const q = p.qc_out;
            if (!Number.isInteger(q)) return null;
            return q === 0 ? 'No checks out of spec' : q + ' ' + plural(q, 'check', 'checks') + ' out of spec';
        }
        return null;
    }

    const NAV_FIELD = { instruments: 'needs_you', checklists: 'round', qc: 'qc_out' };
    /** Hand one payload to both places a count lives: setNav(key, meta|null)
        for the three nav items, setPage(field, text|null) for each field a
        page shows. One call, one payload: they cannot disagree. */
    function counts(p, setNav, setPage) {
        const meta = navMeta(p);
        for (const key of Object.keys(NAV_FIELD)) {
            setNav(key, meta[key] || null);
            if (setPage) setPage(NAV_FIELD[key], pageCount(p, NAV_FIELD[key]));
        }
        return meta;
    }

    // ── the live row ───────────────────────────────────────────────────────

    /** {state, text} for the live row and the strip. `state` is the dot:
        fresh (Live), error (Reconnecting), connecting. */
    function liveWords(st, now) {
        const text = LV.statusText(st, now);
        const state = text.startsWith('Live') ? 'fresh' : text.startsWith('Reconnecting') ? 'error' : 'connecting';
        return { state, text };
    }

    // ── the banner ─────────────────────────────────────────────────────────

    /** Seconds since the record was built, at `now`: the server's age plus the
        time since the answer was read. null when unknown. */
    function recordAge(p, now) {
        if (!p || typeof p.snapshot_age !== 'number') return null;
        const extra = typeof p.read_at === 'number' ? Math.max(0, (now - p.read_at) / 1000) : 0;
        return p.snapshot_age + extra;
    }

    /** The one degraded-data line, or null. Worst first. Never "current".
        `p` the last answer (or null), `st` LEMLive.status(), `sinceMs` when
        this page started asking. */
    function bannerText(p, st, now, sinceMs) {
        const tz = p && p.lab_tz;
        if (st && st.last_ok_at && !st.connected && now - st.last_ok_at > NO_ANSWER_MS) {
            const at = p && p.snapshot_at ? clock(p.snapshot_at, now, tz) : null;
            return 'LEM has not answered for ' + dur((now - st.last_ok_at) / 1000) + '. '
                + (at ? 'This page shows the record as of ' + at + '; ' : 'Nothing on this page is updating; ')
                + "changes can't be saved until LEM answers.";
        }
        if (st && !st.last_ok_at && st.error && sinceMs && now - sinceMs > NO_ANSWER_MS) {
            return "LEM is not answering. Nothing on this page is updating, and changes can't be saved.";
        }
        if (!p) return null;
        const age = recordAge(p, now);
        const at = p.snapshot_at ? clock(p.snapshot_at, now, tz) : null;
        if (p.labcore_online === false) {
            if (!at) return "Nothing has been read from LabCore yet, and it is not answering. Changes can't be saved until it does.";
            return 'Showing the record as of ' + at + '. LabCore has not answered for ' + dur(age || 0)
                + "; changes can't be saved until it does.";
        }
        if (p.snapshot_stale && at) {
            return 'Showing the record as of ' + at + '. LabCore is slow to answer; the record has not refreshed for '
                + dur(age || 0) + '.';
        }
        const m = p.mirror;
        if (m && (m.state === 'filling' || m.state === 'partial')) {
            const rows = typeof m.rows === 'number' ? m.rows.toLocaleString('en-US') + ' rows so far' : 'part-way';
            const to = m.held_to ? clock(m.held_to, now, tz) : null;
            return 'The log copy is filling (' + rows + (to ? ', up to ' + to : '') + '). '
                + (to ? 'History after that is not in the copy yet.' : 'Searches of the whole log are incomplete until it finishes.');
        }
        if (m && m.state === 'behind') {
            const to = m.complete_to ? clock(m.complete_to, now, tz) : null;
            return 'The log copy has not refreshed' + (to ? ' since ' + to : '') + '; searches of the whole log may miss the newest events.';
        }
        return null;
    }

    function fleet(p) {
        const f = p && p.fleet;
        return f && typeof f.total === 'number' ? { checking: f.checking_in, total: f.total } : null;
    }

    const pure = { dur, clock, navMeta, pageCount, counts, liveWords, recordAge, bannerText, fleet,
                   NO_ANSWER_MS };
    if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }
    root.LEMStatus = pure;
    if (typeof document === 'undefined') return;

    // ── the page ──────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    let last = null;
    let started = 0;
    let lastVersion = null;

    function setPage(field, text) {
        document.querySelectorAll('[data-live-count="' + field + '"]').forEach((el) => {
            if (text === null) { el.hidden = true; return; }
            el.hidden = false;
            if (el.textContent !== text) el.textContent = text;
        });
    }

    function tick() {
        const S = root.LEMShell;
        const now = Date.now();
        const st = LV.status();
        const words = liveWords(st, now);
        if (S && S.setStatus) S.setStatus({ live: words });
        const b = $('paused-banner');
        if (b) {
            const text = bannerText(last, st, now, started);
            b.hidden = !text;
            if (b.textContent !== (text || '')) b.textContent = text || '';
        }
    }

    function onUpdate(u) {
        last = u;
        const S = root.LEMShell;
        if (S && S.setNavMeta) counts(u, S.setNavMeta, setPage);
        const f = fleet(u);
        if (S && S.setStatus) S.setStatus({ fleet: f });
        if (lastVersion && u.version && u.version !== lastVersion && S && S.toast) {
            S.toast('LEM was updated to ' + u.version + '. Reload the page to use it.');
        }
        lastVersion = u.version || lastVersion;
        tick();
    }

    function mount() {
        if (!LV || !$('sidebar')) return;
        started = Date.now();
        LV.subscribe(onUpdate);
        LV.start();
        setInterval(tick, 1000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})(typeof window !== 'undefined' ? window : null);
