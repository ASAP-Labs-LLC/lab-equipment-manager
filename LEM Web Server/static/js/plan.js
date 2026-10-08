/* plan.js: the floor plan, drawn flat (ia-final §3.2, §3.8, §10).

   One renderer for every place the floor is drawn: the Instruments page's
   map view (/?view=map) and the wall (/floor, piece 13). It replaces the
   isometric SVG and the 3D site that the old floor page carried; both drew a fixed
   deck with the instruments somewhere on it, so most of the screen was
   empty floor (judge J1: "a half-width grid of empty dashed cells, and dead
   space below").

   The rules it keeps, each tested in tests/js/plan.mjs:

     sized to the placed bays   the grid is the bounding box of the bays
                                somebody saved; no empty edge row or column
     one bay = round(v / 2.05)  the pitch production saves at; a move writes
                                the same kind of number back (coord)
     nobody vanishes            two saved on one bay: the second (by title,
                                then uid) goes to the NEAREST free bay
     nothing moves by accident  canDrag is false unless Arrange was entered
                                by somebody signed in (P15)
     text never overflows       every line is one line, ellipsised, and the
                                bay's title carries the whole story

   Pure functions first (window.LEMPlan; module.exports for node). The DOM
   half (draw) only runs in a browser and sets every string with
   textContent. */
