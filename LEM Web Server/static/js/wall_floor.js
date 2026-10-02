/* wall_floor.js: the floor wall's view (ia-final §3.8), on /floor and /wall.

   The first paint is the server's (templates/_wall_floor_view.html, from
   ui_wall.floor). This keeps it live and draws what only a browser can
   measure: the plan, one uniform cell sized to the room the wall has, by
   plan.js (the same renderer as the Instruments map view).

   What it does, in order of how much a wrong answer would cost:
     * the stale rule: no answer from /api/ui/live for 90 s and the headline
       becomes "Not live · last update 13:15", the plan and the column dim,
       the footer says "Stale" (wall_logic.liveState). Checked every second.
     * refreshes from /api/ui/wall/floor (memory only, 0 LabCore ops) when
       the live feed says something changed, and once a minute regardless.
     * levels: one at a time, every 20 s, pausing while a pointer or a
       finger is on the wall; ?level=<uid> pins one, ?rotate=0 and reduced
       motion stop it (then ‹ › step by hand).
   Every string goes in through textContent. GETs only. */
(function () {
    'use strict';
    const L = window.LEMWallLogic;
    const P = window.LEMPlan;
    const LEVEL_MS = 20000;
    const REFRESH_MS = 60000;
    const GLYPH_FOR_TONE = { stop: 'error', ok: 'final', warn: 'half' };

    function h(tag, attrs, ...kids) {
        const n = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v === null || v === undefined || v === false) continue;
            if (k === 'className') n.className = v;
            else if (k === 'text') n.textContent = String(v);
            else n.setAttribute(k, v === true ? '' : String(v));
        }
        for (const c of kids) if (c !== null && c !== undefined) n.append(c);
        return n;
    }

    function mount(section, kiosk) {
        const $ = (id) => section.querySelector('#' + id);
        let data = null;
        try { data = JSON.parse($('wall-data').textContent); } catch (_e) { data = null; }
        const loadedMs = Date.now();
        const reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        let tz = (data && data.lab_tz) || null;
        let shown = true;
        let lastKind = '';
        let lastLevel = null;
        let lastRefresh = Date.now();
        let inFlight = false;

        // ── levels ───────────────────────────────────────────────────────
        function levels() { return (data && data.levels) || []; }
        const pinned = () => !!kiosk.level && levels().some(l => l.uid === kiosk.level);
        const rot = L.rotator({ count: Math.max(1, levels().length), every: LEVEL_MS, now: Date.now(),
                                enabled: kiosk.rotate && !reduced && !pinned() });
        function levelNow() {
            const lv = levels();
            if (lv.length < 2) return P.currentLevel(data || {}, {});
            if (pinned()) return kiosk.level;
            return lv[Math.min(rot.index, lv.length - 1)].uid;
        }
        function levelName(uid) {
            const lv = levels().find(l => l.uid === uid);
            if (lv) return lv.name;
            const r = ((data && data.instruments) || [])[0];
            const w = r && r.where && r.where.level;
            return w && w !== 'No level' ? w : 'The floor';
        }

        // ── the head ─────────────────────────────────────────────────────
        function liveNow(now) {
            const st = window.LEMLive ? window.LEMLive.status() : { last_ok_at: 0 };
            return L.liveState({ loadedMs, lastOkMs: st.last_ok_at || 0, nowMs: now, tz,
                                 snapshotStale: !!(data && data.stale), builtAt: data && data.built_at,
                                 serverNow: data && data.server_now });
        }
        function drawHead(live) {
            const w = (data && data.wall) || {};
            const lost = live.kind !== 'live';
            $('wf-headline-text').textContent = lost ? live.headline : (w.headline || '');
            $('wf-sub').textContent = live.kind === 'lost'
                ? 'This wall has not heard from LEM since then. What it shows is as it was, and may be wrong now.'
                : live.kind === 'record'
                    ? 'LabCore is not answering, so the record has not refreshed. What this shows is as it was then.'
                    : (w.sub || '');
            $('wf-tone').className = 'wh-glyph glyph ' + (lost ? 'dashed' : (GLYPH_FOR_TONE[w.tone] || 'dashed'));
            section.classList.toggle('is-stale', live.dim);
            $('wf-dot').setAttribute('data-state', live.kind === 'live' ? 'fresh' : 'stale');
            $('wf-live').textContent = live.footer;
        }
        function drawCounts() {
            const w = (data && data.wall) || {};
            const ul = $('wf-counts');
            const nc = (w.counts || []).length;
            ul.hidden = !nc;
            // one row up to three; four as two by two; five or six as three a row
            ul.className = 'wall-counts cols-' + (nc <= 3 ? nc : nc === 4 ? 2 : 3);
            ul.replaceChildren(...(w.counts || []).map(c => h('li', { className: 'wc s-' + c.state },
                h('span', { className: 'glyph ' + c.glyph, 'aria-hidden': 'true' }),
                h('b', { className: 'wc-n', text: c.n }),
                h('span', { className: 'wc-w', text: c.word }))));
            const b = w.benches;
            $('wf-fleet').textContent = b ? b.checking_in + ' of ' + b.total + ' benches checking in' : '';
        }
        function drawAttention() {
            const w = (data && data.wall) || {};
            const items = w.attention || [];
            $('wf-attn').replaceChildren(...items.map(a => h('li', {
                className: 'wa-item s-' + a.state + (a.state === 'not_ok' ? ' stop' : '') + (a.stopped ? ' stopped' : '') },
                h('a', { href: a.href, draggable: 'false' },
                    h('span', { className: 'wa-names', text: a.label || (a.names || []).join(', ') }),
                    h('span', { className: 'wa-word' }, h('span', { className: 'glyph ' + a.glyph, 'aria-hidden': 'true' }),
                      h('span', { className: 'wa-wtext', text: a.word })),
                    h('span', { className: 'wa-detail', text: a.detail || '' })))));
            const more = $('wf-attn-more');
            more.hidden = !w.attention_more;
            more.textContent = w.attention_more ? 'and ' + w.attention_more + ' more on Instruments' : '';
            $('wf-attn-none').hidden = !(Array.isArray(w.attention) && !w.attention.length);
        }

        // ── the plan ─────────────────────────────────────────────────────
        function drawPlan() {
            const plan = $('wf-plan');
            const empty = $('wf-empty');
            const unplaced = $('wf-unplaced');
            const ready = data && data.state === 'ready' && Array.isArray(data.instruments);
            const level = ready ? levelNow() : '';
            lastLevel = level;
            $('wf-level').textContent = ready ? levelName(level) : 'The floor';
            drawDots(level);
            if (!ready || !data.instruments.length) {
                plan.hidden = true;
                plan.replaceChildren();
                unplaced.hidden = true;
                empty.hidden = false;
                empty.textContent = ((data && data.wall) || {}).sub || '';
                return;
            }
            const here = P.onLevel(data.instruments, level, data);
            const lay = P.layout(here, {});
            if (!lay.w) {
                plan.hidden = true;
                plan.replaceChildren();
                empty.hidden = false;
                empty.textContent = 'Nobody has placed an instrument on ' + levelName(level) + ' yet.';
            } else {
                empty.hidden = true;
                plan.hidden = false;
                const wrap = $('wf-plan-wrap');
                const W = wrap.clientWidth, H = wrap.clientHeight;
                const narrow = (W - 24 - 12 * (lay.w - 1)) / lay.w < 120;
                const gap = narrow ? 8 : 12;
                const cellW = (W - 2 * gap - gap * (lay.w - 1)) / lay.w;
                const fill = Math.floor((H - 2 * gap - gap * (lay.h - 1)) / lay.h);
                const cellH = Math.max(40, Math.min(fill, Math.floor(cellW * 1.05), 300));
                P.draw(plan, lay, { cellH, gap, details: data.details || {}, detailsShort: data.details_short || {} });
            }
            if (lay.unplaced.length) {
                unplaced.hidden = false;
                unplaced.replaceChildren(h('span', { className: 'wu-head', text: 'Not on the plan:' }),
                    ...lay.unplaced.map((r, i) => h('a', { href: r.href, className: 'wu-item', draggable: 'false' },
                        (i ? ', ' : ' ') + r.title + ' · ' + ((r.readiness && r.readiness.word) || ''))));
            } else {
                unplaced.hidden = true;
                unplaced.replaceChildren();
            }
        }
        function drawDots(level) {
            const lv = levels();
            const dots = $('wf-dots');
            dots.replaceChildren(...(lv.length > 1 ? lv.map(l => h('i', { className: l.uid === level ? 'on' : '' })) : []));
            $('wf-step').hidden = !(lv.length > 1 && !pinned() && !rot.running());
        }
        function rotText(now) {
            const lv = levels();
            if (lv.length < 2) return '';
            const i = Math.max(0, lv.findIndex(l => l.uid === lastLevel)) + 1;
            const head = 'Level ' + i + ' of ' + lv.length;
            if (pinned()) return head + ' · pinned';
            if (!kiosk.rotate) return head + ' · not rotating';
            if (reduced) return head + ' · rotation off (reduced motion)';
            if (rot.paused) return head + ' · held while you point';
            return head + ' · next in ' + Math.ceil(rot.remaining(now) / 1000) + ' s';
        }

        // ── refresh ──────────────────────────────────────────────────────
        function refresh() {
            if (inFlight || !window.LEMLive) return;
            inFlight = true;
            lastRefresh = Date.now();
            window.LEMLive.bgFetch('/api/ui/wall/floor', { cache: 'no-store', headers: { Accept: 'application/json' } })
                .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(d => {
                    data = d;
                    if (d.lab_tz) tz = d.lab_tz;
                    rot.setCount(Math.max(1, levels().length), Date.now());
                    render();
                })
                .catch(() => { /* the stale rule says it if LEM is gone; the last answer stays */ })
                .then(() => { inFlight = false; });
        }

        function render() {
            if (!shown) return;
            const now = Date.now();
            const live = liveNow(now);
            lastKind = live.kind;
            drawHead(live);
            drawCounts();
            drawAttention();
            drawPlan();
            $('wf-rot').textContent = rotText(now);
        }

        function tick(now) {
            if (!shown) return;
            const before = rot.index;
            rot.tick(now);
            const live = liveNow(now);
            if (live.kind !== lastKind) { lastKind = live.kind; drawHead(live); }
            else $('wf-live').textContent = live.footer;
            if (rot.index !== before) drawPlan();
            $('wf-rot').textContent = rotText(now);
            if (now - lastRefresh > REFRESH_MS) refresh();
        }

        // somebody reading the wall holds it still
        section.addEventListener('pointerenter', () => rot.pause(Date.now()));
        section.addEventListener('pointerdown', () => rot.pause(Date.now()));
        section.addEventListener('pointerleave', () => rot.resume(Date.now()));
        section.addEventListener('touchend', () => setTimeout(() => rot.resume(Date.now()), LEVEL_MS));
        $('wf-prev').addEventListener('click', () => { rot.go(-1, Date.now()); drawPlan(); });
        $('wf-next').addEventListener('click', () => { rot.go(1, Date.now()); drawPlan(); });
        let resizeT = null;
        window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(drawPlan, 120); });

        if (window.LEMLive) {
            window.LEMLive.subscribe((u) => {
                if (u.lab_tz) tz = u.lab_tz;
                if (u.reset || (u.machines && u.machines.length) || (u.kinds && u.kinds.length)) refresh();
            });
        }
        if (pinned()) {
            const i = levels().findIndex(l => l.uid === kiosk.level);
            if (i >= 0) rot.index = i;
        }
        render();
        return {
            name: 'floor', section, tick,
            show() { shown = true; section.hidden = false; render(); },
            hide() { shown = false; section.hidden = true; },
        };
    }

    window.LEMWallFloor = { mount };
})();
