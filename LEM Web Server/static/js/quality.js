/* quality.js: /quality and /quality/standards (ia-final §3.5), drawn from the
   JSON island #quality-data, and the New standard sheet both views open.

     Latest checks   one table, ONE verdict column (judge J3); a control
                     finding is a caption under the verdict, provisional,
                     only when a rule broke; each row goes to the record's
                     QC section; refetched on the live feed
     Standards       the library: Lab ID, checks, used on, certificate
     New standard    name, Lab ID, a test from LabCore's catalogue,
                     expected / s / units, Check it on (reporting first),
                     Save -> the standard's page

   Every failed read has its own sentence, distinct from "none yet". Every
   server string goes in through textContent. GETs on the live feed only;
   the sheet POSTs when its Save is pressed. */
(function () {
    'use strict';
    const R = window.LEMRecord;
    const Q = window.LEMQuality;
    const S = window.LEMShell;
    const h = S.h;
    const $ = (id) => document.getElementById(id);
    const page = $('main');

    let data = null;
    try { data = JSON.parse(($('quality-data') || {}).textContent || 'null'); } catch (_e) { data = null; }
    if (!data) return;
    const view = data.view;

    const glyph = (kind) => h('span', { className: 'glyph ' + kind, 'aria-hidden': 'true' });
    const VCLASS = { out: 's-not_ok', due: 's-ok_but' };
    const PILL_WORD = { error: 's-not_ok', held: 's-ok_but' };
    function why(e) {
        if (!e || e.name === 'TypeError') return 'LEM did not answer';
        return String(e.message || 'no answer').replace(/[.\s]+$/, '');
    }

    // ── the head (one pill, one count) ────────────────────────────────────
    function renderHead(L) {
        const bits = [];
        if (L && L.state === 'ready' && L.pill) {
            // neutral, as the record's: the colour is the glyph's and the word's
            bits.push(h('span', { className: 'pill q-pill', 'data-testid': 'q-pill' }, glyph(L.pill.glyph),
                h('span', { className: PILL_WORD[L.pill.level] || '', text: L.pill.text })));
        }
        if (L && L.state === 'ready' && L.count) bits.push(h('span', { id: 'q-count', text: L.count }));
        $('q-meta').replaceChildren(...bits);
    }
    function renderUnread(L, card) {
        const ready = L && L.state === 'ready';
        $('q-unread').hidden = ready;
        card.hidden = !ready;
        if (ready) return;
        // the server drew the sentence for the first paint; a live answer
        // that changes state redraws it in the same words
        const bad = L && L.state === 'unreadable';
        $('q-unread').firstElementChild.className = 'glyph ' + (bad ? 'error' : 'dashed');
        $('q-unread-head').textContent = bad ? (view === 'latest' ? 'No checks to show yet.' : 'Couldn\'t read the QC library.') : 'Not read yet.';
        $('q-unread-text').textContent = bad
            ? (view === 'latest' ? 'This is not a lab with no QC: LEM has not been able to read it. The table appears by itself as soon as it can.'
                : 'This is not an empty library: ' + (L.error || 'the store did not answer') + '. Reload to try again.')
            : 'LEM reads every instrument within a few seconds of starting. The table appears by itself.';
    }

    // ── Latest checks ─────────────────────────────────────────────────────
    function track(r) {
        const pos = R.bandPos(r, r.value);
        if (!pos) return h('span', { className: 'track none', 'aria-hidden': 'true' });
        const el = h('span', { className: 'track', 'aria-hidden': 'true' },
            h('i', { className: 'band', style: 'left:' + pos.lowPct + '%;width:' + (pos.highPct - pos.lowPct) + '%' }),
            h('i', { className: 'mid', style: 'left:' + pos.midPct + '%' }));
        if (pos.valPct !== null) {
            const past = r.verdict.key === 'none' ? ' past' : '';
            el.appendChild(h('i', { className: 'dot' + (pos.outside ? ' out ' + pos.outside : past), style: 'left:' + pos.valPct + '%' }));
        }
        return el;
    }
    function bandCell(r) {
        if (r.low === null && r.high === null) return h('span', { className: 'muted', text: 'No certified values' });
        const f = (v) => R.fmtQC(v, r);
        return h('span', { className: 'bandtxt' }, f(r.low) + ' – ', h('b', { text: f(r.expected) }), ' – ' + f(r.high) + (r.units ? ' ' + r.units : ''));
    }
    function latestRow(r) {
        // what the When column and the legend already say is not said again
        // ("last passed 3 Aug" beside a When of 3 Aug; the lab's 24 h)
        const v = Object.assign({}, r.verdict, { detail: R.rowDetail(r.verdict.detail, { hours: data.latest.window }, r.at) });
        const std = r.standard;
        const last = r.value === null || r.value === undefined ? '—' : R.fmtQC(r.value, r);
        const verdict = h('td', { className: 'c-verdict' },
            h('span', { className: 'verdict ' + (v.glyph === 'never' ? '' : (VCLASS[v.key] || '')) }, glyph(v.glyph), h('span', { text: v.word })),
            v.detail ? h('span', { className: 'sub', text: v.detail }) : null,
            // the provisional control finding: a caption, never a verdict
            r.control ? h('span', { className: 'sub ctl', 'data-testid': 'q-control' }, h('b', { text: 'Control ' }), r.control.words) : null);
        const tr = h('tr', { className: 'qxrow' + (v.key === 'none' ? ' is-past' : ''), 'data-uid': r.uid, 'data-testid': 'q-row' },
            h('td', { className: 'c-inst' }, h('a', { className: 'iname', href: r.href, text: r.title }),
                h('span', { className: 'fold-std sub', text: std ? std.name : '' })),
            h('td', { className: 'c-check' }, h('span', { className: 'ck', text: r.check }), r.method ? h('span', { className: 'sub', text: r.method }) : null,
                h('span', { className: 'fold-band' }, h('span', { className: 'fold-last', text: 'Last ' + last + (r.at ? ' · ' + R.stamp(r.at, undefined, true) : '') }), bandCell(r), track(r))),
            // the standard by name; its Lab ID is on its page, said once
            h('td', { className: 'c-std' }, std ? h('a', { className: 'stdlink', href: std.href, text: std.name, title: 'Lab ID ' + std.lab_id }) : h('span', { className: 'muted', text: '—' })),
            h('td', { className: 'c-last num', text: last }),
            h('td', { className: 'c-band' }, bandCell(r)),
            h('td', { className: 'c-track' }, track(r)),
            verdict,
            h('td', { className: 'c-when', text: r.at ? R.stamp(r.at) : '' }));
        // the whole row is the target; the name is the real link
        tr.addEventListener('click', (ev) => {
            if (ev.target.closest('a,button')) return;
            if (window.getSelection && String(window.getSelection())) return;
            location.href = r.href;
        });
        return tr;
    }
    function renderLatest() {
        const L = data.latest;
        renderHead(L);
        renderUnread(L, $('q-latest'));
        if (!L || L.state !== 'ready') return;
        const rows = L.rows || [];
        $('q-tbl').hidden = !rows.length;
        $('q-empty').hidden = !!rows.length;
        $('q-legend-note').textContent = L.legend_note || '';
        $('q-rows').replaceChildren(...rows.map(latestRow));
    }

    // live: refetch when an instrument or the snapshot changed. A refresh
    // that fails is said, never swallowed: the table keeps what it last
    // read, dimmed, under a line that says so, and retries.
    let inflight = false, readAt = Date.now(), failed = null, retry = null;
    function showStale() {
        const line = $('q-stale');
        if (!line) return;
        $('q-latest').classList.toggle('is-stale', !!failed);
        line.hidden = !failed;
        if (failed) {
            const d = new Date(readAt);
            const at = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
            $('q-stale-text').textContent = 'Couldn\'t refresh (' + failed + '). Shown as of ' + at + '; it may be out of date.';
        }
    }
    function refetch() {
        if (inflight || !window.LEMLive || view !== 'latest') return;
        inflight = true;
        window.LEMLive.bgFetch('/api/ui/quality')
            .then(r => r.ok ? r.json() : r.json().catch(() => ({})).then(b => { throw new Error((b && b.error) || ('HTTP ' + r.status)); }))
            .then(d => {
                data.latest = d; readAt = Date.now(); failed = null;
                if (retry) { clearInterval(retry); retry = null; }
                renderLatest(); showStale();
            })
            .catch(e => {
                failed = why(e);
                if (!retry) retry = setInterval(() => { if (!document.hidden) refetch(); }, 20000);
                showStale();
            })
            .finally(() => { inflight = false; });
    }
    if (view === 'latest') {
        $('q-stale-retry').addEventListener('click', refetch);
        if (window.LEMLive) {
            let lastAt = data.latest && data.latest.built_at;
            window.LEMLive.subscribe((u) => {
                const changed = u.reset || (u.machines && u.machines.length) || (u.snapshot_at && u.snapshot_at !== lastAt);
                if (u.snapshot_at) lastAt = u.snapshot_at;
                if (changed) refetch();
            });
        }
        // "Wed 15:04" ages on its own; no request
        setInterval(() => { if (!document.hidden) renderLatest(); }, 60000);
    }

    // ── Standards ─────────────────────────────────────────────────────────
    function standardRow(r) {
        const c = r.certificate;
        const checks = r.checks.slice(0, 3).join(', ') + (r.checks.length > 3 ? ' and ' + (r.checks.length - 3) + ' more' : '');
        const tr = h('tr', { className: 'srow', 'data-testid': 's-row' },
            // the phone shows the name, Used on and the certificate; the
            // Lab ID and the checks fold under the name rather than go
            h('td', { className: 'c-name' }, h('a', { className: 'iname', href: r.href, text: r.name }),
                h('span', { className: 'sub fold-qs', text: 'Lab ID ' + r.lab_id + ' · ' + r.n_checks + (r.n_checks === 1 ? ' check' : ' checks') + (checks ? ': ' + checks : '') })),
            h('td', { className: 'c-lab mono', text: r.lab_id }),
            h('td', { className: 'c-checks' }, h('span', { text: r.n_checks + (r.n_checks === 1 ? ' check' : ' checks') }), checks ? h('span', { className: 'sub', text: checks }) : null),
            h('td', { className: 'c-used', text: r.used_on ? r.used_on + (r.used_on === 1 ? ' instrument' : ' instruments') : 'Not in use' }),
            h('td', { className: 'c-cert' }, h('span', { className: 'verdict ' + (c.key === 'uploaded' ? '' : c.key === 'unread' ? '' : 's-ok_but') }, glyph(c.glyph), h('span', { text: c.word })),
                c.detail ? h('span', { className: 'sub', text: c.detail }) : null));
        tr.addEventListener('click', (ev) => {
            if (ev.target.closest('a,button')) return;
            if (window.getSelection && String(window.getSelection())) return;
            location.href = r.href;
        });
        return tr;
    }
    function renderStandards() {
        const L = data.standards;
        renderHead(L);
        renderUnread(L, $('q-standards'));
        if (!L || L.state !== 'ready') return;
        const rows = L.rows || [];
        $('s-tbl').hidden = !rows.length;
        $('s-empty').hidden = !!rows.length;
        $('s-rows').replaceChildren(...rows.map(standardRow));
    }

    // ── New standard ──────────────────────────────────────────────────────
    const sheet = $('new-sheet');
    let catalogue = null;     // {state:'loading'|'ok'|'err', tests, error}
    let reporting = null;     // {state, tests, error}
    let library = null;       // [{name, sample_id_val}] as read, or null
    let picked = '';          // the catalogue name picked, '' until one is
    let chosen = new Set();
    let active = -1;          // the highlighted option in the type-ahead

    function getJSON(url) {
        return fetch(url, { headers: { 'X-LEM-Background': '1' } })
            .then(r => r.json().catch(() => ({})).then(b => {
                if (!r.ok || b.error) throw new Error(b.error || ('LEM answered ' + r.status));
                return b;
            }));
    }
    function loadCatalogue() {
        catalogue = { state: 'loading' };
        renderTestState();
        getJSON('/api/test-names')
            .then(b => { catalogue = { state: 'ok', tests: b.tests || [] }; })
            .catch(e => { catalogue = { state: 'err', error: why(e) }; })
            .finally(() => { renderTestState(); renderList(); renderSum(); });
    }
    function loadReporting() {
        reporting = { state: 'loading' };
        getJSON('/api/ui/quality/reporting')
            .then(b => { reporting = { state: 'ok', tests: b.tests || {} }; })
            .catch(e => { reporting = { state: 'err', error: why(e) }; })
            .finally(renderChips);
    }
    function loadLibrary() {
        // for the duplicate check before Save (the server checks again)
        getJSON('/api/qc-samples').then(b => { library = b.samples || []; }).catch(() => { library = null; });
    }
    function renderTestState() {
        const st = $('new-test-state');
        st.className = 'ta-state caption' + (catalogue && catalogue.state === 'err' ? ' err' : '');
        st.replaceChildren();
        if (!catalogue || catalogue.state === 'loading') { st.textContent = 'Reading LabCore\'s list of tests…'; return; }
        if (catalogue.state === 'err') {
            st.append(h('span', { text: 'Couldn\'t read LabCore\'s list of tests: ' + catalogue.error + '. A test can only be picked from it, so nothing can be saved yet. ' }),
                h('button', { type: 'button', className: 'linkbtn', onclick: loadCatalogue, text: 'Try again' }));
            return;
        }
        if (!catalogue.tests.length) { st.textContent = 'LabCore answered with no tests, so there is nothing to pick. Tests are set up in LabCore.'; return; }
        if (picked) { st.textContent = ''; return; }
        st.textContent = $('new-test').value.trim() && !Q.matchTests(catalogue.tests, $('new-test').value).length
            ? 'No test in LabCore matches “' + $('new-test').value.trim() + '”.' : '';
    }
    function renderList() {
        const list = $('new-test-list');
        const input = $('new-test');
        const q = input.value;
        const hits = (catalogue && catalogue.state === 'ok' && !picked) ? Q.matchTests(catalogue.tests, q) : [];
        list.hidden = !hits.length;
        input.setAttribute('aria-expanded', String(!!hits.length));
        if (active >= hits.length) active = hits.length - 1;
        list.replaceChildren(...hits.map((t, i) => {
            const [title, method] = R.shortTest(t);
            return h('li', { role: 'option', id: 'new-opt-' + i, 'aria-selected': String(i === active), 'data-test': t, 'data-testid': 'new-test-option' },
                h('span', { className: 'o-title', text: title }), method ? h('span', { className: 'o-method', text: method }) : null);
        }));
        if (active >= 0) input.setAttribute('aria-activedescendant', 'new-opt-' + active); else input.removeAttribute('aria-activedescendant');
    }
    function pick(t) {
        picked = t;
        $('new-test').value = t;
        active = -1;
        renderList(); renderTestState(); renderChips(); renderSum();
        $('new-exp').focus();
    }
    $('new-test').addEventListener('input', () => {
        // typing again un-picks: a name typed is not a name picked
        picked = '';
        active = -1;
        renderList(); renderTestState(); renderChips(); renderSum();
    });
    $('new-test').addEventListener('keydown', (ev) => {
        const opts = [...$('new-test-list').querySelectorAll('li')];
        if (ev.key === 'ArrowDown' && opts.length) { ev.preventDefault(); active = Math.min(opts.length - 1, active + 1); renderList(); }
        else if (ev.key === 'ArrowUp' && opts.length) { ev.preventDefault(); active = Math.max(0, active - 1); renderList(); }
        else if (ev.key === 'Enter' && opts.length && !picked) { ev.preventDefault(); pick(opts[Math.max(0, active)].dataset.test); }
        else if (ev.key === 'Escape' && opts.length) { ev.preventDefault(); ev.stopPropagation(); $('new-test-list').hidden = true; }
    });
    $('new-test-list').addEventListener('mousedown', (ev) => ev.preventDefault());   // keep focus in the field
    $('new-test-list').addEventListener('click', (ev) => {
        const li = ev.target.closest('li[data-test]');
        if (li) pick(li.dataset.test);
    });

    function instruments() { return data.instruments || []; }
    function renderChips() {
        const box = $('new-chips');
        const note = $('new-chip-note');
        const all = instruments();
        if (data.instruments === null || data.instruments === undefined) {
            box.replaceChildren();
            note.textContent = 'LEM has not read its instruments yet, so none can be picked. Save the standard, then add it to instruments from its page.';
            return;
        }
        if (!all.length) { box.replaceChildren(); note.textContent = 'There are no instruments in LEM yet. Instruments are added in LabStation\'s LEM module.'; return; }
        const test = picked;
        const ord = Q.chipOrder(all, reporting && reporting.state === 'ok' ? reporting.tests : null, test);
        const chip = (m) => h('button', { type: 'button', className: 'chip', 'aria-pressed': String(chosen.has(m.uid)), 'data-uid': m.uid, 'data-testid': 'new-chip' }, m.title);
        const kids = [];
        if (test && ord.first.length) {
            kids.push(h('span', { className: 'chip-group', text: 'Report this test' }), ...ord.first.map(chip),
                h('span', { className: 'chip-group', text: 'Others' }));
        }
        kids.push(...ord.rest.map(chip));
        box.replaceChildren(...kids);
        if (!test) note.textContent = 'Pick the test first: the instruments that already report it are listed first.';
        else if (reporting && reporting.state === 'err') note.textContent = 'Couldn\'t read which instruments report this test (' + reporting.error + '), so all are listed by name.';
        else if (reporting && reporting.state === 'loading') note.textContent = 'Reading which instruments report this test…';
        else if (!ord.first.length) note.textContent = 'No instrument has reported this test yet.';
        else note.textContent = '';
    }
    $('new-chips').addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-uid]');
        if (!b) return;
        const u = b.dataset.uid;
        if (chosen.has(u)) chosen.delete(u); else chosen.add(u);
        b.setAttribute('aria-pressed', String(chosen.has(u)));
        $('new-err').hidden = true;
        renderSum();
    });
    function fields() {
        return { name: $('new-name').value, labId: $('new-lab').value, test: picked, picked: !!picked,
                 expected: $('new-exp').value, sd: $('new-sd').value, units: $('new-units').value,
                 k: $('new-k').value, hours: $('new-hours').value };
    }
    function renderSum() {
        const f = fields();
        const band = Q.bandPreview(f);
        $('new-band').textContent = band;
        const k = f.k.trim() || '2';
        const hrs = f.hours.trim();
        // one line: the band it will judge by, the rule, the window
        $('new-rule').textContent = (band ? ' · expected ± ' : 'Passes inside expected ± ') + k + ' s · a pass counts for ' + (hrs || $('new-hours').dataset.defaultHours) + ' h';
        const titles = instruments().filter(m => chosen.has(m.uid)).map(m => m.title);
        $('new-sum').textContent = Q.newSentence(f, titles);
        $('new-go').disabled = !(catalogue && catalogue.state === 'ok');
    }
    for (const id of ['new-name', 'new-lab', 'new-exp', 'new-sd', 'new-units', 'new-k', 'new-hours']) {
        $(id).addEventListener('input', () => { $('new-err').hidden = true; renderSum(); });
    }
    $('new-more').addEventListener('click', () => {
        const box = $('new-more-fields');
        box.hidden = !box.hidden;
        $('new-more').setAttribute('aria-expanded', String(!box.hidden));
        $('new-more').textContent = box.hidden ? 'Change' : 'Use the defaults';
        if (box.hidden) { $('new-k').value = '2'; $('new-hours').value = ''; renderSum(); } else $('new-k').focus();
    });
    function openNew() {
        $('new-form').reset();
        picked = ''; chosen = new Set(); active = -1;
        $('new-more-fields').hidden = true;
        $('new-more').textContent = 'Change';
        $('new-more').setAttribute('aria-expanded', 'false');
        $('new-err').hidden = true;
        if (!sheet.open) sheet.showModal();
        loadCatalogue(); loadReporting(); loadLibrary();
        renderList(); renderChips(); renderSum();
        $('new-name').focus();
    }
    sheet.addEventListener('click', (ev) => { if (ev.target.closest('[data-close]')) sheet.close(); });
    $('new-form').addEventListener('submit', (ev) => {
        ev.preventDefault();
        const f = fields();
        const msg = !(catalogue && catalogue.state === 'ok') ? 'LabCore\'s list of tests has not been read, so nothing can be saved.' : Q.newProblem(f, library);
        if (msg) { $('new-err').textContent = msg; $('new-err').hidden = false; return; }
        const go = $('new-go');
        go.disabled = true;
        $('new-err').hidden = true;
        const body = { name: f.name, lab_id: f.labId, instruments: [...chosen],
            test: { name: f.test, expected: f.expected, std_dev: f.sd, units: f.units.trim(), k: f.k.trim() || 2, qc_expire_hours: f.hours.trim() || 0 } };
        fetch('/api/qc-samples/new', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then(r => r.json().catch(() => ({})).then(b => {
                if (!r.ok || b.error || !b.ok) throw new Error(b.error || ('LEM answered ' + r.status + ' and did not save it.'));
                return b;
            }))
            .then(b => {
                // the landing page says what landed; a check that could not
                // be assigned is said there too, never dropped
                const who = window.LEMSignIn ? window.LEMSignIn.user() : '';
                const failedTxt = (b.failed || []).length ? ' Not checked on ' + b.failed.map(x => x.uid).join(', ') + ' yet: add it from Used on.' : '';
                try { sessionStorage.setItem('lem.toast', b.name + ' saved' + (who ? ' · ' + who : '') + '.' + failedTxt); } catch (_e) { /* the page still says it */ }
                location.href = b.href;
            })
            .catch(e => { $('new-err').textContent = (e && e.message) || 'It was not saved.'; $('new-err').hidden = false; go.disabled = false; });
    });

    document.addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-act="new"]');
        if (!b) return;
        if (window.LEMSignIn) window.LEMSignIn.need(b.dataset.gated || '', openNew); else openNew();
    });
    // /quality?new=1 (the record's empty-library link) opens the sheet
    if (/[?&]new=1\b/.test(location.search)) {
        if (window.LEMSignIn) window.LEMSignIn.need('add a QC standard', openNew); else openNew();
    }

    // Trends draws itself (quality_trends.js); this file keeps its New standard sheet
    if (view === 'latest') renderLatest(); else if (view === 'standards') renderStandards();
})();
