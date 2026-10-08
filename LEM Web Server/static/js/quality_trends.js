/* quality_trends.js: /quality/trends (2026-10-08).

   Ryan: "I want a page with like all of them … arrange them together by
   machine and have them show dynamically in one page". /qc already drew
   every check's chart, as a TV wall that pages every 15 s and is in no
   menu; this is the desk's: one group per instrument, worst first, every
   check of it a tile, all on one page that scrolls. The tiles take as many
   columns as the screen has (CSS auto-fill), and each chart redraws to its
   own width when the window changes.

   A chart is the pass band normalised (the dashed lines are the limits,
   whatever the units, so every chart reads the same), time left to right
   over the chosen range (30 · 90 · 180 days, ?range=). An out-of-spec
   result is a triangle, not only a colour. A history that could not be
   read says so; it is never drawn as a flat line.

   Data is /api/ui/wall/qc (snapshot + the local log, 0 LabCore ops),
   refreshed when the live feed says something changed and once a minute.
   GETs only; every string through textContent. */
(function () {
    'use strict';
    const Q = window.LEMQuality;
    const R = window.LEMRecord;
    const S = window.LEMShell;
    const $ = (id) => document.getElementById(id);
    let data = null;
    try { data = JSON.parse(($('quality-data') || {}).textContent || 'null'); } catch (_e) { data = null; }
    if (!data || data.view !== 'trends' || !Q || !S) return;
    const h = S.h;
    const SVG = 'http://www.w3.org/2000/svg';
    const CAP = 1.8;                     // a z beyond this is drawn pinned at the edge
    const DAY = 86400000;
    const WORDS = { out: 'out of spec', due: 'due', never: 'no verdict yet', in: 'in spec' };
    const WORD_CLASS = { out: 's-not_ok', due: 's-ok_but' };
    const PILL_GLYPH = { stop: 'error', warn: 'half', ok: 'final' };

    let T = data.trends;
    let range = Q.trendRange(new URLSearchParams(location.search).get('range'));
    let failed = '';
    let readAt = Date.now();

    const glyph = (kind) => h('span', { className: 'glyph ' + kind, 'aria-hidden': 'true' });
    function s(tag, attrs) {
        const n = document.createElementNS(SVG, tag);
        for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
        return n;
    }
    function why(e) {
        if (!e || e.name === 'TypeError') return 'LEM did not answer';
        return String(e.message || 'no answer').replace(/[.\s]+$/, '');
    }
    const stamp = (at) => (R && R.stamp ? R.stamp(at) : String(at || ''));

    // ── one chart, sized to its box ──────────────────────────────────────
    function chart(box, c) {
        const W = Math.max(60, Math.round(box.clientWidth)), H = Math.max(40, Math.round(box.clientHeight));
        const to = Date.now(), from = to - range * DAY;
        const pts = Q.trendX(c.points, from, to);
        if (!pts.length) {
            box.replaceChildren(h('span', { className: 't-nochart', text:
                c.history === 'unread' ? 'History not read: the local record did not answer'
                : c.history === 'filling' ? 'No history here yet: LEM’s log copy has not filled'
                : (c.points || []).some(p => p && p.z === null) ? 'No pass band recorded for these results'
                : (c.points || []).length ? 'No results in the last ' + range + ' days'
                : 'No results in the last 180 days' }));
            return;
        }
        const pad = 6;
        const y = z => H - pad - ((Math.max(-CAP, Math.min(CAP, z)) + CAP) / (2 * CAP)) * (H - 2 * pad);
        const x = t => pad + t * (W - 2 * pad);
        const outs = pts.filter(p => p.in_spec === false).length;
        const svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img',
            'aria-label': c.title + ', ' + (c.check || c.test) + ': ' + pts.length + ' results in ' + range + ' days, '
                + outs + ' outside the limits' });
        svg.append(s('rect', { x: 0, y: y(1), width: W, height: y(-1) - y(1), class: 'qc-band' }));
        for (const v of [1, -1]) svg.append(s('line', { x1: 0, x2: W, y1: y(v), y2: y(v), class: 'qc-limit' }));
        svg.append(s('line', { x1: 0, x2: W, y1: y(0), y2: y(0), class: 'qc-target' }));
        svg.append(s('polyline', { points: pts.map(p => x(p.t).toFixed(1) + ',' + y(p.z).toFixed(1)).join(' '), class: 'qc-line' }));
        pts.forEach((p, i) => {
            const last = i === pts.length - 1;
            const r = last ? 4 : 2.4;
            const cx = x(p.t), cy = y(p.z);
            let el;
            if (p.in_spec === false) {
                const k = r + 1.5;
                el = s('polygon', { points: [cx, cy - k, cx + k, cy + k * 0.8, cx - k, cy + k * 0.8].map(v => v.toFixed(1)).join(' '),
                                    class: 'qc-pt out' + (last ? ' last' : '') });
            } else {
                el = s('circle', { cx: cx.toFixed(1), cy: cy.toFixed(1), r, class: 'qc-pt' + (last ? ' last' : '') });
            }
            const tip = s('title', {});
            tip.textContent = stamp(p.at) + (p.in_spec === false ? ' · outside the limits' : '');
            el.append(tip);
            svg.append(el);
        });
        box.replaceChildren(svg);
    }

    // ── a tile: one check ────────────────────────────────────────────────
    function tile(c) {
        const v = c.verdict || {};
        const last = c.last || {};
        const value = last.value ? last.value + (last.units ? ' ' + last.units : '') : '';
        const box = h('span', { className: 't-chart' });
        const el = h('a', { className: 't-tile', href: c.href, 'data-testid': 't-tile', 'data-uid': c.uid,
                            title: c.title + ' · ' + c.test + (c.sample_id ? ' · ' + c.sample_id : '') + ' · ' + (v.word || '') },
            h('span', { className: 't-top' },
                h('span', { className: 't-check', text: c.check || c.test }),
                h('span', { className: 't-word' }, glyph(v.glyph || 'dashed'),
                    h('span', { className: WORD_CLASS[v.key] || '', text: (v.word || '') + (v.note ? ' · ' + v.note : '') }))),
            c.method ? h('span', { className: 't-method', text: c.method }) : null,
            box,
            h('span', { className: 't-last' },
                value ? h('b', { text: value }) : h('span', { className: 'muted', text: 'Never run' }),
                last.at ? h('span', { className: 'muted', text: ' · ' + stamp(last.at) }) : null),
            last.range ? h('span', { className: 't-limits', text: 'Limits ' + last.range + (last.target ? ' · target ' + last.target : '') }) : null,
            c.control ? h('span', { className: 't-control', text: c.control.words }) : null);
        el._card = c;
        el._box = box;
        return el;
    }

    // ── a group: one instrument ──────────────────────────────────────────
    function group(g) {
        const counts = Object.entries(g.counts).map(([k, n]) => n + ' ' + WORDS[k]).join(' · ');
        return h('section', { className: 't-group', 'data-uid': g.uid, 'data-testid': 't-group', 'aria-label': g.title },
            h('header', { className: 't-ghead' },
                h('h3', { className: 't-gname' }, glyph(g.cards.find(c => (c.verdict || {}).key === g.worst).verdict.glyph || 'dashed'),
                    h('a', { className: 't-name', href: g.href, text: g.title })),
                h('span', { className: 'caption t-counts', text: counts })),
            h('div', { className: 't-tiles' }, ...g.cards.map(tile)));
    }

    // ── the column grid: the biggest tiles that put every chart on one
    // screen (trendFit), or the 280px minimum and a scroll when they can't
    function layout() {
        const box = $('t-groups');
        const W = box.clientWidth;
        if (!W) return;
        const gap = window.innerWidth < 700 ? 12 : 16;
        const groups = [...box.querySelectorAll('.t-group')];
        const top = box.getBoundingClientRect().top + window.scrollY;
        const f = Q.trendFit({ width: W, height: window.innerHeight - top - 32, gap,
                               sizes: groups.map(g => g.querySelectorAll('.t-tile').length) });
        box.style.setProperty('--t-gap', gap + 'px');
        box.style.setProperty('--tile-w', f.tileW + 'px');
        box.style.setProperty('--chart-h', f.chartH + 'px');
        box.classList.toggle('t-big', f.tileW >= 480);
        for (const g of groups) {
            const span = Math.min(f.cols, g.querySelectorAll('.t-tile').length || 1);
            g.style.setProperty('--g-w', (span * f.tileW + (span - 1) * gap) + 'px');
        }
    }

    function drawCharts() {
        for (const el of document.querySelectorAll('#t-groups .t-tile')) chart(el._box, el._card);
    }

    function renderHead() {
        const bits = [];
        if (T && T.state === 'ready' && T.headline) {
            bits.push(h('span', { className: 'pill q-pill', 'data-testid': 'q-pill' }, glyph(PILL_GLYPH[T.tone] || 'dashed'),
                h('span', { text: T.headline })));
        }
        $('q-meta').replaceChildren(...bits);
    }
    function renderUnread() {
        const ready = T && T.state === 'ready';
        $('q-unread').hidden = ready;
        $('q-trends').hidden = !ready;
        if (ready) return;
        const bad = T && T.state === 'unreadable';
        $('q-unread').firstElementChild.className = 'glyph ' + (bad ? 'error' : 'dashed');
        $('q-unread-head').textContent = bad ? 'No checks to show yet.' : 'Not read yet.';
        $('q-unread-text').textContent = bad
            ? 'This is not a lab with no QC: LEM has not been able to read it. The charts appear by themselves as soon as it can.'
            : 'LEM reads every instrument within a few seconds of starting. The charts appear by themselves.';
    }
    function renderSub() {
        $('t-sub').textContent = failed
            ? 'Couldn’t refresh (' + failed + '): showing the charts as they were at ' + stamp(new Date(readAt).toISOString())
            : 'Every check, by instrument, worst first · the band is the certified limits · updates by itself';
        $('t-sub').classList.toggle('s-ok_but', !!failed);
    }
    function renderRange() {
        for (const b of $('t-range').querySelectorAll('button')) b.setAttribute('aria-pressed', Number(b.dataset.range) === range ? 'true' : 'false');
    }

    function render() {
        renderHead();
        renderUnread();
        renderSub();
        renderRange();
        if (!T || T.state !== 'ready') return;
        const groups = Q.trendGroups(T.cards);
        $('t-empty').hidden = !!groups.length;
        $('t-groups').replaceChildren(...groups.map(group));
        layout();
        drawCharts();
    }

    // ── live: the feed, and once a minute ────────────────────────────────
    let inflight = false;
    function refetch() {
        if (inflight) return;
        inflight = true;
        const get = window.LEMLive && window.LEMLive.bgFetch ? window.LEMLive.bgFetch('/api/ui/wall/qc')
            : fetch('/api/ui/wall/qc', { headers: { 'X-LEM-Background': '1' } });
        get.then(r => r.ok ? r.json() : r.json().catch(() => ({})).then(b => { throw new Error((b && b.error) || ('HTTP ' + r.status)); }))
            .then(d => { T = d; failed = ''; readAt = Date.now(); render(); })
            .catch(e => { failed = why(e); renderSub(); })
            .finally(() => { inflight = false; });
    }
    if (window.LEMLive) {
        let lastAt = T && T.built_at;
        window.LEMLive.subscribe((u) => {
            const changed = u.reset || (u.machines && u.machines.length) || (u.snapshot_at && u.snapshot_at !== lastAt);
            if (u.snapshot_at) lastAt = u.snapshot_at;
            if (changed) refetch();
        });
    }
    setInterval(() => { if (!document.hidden) refetch(); }, 60000);

    $('t-range').addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-range]');
        if (!b) return;
        range = Q.trendRange(b.dataset.range);
        const u = new URL(location.href);
        u.searchParams.set('range', String(range));
        history.replaceState(null, '', u.pathname + u.search);
        renderRange();
        drawCharts();
    });

    // each chart redraws to its own width when the window changes
    let resizeTimer = null;
    window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { layout(); drawCharts(); }, 80); });

    render();
})();
