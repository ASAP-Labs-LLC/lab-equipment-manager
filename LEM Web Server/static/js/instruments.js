/* instruments.js: the Instruments home (ia-final §3.2), drawn from
   /api/ui/instruments (the first paint's copy rides in #instruments-data).

   What it draws, and the one rule for each:
     the fleet pill      ONE verdict on the fleet, never a tally per problem
     Needs you           one tile per cause: the cause and its next step,
                         never a name or a count; it filters the table
     the chips           views, not counts; the address bar is the view
     the table           every instrument, worst first; Can it run? answers T1
     the find box        /api/search, Ctrl K, "Searching…" only after 180 ms,
                         and a sentence saying what it searched

   The same page is the floor map (?view=map, piece 12): then the table and
   chips are not drawn, the Needs-you tiles stack in a column and filter the
   plan instead of the table, and static/js/floor_map.js (LEMFloorMap) draws
   the plan from the same answer, handed over on every render.

   It refetches when the live feed (LEMLive) says an instrument or the record
   changed. GETs only: an open page never POSTs on a timer (A.7). Every
   string goes in through textContent (LEMShell.h). The pure parts are
   static/js/instruments_logic.js (node-tested). */
(function () {
    'use strict';
    const L = window.LEMInstruments;
    const S = window.LEMShell;
    const h = S.h;
    const $ = (id) => document.getElementById(id);
    const SVG = 'http://www.w3.org/2000/svg';

    let data = null;
    let view = L.parseView(location.search);
    let unknown = L.unknownView(location.search);   // a ?filter= with no view: said, not obeyed
    let refreshFailedAt = null;
    const page = $('main');
    const hasQuality = page && page.dataset.hasQuality === 'true';
    const MAP = !!(page && page.dataset.view === 'map');
    const M = () => (MAP ? window.LEMFloorMap : null);
    /** The view the Needs-you tiles answer to: the map's own when on the map. */
    const tileView = () => (M() ? { filter: '', level: '', cause: M().view().cause } : view);

    try { data = JSON.parse(($('instruments-data') || {}).textContent || 'null'); } catch (_e) { data = null; }

    const hrefs = {};
    function remember() {
        for (const r of (data && data.instruments) || []) hrefs[r.uid] = r.href;
    }
    function hrefFor(uid, section) {
        const base = hrefs[uid] || '/instruments/' + encodeURIComponent(uid);
        return section ? base + '#' + section : base;
    }

    // ── glyphs: a shape per state (§4.1) ──────────────────────────────────
    function svg(tag, attrs) {
        const el = document.createElementNS(SVG, tag);
        for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
        return el;
    }
    /** The 22px circle a tile carries (GC's idiom): tick when fine, triangle
        when bad, half-ring when due, dashed ring when unknown. */
    function circle(kind) {
        const box = svg('svg', { viewBox: '0 0 22 22', class: 'tglyph g-' + kind, 'aria-hidden': 'true' });
        if (kind === 'ok' || kind === 'final') {
            box.appendChild(svg('circle', { cx: 11, cy: 11, r: 10, class: 'fill' }));
            box.appendChild(svg('path', { d: 'M6.5 11.2l3 3 6-6.4', class: 'mark' }));
        } else if (kind === 'bad' || kind === 'error') {
            box.appendChild(svg('path', { d: 'M11 2.5L20.5 19H1.5z', class: 'fill' }));
            box.appendChild(svg('path', { d: 'M11 8.5v4.6M11 15.6v.4', class: 'mark' }));
        } else if (kind === 'due' || kind === 'half') {
            box.appendChild(svg('circle', { cx: 11, cy: 11, r: 9.25, class: 'ring' }));
            box.appendChild(svg('path', { d: 'M11 1.75a9.25 9.25 0 0 1 0 18.5z', class: 'fill' }));
        } else if (kind === 'off') {
            box.appendChild(svg('rect', { x: 4.5, y: 4.5, width: 13, height: 13, rx: 1.5, transform: 'rotate(45 11 11)', class: 'ring' }));
        } else if (kind === 'more') {
            box.appendChild(svg('circle', { cx: 11, cy: 11, r: 9.25, class: 'ring' }));
            box.appendChild(svg('path', { d: 'M11 6.5v9M6.5 11h9', class: 'mark2' }));
        } else {
            box.appendChild(svg('circle', { cx: 11, cy: 11, r: 9.25, class: 'ring dashed' }));
        }
        return box;
    }
    function glyph(kind) { return h('span', { className: 'glyph ' + kind, 'aria-hidden': 'true' }); }

    // ── the page head: one pill ───────────────────────────────────────────
    function renderHead() {
        const meta = $('inst-meta');
        if (!meta) return;
        if (!data || data.state !== 'ready' || !data.fleet) { meta.replaceChildren(); return; }
        const f = data.fleet;
        // an empty lab has no verdict: no pill (the table says it in words)
        const bits = f.pill ? [h('span', { className: 'pill ' + f.pill.level, id: 'fleet-pill', 'data-testid': 'fleet-pill' },
            glyph(f.pill.glyph), f.pill.text)] : [];
        bits.push(h('span', { id: 'fleet-count' }, f.total + (f.total === 1 ? ' instrument' : ' instruments')));
        if ((data.levels || []).length) {
            bits.push(h('span', { className: 'sep', 'aria-hidden': 'true' }, '·'));
            bits.push(h('span', { id: 'fleet-levels' }, 'on ' + data.levels.length + ' levels'));
        }
        meta.replaceChildren(...bits);
    }

    // ── what could not be read ────────────────────────────────────────────
    function renderUnread() {
        const box = $('inst-unread');
        const ready = data && data.state === 'ready';
        box.hidden = ready;
        $('needs').hidden = !ready;
        for (const sel of ['.inst-table', '#floor-map']) {
            const el = document.querySelector(sel);
            if (el) el.hidden = !ready;
        }
        if (ready) return;
        const failed = data && data.state === 'unreadable';
        box.replaceChildren(
            glyph(failed ? 'error' : 'dashed'),
            h('div', {},
                h('b', { text: failed ? 'No instruments to show yet.' : 'Not read from LabCore yet.' }),
                h('p', { className: 'caption', text: failed
                    ? 'This is not an empty lab: LEM has not been able to read it. The list appears by itself as soon as LabCore answers.'
                    : 'LEM reads every instrument within a few seconds of starting. The list appears by itself.' })));
    }

    // ── Needs you ─────────────────────────────────────────────────────────
    // A tile is a cause and its remedy; it names nobody and counts nothing.
    // Its link filters the table below to its rows, where each instrument is
    // named once, with its verdict. The tile whose cause is the view is the
    // current one (GC's ink border); with no cause chosen, none is.
    function tile(t, current) {
        const w = L.tileWords(t, tileView());
        const kids = [circle(t.more ? 'more' : t.glyph),
                      h('span', { className: 't-word s-' + (t.more ? 'more' : t.state) }, w.head)];
        if (w.next) kids.push(h('span', { className: 't-next' }, w.next));
        kids.push(h('span', { className: 't-link' }, w.link));
        const href = M() ? M().tileHref(t.key, w.active)
            : w.active ? '/' + L.viewQuery({ filter: '', level: view.level, cause: '' }) : t.href;
        return h('a', { className: 'ntile' + (current ? ' current' : ''), href,
                        'aria-current': w.active ? 'true' : null,
                        'data-testid': 'needs-tile', 'data-key': t.key }, ...kids);
    }

    function renderNeeds() {
        const ny = data && data.needs_you;
        const box = $('needs-tiles');
        const empty = $('needs-empty');
        if (!ny) { box.replaceChildren(); return; }
        empty.hidden = ny.count > 0;
        box.hidden = ny.count === 0;
        const chosen = L.currentTile(ny.tiles, tileView());
        box.replaceChildren(...ny.tiles.map((t, i) => tile(t, i === chosen)));
        // few tiles share the row instead of leaving most of it empty (on
        // the map they stack in their column, one to a row)
        box.style.setProperty('--cols', MAP ? '1' : String(Math.min(6, Math.max(3, ny.tiles.length))));
        $('needs-caption').textContent = L.needsCaption(ny, refreshFailedAt);
    }

    // ── chips and the table ───────────────────────────────────────────────
    function causeWords() { return L.problemWords((data && data.instruments) || []); }
    function renderChips() {
        const row = $('inst-chips');
        const list = L.chips(data, view, causeWords());
        const kids = [];
        let lastGroup = null;
        for (const c of list) {
            if (lastGroup && c.group !== lastGroup) kids.push(h('span', { className: 'chip-sep', 'aria-hidden': 'true' }));
            lastGroup = c.group;
            const b = h('button', { type: 'button', className: 'chip' + (c.clears ? ' clears' : ''),
                                    'aria-pressed': c.pressed ? 'true' : 'false',
                                    'aria-label': c.clears ? c.label + ', clear this view' : null }, c.label);
            if (c.clears) b.appendChild(h('span', { className: 'x', 'aria-hidden': 'true' }, '×'));
            b.addEventListener('click', () => go(c.next));
            kids.push(b);
        }
        row.replaceChildren(...kids);
    }

    function sub(text, cls) { return h('span', { className: 'sub' + (cls ? ' ' + cls : '') }, text); }

    function rowEl(r, now) {
        const rd = r.readiness;
        const lq = r.last_qc || {};
        const b = r.bench || {};
        const benchWhen = b.at ? L.when(b.at, now) : '';
        const name = h('td', { className: 'c-name' },
            h('a', { className: 'iname', href: r.href }, r.title),
            sub([b.word, benchWhen].filter(Boolean).join(' · '), 'fold-bench'));
        const run = h('td', { className: 'c-run' },
            h('span', { className: 'verdict s-' + rd.state }, glyph(rd.glyph), h('span', { text: rd.word })),
            rd.detail ? sub(rd.detail) : null);
        let qc;
        if (view.filter === 'maintenance') {
            const nd = L.nextDue(r);
            qc = h('td', { className: 'c-qc c-due' },
                h('span', { className: 'fold-label' }, 'Next due: '),
                h('span', { className: nd.sub ? (nd.soon ? 'due-soon' : '') : 'none' },
                    nd.soon ? glyph('half') : null, h('span', { text: nd.main })),
                nd.sub ? sub(nd.sub) : null);
        } else {
            qc = h('td', { className: 'c-qc' },
                lq.at ? h('span', {}, L.when(lq.at, now)) : h('span', { className: 'none' }, lq.word || ''),
                lq.at ? sub(lq.test + (lq.checks > 1 ? ' · ' + lq.checks + ' checks' : '')) : null);
        }
        const bench = h('td', { className: 'c-bench' },
            h('span', { className: 'bstate' }, glyph(b.glyph || 'never'), h('span', { text: b.word || '' })),
            benchWhen ? sub(b.state === 'in' ? 'last poll ' + benchWhen : 'since ' + benchWhen) : null);
        const where = h('td', { className: 'c-where' },
            h('span', {}, r.where.level), r.where.placed ? null : sub('Not on the map'));
        const tr = h('tr', { className: 'irow', 'data-state': rd.state, 'data-uid': r.uid, 'data-testid': 'inst-row' },
            name, run, qc, bench, where);
        tr.addEventListener('click', (ev) => {
            if (ev.target.closest('a')) return;
            if (window.getSelection && String(window.getSelection())) return;   // selecting text is not a click
            location.href = r.href;
        });
        return tr;
    }

    function renderTable() {
        const rows = L.filterRows((data && data.instruments) || [], view);
        const all = ((data && data.instruments) || []).length;
        const now = Date.now();
        $('inst-rows').replaceChildren(...rows.map(r => rowEl(r, now)));
        const none = $('inst-none');
        none.hidden = rows.length > 0 || all === 0;
        if (!rows.length) {
            none.replaceChildren(
                all ? 'No instrument matches this view. ' : '',
                all ? h('a', { href: '/', className: 'link', 'data-view': '' }, 'Show all instruments') : '');
        }
        if (all === 0 && data && data.state === 'ready') {
            none.hidden = false;
            none.replaceChildren('LabCore answered with no instruments. They are added in LabStation › LEM module › New machine…');
        }
        $('maint-note').hidden = view.filter !== 'maintenance';
        const un = $('view-unknown');
        if (un) {
            un.hidden = !unknown;
            $('view-unknown-text').textContent = unknown ? L.unknownViewText(unknown) : '';
        }
        // the Maintenance view is sorted by the column it shows, and says so
        const th = $('col-third');
        const sorted = L.sortedBy(view) === 'third';
        th.replaceChildren(L.thirdColumn(view));
        if (sorted) th.appendChild(h('span', { className: 'sort-ind', 'aria-hidden': 'true', title: 'Soonest first' }, '↑'));
        if (sorted) th.setAttribute('aria-sort', 'ascending'); else th.removeAttribute('aria-sort');
        $('inst-shown').textContent = (rows.length !== all) ? 'Showing ' + rows.length + ' of ' + all : '';
    }

    function render() {
        remember();
        renderUnread();
        renderHead();
        if (!data || data.state !== 'ready') return;
        renderNeeds();
        if (MAP) { if (M()) M().render(data, { failedAt: refreshFailedAt }); return; }
        renderChips();
        renderTable();
    }

    // ── the address bar is the view ───────────────────────────────────────
    function go(next) {
        view = Object.assign({ filter: '', level: '', cause: '' }, next || {});
        unknown = '';
        const url = '/' + L.viewQuery(view);
        if (location.pathname + location.search !== url) history.pushState(view, '', url);
        renderNeeds();
        renderChips();
        renderTable();
    }
    window.addEventListener('popstate', () => {
        if (MAP) { if (M()) M().go(window.LEMPlan.parseMapView(location.search), { push: false }); renderNeeds(); return; }
        view = L.parseView(location.search); unknown = L.unknownView(location.search); renderNeeds(); renderChips(); renderTable();
    });
    // a link to this same list with another view (a merged tile, "+N more",
    // "Show all") changes the view in place instead of reloading the page
    document.addEventListener('click', (ev) => {
        const a = ev.target.closest && ev.target.closest('a[href]');
        if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
        const u = new URL(a.getAttribute('href'), location.href);
        if (u.origin !== location.origin || (u.pathname !== '/' && u.pathname !== '/instruments') || u.hash) return;
        if (!page.contains(a)) return;
        if (MAP) {
            // on the map, a map link (a tile, a level, "Place it") changes the
            // map in place; a link to the list is a real navigation
            if (new URLSearchParams(u.search).get('view') !== 'map' || !M()) return;
            ev.preventDefault();
            M().go(window.LEMPlan.parseMapView(u.search));
            renderNeeds();
            return;
        }
        ev.preventDefault();
        go(L.parseView(u.search));
        $('inst-chips').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });

    // ── live: refetch when an instrument or the record changed ────────────
    let inflight = false;
    function refetch() {
        if (inflight || !window.LEMLive) return;
        inflight = true;
        window.LEMLive.bgFetch('/api/ui/instruments')
            .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(d => { data = d; refreshFailedAt = null; render(); })
            .catch(() => {
                refreshFailedAt = refreshFailedAt || new Date().toISOString();
                renderNeeds();
                if (M()) M().render(data, { failedAt: refreshFailedAt });
            })
            .finally(() => { inflight = false; });
    }
    if (window.LEMLive) {
        let lastAt = data && data.built_at;
        window.LEMLive.subscribe((u) => {
            const changed = u.reset || (u.machines && u.machines.length) ||
                (u.kinds && (u.kinds.includes('machines') || u.kinds.includes('snapshot'))) ||
                (u.snapshot_at && u.snapshot_at !== lastAt);
            if (u.snapshot_at) lastAt = u.snapshot_at;
            if (changed) refetch();
        });
    }
    // times ("Wed 15:04") age on their own; no request
    setInterval(() => { if (!MAP && data && data.state === 'ready' && !document.hidden) renderTable(); }, 60000);

    // ── find ──────────────────────────────────────────────────────────────
    const q = $('find-q');
    const pop = $('find-pop');
    const list = $('find-list');
    const state = $('find-state');
    const note = $('find-note');
    const find = $('find');
    let seq = 0;
    let timer = null;
    let loadTimer = null;
    let active = -1;
    let rowsShown = [];

    function open(on) {
        pop.hidden = !on;
        q.setAttribute('aria-expanded', on ? 'true' : 'false');
        if (!on) { active = -1; q.removeAttribute('aria-activedescendant'); }
    }
    function setActive(i) {
        const items = list.querySelectorAll('[role="option"]');
        if (!items.length) return;
        active = (i + items.length) % items.length;
        items.forEach((el, k) => el.setAttribute('aria-selected', k === active ? 'true' : 'false'));
        q.setAttribute('aria-activedescendant', items[active].id);
        items[active].scrollIntoView({ block: 'nearest' });
    }
    function say(text, kind) {
        state.textContent = text || '';
        state.className = 'find-state' + (kind ? ' ' + kind : '');
        state.hidden = !text;
    }
    function showRows(rows) {
        rowsShown = rows;
        list.replaceChildren(...rows.map((r, i) => h('li', { role: 'option', id: 'find-opt-' + i, 'aria-selected': 'false' },
            h('a', { href: r.href, tabindex: '-1' },
                h('span', { className: 'fk' }, r.kind),
                h('span', { className: 'fl' }, r.label),
                r.detail ? h('span', { className: 'fd' }, r.detail) : null))));
        list.hidden = !rows.length;
    }
    function search(text) {
        const mine = ++seq;
        clearTimeout(loadTimer);
        const started = Date.now();
        loadTimer = setTimeout(() => {
            if (mine === seq && L.showLoading(Date.now() - started)) { say('Searching…', 'busy'); open(true); }
        }, L.LOADING_AFTER_MS);
        fetch('/api/search?q=' + encodeURIComponent(text), { headers: { 'X-LEM-Background': '1' } })
            .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(a => {
                if (mine !== seq) return;
                clearTimeout(loadTimer);
                const rows = L.searchRows(a, hrefFor, { hasQuality });
                showRows(rows);
                say(rows.length ? '' : L.searchEmpty(a, text));
                note.textContent = rows.length || a.state === 'no_match' ? L.searchNote(a) : '';
                open(true);
                if (rows.length) setActive(0);
            })
            .catch(e => {
                if (mine !== seq) return;
                clearTimeout(loadTimer);
                showRows([]);
                say(L.searchFailed(e && e.message), 'err');
                note.textContent = '';
                open(true);
            });
    }
    q.addEventListener('input', () => {
        clearTimeout(timer);
        const text = q.value.trim();
        if (text.length < 2) {
            seq++; clearTimeout(loadTimer);
            showRows([]); note.textContent = '';
            if (text.length === 1) { say('Type two or more letters.'); open(true); } else open(false);
            return;
        }
        timer = setTimeout(() => search(text), 120);
    });
    q.addEventListener('keydown', (ev) => {
        if (ev.key === 'ArrowDown') { ev.preventDefault(); if (pop.hidden && q.value.trim().length >= 2) open(true); setActive(active + 1); }
        else if (ev.key === 'ArrowUp') { ev.preventDefault(); setActive(active - 1); }
        else if (ev.key === 'Enter') {
            const r = rowsShown[active >= 0 ? active : 0];
            if (r) { ev.preventDefault(); pick(r); }
        } else if (ev.key === 'Escape') {
            if (!pop.hidden) { ev.stopPropagation(); open(false); } else { q.value = ''; q.blur(); find.classList.remove('open'); }
        }
    });
    function pick(r) {
        if (r.kind === 'Instrument') S.addRecent({ href: r.href, label: r.label });
        location.href = r.href;
    }
    list.addEventListener('click', (ev) => {
        const li = ev.target.closest('li[role="option"]');
        if (!li) return;
        const r = rowsShown[Number(li.id.replace('find-opt-', ''))];
        if (r && r.kind === 'Instrument') S.addRecent({ href: r.href, label: r.label });
    });
    q.addEventListener('focus', () => { if (q.value.trim().length >= 2 && (rowsShown.length || state.textContent)) open(true); });
    document.addEventListener('click', (ev) => {
        if (!find.contains(ev.target)) { open(false); if (!q.value) find.classList.remove('open'); }
    });
    $('find-open').addEventListener('click', () => { find.classList.add('open'); q.focus(); });
    document.addEventListener('keydown', (ev) => {
        if (!L.isFindKey(ev)) return;
        ev.preventDefault();
        find.classList.add('open');
        q.focus();
        q.select();
    });

    // the map asks for the tiles to be redrawn when its cause changes
    if (MAP) document.addEventListener('lem:map-view', () => renderNeeds());
    render();
})();
