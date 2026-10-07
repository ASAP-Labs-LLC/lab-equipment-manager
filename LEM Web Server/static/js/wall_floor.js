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
     * levels are pages (Ryan, 2026-10-07: "like android app pages, where
       you slide left to right on them"): one level at a time, in the
       positions placed with Arrange, each up for ?dwell= seconds (20),
       pausing while a pointer is on the wall. Tabs, arrow keys, ‹ › and a
       swipe turn it by hand and hold it a minute; each tab carries the
       worst state on its level. ?level=<uid> pins one, ?rotate=0 and
       reduced motion stop it. Every level at once only with ?whole=1.
   Every string goes in through textContent. GETs only. */
(function () {
    'use strict';
    const L = window.LEMWallLogic;
    const P = window.LEMPlan;
    const HOLD_MS = 60000;
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
        // the wall's own data, not only the live feed, must be answering:
        // a /api/ui/wall/floor that fails while /api/ui/live answers would
        // otherwise leave a frozen wall saying "Live"
        let dataOkMs = Date.now();
        let dataFailing = false;
        let asked = 0, askedAt = null, ctl = null;

        // ── levels ───────────────────────────────────────────────────────
        function levels() { return (data && data.levels) || []; }
        const pinned = () => !!kiosk.level && levels().some(l => l.uid === kiosk.level);
        const LEVEL_MS = kiosk.dwell * 1000;
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
            const heard = dataFailing ? Math.min(st.last_ok_at || 0, dataOkMs) : (st.last_ok_at || 0);
            return L.liveState({ loadedMs: dataFailing ? Math.min(loadedMs, dataOkMs) : loadedMs, lastOkMs: heard, nowMs: now, tz,
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
            // one group per run of one state, worst first; the state word
            // heads its group, said once; each row is a name and its whole
            // reason (round 4: the word said on every card competed with
            // the failures, and the two worst reasons were cut)
            const groups = [];
            for (const a of items) {
                const g = groups[groups.length - 1];
                if (g && g[0].state === a.state) g.push(a); else groups.push([a]);
            }
            $('wf-attn').replaceChildren(...groups.map(g => h('li', { className: 'wa-group s-' + g[0].state },
                h('span', { className: 'wa-word' }, h('span', { className: 'glyph ' + g[0].glyph, 'aria-hidden': 'true' }),
                  h('span', { className: 'wa-wtext', text: g[0].word })),
                h('ul', { className: 'wa-rows' }, ...g.map(a => h('li', {
                    className: 'wa-item s-' + a.state + (a.state === 'not_ok' ? ' stop' : '') + (a.stopped ? ' stopped' : '') },
                    h('a', { href: a.href, draggable: 'false' },
                        h('span', { className: 'wa-names', text: a.label || (a.names || []).join(', ') }),
                        h('span', { className: 'wa-detail', text: a.detail || '' }))))))));
            const more = $('wf-attn-more');
            more.hidden = !w.attention_more;
            more.textContent = w.attention_more ? 'and ' + w.attention_more + ' more on Instruments' : '';
            $('wf-attn-none').hidden = !(Array.isArray(w.attention) && !w.attention.length);
        }

        // ── the plan ─────────────────────────────────────────────────────
        // The whole floor at once when every bay can still hold its words at
        // the bar's sizes (wall_logic.floorPack); one level at a time only
        // when it cannot. `whole` is the pack in use, or null.
        let whole = null;
        let emptyLevels = [];
        function px(v) { return parseFloat(v) || 0; }
        function sizes() {
            const vw = window.innerWidth / 100, vh = window.innerHeight / 100;
            const name = Math.max(18, 2.04 * vh), word = Math.max(16, 1.86 * vh), det = Math.max(14, 1.49 * vh);
            const pad = Math.min(14, Math.max(8, 1.1 * vh));
            const hpad = Math.min(16, Math.max(10, 0.75 * vw));
            return {
                // a two-line name, the verdict on one line, and the detail:
                // the bay keeps all three (round 3: Pensky-Martens 1 lost
                // "QC out of spec" in a bay too short for it)
                minH: Math.ceil(2 * pad + 2.4 * name + 4 + 1.3 * word + 1.3 * det + 3 + 2),
                // the longest whole verdict ("OK to run, but…", 7.44em at
                // 600) on one line, at the tight padding (8px a side)
                minW: Math.ceil(word * 7.6 + 16),
                head: Math.ceil(Math.max(16, 1.75 * vh) * 1.9),
                // the Needs-attention column (CSS: clamp(320px, 23vw, 460px))
                attnW: Math.min(460, Math.max(320, 23 * vw)),
                attnGap: Math.min(36, Math.max(16, 1.8 * vw)),
            };
        }
        // `noHole`: Needs attention did not fit the hole last time it was
        // tried at this size, so the column it is (measured, not wished)
        let noHole = '';
        // held, not looked up: in the hole the column lives inside a level
        // row, and a redraw replaces the rows
        const mainEl = section.querySelector('.wall-main');
        const attnEl = section.querySelector('.wall-attn');
        function packAll(W, H) {
            const lv = levels();
            emptyLevels = [];
            if (lv.length < 2 || pinned()) return null;
            const parts = [];
            for (const l of lv) {
                const lay = P.layout(P.onLevel(data.instruments, l.uid, data), {});
                if (lay.w) parts.push({ uid: l.uid, name: l.name, lay, w: lay.w, h: lay.h });
                else emptyLevels.push(l.name);
            }
            if (parts.length < 2) return null;
            const sz = sizes();
            const key = W + 'x' + H;
            const o = { gap: 12, panelGap: 20, head: sz.head, minW: sz.minW, minH: sz.minH,
                        attnW: sz.attnW, attnGap: sz.attnGap,
                        holeMinW: noHole === key ? Infinity : sz.attnW, holeMinH: 260 };
            const p = L.wallLayout(parts, W, H, o);
            return p ? Object.assign(p, { parts, head: sz.head, key }) : null;
        }
        function bayOpts(cellH, gap, words) {
            return { cellH, gap, fullWords: true, words: words || 'full',
                     details: data.details || {}, detailsShort: data.details_short || {} };
        }
        // Needs attention lives beside the plan, or in the hole the levels
        // leave (wall_logic.wallLayout); the element moves, nothing is redrawn
        function placeAttn(hole, row) {
            const main = mainEl, aside = attnEl;
            if (hole && row) {
                main.dataset.layout = 'hole';
                aside.classList.remove('tight', 'tighter');
                aside.classList.add('in-hole');
                aside.style.width = hole.w + 'px';
                aside.style.height = hole.h + 'px';
                aside.style.setProperty('--wl-head', whole.head + 'px');
                row.append(aside);
            } else {
                main.dataset.layout = 'column';
                aside.classList.remove('in-hole', 'tight', 'tighter');
                aside.style.width = aside.style.height = '';
                if (aside.parentElement !== main) main.append(aside);
            }
        }
        function attnFits() {
            const aside = attnEl;
            const box = aside.getBoundingClientRect();
            return [...aside.querySelectorAll('.wa-group, .wa-item, .wa-more, .wa-none')].every(e =>
                e.hidden || e.getBoundingClientRect().bottom <= box.bottom + 0.5);
        }
        function drawWhole(plan, p, words) {
            plan.className = 'wall-levels';
            plan.style.setProperty('--wl-head', p.head + 'px');
            const hosts = [];
            const rows = p.rows.map(r => h('div', { className: 'wl-row' }, ...r.map(i => {
                const part = p.parts[i];
                const host = h('div', { className: 'plan wallplan', 'data-level': part.uid });
                hosts.push([host, part]);
                return h('section', { className: 'wl-panel', 'aria-label': part.name,
                                      style: 'width:' + (part.w * p.cellW + 12 * (part.w + 1)) + 'px' },
                    h('h3', { className: 'wl-name', text: part.name }), host);
            })));
            plan.replaceChildren(...rows);
            placeAttn(p.mode === 'hole' ? p.hole : null, p.mode === 'hole' ? rows[p.hole.row] : null);
            // drawn once the panels are in the page, so plan.js can measure
            for (const [host, part] of hosts) P.draw(host, part.lay, bayOpts(p.cellH, 12, words));
            return hosts.map(x => x[0]);
        }
        /** One vocabulary everywhere (§4.1): a bay says the app's whole
            word, never a short form. If any bay could not say it on one
            line, the floor is redrawn tight (less padding, the bar's
            smallest word size); if one still cannot, that word may take a
            second line, whole. */
        function oneVocabulary(hosts, redraw) {
            const cut = () => hosts.some(x => x.querySelector('.bay[data-word-cut]'));
            if (!cut()) return;
            hosts = [].concat(redraw('tight') || hosts);
            if (cut()) redraw('wrap');
        }
        function drawPlan() {
            const plan = $('wf-plan');
            const empty = $('wf-empty');
            const unplaced = $('wf-unplaced');
            const ready = data && data.state === 'ready' && Array.isArray(data.instruments);
            const wrap = $('wf-plan-wrap');
            const main = mainEl;
            // the whole floor needs no level bar (the footer says "All 3
            // levels shown"), and its 38px go to the bays and the hole
            const card = section.querySelector('.wall-plan-card');
            const tryWhole = !!(kiosk.whole && ready && data.instruments.length && levels().length >= 2 && !pinned());
            card.classList.toggle('whole', tryWhole);
            whole = tryWhole ? packAll(main.clientWidth, wrap.clientHeight) : null;
            if (!whole) card.classList.remove('whole');
            if (!whole || whole.mode !== 'hole') placeAttn(null);
            const level = ready && !whole ? levelNow() : '';
            lastLevel = level;
            $('wf-level').textContent = !ready ? 'The floor'
                : whole ? 'The whole floor · ' + whole.parts.length + ' levels' : levelName(level);
            drawDots(whole ? null : level);
            if (!ready || !data.instruments.length) {
                plan.hidden = true;
                plan.replaceChildren();
                unplaced.hidden = true;
                empty.hidden = false;
                empty.textContent = ((data && data.wall) || {}).sub || '';
                return;
            }
            let lost = [];
            main.classList.remove('tight-plan');
            if (whole) {
                empty.hidden = true;
                plan.hidden = false;
                oneVocabulary(drawWhole(plan, whole, 'full'), (w) => drawWhole(plan, whole, w));
                if (whole.mode === 'hole' && !attnFits()) {
                    // first the details go to one line, then the names
                    const aside = attnEl;
                    aside.classList.add('tight');
                    if (!attnFits()) aside.classList.add('tighter');
                    if (!attnFits()) {
                        // the hole cannot hold the worst five: the column, at this size
                        aside.classList.remove('tight', 'tighter');
                        noHole = whole.key;
                        return drawPlan();
                    }
                }
                const placed = new Set(whole.parts.flatMap(pt => pt.lay.bays.map(b => b.uid)));
                lost = data.instruments.filter(r => !placed.has(r.uid));
            } else {
                plan.className = 'plan wallplan';
                const here = P.onLevel(data.instruments, level, data);
                const lay = P.layout(here, {});
                lost = lay.unplaced;
                if (!lay.w) {
                    plan.hidden = true;
                    plan.replaceChildren();
                    empty.hidden = false;
                    empty.textContent = 'Nobody has placed an instrument on ' + levelName(level) + ' yet.';
                } else {
                    empty.hidden = true;
                    plan.hidden = false;
                    // measured per draw: a tight floor narrows Needs
                    // attention (.tight-plan), which widens the plan
                    const one = (words) => {
                        main.classList.toggle('tight-plan', words !== 'full');
                        const W = wrap.clientWidth, H = wrap.clientHeight;
                        const narrow = (W - 24 - 12 * (lay.w - 1)) / lay.w < 120;
                        const gap = narrow ? 8 : 12;
                        const cellW = (W - 2 * gap - gap * (lay.w - 1)) / lay.w;
                        const fill = Math.floor((H - 2 * gap - gap * (lay.h - 1)) / lay.h);
                        const cellH = Math.max(40, Math.min(fill, Math.floor(cellW * 1.05), 300));
                        P.draw(plan, lay, bayOpts(cellH, gap, words));
                        return [plan];
                    };
                    oneVocabulary(one('full'), one);
                }
            }
            if (!whole || whole.mode !== 'hole') {
                // the column too: details to one line, then names, before
                // anything is clipped
                attnEl.classList.remove('tight', 'tighter');
                if (!attnFits()) attnEl.classList.add('tight');
                if (!attnFits()) attnEl.classList.add('tighter');
            }
            if (lost.length) {
                unplaced.hidden = false;
                unplaced.replaceChildren(h('span', { className: 'wu-head', text: 'Not on the plan:' }),
                    ...lost.map((r, i) => h('a', { href: r.href, className: 'wu-item', draggable: 'false' },
                        (i ? ', ' : ' ') + r.title + ' · ' + ((r.readiness && r.readiness.word) || ''))));
            } else {
                unplaced.hidden = true;
                unplaced.replaceChildren();
            }
        }
        /** The level tabs: every page by name, the one shown marked, each
            with the worst state on it, so a problem on a page nobody is
            looking at is still on the screen. */
        function drawDots(level) {
            const lv = levels();
            const tabs = $('wf-tabs');
            const many = lv.length > 1 && !!level;
            tabs.hidden = !many;
            tabs.replaceChildren(...(many ? lv.map((l, i) => {
                const here = P.onLevel(data.instruments, l.uid, data);
                const st = L.worstState(here.map(r => r.readiness && r.readiness.state));
                const b = h('button', { type: 'button', className: 'wp-tab' + (st ? ' s-' + st : ''),
                                        'data-state': st, 'aria-current': l.uid === level ? 'page' : null,
                                        disabled: pinned() ? true : null },
                    h('span', { className: 'glyph ' + (L.stateGlyph(st) || 'dashed'), 'aria-hidden': 'true' }),
                    h('span', { className: 'wp-tname', text: l.name }));
                b.addEventListener('click', () => turn(i - rot.index));
                return b;
            }) : []));
            $('wf-step').hidden = !(many && !pinned());
        }
        /** A page turned by hand: shown at once, held a minute. */
        function turn(step) {
            if (pinned() || whole || levels().length < 2 || !step) return;
            const now = Date.now();
            rot.go(step, now);
            if (rot.enabled) rot.hold(now, HOLD_MS);
            drawPlan();
            $('wf-rot').textContent = rotText(now);
        }
        function rotText(now) {
            const lv = levels();
            const skipped = emptyLevels.length ? ' · ' + emptyLevels.join(', ') + ': nothing placed' : '';
            if (whole) return 'All ' + whole.parts.length + ' levels shown · nothing to rotate' + skipped;
            if (lv.length < 2) return (lv.length ? '1 level' : 'One floor') + ' · nothing to rotate';
            const i = Math.max(0, lv.findIndex(l => l.uid === lastLevel)) + 1;
            const head = levelName(lastLevel) + ' · ' + i + ' of ' + lv.length;
            if (pinned()) return head + ' · pinned';
            if (!kiosk.rotate) return head + ' · not rotating';
            if (reduced) return head + ' · rotation off (reduced motion)';
            if (rot.holdUntil) return head + ' · held · cycling again in ' + Math.ceil((rot.holdUntil - now) / 1000) + ' s';
            if (rot.paused) return head + ' · held while you point';
            return head + ' · next in ' + Math.ceil(rot.remaining(now) / 1000) + ' s';
        }

        // ── refresh ──────────────────────────────────────────────────────
        function refresh() {
            if (inFlight || !window.LEMLive) return;
            inFlight = true;
            lastRefresh = Date.now();
            // a deadline: a request that never answers is a failed read
            // (tick → abandon), not a wall that says "Live" on frozen data
            const ticket = ++asked;
            askedAt = Date.now();
            ctl = typeof AbortController === 'function' ? new AbortController() : null;
            window.LEMLive.bgFetch('/api/ui/wall/floor', { cache: 'no-store', headers: { Accept: 'application/json' },
                                                     signal: ctl ? ctl.signal : undefined })
                .then(r => { if (ticket !== asked) throw new Error('abandoned'); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
                .then(d => {
                    if (ticket !== asked) return;
                    data = d;
                    dataOkMs = Date.now();
                    dataFailing = false;
                    if (d.lab_tz) tz = d.lab_tz;
                    rot.setCount(Math.max(1, levels().length), Date.now());
                    render();
                })
                .catch(() => {
                    if (ticket !== asked) return;
                    // the last answer stays; try again in 10 s, and after 90 s
                    // without one the stale rule says so (liveNow)
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
            drawCounts();
            drawAttention();
            drawPlan();
            $('wf-rot').textContent = rotText(now);
        }

        function tick(now) {
            if (!shown) return;
            const before = rot.index;
            const held = rot.holdUntil;
            if (!whole) rot.tick(now);
            if (held && !rot.holdUntil) drawDots(lastLevel);
            const live = liveNow(now);
            if (live.kind !== lastKind) { lastKind = live.kind; drawHead(live); }
            else $('wf-live').textContent = live.footer;
            if (rot.index !== before) drawPlan();
            $('wf-rot').textContent = rotText(now);
            abandonOverdue(now);
            if (now - lastRefresh > REFRESH_MS) refresh();
        }

        // somebody reading the wall holds it still
        // (a hand turn's minute outlasts the pointer that made it)
        section.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') rot.pause(Date.now()); });
        section.addEventListener('pointerleave', (e) => {
            if (e.pointerType === 'mouse' && !rot.holdUntil) rot.resume(Date.now());
        });
        $('wf-prev').addEventListener('click', () => turn(-1));
        $('wf-next').addEventListener('click', () => turn(1));
        document.addEventListener('keydown', (e) => {
            if (!shown || e.altKey || e.ctrlKey || e.metaKey) return;
            if (e.key === 'ArrowRight') { e.preventDefault(); turn(1); }
            else if (e.key === 'ArrowLeft') { e.preventDefault(); turn(-1); }
        });
        // a swipe across the plan turns the page, like a phone's home
        // screens; the bay under the finger at the end is not opened
        const wrapEl = $('wf-plan-wrap');
        let down = null, swallow = false;
        wrapEl.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, id: e.pointerId }; });
        wrapEl.addEventListener('pointerup', (e) => {
            if (!down || down.id !== e.pointerId) return;
            const step = L.swipeStep(e.clientX - down.x, e.clientY - down.y);
            down = null;
            if (step) { swallow = true; setTimeout(() => { swallow = false; }, 400); turn(step); }
        });
        wrapEl.addEventListener('pointercancel', () => { down = null; });
        wrapEl.addEventListener('click', (e) => {
            if (swallow) { e.preventDefault(); e.stopPropagation(); swallow = false; }
        }, true);
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
