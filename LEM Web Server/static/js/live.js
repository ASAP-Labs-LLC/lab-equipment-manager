/* live.js: the live-update client, ported from GC hub's static/js/live.js
   (v4.0 "Live updates"). What changed for LEM: pollUrl() asks
   /api/ui/live (ia-final §5), the answer's fields are LEM's (machines,
   needs_you, fleet, round, qc_out, snapshot_age, labcore_online, mirror,
   notifications, jobs, lab_tz instead of GC's samples, agents and hub), and
   the background header is X-LEM-Background. The poller, its cadence, its
   back-off, its hung-request timeout and its "never Live after 90 s" rule are
   GC's, line for line.

   window.LEMLive:
     start()               idempotent; a Promise that resolves after the first
                           answer (or the first failure). Polls every 3 s while
                           the tab is visible, 30 s while hidden (backing off on
                           errors: at most 12 s visible, 60 s hidden), and at
                           once on visibilitychange/focus. Never two requests at
                           once. Only ever GETs: an open page never POSTs on a
                           timer (constraints A.7, the idle-gated deploy).
     subscribe(fn)         -> unsubscribe. fn(update) after every poll that
                           changed something, and once with {reset: true} after
                           start or a server restart (a late subscriber gets its
                           own reset at once).
                           update = {reset, machines: [uids changed], kinds,
                                     needs_you, fleet, round, qc_out,
                                     snapshot_age, snapshot_at, snapshot_stale,
                                     labcore_online, mirror, notifications,
                                     notifications_unread, jobs, version,
                                     version_changed, server_now, lab_tz,
                                     nav_meta, boot, read_at}
     last()                the latest folded state (a copy), or null.
     status()              {connected, last_ok_at (ms), error}; statusText()
                           says it: "Live · updated 12 s ago" /
                           "Reconnecting… · last update 40 s ago".
     bgFetch(url, opts)    a GET marked as background. Refuses anything else.
   Pure (node-tested in tests/js/live.mjs and live_poller.mjs): nextDelay,
   initialState, applyResponse, pollUrl, statusText, agoText, createPoller. */
