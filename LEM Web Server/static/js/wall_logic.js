/* wall_logic.js: the wall kiosks' pure rules (ia-final §3.8), for /floor,
   /qc and /wall. Node-tested in tests/js/wall_logic.mjs; the DOM halves are
   wall_floor.js, wall_qc.js and wall.js.

   What lives here and nowhere else:

     liveState    the stale rule. No answer from /api/ui/live for 90 s and
                  the wall says "Not live · last update 13:15" and dims; a
                  snapshot the server says is stale is "Not current". A wall
                  that froze at green must not look like a wall that is green.
     clock, when  the lab's local time with its zone (lab_tz), by
                  Intl.DateTimeFormat. Never an ISO string sliced.
     parseKiosk   ?theme=dark|light, ?level=<uid>, ?rotate=0, ?show=, ?every=.
                  Junk is ignored, never obeyed.
     rotator      a page/level rotation that pauses on pointer or touch and
                  never runs when motion is reduced or rotate=0.
     qcGrid       how many /qc cards fit, measured, not wished.

   window.LEMWallLogic; module.exports for node. */
(function (root) {
    'use strict';

    const STALE_MS = 90000;
    const SAFE_KEY = /^[A-Za-z0-9_-]{1,64}$/;
    const WALLS = ['floor', 'qc'];

    // ── time ─────────────────────────────────────────────────────────────
    const _fmts = {};
    function _fmt(tz, seconds, zone) {
        const key = (tz || '') + '|' + seconds + '|' + zone;
        if (_fmts[key]) return _fmts[key];
        const opts = { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };
        if (seconds) opts.second = '2-digit';
        if (zone) opts.timeZoneName = 'short';
        let f;
        try { f = new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: tz || undefined }, opts)); }
        catch (_e) { f = new Intl.DateTimeFormat('en-US', opts); }   // a zone this browser does not know
        _fmts[key] = f;
        return f;
    }
    function _parts(f, ms) {
        const out = {};
        for (const p of f.formatToParts(new Date(ms))) out[p.type] = p.value;
        return out;
    }
    /** "13:15:04 PDT" (with seconds and zone) or "13:15", in the lab's zone. */
    function clock(ms, tz, full) {
        if (typeof ms !== 'number' || !Number.isFinite(ms)) return '—';
        const p = _parts(_fmt(tz, !!full, !!full), ms);
        const hm = p.hour + ':' + p.minute + (full ? ':' + p.second : '');
        return full && p.timeZoneName ? hm + ' ' + p.timeZoneName : hm;
    }
    /** The UTC offset at the end of an ISO stamp ("-07:00", "Z"), or ''. */
    function _offset(iso) {
        const m = /([+-]\d\d:?\d\d|Z)$/.exec(String(iso || ''));
        return m ? m[1] : '';
    }
    /** An ISO stamp as epoch ms. A naive stamp (the server writes its own
        local time without a zone) takes the server's offset from
        `serverNow`, so a TV set to another zone still reads lab time. */
    function toMs(iso, serverNow) {
        if (iso == null || iso === '') return null;
        if (typeof iso === 'number') return Number.isFinite(iso) ? iso : null;
        let s = String(iso);
        if (!_offset(s) && /T\d\d:\d\d/.test(s)) {
            const off = _offset(serverNow);
            if (off) s = s.replace(/(\.\d+)?$/, '') + off;
        }
        const ms = Date.parse(s);
        return Number.isFinite(ms) ? ms : null;
    }
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function _day(ms, tz) {
        let f;
        try { f = new Intl.DateTimeFormat('en-US', { timeZone: tz || undefined, year: 'numeric', month: 'numeric', day: 'numeric' }); }
        catch (_e) { f = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'numeric', day: 'numeric' }); }
        const p = _parts(f, ms);
        return { y: +p.year, m: +p.month, d: +p.day };
    }
    /** When a result was: "09:12" today, "30 Sep 15:04" before, in lab time. */
    function when(iso, tz, nowMs, serverNow) {
        const ms = toMs(iso, serverNow);
        if (ms == null) return '';
        const a = _day(ms, tz), b = _day(nowMs == null ? Date.now() : nowMs, tz);
        const hm = clock(ms, tz, false);
        if (a.y === b.y && a.m === b.m && a.d === b.d) return hm;
        return a.d + ' ' + MONTHS[a.m - 1] + (a.y !== b.y ? ' ' + a.y : '') + ' ' + hm;
    }

    // ── the stale rule ───────────────────────────────────────────────────
    /** {kind: 'live'|'lost'|'record', dim, headline, footer}. `headline` is
        null while live (the wall's own sentence stands). */
    function liveState(o) {
        o = o || {};
        const now = typeof o.nowMs === 'number' ? o.nowMs : Date.now();
        const heard = Math.max(o.lastOkMs || 0, o.loadedMs || 0);
        if (now - heard > STALE_MS) {
            return { kind: 'lost', dim: true,
                     headline: 'Not live · last update ' + clock(heard, o.tz, false),
                     footer: 'Stale · last update ' + clock(heard, o.tz, true) };
        }
        if (o.snapshotStale) {
            const at = toMs(o.builtAt, o.serverNow);
            return { kind: 'record', dim: true,
                     headline: 'Not current · record from ' + clock(at, o.tz, false),
                     footer: 'Record from ' + clock(at, o.tz, true) + ' · LabCore not answering' };
        }
        return { kind: 'live', dim: false, headline: null,
                 footer: 'Live · updated ' + clock(heard, o.tz, true) };
    }

    // ── kiosk parameters ─────────────────────────────────────────────────
    function parseKiosk(search) {
        let p;
        try { p = new URLSearchParams(search || ''); } catch (_e) { p = new URLSearchParams(''); }
        const theme = ['dark', 'light'].includes(p.get('theme')) ? p.get('theme') : '';
        const lv = p.get('level') || '';
        const show = String(p.get('show') || '').split(',').map(s => s.trim()).filter(s => WALLS.includes(s));
        const every = parseInt(p.get('every'), 10);
        return {
            theme,
            level: SAFE_KEY.test(lv) ? lv : '',
            rotate: p.get('rotate') !== '0',
            show: show.length ? Array.from(new Set(show)) : WALLS.slice(),
            every: Number.isFinite(every) ? Math.min(600, Math.max(15, every)) : 60,
        };
    }
    function wallSequence(show) { return (show && show.length ? show : WALLS).slice(); }

    // ── rotation ─────────────────────────────────────────────────────────
    /** A clock-driven page turner over injected time. tick(now) turns at
        most one page per call; pause()/resume() hold it while somebody is
        reading; go(step) turns by hand and restarts the period. */
    function rotator(o) {
        o = o || {};
        const every = o.every > 0 ? o.every : 15000;
        const enabled = o.enabled !== false;
        const r = {
            index: 0, count: Math.max(1, o.count | 0), every, enabled,
            paused: false, since: o.now || 0,
            tick(now) {
                if (this.enabled && !this.paused && this.count > 1 && now - this.since >= this.every) {
                    this.index = (this.index + 1) % this.count;
                    this.since = now;
                }
                return this;
            },
            pause(now) { if (!this.paused) { this.paused = true; this.since = now; } return this; },
            resume(now) { if (this.paused) { this.paused = false; this.since = now; } return this; },
            go(step, now) {
                this.index = ((this.index + step) % this.count + this.count) % this.count;
                this.since = now;
                return this;
            },
            setCount(n, now) {
                this.count = Math.max(1, n | 0);
                if (this.index >= this.count) { this.index = 0; this.since = now; }
                return this;
            },
            remaining(now) { return Math.max(0, this.every - (now - this.since)); },
            running() { return this.enabled && !this.paused && this.count > 1; },
        };
        return r;
    }

    // ── /qc capacity ─────────────────────────────────────────────────────
    /** Columns of at least 420px and rows of at least 210px, so a card is
        big enough to read from across the room; never more than 5 by 4. */
    function qcGrid(width, height) {
        const cols = Math.max(1, Math.min(5, Math.floor((width + 16) / 436)));
        const rows = Math.max(1, Math.min(4, Math.floor((height + 16) / 226)));
        return { cols, rows, per: cols * rows };
    }
    function pages(n, per) { return Math.max(1, Math.ceil((n || 0) / Math.max(1, per))); }

    // ── the whole floor at once ──────────────────────────────────────────
    /** Every level in one view, if the bays stay readable.
        `levels` are [{uid, w, h}] (each level's plan in cells), in order;
        W x H the room the plan has. Levels go row-major, k to a row, every
        bay one uniform cell; each row of levels has a heading (o.head px)
        and o.panelGap between levels. The k whose cell is best balanced
        (width / 1.3 against height) wins, among those whose cell is at least
        o.minW x o.minH. null when none is: then the wall rotates levels. */
    function floorPack(levels, W, H, o) {
        o = o || {};
        const n = (levels || []).length;
        if (!n || !(W > 0) || !(H > 0)) return null;
        const gap = o.gap == null ? 12 : o.gap, pg = o.panelGap == null ? 20 : o.panelGap;
        const head = o.head || 0, minW = o.minW || 0, minH = o.minH || 0, maxH = o.maxH || 300;
        let best = null;
        for (let k = 1; k <= n; k++) {
            const rows = [];
            for (let i = 0; i < n; i += k) rows.push(Array.from({ length: Math.min(k, n - i) }, (_, j) => i + j));
            let cellW = Infinity, cells = 0;
            for (const r of rows) {
                const cols = r.reduce((a, i) => a + Math.max(1, levels[i].w | 0), 0);
                // each level is its own grid: gutters inside it, panelGap between
                cellW = Math.min(cellW, (W - gap * (cols - r.length) - 2 * gap * r.length - pg * (r.length - 1)) / cols);
                cells += Math.max(...r.map(i => Math.max(1, levels[i].h | 0)));
            }
            const free = H - head * rows.length - gap * (cells - rows.length) - 2 * gap * rows.length - pg * (rows.length - 1);
            cellW = Math.floor(cellW);
            const cellH = Math.min(Math.floor(free / cells), Math.floor(cellW * 1.05), maxH);
            if (cellW < minW || cellH < minH) continue;
            const score = Math.min(cellW / 1.3, cellH);
            if (!best || score > best.score) best = { perRow: k, rows, cellW, cellH, score };
        }
        return best;
    }

    const api = { STALE_MS, floorPack, clock, when, toMs, liveState, parseKiosk, wallSequence, rotator, qcGrid, pages };
    root.LEMWallLogic = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
