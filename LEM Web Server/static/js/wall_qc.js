/* wall_qc.js: the QC wall's view (ia-final §3.8), on /qc and /wall.

   One card per (instrument, check), worst first, as many as fit the screen
   at a size read from across the room (wall_logic.qcGrid); the rest rotate
   every 15 s with page dots. It never scrolls. Rotation pauses while a
   pointer or a finger is on the wall; ?rotate=0 and reduced motion stop it
   (‹ › then step by hand).

   A card says its verdict word first (the snapshot's, the same the record
   says), then the instrument, the check, the last value at the band's
   decimals and when, in the lab's time. Its chart is normalised to the
   pass band (A.6: -1 and +1 are the limits whatever the units), so one
   target line runs across the whole wall. A history that could not be read
   says so in the chart; it is never drawn as a flat line. A control chip
   appears only when a control rule broke.

   The stale rule is the floor's (wall_logic.liveState). Data comes from
   /api/ui/wall/qc (snapshot + the local record, 0 LabCore ops), refreshed
   when the live feed says something changed and once a minute. GETs only;
   every string through textContent; the chart is built with DOM calls. */
(function () {
    'use strict';
    const L = window.LEMWallLogic;
    const PAGE_MS = 15000;
    const REFRESH_MS = 60000;
    const SVG = 'http://www.w3.org/2000/svg';
    const CAP = 1.8;               // a z beyond this is drawn pinned at the edge
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
    function s(tag, attrs) {
        const n = document.createElementNS(SVG, tag);
        for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
        return n;
    }

    /** The card's chart, sized to its box (no stretched dots). */
    function chart(box, card) {
        const W = Math.max(40, Math.round(box.clientWidth)), H = Math.max(24, Math.round(box.clientHeight));
        const pts = (card.points || []).filter(p => typeof p.z === 'number' && Number.isFinite(p.z));
        if (!pts.length) {
            box.replaceChildren(h('span', { className: 'qc-nochart', text:
                card.history === 'unread' ? 'History not read: the local record did not answer'
                : card.history === 'filling' ? 'No history here yet: LEM’s log copy has not filled'
                : (card.points || []).length ? 'No pass band recorded for these results'
                : card.last && card.last.at ? 'No chart history in LEM’s log yet'
                : 'No results in the last 180 days' }));
            return;
        }
        const pad = 6;
        const y = z => H - pad - ((Math.max(-CAP, Math.min(CAP, z)) + CAP) / (2 * CAP)) * (H - 2 * pad);
        const n = pts.length;
        const x = i => n < 2 ? W / 2 : pad + i * (W - 2 * pad) / (n - 1);
        const svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img',
                               'aria-label': card.title + ', ' + card.test + ': ' + n + ' results, '
                               + pts.filter(p => p.in_spec === false).length + ' outside the limits' });
        svg.append(s('rect', { x: 0, y: y(1), width: W, height: y(-1) - y(1), class: 'qc-band' }));
        for (const v of [1, -1]) svg.append(s('line', { x1: 0, x2: W, y1: y(v), y2: y(v), class: 'qc-limit' }));
        svg.append(s('line', { x1: 0, x2: W, y1: y(0), y2: y(0), class: 'qc-target' }));
        svg.append(s('polyline', { points: pts.map((p, i) => x(i).toFixed(1) + ',' + y(p.z).toFixed(1)).join(' '),
                                   class: 'qc-line' }));
        pts.forEach((p, i) => {
            const last = i === n - 1;
            const r = last ? 5 : 2.6;
            if (p.in_spec === false) {
                // outside the band: a triangle, not only a colour (§9.1 #2)
                const cx = x(i), cy = y(p.z), k = r + 1.5;
                svg.append(s('polygon', { points: [cx, cy - k, cx + k, cy + k * 0.8, cx - k, cy + k * 0.8].map(v => v.toFixed(1)).join(' '),
                                          class: 'qc-pt out' + (last ? ' last' : '') }));
            } else {
                svg.append(s('circle', { cx: x(i).toFixed(1), cy: y(p.z).toFixed(1), r, class: 'qc-pt' + (last ? ' last' : '') }));
            }
        });
        box.replaceChildren(svg);
    }

    function mount(section, kiosk) {
        const $ = (id) => section.querySelector('#' + id);
        let data = null;
        try { data = JSON.parse($('wall-qc-data').textContent); } catch (_e) { data = null; }
        const loadedMs = Date.now();
        const reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        let tz = (data && data.lab_tz) || null;
        let shown = true;
        let lastKind = '';
        let lastRefresh = Date.now();
        let inFlight = false;
        // the wall's own data must be answering too (see wall_floor.js)
        let dataOkMs = Date.now();
        let dataFailing = false;
        let asked = 0, askedAt = null, ctl = null;
        let per = 9;
        const rot = L.rotator({ count: 1, every: PAGE_MS, now: Date.now(), enabled: kiosk.rotate && !reduced });

        function cards() { return (data && data.cards) || []; }
        function liveNow(now) {
            const st = window.LEMLive ? window.LEMLive.status() : { last_ok_at: 0 };
            const heard = dataFailing ? Math.min(st.last_ok_at || 0, dataOkMs) : (st.last_ok_at || 0);
            return L.liveState({ loadedMs: dataFailing ? Math.min(loadedMs, dataOkMs) : loadedMs, lastOkMs: heard, nowMs: now, tz,
                                 snapshotStale: !!(data && data.stale), builtAt: data && data.built_at,
                                 serverNow: data && data.server_now });
        }
        function drawHead(live) {
            const lost = live.kind !== 'live';
            $('wq-headline-text').textContent = lost ? live.headline : ((data && data.headline) || '');
            $('wq-sub').textContent = live.kind === 'lost'
                ? 'This wall has not heard from LEM since then. What it shows is as it was, and may be wrong now.'
                : live.kind === 'record'
                    ? 'LabCore is not answering, so the verdicts have not refreshed. What this shows is as it was then.'
                    : ((data && data.sub) || '');
            $('wq-tone').className = 'wh-glyph glyph ' + (lost ? 'dashed' : (GLYPH_FOR_TONE[data && data.tone] || 'dashed'));
            section.classList.toggle('is-stale', live.dim);
            $('wq-dot').setAttribute('data-state', live.kind === 'live' ? 'fresh' : 'stale');
            $('wq-live').textContent = live.footer;
        }

        function cardEl(c) {
            const v = c.verdict || {};
            const last = c.last || {};
            const value = last.value ? last.value + (last.units ? ' ' + last.units : '') : '';
            const at = last.at ? L.when(last.at, tz, Date.now(), data && data.server_now) : '';
            return h('a', { className: 'qc-card v-' + v.key + (v.key === 'out' ? ' stop' : ''), href: c.href,
                            draggable: 'false', 'data-testid': 'qc-card', title: c.title + ' · ' + c.test + (c.sample_id ? ' · ' + c.sample_id : '') + ' · ' + v.word },
                h('span', { className: 'qc-top' },
                    h('span', { className: 'qc-word' }, h('span', { className: 'glyph ' + v.glyph, 'aria-hidden': 'true' }),
                      h('span', { className: 'qc-wtext', text: v.word + (v.note ? ' · ' + v.note : '') })),
                    c.control ? h('span', { className: 'qc-chip', text: c.control.words }) : null),
                h('span', { className: 'qc-name', text: c.title }),
                // what tells this check from the instrument's others leads
                // ("10% Recovery"); what they share is quieter and may be cut
                h('span', { className: 'qc-check', text: c.check || c.test }),
                c.method ? h('span', { className: 'qc-method', text: c.method }) : null,
                h('span', { className: 'qc-chart' }),
                h('span', { className: 'qc-last' },
                    value ? h('b', { className: 'qc-value', text: value }) : h('span', { className: 'qc-value none', text: 'Never run' }),
                    at ? h('span', { className: 'qc-at', text: '· ' + at }) : null),
                last.range ? h('span', { className: 'qc-limits', text: 'Limits ' + last.range
                    + (last.target ? ' · target ' + last.target : '') }) : null);
        }

        function drawGrid() {
            const grid = $('wq-grid');
            const empty = $('wq-empty');
            const all = cards();
            if (!data || !Array.isArray(data.cards) || !all.length) {
                grid.hidden = true;
                grid.replaceChildren();
                empty.hidden = false;
                empty.textContent = (data && data.sub) || '';
                $('wq-pages').replaceChildren();
                $('wq-count').textContent = '';
                return;
            }
            empty.hidden = true;
            grid.hidden = false;
            const g = L.qcGrid(grid.clientWidth, grid.clientHeight);
            per = g.per;
            const pages = L.pages(all.length, per);
            rot.setCount(pages, Date.now());
            const page = all.slice(rot.index * per, rot.index * per + per);
            grid.style.setProperty('--qc-cols', String(g.cols));
            grid.style.setProperty('--qc-rows', String(g.rows));
            grid.replaceChildren(...page.map(cardEl));
            grid.querySelectorAll('.qc-card').forEach((el, i) => chart(el.querySelector('.qc-chart'), page[i]));
            $('wq-pages').replaceChildren(...(pages > 1 ? Array.from({ length: pages }, (_, i) => h('i', { className: i === rot.index ? 'on' : '' })) : []));
            $('wq-count').textContent = all.length + (all.length === 1 ? ' check' : ' checks') + (pages > 1 ? ' · ' + per + ' a page' : '');
            $('wq-step').hidden = !(pages > 1 && !rot.running());
        }
        function rotText(now) {
            const pages = L.pages(cards().length, per);
            if (pages < 2) return '';
            const head = 'Page ' + (rot.index + 1) + ' of ' + pages;
            if (!kiosk.rotate) return head + ' · not rotating';
            if (reduced) return head + ' · rotation off (reduced motion)';
            if (rot.paused) return head + ' · held while you point';
            return head + ' · next in ' + Math.ceil(rot.remaining(now) / 1000) + ' s';
        }

        function refresh() {
            if (inFlight || !window.LEMLive) return;
            inFlight = true;
            lastRefresh = Date.now();
            // a deadline: a request that never answers is a failed read
            // (tick → abandon), not a wall that says "Live" on frozen data
            const ticket = ++asked;
            askedAt = Date.now();
            ctl = typeof AbortController === 'function' ? new AbortController() : null;
            window.LEMLive.bgFetch('/api/ui/wall/qc', { cache: 'no-store', headers: { Accept: 'application/json' },
                                                     signal: ctl ? ctl.signal : undefined })
                .then(r => { if (ticket !== asked) throw new Error('abandoned'); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(d => { if (ticket !== asked) return; data = d; dataOkMs = Date.now(); dataFailing = false; if (d.lab_tz) tz = d.lab_tz; render(); })
                .catch(() => {
                    if (ticket !== asked) return;
                    // the last answer stays; again in 10 s; 90 s without one is stale
                    dataFailing = true;
                    lastRefresh = Date.now() - REFRESH_MS + 10000;
                })
                .then(() => { if (ticket === asked) { inFlight = false; askedAt = null; } });
        }
        // the deadline, on the same clock as the stale rule
        function abandonOverdue(now) {
            if (!inFlight || !L.overdue(askedAt, now)) return;
            asked++;
            if (ctl) { try { ctl.abort(); } catch (_e) { /* already settled */ } }
            ctl = null;
            inFlight = false;
            askedAt = null;
            dataFailing = true;
            lastRefresh = now - REFRESH_MS + 10000;
        }

        function render() {
            if (!shown) return;
            const now = Date.now();
            const live = liveNow(now);
            lastKind = live.kind;
            drawHead(live);
            drawGrid();
            $('wq-rot').textContent = rotText(now);
        }
        function tick(now) {
            if (!shown) return;
            const before = rot.index;
            rot.tick(now);
            const live = liveNow(now);
            if (live.kind !== lastKind) { lastKind = live.kind; drawHead(live); }
            else $('wq-live').textContent = live.footer;
            if (rot.index !== before) drawGrid();
            $('wq-rot').textContent = rotText(now);
            abandonOverdue(now);
            if (now - lastRefresh > REFRESH_MS) refresh();
        }

        section.addEventListener('pointerenter', () => rot.pause(Date.now()));
        section.addEventListener('pointerdown', () => rot.pause(Date.now()));
        section.addEventListener('pointerleave', () => rot.resume(Date.now()));
        section.addEventListener('touchend', () => setTimeout(() => rot.resume(Date.now()), PAGE_MS));
        $('wq-prev').addEventListener('click', () => { rot.go(-1, Date.now()); drawGrid(); });
        $('wq-next').addEventListener('click', () => { rot.go(1, Date.now()); drawGrid(); });
        let resizeT = null;
        window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(drawGrid, 120); });
        document.addEventListener('lem:theme', () => drawGrid());
        if (window.LEMLive) {
            window.LEMLive.subscribe((u) => {
                if (u.lab_tz) tz = u.lab_tz;
                if (u.reset || (u.machines && u.machines.length) || (u.kinds && u.kinds.length)) refresh();
            });
        }
        render();
        return {
            name: 'qc', section, tick,
            show() { shown = true; section.hidden = false; render(); },
            hide() { shown = false; section.hidden = true; },
        };
    }

    window.LEMWallQC = { mount };
})();