(function (root) {
    'use strict';

    const VISIBLE_MS = 3000;
    const HIDDEN_MS = 30000;
    const VISIBLE_MAX_MS = 12000;
    const HIDDEN_MAX_MS = 60000;
    const BG_HEADER = 'X-LEM-Background';
    const LIVE_SECONDS = 90;
    // a poll the server accepted but never answered (hung, half-open) is given
    // up after this, so the status says so and the next poll can go out
    const POLL_TIMEOUT_MS = 15000;
    // no answer for longer than this is never "Live" (three hidden-tab polls)
    const STALE_MS = 3 * HIDDEN_MS;
    // the fields that are the lab's state; a change in any is an update
    const FIELDS = ['needs_you', 'fleet', 'round', 'qc_out', 'snapshot_at', 'snapshot_stale',
                    'labcore_online', 'mirror', 'notifications_unread', 'jobs', 'lab_tz', 'nav_meta',
                    'transfer'];

    function nextDelay(visible, failures) {
        const base = visible ? VISIBLE_MS : HIDDEN_MS;
        const cap = visible ? VISIBLE_MAX_MS : HIDDEN_MAX_MS;
        const f = Number.isFinite(failures) && failures > 0 ? Math.min(Math.floor(failures), 10) : 0;
        return Math.min(base * Math.pow(2, f), cap);
    }

    function initialState() {
        return { cursor: null, fields: {}, notifications: [], version: null,
                 snapshot_age: null, server_now: null, seen: false };
    }

    function _same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
    function _copy(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
    function _boot(cursor) {
        const i = typeof cursor === 'string' ? cursor.indexOf(':') : -1;
        return i > 0 ? cursor.slice(0, i) : null;
    }

    /** Fold one /api/ui/live answer into the state: {state, update}; update is
        null when nothing changed. Pure: *state* is not modified. A field the
        answer lacks keeps its last value. The record's age ticking is not a
        change (a page re-renders ages on its own timer). */
    function applyResponse(state, response) {
        if (!response || typeof response !== 'object' || typeof response.cursor !== 'string') {
            return { state, update: null };
        }
        const fields = Object.assign({}, state.fields);
        for (const k of FIELDS) if (k in response) fields[k] = _copy(response[k]);
        const notifications = Array.isArray(response.notifications)
            ? _copy(response.notifications) : _copy(state.notifications || []);
        const version = typeof response.version === 'string' ? response.version : state.version;
        const versionChanged = !!(state.version && version && version !== state.version);
        const reset = !!response.reset || !state.seen;
        const machines = reset ? [] : (Array.isArray(response.machines) ? response.machines.slice() : []);
        const kinds = Array.isArray(response.kinds) ? response.kinds.slice() : [];
        const age = typeof response.snapshot_age === 'number' ? response.snapshot_age
            : ('snapshot_age' in response ? null : state.snapshot_age);
        const changed = reset || machines.length > 0 || kinds.length > 0 || versionChanged
            || !_same(state.fields, fields) || !_same(state.notifications || [], notifications);
        const next = { cursor: response.cursor, fields, notifications, version,
                       snapshot_age: age, server_now: response.server_now || state.server_now,
                       seen: true };
        const update = changed
            ? Object.assign(_copy(fields), { reset, machines, kinds, notifications: _copy(notifications),
                version, version_changed: versionChanged, boot: _boot(response.cursor),
                snapshot_age: age, server_now: next.server_now })
            : null;
        return { state: next, update };
    }

    function pollUrl(cursor) {
        return cursor ? '/api/ui/live?since=' + encodeURIComponent(cursor) : '/api/ui/live';
    }

    function _toMs(t) {
        if (t == null) return null;
        if (typeof t === 'number') return Number.isFinite(t) ? t : null;
        const ms = Date.parse(t);
        return Number.isFinite(ms) ? ms : null;
    }

    /** "just now", "42 s ago", "5 min ago", "3 h ago", "2 d ago"; "never". */
    function agoText(t, now) {
        const ms = _toMs(t);
        if (ms == null) return 'never';
        const s = Math.max(0, Math.round(((now == null ? Date.now() : now) - ms) / 1000));
        if (s < 5) return 'just now';
        if (s < 60) return s + ' s ago';
        if (s < 3600) return Math.floor(s / 60) + ' min ago';
        if (s < 86400) return Math.floor(s / 3600) + ' h ago';
        return Math.floor(s / 86400) + ' d ago';
    }

    /** The live row's words. */
    function statusText(st, now) {
        if (!st || !st.last_ok_at) return 'Connecting…';
        const stale = (now == null ? Date.now() : now) - st.last_ok_at > STALE_MS;
        if (!st.connected || stale) return 'Reconnecting… · last update ' + agoText(st.last_ok_at, now);
        return 'Live · updated ' + agoText(st.last_ok_at, now);
    }

    /** A background GET. */
    function bgFetch(url, opts, fetchFn) {
        const o = Object.assign({}, opts || {});
        const method = String(o.method || 'GET').toUpperCase();
        if (method !== 'GET') return Promise.reject(new Error('bgFetch is for GET requests only'));
        o.method = 'GET';
        o.credentials = o.credentials || 'same-origin';
        o.headers = Object.assign({}, o.headers || {}, { [BG_HEADER]: '1' });
        const f = fetchFn || root.fetch;
        return Promise.resolve().then(() => f(url, o));
    }

    /** The poller, over injectable deps: {fetch, setTimeout, clearTimeout,
        now, isVisible, on(event, fn), off(event, fn)}. */
    function createPoller(deps) {
        const d = deps;
        let state = initialState();
        let subs = [];
        let started = false;
        let timer = null;
        let inFlight = false;
        let again = false;
        let failures = 0;
        let status = { connected: false, last_ok_at: 0, error: null };
        let startPromise = null;
        let resolveStart = null;
        let readAt = null;

        function emit(update) {
            for (const fn of subs.slice()) {
                try { fn(update); } catch (e) {
                    if (root.console) console.error('LEMLive subscriber', e);
                }
            }
        }

        function schedule(ms) {
            if (timer != null) d.clearTimeout(timer);
            timer = d.setTimeout(() => { timer = null; pollNow(); }, ms);
        }

        function pollNow() {
            if (!started) return;
            if (inFlight) { again = true; return; }
            inFlight = true;
            if (timer != null) { d.clearTimeout(timer); timer = null; }
            const ctl = typeof AbortController === 'function' ? new AbortController() : null;
            let gaveUp = null;
            const hung = new Promise((_, reject) => {
                gaveUp = d.setTimeout(() => {
                    gaveUp = null;
                    if (ctl) { try { ctl.abort(); } catch (_e) { /* already done */ } }
                    reject(new Error('no answer from LEM in ' + Math.round(POLL_TIMEOUT_MS / 1000) + ' s'));
                }, POLL_TIMEOUT_MS);
            });
            hung.catch(() => {});
            const opts = { cache: 'no-store', headers: { Accept: 'application/json' } };
            if (ctl) opts.signal = ctl.signal;
            Promise.race([bgFetch(pollUrl(state.cursor), opts, d.fetch), hung])
                .then(r => Promise.race([Promise.resolve(r).then(r => {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.text().then(t => JSON.parse(t));
                }), hung]))
                .then(body => {
                    const out = applyResponse(state, body);
                    if (out.state === state) throw new Error('bad /api/ui/live answer');
                    state = out.state;
                    readAt = d.now();
                    failures = 0;
                    status = { connected: true, last_ok_at: d.now(), error: null };
                    if (out.update) { out.update.read_at = readAt; emit(out.update); }
                })
                .catch(err => {
                    failures++;
                    status = { connected: false, last_ok_at: status.last_ok_at,
                               error: String((err && err.message) || err) };
                })
                .then(() => {
                    if (gaveUp != null) { d.clearTimeout(gaveUp); gaveUp = null; }
                    inFlight = false;
                    if (resolveStart) { const r = resolveStart; resolveStart = null; r(); }
                    if (!started) return;
                    if (again) { again = false; pollNow(); return; }
                    schedule(nextDelay(d.isVisible(), failures));
                });
        }

        function onWake() {
            if (started && d.isVisible()) pollNow();
        }

        function start() {
            if (startPromise) return startPromise;
            started = true;
            state = initialState();
            failures = 0;
            startPromise = new Promise(r => { resolveStart = r; });
            d.on('visibilitychange', onWake);
            d.on('focus', onWake);
            pollNow();
            return startPromise;
        }

        function stop() {
            started = false;
            startPromise = null;
            if (timer != null) { d.clearTimeout(timer); timer = null; }
            d.off('visibilitychange', onWake);
            d.off('focus', onWake);
        }

        function snapshot() {
            return Object.assign(_copy(state.fields), {
                reset: true, machines: [], kinds: [], notifications: _copy(state.notifications),
                version: state.version, version_changed: false, boot: _boot(state.cursor),
                snapshot_age: state.snapshot_age, server_now: state.server_now, read_at: readAt });
        }

        function subscribe(fn) {
            if (typeof fn !== 'function') return function () {};
            subs.push(fn);
            if (state.seen) {
                const snap = snapshot();
                Promise.resolve().then(() => {
                    if (subs.includes(fn)) { try { fn(snap); } catch (_) { /* its problem */ } }
                });
            }
            return function unsubscribe() { subs = subs.filter(x => x !== fn); };
        }

        return {
            start, stop, subscribe, pollNow,
            last: () => (state.seen ? snapshot() : null),
            status: () => {
                // backstop: whatever stalled the poll, an old answer is not "connected"
                const st = Object.assign({}, status);
                const limit = 3 * nextDelay(d.isVisible(), 0) + POLL_TIMEOUT_MS;
                if (st.connected && st.last_ok_at && d.now() - st.last_ok_at > limit) {
                    st.connected = false;
                    st.error = st.error || 'no answer from LEM';
                }
                return st;
            },
        };
    }

    // ── the page's poller ──────────────────────────────────────────────────
    const hasDom = typeof document !== 'undefined';
    const browserDeps = {
        fetch: (url, opts) => root.fetch(url, opts),
        setTimeout: (fn, ms) => setTimeout(fn, ms),
        clearTimeout: (id) => clearTimeout(id),
        now: () => Date.now(),
        isVisible: () => !hasDom || document.visibilityState !== 'hidden',
        on: (ev, fn) => {
            if (ev === 'visibilitychange') { if (hasDom) document.addEventListener(ev, fn); }
            else if (typeof root.addEventListener === 'function') root.addEventListener(ev, fn);
        },
        off: (ev, fn) => {
            if (ev === 'visibilitychange') { if (hasDom) document.removeEventListener(ev, fn); }
            else if (typeof root.removeEventListener === 'function') root.removeEventListener(ev, fn);
        },
    };
    const page = createPoller(browserDeps);

    const api = {
        start: () => page.start(), stop: () => page.stop(), subscribe: fn => page.subscribe(fn),
        last: () => page.last(), status: () => page.status(), pollNow: () => page.pollNow(),
        bgFetch, nextDelay, initialState, applyResponse, pollUrl, statusText, agoText, createPoller,
        LIVE_SECONDS, POLL_TIMEOUT_MS, STALE_MS, FIELDS,
    };
    root.LEMLive = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
