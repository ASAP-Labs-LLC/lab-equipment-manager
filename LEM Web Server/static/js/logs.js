/* logs.js: the /logs page (ia-final §3.6). The machine log, filterable.

   The address bar is the state (§1). Every filter writes the query string
   (pushState for a choice, replaceState while typing) and the list is read
   from it, so a reload, Back, or a pasted link shows exactly this view.
   `equipment=` is the record's spelling ("Open in the full log"); the old
   page's `machine=` still opens the same view.

   States, each its own sentence: reading; a read that failed ("Couldn't
   read the log", never "nothing matches"); a partial answer (the rows, and a
   line that says the list may be incomplete); nothing matches (with Clear);
   nothing recorded at all. "Show older" appends the next page by the
   server's keyset cursor, so rows that share a time are neither skipped nor
   repeated. A row opens the Log entry sheet (log_view.js), and the sheet's
   button opens the instrument's record at #log. */
(function () {
    'use strict';
    const G = window.LEMLog;
    const V = window.LEMLogView;
    const h = V.h;
    const $ = (id) => document.getElementById(id);
    const PAGE = 100;
    const COLS = ['when', 'inst', 'kind', 'lab', 'test', 'value', 'who'];

    let state = G.parseQuery(location.search);
    let shown = [];           // events on screen
    let next = null;          // cursor for "Show older"
    let total = null;
    let seq = 0;              // a slower answer to an older question never paints
    let instruments = [];

    // ── the filter controls mirror `state` ────────────────────────────────
    function paintFilters() {
        $('f-q').value = state.q;
        const sel = $('f-equipment');
        if (state.equipment && !Array.from(sel.options).some(o => o.value === state.equipment)) {
            // an instrument the list has not loaded (or a retired one): kept,
            // named by its uid, never silently dropped from the filter
            sel.appendChild(h('option', { value: state.equipment, text: state.equipment }));
        }
        sel.value = state.equipment;
        for (const b of $('f-kind').querySelectorAll('[data-group]')) {
            b.setAttribute('aria-pressed', String((b.dataset.group === 'all' && !state.kind) || b.dataset.group === state.kind));
        }
        const today = G.localToday();
        const preset = G.presetFor(state.since, state.until, today);
        const custom = preset === 'custom' || $('f-range').dataset.custom === '1';
        $('f-range').value = custom ? 'custom' : preset;
        $('f-dates').hidden = !custom;
        $('f-since').value = state.since;
        $('f-until').value = state.until;
        const any = !!(state.q || state.equipment || state.kind || state.since || state.until);
        $('f-clear').hidden = !any;
        const api = G.apiQuery(state, { limit: 'all' });
        $('export-view').href = '/api/logs.csv' + (api ? '?' + api : '');
    }

    function setState(patch, how) {
        state = Object.assign({}, state, patch);
        const url = location.pathname + G.toQuery(state);
        if (how === 'replace') history.replaceState(null, '', url);
        else history.pushState(null, '', url);
        paintFilters();
        load();
    }

    // ── reading ───────────────────────────────────────────────────────────
    function stateLine(kind, text, opts) {
        const el = $('logs-state');
        el.hidden = kind === 'none';
        el.dataset.state = kind;
        el.querySelector('.glyph').className = 'glyph ' + ({ loading: 'working', error: 'error', empty: 'never' }[kind] || 'never');
        $('logs-state-text').textContent = text || '';
        $('logs-retry').hidden = kind !== 'error';
        $('logs-state-clear').hidden = !(opts && opts.clear);
    }

    function header() {
        const searched = !!state.q;
        const c = G.countLine({ total, shown: shown.length, searched, source: lastSource });
        $('logs-count').textContent = c.count;
        $('logs-source').textContent = c.source;
        const more = !!next;
        $('logs-more-row').hidden = !more;
        $('logs-shown').textContent = more ? 'Showing ' + shown.length.toLocaleString('en-US') +
            (total !== null ? ' of ' + total.toLocaleString('en-US') : '') : '';
    }

    let lastSource = null;
    function paintRows(events, append) {
        const body = $('logs-rows');
        const rows = events.map(e => V.row(e, { cols: COLS }));
        if (append) rows.forEach(r => body.appendChild(r)); else body.replaceChildren(...rows);
    }

    function ask(extra, background) {
        const q = G.apiQuery(state, Object.assign({ limit: PAGE }, extra || {}));
        const url = '/api/logs' + (q ? '?' + q : '');
        const f = background && window.LEMLive ? window.LEMLive.bgFetch(url) : fetch(url, { headers: { Accept: 'application/json' } });
        return f.then(r => r.json().catch(() => ({})).then(b => {
            if (!r.ok) throw Object.assign(new Error('HTTP ' + r.status), { said: b && b.error, status: r.status });
            return b;
        }));
    }

    function load() {
        const my = ++seq;
        // the table keeps what it had until the answer, dimmed; the line
        // says it is reading (after a beat, so a fast answer never flashes)
        $('logs-table').classList.add('is-reading');
        const beat = setTimeout(() => { if (my === seq) stateLine('loading', state.q ? 'Searching every entry for “' + state.q + '”…' : 'Reading the log…'); }, 180);
        return ask().then(b => {
            if (my !== seq) return;
            clearTimeout(beat);
            shown = b.events || [];
            next = b.next || null;
            total = typeof b.total === 'number' ? b.total : null;
            lastSource = b.source || null;
            paintRows(shown, false);
            partial(b.error);
            header();
            if (shown.length) stateLine('none');
            else if (b.error) stateLine('error', 'Couldn\'t read the log: ' + b.error + ' This is not an empty log.');
            else if (state.q || state.equipment || state.kind || state.since || state.until) {
                stateLine('empty', state.q ? 'Nothing anywhere in the record matches “' + state.q + '” with these filters. This searched every entry, not only the recent ones.'
                    : 'Nothing in the log matches these filters.', { clear: true });
            } else stateLine('empty', 'Nothing recorded yet. Benches write here as they read results, and every change made in LEM is kept here too.');
        }).catch(e => {
            if (my !== seq) return;
            clearTimeout(beat);
            shown = []; next = null; total = null;
            paintRows([], false);
            partial(null);
            $('logs-count').textContent = 'The log could not be read';
            $('logs-source').textContent = '';
            $('logs-more-row').hidden = true;
            stateLine('error', 'Couldn\'t read the log' + (e && e.said ? ': ' + e.said : (e && e.status ? ' (LEM answered ' + e.status + ').' : ': LEM did not answer.')) + ' This is not an empty log; nothing here is a statement about the record.');
        }).finally(() => { if (my === seq) $('logs-table').classList.remove('is-reading'); });
    }

    function partial(err) {
        $('logs-partial').hidden = !err;
        $('logs-partial-text').textContent = err || '';
    }

    function older() {
        if (!next) return;
        const btn = $('logs-more');
        btn.disabled = true;
        btn.textContent = 'Reading…';
        const my = seq;
        ask({ before: next }).then(b => {
            if (my !== seq) return;
            const evs = b.events || [];
            shown = shown.concat(evs);
            next = b.next || null;
            paintRows(evs, true);
            header();
        }).catch(e => {
            if (window.LEMShell) window.LEMShell.toast('Couldn\'t read older entries' + (e && e.said ? ': ' + e.said : '') + '. Try again.', 'err');
        }).finally(() => { btn.disabled = false; btn.textContent = 'Show older'; });
    }

    // ── the instrument filter ─────────────────────────────────────────────
    function loadInstruments() {
        fetch('/api/ui/instruments').then(r => r.ok ? r.json() : null).then(d => {
            if (!d || !Array.isArray(d.instruments)) return;
            instruments = d.instruments.map(r => ({ uid: r.uid, title: r.title }))
                .sort((a, b) => a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
            const sel = $('f-equipment');
            sel.replaceChildren(h('option', { value: '', text: 'All instruments' }),
                ...instruments.map(i => h('option', { value: i.uid, text: i.title })));
            paintFilters();
        }).catch(() => { /* the select keeps "All instruments" and any uid from the URL */ });
    }

    // ── wiring ────────────────────────────────────────────────────────────
    $('filters').addEventListener('submit', (ev) => ev.preventDefault());
    let typing = null;
    $('f-q').addEventListener('input', () => {
        clearTimeout(typing);
        typing = setTimeout(() => setState({ q: $('f-q').value.trim() }, 'replace'), 250);
    });
    $('f-equipment').addEventListener('change', () => setState({ equipment: $('f-equipment').value }));
    $('f-kind').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-group]');
        if (!b) return;
        setState({ kind: b.dataset.group === 'all' ? '' : b.dataset.group });
    });
    $('f-range').addEventListener('change', () => {
        const v = $('f-range').value;
        if (v === 'custom') {
            $('f-range').dataset.custom = '1';
            $('f-dates').hidden = false;
            $('f-since').focus();
            return;
        }
        delete $('f-range').dataset.custom;
        setState(G.rangeFor(v, G.localToday()));
    });
    const dateChange = () => setState({ since: $('f-since').value, until: $('f-until').value });
    $('f-since').addEventListener('change', dateChange);
    $('f-until').addEventListener('change', dateChange);
    const clear = () => { delete $('f-range').dataset.custom; setState({ equipment: '', kind: '', since: '', until: '', q: '' }); };
    $('f-clear').addEventListener('click', clear);
    $('logs-state-clear').addEventListener('click', clear);
    $('logs-retry').addEventListener('click', () => load());
    $('logs-more').addEventListener('click', older);
    window.addEventListener('popstate', () => { state = G.parseQuery(location.search); paintFilters(); load(); });

    // the export menu: one button, three files
    const exBtn = $('export-btn'), exMenu = $('export-menu');
    function exportMenu(open) {
        exMenu.hidden = !open;
        exBtn.setAttribute('aria-expanded', String(open));
        if (open) { const first = exMenu.querySelector('a'); if (first) first.focus(); }
    }
    exBtn.addEventListener('click', (ev) => { ev.stopPropagation(); exportMenu(exMenu.hidden); });
    document.addEventListener('click', (ev) => { if (!exMenu.hidden && !ev.target.closest('.export-wrap')) exportMenu(false); });
    exMenu.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') { exportMenu(false); exBtn.focus(); }
    });
    exMenu.addEventListener('click', (ev) => { if (ev.target.closest('a')) exportMenu(false); });

    // the zone stands in the header, once (§4.3)
    const z = G.zoneName();
    if (z) $('th-when').textContent = 'When (' + z + ')';

    // live: a new entry on the first page appears by itself; a list somebody
    // has paged into, a search, or an open sheet is left as it is
    if (window.LEMLive) {
        let lastAsk = 0;
        window.LEMLive.subscribe((u) => {
            const changed = u.reset || (u.machines && u.machines.length);
            if (!changed || document.hidden || state.q) return;
            if (shown.length > PAGE || $('log-sheet').open) return;
            if (Date.now() - lastAsk < 15000) return;
            lastAsk = Date.now();
            const my = seq;
            ask(null, true).then(b => {
                if (my !== seq || shown.length > PAGE) return;
                const evs = b.events || [];
                if (evs.length && shown.length && evs[0].id === shown[0].id && b.total === total) return;
                shown = evs; next = b.next || null;
                total = typeof b.total === 'number' ? b.total : null;
                lastSource = b.source || lastSource;
                paintRows(shown, false);
                partial(b.error);
                header();
                if (shown.length) stateLine('none');
            }).catch(() => { /* the next tick asks again; what is on screen stays */ });
        });
    }

    paintFilters();
    loadInstruments();
    load();
})();