(function (root) {
    'use strict';

    const PITCH = 2.05;
    const SAFE_KEY = /^[A-Za-z0-9_-]{1,64}$/;

    function bayIndex(v) {
        if (typeof v !== 'number' || !Number.isFinite(v)) return null;
        return Math.round(v / PITCH);
    }
    /** The saved number for bay index i: 2 -> 4.1, -1 -> -2.05. */
    function coord(i) {
        const v = Math.round(i * PITCH * 100) / 100;
        return v === 0 ? 0 : v;                      // never -0
    }

    // ── the address bar is the view ───────────────────────────────────────
    function parseMapView(search) {
        let p;
        try { p = new URLSearchParams(search || ''); } catch (_e) { p = new URLSearchParams(''); }
        const safe = (k) => { const v = p.get(k) || ''; return SAFE_KEY.test(v) ? v : ''; };
        return { level: safe('level'), cause: safe('cause'), focus: safe('focus'),
                 arrange: p.get('arrange') === '1' };
    }
    function mapQuery(v) {
        const p = new URLSearchParams();
        p.set('view', 'map');
        v = v || {};
        if (v.level) p.set('level', v.level);
        if (v.cause) p.set('cause', v.cause);
        if (v.focus) p.set('focus', v.focus);
        if (v.arrange) p.set('arrange', '1');
        return '?' + p.toString();
    }

    // ── which level ───────────────────────────────────────────────────────
    /** The level the plan draws: '' when the lab has one populated level
        (then the plan is everyone, and no level is named). Otherwise the
        level asked for, else the focused instrument's, else the lab's
        default, else the first. A level nobody stands on is not obeyed. */
    function currentLevel(data, view) {
        const levels = (data && data.levels) || [];
        if (levels.length < 2) return '';
        const known = new Set(levels.map(l => l.uid));
        view = view || {};
        if (view.level && known.has(view.level)) return view.level;
        const rows = (data && data.instruments) || [];
        if (view.focus) {
            const r = rows.find(x => x.uid === view.focus);
            if (r && known.has(r.level_uid)) return r.level_uid;
        }
        if (view.cause) {
            // a Needs-you tile opens on where its first (worst) instrument is
            const r = rows.find(x => ((x && x.problems) || []).some(p => p && p.key === view.cause));
            if (r && known.has(r.level_uid)) return r.level_uid;
        }
        if (data && known.has(data.default_level)) return data.default_level;
        return levels[0].uid;
    }
    /** The instruments standing on `level`. One with no level, or a level
        the lab no longer has, stands on the default (where the old floor
        drew it), so nobody falls off every plan. */
    function onLevel(rows, level, data) {
        if (!level) return (rows || []).slice();
        const known = new Set(((data && data.levels) || []).map(l => l.uid));
        const fallback = data && known.has(data.default_level) ? data.default_level
            : (((data && data.levels) || [])[0] || {}).uid;
        return (rows || []).filter(r => (known.has(r.level_uid) ? r.level_uid : fallback) === level);
    }

    // ── the layout ────────────────────────────────────────────────────────
    function _pos(r) {
        const p = r && r.where && r.where.pos;
        if (!Array.isArray(p) || p.length !== 2) return null;
        const x = bayIndex(p[0]), y = bayIndex(p[1]);
        return x === null || y === null ? null : [x, y];
    }
    const _order = (a, b) => {
        const ta = String(a.title || '').toLowerCase(), tb = String(b.title || '').toLowerCase();
        return ta < tb ? -1 : ta > tb ? 1 : String(a.uid) < String(b.uid) ? -1 : String(a.uid) > String(b.uid) ? 1 : 0;
    };

    /** {w, h, origin:[bx, by], bays:[{uid, row, x, y, spilled}], unplaced:[row]}
        x/y are grid cells from 0; origin is the bay index of cell (0, 0).
        opts.ring adds that many empty bays on every side (Arrange's room to
        grow). opts.moved {uid: [x, y] saved} overrides a row's saved bay
        while its save is in flight. */
    function layout(rows, opts) {
        opts = opts || {};
        const moved = opts.moved || {};
        const seq = (rows || []).slice().sort(_order);
        const taken = new Map();
        const claimed = [], spill = [], unplaced = [];
        for (const r of seq) {
            const o = moved[r.uid];
            const p = o ? [bayIndex(o[0]), bayIndex(o[1])] : _pos(r);
            if (!p || p[0] === null || p[1] === null) { unplaced.push(r); continue; }
            const k = p[0] + ',' + p[1];
            if (taken.has(k)) { spill.push([r, p]); continue; }
            taken.set(k, r.uid);
            claimed.push({ uid: r.uid, row: r, bx: p[0], by: p[1], spilled: false });
        }
        // the box the saved bays make; a spilled bay prefers to stay inside it
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const b of claimed) { x0 = Math.min(x0, b.bx); y0 = Math.min(y0, b.by); x1 = Math.max(x1, b.bx); y1 = Math.max(y1, b.by); }
        for (const [r, p] of spill) {
            let best = null;
            for (let d = 1; d < 64 && !best; d++) {
                const cands = [];
                for (let dy = -d; dy <= d; dy++) {
                    for (let dx = -d; dx <= d; dx++) {
                        if (Math.max(Math.abs(dx), Math.abs(dy)) !== d) continue;
                        const x = p[0] + dx, y = p[1] + dy;
                        if (taken.has(x + ',' + y)) continue;
                        const outside = (x < x0 || x > x1 || y < y0 || y > y1) ? 1 : 0;
                        cands.push([outside, dx * dx + dy * dy, y, x]);
                    }
                }
                cands.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || a[3] - b[3]);
                if (cands.length) best = cands[0];
            }
            const x = best[3], y = best[2];
            taken.set(x + ',' + y, r.uid);
            claimed.push({ uid: r.uid, row: r, bx: x, by: y, spilled: true });
            x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
        }
        if (!claimed.length) return { w: 0, h: 0, origin: [0, 0], bays: [], unplaced };
        const ring = Math.max(0, opts.ring | 0);
        const ox = x0 - ring, oy = y0 - ring;
        return {
            w: x1 - x0 + 1 + 2 * ring, h: y1 - y0 + 1 + 2 * ring, origin: [ox, oy],
            bays: claimed.map(b => ({ uid: b.uid, row: b.row, x: b.bx - ox, y: b.by - oy, spilled: b.spilled })),
            unplaced,
        };
    }
    function toSaved(x, y, origin) { return { x: coord(x + origin[0]), y: coord(y + origin[1]) }; }
    function occupant(lay, x, y) {
        const b = (lay && lay.bays || []).find(b => b.x === x && b.y === y);
        return b ? b.uid : null;
    }
    function isFree(lay, x, y) {
        return !!lay && x >= 0 && y >= 0 && x < lay.w && y < lay.h && occupant(lay, x, y) === null;
    }

    /** P15: a drag is a deliberate act, so only in Arrange, only signed in. */
    function canDrag(state) { return !!(state && state.arranging && state.user); }

    // ── sizes ─────────────────────────────────────────────────────────────
    // 96: a two-line name, the word and the detail in 10px of padding.
    // 240, and never taller than wide: past that a bay is a poster.
    const CELL_MIN = 96, CELL_MAX = 240, CELL_DEFAULT = 112, CELL_NARROW = 128;
    /** One uniform cell height that fills the height the plan has, within
        a readable bay and short of a poster (`width`, when known, is the
        cell's width: a bay is never taller than it is wide). */
    function cellHeight(avail, rows, gap, width, min) {
        if (!Number.isFinite(avail) || !(rows > 0)) return CELL_DEFAULT;
        const floor = Number.isFinite(min) && min > 0 ? min : CELL_MIN;
        const h = Math.floor((avail - (gap || 0) * (rows - 1)) / rows);
        const cap = Number.isFinite(width) && width > 0 ? Math.min(CELL_MAX, Math.floor(width)) : CELL_MAX;
        return Math.max(floor, Math.min(cap, h));
    }
    /** How tightly to pack: a floor whose bays would be under 128px wide
        at the 12px gutter packs at 8px (and the bay's own padding with it)
        rather than cutting every word. Arrange is always compact and its
        bays may be shorter: they show a name and a glyph, which is all a
        move needs. */
    function density(planWidth, cols, arranging, narrow) {
        const at12 = cols > 0 && planWidth > 0 ? (planWidth - 24 - 12 * (cols - 1)) / cols : Infinity;
        const compact = !!arranging || at12 < 128;
        // `narrow` (a phone): the bay keeps room for a two-line name, verdict
        // and detail, taller than wide if it must, so nothing is cut
        return { compact, gap: compact ? 8 : 12, minH: arranging ? 72 : narrow ? CELL_NARROW : CELL_MIN };
    }

    // ── a bay's words ─────────────────────────────────────────────────────
    function bayWords(r) {
        const rd = (r && r.readiness) || {};
        const full = String(rd.detail || '');
        const detail = full.split(' · ')[0];
        const name = String((r && r.title) || (r && r.uid) || '');
        const word = String(rd.word || '');
        const bench = (r && r.bench) || {};
        return {
            name, word, detail, glyph: rd.glyph || 'never', state: rd.state || '',
            title: name + ' · ' + word + (full ? ': ' + full : ''),
            stop: rd.state === 'not_ok',
            stopped: bench.state === 'stopped' || bench.state === 'never',
        };
    }
    const SHORT = { not_ok: 'Not OK', ok_but: 'OK, but…', ok: 'OK', off_line: 'Off line',
                    cant_tell: 'Can’t tell', no_qc: 'No QC' };
    /** The verdict in fewer letters, for a bay too narrow for the whole
        word. The glyph's shape still tells the states apart. */
    function shortWord(state) { return SHORT[state] || ''; }
    /** The first n characters, cut back to a word when one ends near, with
        an ellipsis; never ending on a separator. */
    function cutAt(text, n) {
        const s = String(text || '');
        if (s.length <= n) return s;
        let cut = s.slice(0, Math.max(0, n));
        const sp = cut.lastIndexOf(' ');
        if (sp > n * 0.6) cut = cut.slice(0, sp);
        return cut.replace(/[\s,·:;—-]+$/, '') + '…';
    }

    /** A last word that is a number or one or two letters ("1", "S",
        "NS") is glued to the word before it with a no-break space, so a
        name that needs two lines breaks as "PAC / Flash 1", never
        "PAC Flash / 1". A real word ("NIR") is left free to wrap. */
    function glue(name) {
        const s = String(name || '');
        const i = s.lastIndexOf(' ');
        const last = s.slice(i + 1);
        if (i <= 0 || !(/^\d{1,3}$/.test(last) || /^[A-Za-z]{1,2}$/.test(last))) return s;
        return s.slice(0, i) + '\u00a0' + last;
    }

    /** With a cause chosen in the Needs-you column, the instruments without
        it step back; the ones with it stay at full strength. */
    function dimmed(r, cause) {
        if (!cause) return false;
        return !((r && r.problems) || []).some(p => p && p.key === cause);
    }

    // ── levels as pages (2026-10-07) ──────────────────────────────────────
    // The wall's rank and swipe (wall_logic.js), held equal to it by
    // tests/js/plan.mjs: the map and the wall must never disagree about which
    // state is worse. "Can't tell" (never checked in) outranks "Off line",
    // which somebody chose; anything unrecognised is "Can't tell", never OK.
    const STATE_RANK = { ok: 0, off_line: 1, cant_tell: 2, ok_but: 3, not_ok: 4 };
    const STATE_GLYPH = { not_ok: 'error', ok_but: 'half', cant_tell: 'dashed', off_line: 'off', ok: 'final' };
    function worstState(states) {
        let worst = null;
        for (const raw of states || []) {
            const st = Object.prototype.hasOwnProperty.call(STATE_RANK, raw) ? raw : 'cant_tell';
            if (worst === null || STATE_RANK[st] > STATE_RANK[worst]) worst = st;
        }
        return worst;
    }
    function stateGlyph(state) { return STATE_GLYPH[state] || ''; }
    /** A finished drag as a page turn: -1, 0 or +1 (finger leftwards = next). */
    function swipeStep(dx, dy) {
        if (Math.abs(dx) < 60 || Math.abs(dx) < 1.5 * Math.abs(dy)) return 0;
        return dx < 0 ? 1 : -1;
    }
    /** Each level's tab: its worst state, and how many on it need you. */
    function levelMarks(data) {
        const rows = (data && data.instruments) || [];
        return ((data && data.levels) || []).map(l => {
            const here = onLevel(rows, l.uid, data);
            const state = worstState(here.map(r => r && r.readiness && r.readiness.state));
            return { uid: l.uid, name: l.name, state, glyph: stateGlyph(state),
                     need: here.filter(r => r && r.needs_you).length, total: here.length };
        });
    }
    /** The level `step` away from `current` (the default when none), wrapping. */
    function stepLevel(data, current, step) {
        const lv = (data && data.levels) || [];
        if (!lv.length) return current || '';
        let i = lv.findIndex(l => l.uid === (current || currentLevel(data, {})));
        if (i < 0) i = 0;
        return lv[((i + step) % lv.length + lv.length) % lv.length].uid;
    }
    /** A detail shortened by meaning: the date tail, then all after " · ". */
    function shortDetail(text) {
        let s = String(text || '').split(' · ')[0];
        s = s.replace(/\s+since\s+.*$/, '');
        return s.trim();
    }

    const api = { PITCH, bayIndex, coord, parseMapView, mapQuery, currentLevel, onLevel, layout,
                  toSaved, occupant, isFree, canDrag, cellHeight, density, bayWords, dimmed, shortWord, cutAt, glue,
                  worstState, stateGlyph, swipeStep, levelMarks, stepLevel, shortDetail,
                  CELL_MIN, CELL_MAX, draw: null };

    // ── the DOM half ──────────────────────────────────────────────────────
    if (typeof document !== 'undefined') {
        const el = (tag, attrs, ...kids) => {
            const n = document.createElement(tag);
            for (const [k, v] of Object.entries(attrs || {})) {
                if (v === null || v === undefined || v === false) continue;
                if (k === 'className') n.className = v;
                else if (k === 'text') n.textContent = v;
                else if (k === 'style') n.setAttribute('style', v);
                else n.setAttribute(k, v === true ? '' : String(v));
            }
            for (const kid of kids) if (kid !== null && kid !== undefined) n.append(kid);
            return n;
        };
        /** Draw `lay` into `host` (an element that becomes the grid).
            opts: arranging, focus (uid), cause (key), picked (uid), saving
            (Set of uids), cellH (px), details and detailsShort ({uid:
            line}: the wall's own detail line and the shorter one it falls
            back to before cutting, ui_wall.bay_details). In view mode a bay is a link to its
            record; in Arrange it is a button and the free cells are drop
            targets. Nothing in view mode can be dragged: links carry
            draggable="false" so even the browser's own link drag is off. */
        api.draw = function draw(host, lay, opts) {
            opts = opts || {};
            host.style.setProperty('--plan-cols', String(Math.max(1, lay.w)));
            host.style.setProperty('--plan-rows', String(Math.max(1, lay.h)));
            if (opts.cellH) host.style.setProperty('--cell-h', opts.cellH + 'px');
            host.style.setProperty('--plan-gap', (opts.gap || 12) + 'px');
            host.classList.toggle('compact', !!opts.compact);
            host.classList.toggle('arranging', !!opts.arranging);
            const kids = [];
            if (opts.arranging) {
                for (let y = 0; y < lay.h; y++) {
                    for (let x = 0; x < lay.w; x++) {
                        if (occupant(lay, x, y) !== null) continue;
                        kids.push(el('button', { type: 'button', className: 'cell', 'data-x': x, 'data-y': y,
                            style: 'grid-column:' + (x + 1) + ';grid-row:' + (y + 1),
                            'aria-label': 'Empty bay, row ' + (y + 1) + ', column ' + (x + 1),
                            'data-testid': 'plan-cell' }));
                    }
                }
            }
            for (const b of lay.bays) {
                const w = bayWords(b.row);
                const cls = ['bay', 's-' + w.state];
                if (w.stop) cls.push('stop');
                if (w.stopped) cls.push('stopped');
                if (opts.focus === b.uid) cls.push('focus');
                if (opts.picked === b.uid) cls.push('picked');
                if (dimmed(b.row, opts.cause)) cls.push('dim');
                if (opts.saving && opts.saving.has(b.uid)) cls.push('saving');
                const attrs = { className: cls.join(' '), 'data-uid': b.uid, 'data-x': b.x, 'data-y': b.y,
                                'data-testid': 'plan-bay', title: w.title,
                                style: 'grid-column:' + (b.x + 1) + ';grid-row:' + (b.y + 1),
                                'aria-current': opts.focus === b.uid ? 'true' : null };
                const glyph = el('span', { className: 'glyph ' + w.glyph, 'aria-hidden': 'true' });
                // On the wall (fullWords) the glyph is the bay's corner mark,
                // floated at the end of the name's first line: the word line
                // is the whole word and nothing else, so "OK to run, but…"
                // fits a 7-wide floor without a short form (round 4), and
                // the state reads from across the room by its shape alone.
                const lines = opts.fullWords ? [
                    el('span', { className: 'b-name' }, glyph, el('span', { className: 'b-ntext', text: glue(w.name) })),
                    el('span', { className: 'b-word' }, el('span', { className: 'b-wtext', text: w.word })),
                ] : [
                    el('span', { className: 'b-name', text: glue(w.name) }),
                    el('span', { className: 'b-word' }, glyph, el('span', { className: 'b-wtext', text: w.word })),
                ];
                lines.push(
                    el('span', { className: 'b-detail', 'data-short': (opts.detailsShort && opts.detailsShort[b.uid]) || null,
                        text: opts.saving && opts.saving.has(b.uid) ? 'Saving…'
                        : ((opts.details && opts.details[b.uid]) || w.detail || (b.row.bench && b.row.bench.word) || '') }));
                // one dot per check, its verdict's shape (2026-10-07): GC's five
                // read at a glance, and a hover opens each result (checkcard.js)
                const cks = (b.row && b.row.checks) || [];
                if (cks.length) {
                    lines.push(el('span', { className: 'b-dots', 'aria-hidden': 'true' },
                        ...cks.map(c => el('i', { className: 'k-' + (c.key || 'none') }))));
                }
                let node;
                if (opts.arranging) {
                    node = el('button', Object.assign(attrs, { type: 'button', 'aria-pressed': opts.picked === b.uid ? 'true' : 'false',
                        'aria-label': w.name + ', pick it up to move it' }), ...lines);
                } else {
                    node = el('a', Object.assign(attrs, { href: b.row.href || '#', draggable: 'false' }), ...lines);
                }
                kids.push(node);
            }
            host.replaceChildren(...kids);
            host.classList.toggle('fullwords', !!opts.fullWords);
            // the map (2026-10-07): a verdict is the whole word and a detail
            // wraps to two lines; neither is ever cut to "…"
            host.classList.toggle('wraplines', !!opts.wrapLines && !opts.fullWords);
            // the wall never shortens a word (one vocabulary, §4.1): a floor
            // too narrow for them is drawn 'tight' (less padding), and as a
            // last resort 'wrap' lets a word that still does not fit take a
            // second line, whole
            host.classList.toggle('tightwords', !!opts.fullWords && (opts.words === 'tight' || opts.words === 'wrap'));
            host.classList.toggle('wrapwords', !!opts.fullWords && opts.words === 'wrap');
            host.classList.toggle('roomy', (opts.cellH || 0) >= 140 && (opts.cellH || 0) < 200);
            host.classList.toggle('grand', (opts.cellH || 0) >= 200);
            fit(host);
        };

        /* Every line fits its bay, measured rather than hoped for. CSS's
           text-overflow is the fallback; this cuts the text itself (at a
           word where one is near) so nothing is wider than its box: the
           name may take two lines, the verdict falls back to its short word
           before it is ever cut, and the detail is cut last. The bay's
           title keeps the whole story. */
        function over(el, tall) {
            return el.scrollWidth > el.clientWidth + 0.5 || (tall && el.scrollHeight > el.clientHeight + 1);
        }
        // `box` is the element whose overflow counts (the word's own line
        // on the wall, where its glyph sits inline in the text)
        function fitText(el, full, short, tall, box) {
            box = box || el;
            el.textContent = full;
            if (!over(box, tall)) return;
            if (short) { el.textContent = short; if (!over(box, tall)) return; }
            const s = short || full;
            let lo = 0, hi = s.length - 1;
            while (lo < hi) {
                const mid = (lo + hi + 1) >> 1;
                el.textContent = cutAt(s, mid);
                if (over(box, tall)) hi = mid - 1; else lo = mid;
            }
            el.textContent = cutAt(s, lo);
            if (over(box, tall)) el.textContent = '…';
        }
        function fit(host) {
            // fullwords (the wall): the verdict is the app's whole word, on
            // one line, never a short form and never cut (round 4: "OK,
            // but…" in the bays beside "OK to run, but…" in the counts was
            // two vocabularies on one screen). When it does not fit, the bay
            // is marked (data-word-cut) and the wall redraws the floor
            // tighter (wall_floor.js); only in 'wrapwords', the last resort,
            // may it take two lines. When a bay is too short for all three
            // lines, the detail steps aside last of all (the title keeps it).
            const full = host.classList.contains('fullwords');
            const wrap = host.classList.contains('wrapwords');
            if (host.classList.contains('wraplines')) { fitLines(host); return; }
            for (const bay of host.querySelectorAll('.bay')) {
                const name = bay.querySelector('.b-name');
                const word = bay.querySelector('.b-wtext');
                const det = bay.querySelector('.b-detail');
                const state = (bay.className.match(/\bs-([a-z_]+)/) || [])[1] || '';
                const ntext = name && name.querySelector('.b-ntext');
                if (ntext) fitText(ntext, ntext.textContent, '', true, name);
                else if (name) fitText(name, name.textContent, '', true);
                if (word && full) {
                    bay.removeAttribute('data-word-cut');
                    if (!wrap && over(word, false)) bay.setAttribute('data-word-cut', '1');
                } else if (word) fitText(word, word.textContent, shortWord(state), false, word);
                if (det) det.hidden = false;
                if (det && det.textContent) fitText(det, det.textContent, det.getAttribute('data-short') || '', false);
                if (full && det && bay.scrollHeight > bay.clientHeight + 1) det.hidden = true;
            }
        }
        /* The map's bays (wraplines): the name may take two lines as
           before; the verdict is the app's whole word, on up to two lines,
           its short form only if even that does not fit; the detail is
           whole on up to two lines, else shortened by meaning (shortDetail),
           else it steps aside (the bay's title keeps the whole story).
           Nothing ends in a cut "…". */
        function lines(el) {
            const lh = parseFloat(getComputedStyle(el).lineHeight) || 16;
            return Math.round(el.scrollHeight / lh);
        }
        function fitLines(host) {
            for (const bay of host.querySelectorAll('.bay')) {
                const name = bay.querySelector('.b-name');
                const word = bay.querySelector('.b-wtext');
                const det = bay.querySelector('.b-detail');
                const dots = bay.querySelector('.b-dots');
                const state = (bay.className.match(/\bs-([a-z_]+)/) || [])[1] || '';
                // the check dots are the first thing a crowded bay gives up:
                // the words are fitted without them, and the dots come back
                // only if there is still room (the hover has every check)
                if (dots) dots.hidden = true;
                if (name) fitText(name, name.textContent, '', true);
                if (word && (lines(word) > 2 || over(word, false))) {
                    const s = shortWord(state);
                    if (s) word.textContent = s;
                }
                if (!det) continue;
                det.hidden = false;
                const full = det.textContent;
                if (!full) continue;
                const tooBig = () => lines(det) > 2 || over(det, false) || bay.scrollHeight > bay.clientHeight + 1;
                if (tooBig()) det.textContent = shortDetail(full);
                if (tooBig()) { det.textContent = full; det.hidden = true; }
            }
            for (const dots of host.querySelectorAll('.bay .b-dots')) {
                // a crowded bay does not overflow, it squeezes: the name (its
                // own overflow hidden) gives up height first. So the dots stay
                // only if no line got shorter and nothing spilled.
                const bay = dots.closest('.bay');
                const kids = [...bay.querySelectorAll('.b-name, .b-word, .b-detail')].filter(e => !e.hidden);
                const before = kids.map(e => e.clientHeight);
                dots.hidden = false;
                const squeezed = kids.some((e, i) => e.clientHeight < before[i] || e.scrollHeight > e.clientHeight + 1);
                if (squeezed || bay.scrollHeight > bay.clientHeight + 1) dots.hidden = true;
            }
        }
        api.fit = fit;
    }

    root.LEMPlan = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
