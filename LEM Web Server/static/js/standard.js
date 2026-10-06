/* standard.js: one QC standard (ia-final §3.5), drawn from #standard-data.

     the head      one pill (glyph + word), the Lab ID, how many checks
     Used on       the card: every instrument checked on it, one line each
                   ("Agilent GC 2 · Flash Point · No verdict yet"), worst
                   first, each a link to the record's QC; the page's one
                   primary, "Add to instruments…"
     sections      Certified values · Certificate (file only) · Changeover
                   · Rename · Delete (refused while a certificate is held
                   or it is in use)

   Every failed read has its own sentence. Every server string goes in
   through textContent. The sheets POST when their button is pressed; a
   refusal keeps the sheet open with the server's sentence. */
(function () {
    'use strict';
    const R = window.LEMRecord;
    const Q = window.LEMQuality;
    const S = window.LEMShell;
    const h = S.h;
    const $ = (id) => document.getElementById(id);
    const page = $('main');

    let data = null;
    try { data = JSON.parse(($('standard-data') || {}).textContent || 'null'); } catch (_e) { data = null; }
    if (!data || data.state !== 'ready') return;

    S.addRecent({ href: data.href, label: data.name });
    try {
        const t = sessionStorage.getItem('lem.toast');
        if (t) { sessionStorage.removeItem('lem.toast'); S.toast(t); }
    } catch (_e) { /* no storage: nothing was handed over */ }

    const glyph = (kind) => h('span', { className: 'glyph ' + kind, 'aria-hidden': 'true' });
    const VCLASS = { out: 's-not_ok', due: 's-ok_but' };
    const who = () => (window.LEMSignIn ? window.LEMSignIn.user() : '');
    function hm() { const d = new Date(); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
    function why(e) {
        if (!e || e.name === 'TypeError') return 'LEM did not answer';
        return String(e.message || 'no answer').replace(/[.\s]+$/, '');
    }

    // ── the head ──────────────────────────────────────────────────────────
    function renderHead() {
        const hd = data.head;
        const sep = () => h('span', { className: 'sep', 'aria-hidden': 'true' }, '·');
        const bits = [h('span', { className: 'pill q-pill', 'data-testid': 'std-pill' }, glyph(hd.pill.glyph),
                h('span', { className: { error: 's-not_ok', held: 's-ok_but' }[hd.pill.level] || '', text: hd.pill.text })),
            sep(), h('span', {}, 'Lab ID ', h('span', { className: 'mono', text: hd.lab_id })),
            sep(), h('span', { text: hd.n_checks + (hd.n_checks === 1 ? ' check' : ' checks') })];
        if (hd.used_on && hd.pill.text.indexOf('In use') !== 0) bits.push(sep(), h('span', { text: 'used on ' + hd.used_on + (hd.used_on === 1 ? ' instrument' : ' instruments') }));
        $('std-meta').replaceChildren(...bits);
    }

    // ── Used on ───────────────────────────────────────────────────────────
    function detail(r) {
        const parts = [];
        // the When already says when it last passed; the lab's 24 h goes without saying here
        const d = R.rowDetail(r.verdict.detail, { hours: 24 }, r.at);
        if (d) parts.push(d);
        if (r.value !== null && r.value !== undefined) parts.push('last ' + R.fmtQC(r.value, r) + (r.units ? ' ' + r.units : ''));
        if (r.at) parts.push(R.stamp(r.at, undefined, true));
        return parts.join(' · ');
    }
    function renderUsedOn() {
        const u = data.used_on;
        $('uon-cap').textContent = u.caption;
        const rows = u.rows || [];
        $('uon-list').hidden = !rows.length;
        $('uon-none').hidden = !!rows.length;
        if (u.rows === null) {
            $('uon-none').replaceChildren(glyph('error'), h('span', { text: 'Couldn\'t read which instruments are checked on it. This is not "none": reload to try again.' }));
        } else if (!rows.length) {
            $('uon-none').replaceChildren(glyph('never'), h('span', { text: 'Not checked on any instrument yet. Add it to the instruments that run it; each reads No verdict yet until its first run.' }));
        }
        $('uon-list').replaceChildren(...rows.map(r => {
            const v = r.verdict;
            const d = detail(r);
            return h('li', {}, h('a', { className: 'uon-row', href: r.href, 'data-testid': 'uon-row', 'aria-label': r.line + (d ? ' · ' + d : '') },
                glyph(v.glyph),
                h('span', { className: 'uon-line' }, h('b', { text: r.title }), ' · ', h('span', { text: r.check }), ' · ',
                    h('span', { className: 'uon-word ' + (v.glyph === 'never' ? '' : (VCLASS[v.key] || '')), text: v.word })),
                r.method ? h('span', { className: 'uon-method', text: r.method }) : h('span', { className: 'uon-method' }),
                h('span', { className: 'uon-detail', text: d })));
        }));
        // the primary needs something to add: no checks, no door
        $('uon-add').hidden = !data.values.length;
    }

    // ── Certified values ──────────────────────────────────────────────────
    function renderValues() {
        const vals = data.values;
        const body = $('val-body');
        if (!vals.length) {
            body.replaceChildren(h('p', { className: 'empty-note' }, glyph('never'), h('span', { text: 'It certifies no test yet, so nothing can be checked on it.' })));
            return;
        }
        const f = (v, t) => R.fmtQC(v, t);
        const band = (t) => h('span', { className: 'bandtxt' }, f(t.low, t) + ' – ', h('b', { text: f(t.expected, t) }), ' – ' + f(t.high, t) + (t.units ? ' ' + t.units : ''));
        const win = (t) => t.window.replace(' (the lab default)', '');
        const dflt = (t) => / \(the lab default\)$/.test(t.window);
        const used = (t) => t.used_on ? t.used_on + (t.used_on === 1 ? ' instrument' : ' instruments') : 'None yet';
        // The narrow screens fold, they never drop (§6, §9.1 rule 6). Below
        // 1100 px the band column goes and the band is said again under the
        // test's name, as the record and /quality say theirs; below 700 the
        // s, k, Window and Used on columns go too and their words follow as
        // one quiet line. Round 1 hid the band and drew nothing in its place:
        // the page lost the very numbers it exists to hold.
        const fold = (t) => h('span', { className: 'fold-band' }, band(t),
            h('span', { className: 'fold-std', text: 's ' + t.std_dev + ' · k ' + t.k + ' · ' + win(t) + (dflt(t) ? ' (default)' : '') + ' · used on ' + used(t).toLowerCase() }));
        body.replaceChildren(h('div', { className: 'card table-card' }, h('table', { className: 'tbl qc qv' },
            h('thead', {}, h('tr', {}, ...[['Test', 'c-check'], ['min – target – max', 'c-band'], ['s', 'num c-sk'], ['k', 'num c-sk'], ['Window', 'c-win'], ['Used on', 'c-used']]
                .map(([t, cls]) => h('th', { scope: 'col', className: cls }, t === 'min – target – max' ? h('span', {}, 'min – ', h('b', { text: 'target' }), ' – max') : t)))),
            h('tbody', {}, ...vals.map(t => h('tr', {},
                h('td', { className: 'c-check' }, h('b', { text: t.check }), t.method ? h('span', { className: 'sub', text: t.method }) : null, fold(t)),
                h('td', { className: 'c-band' }, band(t)),
                h('td', { className: 'num c-sk', text: String(t.std_dev) }),
                h('td', { className: 'num c-sk', text: String(t.k) }),
                // "24 h (the lab default)" said once per row would be the
                // same words seven times: the word, then a quiet "default"
                h('td', { className: 'c-win' }, win(t), dflt(t) ? h('span', { className: 'dflt', text: ' · default' }) : null),
                h('td', { className: 'c-used', text: used(t) })))))));
    }

    // ── Certificate (file only) ───────────────────────────────────────────
    const ST = { valid: ['In date', 'final'], expiring: ['Expiring', 'half'], expired: ['Expired', 'half'], none: ['No expiry', 'final'] };
    function renderCert() {
        const body = $('cert-body');
        const rows = data.certificates.rows;
        const up = h('button', { type: 'button', className: 'btn btn-sm gate-lock', 'data-act': 'upload', 'data-gated': 'upload a certificate', 'data-testid': 'cert-upload' }, 'Upload…');
        if (rows === null) {
            body.replaceChildren(h('p', { className: 'empty-note err' }, glyph('error'), h('span', { text: 'Couldn\'t read its certificates. This is not "none on file": reload to try again.' })));
            return;
        }
        if (!rows.length) {
            body.replaceChildren(h('p', { className: 'empty-note' }, glyph('half'), h('span', { text: 'No certificate on file. The expiry report lists this standard until one is uploaded.' })),
                h('div', { className: 'row sec-actions' }, up));
            return;
        }
        body.replaceChildren(h('ul', { className: 'cert-list' }, ...rows.map(c => {
            const [word, g] = c.current ? ['Current', 'final'] : (ST[c.status] || ['', 'never']);
            return h('li', { className: 'cert-row' + (c.current ? ' is-current' : ''), 'data-testid': 'cert-row' },
                h('span', { className: 'ico ico-doc', 'aria-hidden': 'true' }),
                h('span', { className: 'cert-main' }, h('a', { className: 'link', href: c.href, download: '', text: c.filename }),
                    h('span', { className: 'sub', text: Q.certLine(c) })),
                h('span', { className: 'verdict ' + (g === 'half' ? 's-ok_but' : '') }, glyph(g), h('span', { text: word })),
                h('button', { type: 'button', className: 'linkbtn', 'data-act': 'cert-del', 'data-uid': c.uid, 'data-name': c.filename, 'data-gated': 'remove a certificate' }, 'Remove…'));
        })), h('div', { className: 'row sec-actions' }, up));
    }

    // ── Delete ────────────────────────────────────────────────────────────
    function renderDelete() {
        const b = data.delete.blocked;
        $('del-body').replaceChildren(b
            ? h('p', { className: 'empty-note', 'data-testid': 'del-blocked' }, glyph('never'), h('span', { text: b }))
            : h('div', { className: 'row sec-actions' }, h('button', { type: 'button', className: 'btn btn-sm btn-danger gate-lock', 'data-act': 'delete', 'data-gated': 'delete this standard', 'data-testid': 'del-open' }, 'Delete…')));
    }

    // ── the sheets ────────────────────────────────────────────────────────
    function openSheet(id) { const d = $(id); if (!d.open) d.showModal(); }
    for (const d of document.querySelectorAll('dialog.rec-sheet')) {
        d.addEventListener('click', (ev) => { if (ev.target.closest('[data-close]')) d.close(); });
    }
    function send(method, url, body, isForm) {
        const opts = { method, headers: isForm ? {} : { 'Content-Type': 'application/json' }, body: isForm ? body : JSON.stringify(body) };
        return fetch(url, opts).then(r => r.json().catch(() => ({})).then(b => {
            if (!r.ok || b.error || b.ok === false) throw new Error(b.error || ('LEM answered ' + r.status + ' and did not save it.'));
            return b;
        }));
    }
    function wire(form, err, go, check, run) {
        form.addEventListener('submit', (ev) => {
            ev.preventDefault();
            const msg = check();
            if (msg) { err.textContent = msg; err.hidden = false; return; }
            go.disabled = true;
            err.hidden = true;
            run().catch(e => { err.textContent = (e && e.message) || 'It was not saved.'; err.hidden = false; })
                .finally(() => { go.disabled = false; });
        });
    }
    function handOff(text, href) {
        try { sessionStorage.setItem('lem.toast', text); } catch (_e) { /* the next page still says what is true */ }
        location.href = href;
    }

    // Check it on (the primary): one check at a time, reporting first
    let reporting = null;
    let asChosen = new Set(), asBefore = [];
    function instruments() { return data.instruments || []; }
    function asTest() { return $('as-test').value; }
    function holders(test) {
        return [...new Set((data.used_on.rows || []).filter(r => Q.norm(r.test) === Q.norm(test)).map(r => r.uid))];
    }
    function renderAssign() {
        const test = asTest();
        const all = instruments();
        const box = $('as-chips'), note = $('as-chip-note');
        // an assigned instrument LEM no longer knows is still offered, so it can be taken off
        const known = new Set(all.map(m => m.uid));
        const extra = asBefore.filter(u => !known.has(u)).map(u => ({ uid: u, title: u }));
        const ord = Q.chipOrder(all.concat(extra), reporting && reporting.state === 'ok' ? reporting.tests : null, test);
        const chip = (m) => h('button', { type: 'button', className: 'chip', 'aria-pressed': String(asChosen.has(m.uid)), 'data-uid': m.uid, 'data-testid': 'as-chip' }, m.title);
        const kids = [];
        if (ord.first.length) kids.push(h('span', { className: 'chip-group', text: 'Report this test' }), ...ord.first.map(chip), h('span', { className: 'chip-group', text: 'Others' }));
        kids.push(...ord.rest.map(chip));
        box.replaceChildren(...kids);
        if (data.instruments === null) note.textContent = 'LEM has not read its instruments yet; only those already checked on it are shown.';
        else if (reporting && reporting.state === 'err') note.textContent = 'Couldn\'t read which instruments report this test (' + reporting.error + '), so all are listed by name.';
        else if (reporting && reporting.state === 'loading') note.textContent = 'Reading which instruments report this test…';
        else note.textContent = '';
        const titles = Object.fromEntries(all.concat(extra).map(m => [m.uid, m.title]));
        $('as-sum').textContent = Q.assignChange(asBefore, [...asChosen], titles);
    }
    function resetAssign() {
        asBefore = holders(asTest());
        asChosen = new Set(asBefore);
        $('as-err').hidden = true;
        renderAssign();
    }
    $('as-test').addEventListener('change', resetAssign);
    $('as-chips').addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-uid]');
        if (!b) return;
        const u = b.dataset.uid;
        if (asChosen.has(u)) asChosen.delete(u); else asChosen.add(u);
        $('as-err').hidden = true;
        renderAssign();
    });
    function sheetAssign() {
        const sel = $('as-test');
        sel.replaceChildren(...data.values.map(t => h('option', { value: t.test, text: t.check + (t.method ? ' · ' + t.method : '') })));
        $('as-test-field').hidden = data.values.length < 2;
        $('as-title').textContent = 'Check ' + data.name + ' on instruments';
        openSheet('assign-sheet');
        resetAssign();
        if (!reporting || reporting.state === 'err') {
            reporting = { state: 'loading' };
            fetch('/api/ui/quality/reporting', { headers: { 'X-LEM-Background': '1' } })
                .then(r => r.json().catch(() => ({})).then(b => { if (!r.ok || b.error) throw new Error(b.error || ('LEM answered ' + r.status)); return b; }))
                .then(b => { reporting = { state: 'ok', tests: b.tests || {} }; })
                .catch(e => { reporting = { state: 'err', error: why(e) }; })
                .finally(renderAssign);
        }
    }
    wire($('as-form'), $('as-err'), $('as-go'),
        () => Q.assignChange(asBefore, [...asChosen], {}) === 'Nothing has changed.' ? 'Nothing has changed.' : '',
        () => send('POST', '/api/qc-samples/assign', { name: data.name, test: asTest(), instruments: [...asChosen] }).then(b => {
            $('assign-sheet').close();
            const n = (b.added || []).length, m = (b.removed || []).length;
            S.toast([n ? 'Added to ' + n + (n === 1 ? ' instrument' : ' instruments') : '', m ? 'taken off ' + m : ''].filter(Boolean).join(', ') + ' · ' + who() + ' · ' + hm());
            refetch();
        }));

    // upload a certificate
    function sheetUpload() {
        $('up-form').reset();
        $('up-err').hidden = true;
        openSheet('up-sheet');
    }
    wire($('up-form'), $('up-err'), $('up-go'),
        () => ($('up-file').files && $('up-file').files.length) ? '' : 'Choose the certificate file.',
        () => {
            const fd = new FormData();
            fd.append('standard', data.name);
            fd.append('file', $('up-file').files[0]);
            if ($('up-issued').value) fd.append('issued_at', $('up-issued').value);
            if ($('up-expires').value) fd.append('expires_at', $('up-expires').value);
            return send('POST', '/api/qc-standards/certificates', fd, true).then(() => {
                $('up-sheet').close();
                S.toast('Certificate filed · ' + who() + ' · ' + hm());
                refetch();
            });
        });

    // changeover. The library is read when the sheet opens so a Lab ID
    // another standard already holds is refused before anything is written:
    // two standards on one Lab ID leave the bench unable to tell which it ran.
    let chgLib = null;
    function sheetChangeover() {
        chgLib = null;
        fetch('/api/qc-samples', { headers: { 'X-LEM-Background': '1' } })
            .then(r => r.ok ? r.json() : null).then(b => { chgLib = b && b.samples; }).catch(() => { chgLib = null; });
        $('chg-form').reset();
        $('chg-err').hidden = true;
        const n = data.head.used_on || 0;
        $('chg-why').textContent = 'The new lot gets these certified values' + (n ? ' and the ' + n + (n === 1 ? ' instrument' : ' instruments') + ' checked on this one' : '') +
            '. Upload its own certificate afterwards: a certificate describes one lot.';
        // retiring a lot that holds a certificate would orphan the file:
        // that lot stays in the library until its certificate is removed
        const held = (data.certificates.rows || []).length > 0;
        $('chg-retire').checked = false;
        $('chg-retire').closest('label').hidden = held;
        openSheet('chg-sheet');
        $('chg-name').focus();
    }
    wire($('chg-form'), $('chg-err'), $('chg-go'),
        () => {
            const name = $('chg-name').value.trim(), lab = $('chg-lab').value.trim();
            if (!name) return 'Name the new lot.';
            if (!lab) return 'Enter the new lot\'s Lab ID.';
            const taken = (chgLib || []).find(x => String(x.sample_id_val || '').trim().toLowerCase() === lab.toLowerCase());
            if (taken) return lab + ' is already the Lab ID of ' + taken.name + '. A new lot runs under a Lab ID of its own.';
            const named = (chgLib || []).find(x => Q.norm(x.name) === Q.norm(name));
            if (named) return 'There is already a standard called ' + named.name + '.';
            return '';
        },
        () => {
            const name = $('chg-name').value.trim().split(/\s+/).join(' ');
            return send('POST', '/api/qc-samples/changeover', { old_name: data.name, new_name: name, new_id_val: $('chg-lab').value.trim(), retire_old: $('chg-retire').checked })
                .then(b => handOff(name + ' replaces ' + data.name + ' on ' + (b.moved || 0) + ((b.moved || 0) === 1 ? ' instrument' : ' instruments') + '. Upload its certificate.',
                    '/quality/standards/' + encodeURIComponent(name)));
        });

    // rename
    function sheetRename() {
        $('ren-name').value = data.name;
        $('ren-err').hidden = true;
        openSheet('ren-sheet');
        $('ren-name').select();
    }
    wire($('ren-form'), $('ren-err'), $('ren-go'),
        () => !$('ren-name').value.trim() ? 'Give it a new name.' : $('ren-name').value.trim() === data.name ? 'That is the name it already has.' : '',
        () => send('POST', '/api/qc-samples/rename', { name: data.name, new_name: $('ren-name').value })
            .then(b => handOff('Renamed to ' + b.name + ' · ' + who() + ' · ' + hm(), b.href)));

    // delete: the standard, or one certificate
    let delKind = 'standard', delUid = '';
    function sheetDelete(kind, uid, fname) {
        delKind = kind; delUid = uid || '';
        $('del-err').hidden = true;
        if (kind === 'cert') {
            $('del-title').textContent = 'Remove ' + fname;
            $('del-why').textContent = 'The file and its dates leave LEM. If it was the only certificate in date, the standard reads Certificate needed.';
            $('del-go').textContent = 'Remove it';
        } else {
            $('del-title').textContent = 'Delete ' + data.name;
            $('del-why').textContent = 'It leaves the QC library and benches stop recognising its Lab ID ' + data.head.lab_id + '. Its QC history stays in the log.';
            $('del-go').textContent = 'Delete it';
        }
        openSheet('del-sheet');
    }
    wire($('del-form'), $('del-err'), $('del-go'), () => '',
        () => delKind === 'cert'
            ? send('DELETE', '/api/qc-standards/certificates/' + encodeURIComponent(delUid), {}).then(() => {
                $('del-sheet').close(); S.toast('Certificate removed · ' + who() + ' · ' + hm()); refetch();
            })
            : send('DELETE', '/api/qc-samples', { name: data.name }).then(() => handOff(data.name + ' deleted · ' + who() + ' · ' + hm(), '/quality/standards')));

    document.addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-act]');
        if (!b || !page.parentNode.contains(b) || b.closest('dialog')) return;
        const act = b.dataset.act;
        const go = {
            assign: sheetAssign, upload: sheetUpload, changeover: sheetChangeover, rename: sheetRename,
            delete: () => sheetDelete('standard'), 'cert-del': () => sheetDelete('cert', b.dataset.uid, b.dataset.name),
        }[act];
        if (!go) return;
        if (window.LEMSignIn) window.LEMSignIn.need(b.dataset.gated || '', go); else go();
    });

    // ── live ──────────────────────────────────────────────────────────────
    let inflight = false;
    function refetch() {
        if (inflight) return;
        inflight = true;
        const url = '/api/ui/standards/' + encodeURIComponent(data.name);
        const get = window.LEMLive ? window.LEMLive.bgFetch(url) : fetch(url);
        get.then(r => r.json().catch(() => ({})).then(b => ({ r, b })))
            .then(({ r, b }) => {
                if (b && b.state === 'moved' && b.href) { location.href = b.href; return; }
                if (r.status === 404) { location.reload(); return; }
                if (!r.ok || !b || b.state !== 'ready') throw new Error((b && b.error) || ('HTTP ' + r.status));
                const inst = data.instruments;
                data = b;
                if (!data.instruments) data.instruments = inst;
                render();
            })
            .catch(e => S.toast('Couldn\'t refresh this standard (' + why(e) + '). What is shown may be out of date.'))
            .finally(() => { inflight = false; });
    }
    if (window.LEMLive) {
        let lastAt = data.built_at;
        window.LEMLive.subscribe((u) => {
            const mine = new Set((data.used_on.rows || []).map(r => r.uid));
            const changed = u.reset || (u.machines || []).some(x => mine.has(x)) || (u.snapshot_at && u.snapshot_at !== lastAt);
            if (u.snapshot_at) lastAt = u.snapshot_at;
            if (changed) refetch();
        });
    }

    function render() {
        renderHead();
        renderUsedOn();
        renderValues();
        renderCert();
        renderDelete();
    }
    render();
})();
