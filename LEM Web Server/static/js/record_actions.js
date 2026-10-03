/* record_actions.js: the record's action sections (ia-final §3.1 table,
   piece 6). They replace the floor's right-click menu: every action is a
   visible button in the section it belongs to, so a tablet reaches all of it.

     Maintenance and calibration   Schedule… (presets in days) · per task
                                   Mark done… (note required) · Edit… (with
                                   Delete) · Recently completed
     Correction factors            Add a correction… · Edit… · Remove… · the
                                   change history link
     Corrective actions            Open a corrective action… · per action its
                                   next step, and Record, Verify, Close,
                                   Withdraw, Assign, Note
     Documents                     Upload… · Download · Delete…
     Placement                     Move to another level… · Move on the map ·
                                   Reset position
     Remove                        Remove from LEM… (typed name, password)

   The rules are record.js's, shared through window.LEMRecordPage: one send()
   for every write, a refused write keeps its sheet open with the server's
   sentence and no toast, a saved one closes it, toasts what/who/when and
   repaints in place. The words and refusals are record_actions_logic.js's
   (node-tested). Each section that reads its own route says "reading",
   "couldn't read" (with Try again) and "none yet" in different sentences. */
(function () {
    'use strict';
    const P = window.LEMRecordPage;
    if (!P) return;
    const A = window.LEMRecordActions;
    const R = window.LEMRecord;
    const S = window.LEMShell;
    const h = S.h;
    const $ = (id) => document.getElementById(id);
    const enc = encodeURIComponent;
    const uid = P.uid;
    const glyph = P.glyph;
    const data = () => P.data();
    const say = (what) => S.toast(A.toast(what, P.who(), P.hm()));

    // ── reads a person pays for by opening the record ─────────────────────
    const reads = {};          // name -> {state:'loading'|'ok'|'err', body, error}
    const ROUTES = {
        corrections: () => '/api/machines/' + enc(uid) + '/corrections',
        actions: () => '/api/equipment/' + enc(uid) + '/actions',
        documents: () => '/api/equipment/' + enc(uid) + '/documents',
        history: () => '/api/machines/' + enc(uid) + '/maintenance-history',
    };
    const DRAW = { corrections: drawCorrections, actions: drawActions, documents: drawDocuments, history: drawMaintenance };
    function load(name) {
        reads[name] = Object.assign({}, reads[name] || {}, { state: reads[name] && reads[name].state === 'ok' ? 'ok' : 'loading', busy: true });
        DRAW[name]();
        return fetch(ROUTES[name](), { headers: { 'X-LEM-Background': '1' } })
            .then(r => r.json().catch(() => ({})).then(b => {
                if (!r.ok || b.error) throw new Error(b.error || ('LEM answered ' + r.status));
                reads[name] = { state: 'ok', body: b };
            }))
            .catch(e => { reads[name] = { state: 'err', error: P.why(e) }; })
            .finally(() => DRAW[name]());
    }
    function reading(text) {
        return h('p', { className: 'empty-note', role: 'status', 'aria-busy': 'true' }, glyph('never'), h('span', { text }));
    }
    function failed(what, name) {
        return h('div', { className: 'empty-note sec-err', role: 'status' }, glyph('error'),
            h('span', { text: 'Couldn\'t read ' + what + ': ' + reads[name].error + '. This is not an empty list.' }),
            h('button', { type: 'button', className: 'btn btn-ghost btn-sm', onclick: () => load(name), text: 'Try again' }));
    }
    function none(text) { return h('p', { className: 'empty-note' }, glyph('never'), h('span', { text })); }
    const gated = (act, gate, label, extra) => h('button', Object.assign({ type: 'button', className: 'btn btn-sm gate-lock', 'data-act': act, 'data-gated': gate, text: label }, extra || {}));
    const ghost = (act, gate, label, extra) => h('button', Object.assign({ type: 'button', className: 'btn btn-ghost btn-sm gate-lock', 'data-act': act, 'data-gated': gate, text: label }, extra || {}));
    const lower = (s) => s ? s.charAt(0).toLowerCase() + s.slice(1) : s;
    function errOff(id) { $(id).hidden = true; }

    // ── Maintenance and calibration ───────────────────────────────────────
    const KIND = { pm: 'PM', calibration: 'Calibration' };
    function drawMaintenance() {
        const tasks = data().maintenance || [];
        const body = $('mt-body');
        if (!tasks.length) {
            body.replaceChildren(none('Nothing scheduled for this instrument.'), recent());
            return;
        }
        body.replaceChildren(h('div', { className: 'card table-card' }, h('table', { className: 'tbl mt', 'data-testid': 'mt-table' },
            h('thead', {}, h('tr', {}, ...[['Task', ''], ['How often', 'm-every'], ['Last done', 'm-last'], ['Due', ''], ['', 'm-act']]
                .map(([t, cls]) => h('th', { scope: 'col', className: cls, text: t }, t ? null : h('span', { className: 'visually-hidden', text: 'Actions' }))))),
            h('tbody', {}, ...tasks.map(t => h('tr', { 'data-task': t.uid, 'data-testid': 'mt-row' },
                h('td', {}, h('b', { text: t.name }), h('span', { className: 'sub', text: KIND[t.kind] || '' })),
                h('td', { className: 'm-every', text: A.everyText(t.every) }),
                h('td', { className: 'm-last', text: t.last_done ? R.dayIn(t.last_done) : 'Never' }),
                h('td', {}, h('span', { className: 'verdict ' + (t.glyph === 'half' ? 's-ok_but' : '') }, glyph(t.glyph === 'half' ? 'half' : 'final'), h('span', { text: t.word })),
                    h('span', { className: 'sub', text: A.dueText(t) })),
                h('td', { className: 'm-act' },
                    gated('mt-done', 'mark ' + lower(t.name) + ' done', 'Mark done…', { 'data-uid': t.uid, 'data-testid': 'mt-done' }),
                    ghost('mt-edit', 'edit ' + lower(t.name), 'Edit…', { 'data-uid': t.uid, 'data-testid': 'mt-edit' }))))))),
        recent());
    }
    // what was done lately: who did it and what they found (an auditor's
    // question), from the instrument's history, read when the page opens
    function recent() {
        const r = reads.history;
        const wrap = h('div', { className: 'mt-recent', 'data-testid': 'mt-recent' }, h('h3', { text: 'Recently completed' }));
        if (!r || r.state === 'loading') { wrap.appendChild(h('p', { className: 'caption', role: 'status', text: 'Reading what was done…' })); return wrap; }
        if (r.state === 'err') {
            wrap.appendChild(h('p', { className: 'caption sec-err-line', role: 'status' }, glyph('error'),
                h('span', { text: ' Couldn\'t read what was done: ' + r.error + '. ' }),
                h('button', { type: 'button', className: 'btn btn-ghost btn-sm', onclick: () => load('history'), text: 'Try again' })));
            return wrap;
        }
        const items = (r.body.history || []).slice(0, 3);
        if (!items.length) { wrap.appendChild(h('p', { className: 'caption', text: 'Nothing has been marked done yet.' })); return wrap; }
        wrap.appendChild(h('ul', { className: 'mt-done-list' }, ...items.map(x => h('li', {},
            h('span', { className: 'd-when', text: R.dayIn(x.completed) }),
            h('span', { className: 'd-what' }, h('b', { text: x.task || KIND[x.kind] || 'Task' }), x.note ? ' · ' + x.note : ''),
            h('span', { className: 'd-who', text: x.by || '' })))));
        return wrap;
    }
    function task(id) { return (data().maintenance || []).find(t => t.uid === id) || null; }
    function markDone(b) {
        const t = task(b.dataset.uid);
        if (!t) return;
        P.sheetDone({ kind: t.kind, task: t.uid, tasks: [{ uid: t.uid, name: t.name, next_due: t.next_due }] });
    }

    // the Schedule / Edit sheet: chips set kind + days + name; a typed name stays
    let sched = A.scheduleState();
    function paintSched() {
        $('sched-presets').replaceChildren(...A.PRESETS.map(p => h('button', {
            type: 'button', className: 'chip', 'data-preset': p.key, 'aria-pressed': String(A.pressedPreset(sched) === p.key), text: A.presetChip(p) })));
        if ($('sched-name').value !== sched.name) $('sched-name').value = sched.name;
        for (const b of $('sched-kind').querySelectorAll('button')) b.setAttribute('aria-checked', String(b.dataset.kind === sched.kind));
        if (String($('sched-days').value) !== String(sched.days)) $('sched-days').value = String(sched.days);
        const dup = A.duplicateNote(data().maintenance || [], sched);
        $('sched-dup').textContent = dup;
        $('sched-dup').hidden = !dup;
    }
    function openSchedule(t) {
        sched = A.scheduleState(t || null);
        $('sched-title').textContent = t ? 'Edit ' + t.name + ' on ' + data().title : 'Schedule PM or calibration on ' + data().title;
        $('sched-last').value = sched.lastDone;
        $('sched-last').max = A.localDay();
        $('sched-go').textContent = t ? 'Save' : 'Schedule it';
        $('sched-del').hidden = !t;
        errOff('sched-err');
        paintSched();
        P.keepSection('maintenance');
        P.openSheet('sched-sheet');
        (t ? $('sched-name') : $('sched-presets').querySelector('button')).focus();
    }
    $('sched-presets').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-preset]');
        if (!b) return;
        sched = A.applyPreset(sched, A.PRESETS.find(p => p.key === b.dataset.preset));
        errOff('sched-err');
        paintSched();
        // the chips are redrawn; keep the keyboard where it was
        const again = $('sched-presets').querySelector('[data-preset="' + b.dataset.preset + '"]');
        if (again) again.focus();
    });
    $('sched-kind').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-kind]');
        if (!b) return;
        sched = A.applyKind(sched, b.dataset.kind);
        paintSched();
    });
    $('sched-name').addEventListener('input', () => { sched = A.typedName(sched, $('sched-name').value); paintSched(); });
    $('sched-days').addEventListener('input', () => {
        const v = $('sched-days').value.trim();
        sched = A.applyDays(sched, /^\d+$/.test(v) ? Number(v) : NaN);
        paintSched();
    });
    $('sched-last').addEventListener('input', () => { sched = Object.assign({}, sched, { lastDone: $('sched-last').value }); });
    P.submit($('sched-form'), $('sched-err'), $('sched-go'), {
        check: () => A.scheduleProblem(Object.assign({}, sched, { lastDone: $('sched-last').value }), Date.now()),
        url: () => '/api/machines/' + enc(uid) + '/maintenance',
        body: () => A.scheduleBody(Object.assign({}, sched, { lastDone: $('sched-last').value })),
        section: () => 'maintenance',
    }, () => say(A.scheduleBody(sched).name + (sched.uid ? ' saved' : ' scheduled')));
    $('sched-del').addEventListener('click', () => {
        const t = task(sched.uid);
        if (!t) return;
        $('sched-sheet').close();
        confirmSheet({
            title: 'Delete ' + t.name + ' from ' + data().title,
            why: 'It is no longer scheduled or warned about. What was done before stays in the instrument\'s history.',
            go: 'Delete it', section: 'maintenance',
            url: '/api/maintenance/' + enc(t.uid), method: 'DELETE',
            toast: t.name + ' deleted',
        });
    });

    // ── Correction factors ────────────────────────────────────────────────
    function corrRows() { return ((reads.corrections || {}).body || {}).corrections || []; }
    function drawCorrections() {
        const r = reads.corrections;
        const body = $('corr-body');
        const link = $('corr-history');
        link.textContent = A.historyLabel(r && r.state === 'ok' ? r.body.history : null);
        if (!r || (r.state === 'loading' && !r.body)) { body.replaceChildren(reading('Reading its correction factors…')); return; }
        if (r.state === 'err') { body.replaceChildren(failed('its correction factors', 'corrections')); return; }
        const rows = corrRows();
        if (!rows.length) {
            body.replaceChildren(none('No correction factors: every reading of this instrument is reported as measured.'));
            return;
        }
        body.replaceChildren(h('div', { className: 'kv corr-kv', 'data-testid': 'corr-list' }, ...rows.flatMap(c => {
            const [title, method] = R.shortTest(c.test_name);
            return [h('div', { className: 'k' }, h('span', { text: title }), method ? h('span', { className: 'sub', text: method }) : null),
                h('div', { className: 'v corr-v' },
                    h('b', { className: 'num', text: A.offsetText(c.correction, c.units) }),
                    h('span', { className: 'caption', text: A.whoWhen(c.updated_by, c.updated_at) }),
                    h('span', { className: 'grow' }),
                    ghost('corr-edit', 'change a correction factor', 'Edit…', { 'data-test': c.test_name, 'data-testid': 'corr-edit' }),
                    ghost('corr-remove', 'remove a correction factor', 'Remove…', { 'data-test': c.test_name, 'data-testid': 'corr-remove' }))];
        })));
    }
    let corrEdit = null;          // the test being changed, or null for a new one
    function openCorrection(b) {
        const r = reads.corrections;
        const existing = b && b.dataset.test ? corrRows().find(c => c.test_name === b.dataset.test) : null;
        corrEdit = existing ? existing.test_name : null;
        const have = new Set(corrRows().map(c => c.test_name));
        const methods = ((r && r.body && r.body.methods) || []).filter(m => !have.has(m));
        const sel = $('corr-test');
        sel.replaceChildren(...methods.map(m => h('option', { value: m, text: R.shortTest(m)[0] + (R.shortTest(m)[1] ? ' · ' + R.shortTest(m)[1] : '') })),
            h('option', { value: '__other', text: methods.length ? 'Another test…' : 'A test this bench reports…' }));
        sel.value = methods[0] || '__other';
        $('corr-test-field').hidden = !!existing;
        $('corr-other-field').hidden = !!existing || sel.value !== '__other';
        $('corr-other').value = '';
        $('corr-title').textContent = existing ? 'Change the correction for ' + R.shortTest(existing.test_name)[0] : 'Add a correction to ' + data().title;
        $('corr-value').value = existing ? String(existing.correction) : '';
        $('corr-units').value = existing ? (existing.units || '') : unitsFor(sel.value);
        $('corr-reason').value = '';
        errOff('corr-err');
        P.keepSection('corrections');
        P.openSheet('corr-sheet');
        (existing ? $('corr-value') : sel).focus();
    }
    function unitsFor(test) {
        const c = (data().qc.checks || []).find(x => x.test === test);
        return c && c.units ? c.units : '';
    }
    $('corr-test').addEventListener('change', () => {
        const other = $('corr-test').value === '__other';
        $('corr-other-field').hidden = !other;
        if (!other && !$('corr-units').value) $('corr-units').value = unitsFor($('corr-test').value);
        if (other) $('corr-other').focus();
    });
    function corrTest() {
        if (corrEdit) return corrEdit;
        return $('corr-test').value === '__other' ? $('corr-other').value.trim() : $('corr-test').value;
    }
    P.submit($('corr-form'), $('corr-err'), $('corr-go'), {
        check: () => corrTest() ? A.correctionProblem($('corr-value').value, $('corr-reason').value) : 'Say which test the correction is for.',
        url: () => '/api/machines/' + enc(uid) + '/corrections',
        body: () => ({ test_name: corrTest(), correction: A.correctionNumber($('corr-value').value), units: $('corr-units').value.trim(), reason: $('corr-reason').value.trim() }),
        section: () => 'corrections',
    }, (b) => {
        say('Correction on ' + R.shortTest(b.test_name || corrTest())[0] + ' set to ' + A.offsetText(b.correction, $('corr-units').value.trim()));
        load('corrections');
    });
    function removeCorrection(b) {
        const c = corrRows().find(x => x.test_name === b.dataset.test);
        if (!c) return;
        const name = R.shortTest(c.test_name)[0];
        confirmSheet({
            title: 'Remove the correction for ' + name,
            why: 'Readings of ' + name + ' are reported as measured from the bench\'s next result on, without the ' + A.offsetText(c.correction, c.units) + '. The change is kept in its history with your reason.',
            reason: true, go: 'Remove it', section: 'corrections',
            url: '/api/machines/' + enc(uid) + '/corrections/' + enc(c.test_name), method: 'DELETE',
            body: (reason) => ({ reason }),
            toast: 'Correction on ' + name + ' removed', after: () => load('corrections'),
        });
    }

    // ── Corrective actions ────────────────────────────────────────────────
    const STEPS = ['open', 'actioned', 'verified', 'closed'];
    function actionRows() { return ((reads.actions || {}).body || {}).actions || []; }
    let showFinished = false;
    function drawActions() {
        const r = reads.actions;
        const body = $('ca-body');
        if (!r || (r.state === 'loading' && !r.body)) { body.replaceChildren(reading('Reading its corrective actions…')); return; }
        if (r.state === 'err') { body.replaceChildren(failed('its corrective actions', 'actions')); return; }
        const all = actionRows();
        const open = all.filter(a => ['open', 'actioned', 'verified'].includes(a.state));
        const done = all.filter(a => !open.includes(a));
        const kids = [];
        if (!all.length) kids.push(none('No corrective actions on file for this instrument.'));
        else if (!open.length) kids.push(none('Nothing open.'));
        if (open.length) kids.push(h('ul', { className: 'ca-list', 'data-testid': 'ca-open-list' }, ...open.map(actionRow)));
        if (done.length) {
            kids.push(h('button', { type: 'button', className: 'btn btn-ghost btn-sm ca-more', 'aria-expanded': String(showFinished), 'data-testid': 'ca-finished-toggle',
                onclick: () => { showFinished = !showFinished; drawActions(); },
                text: (showFinished ? 'Hide ' : 'Show ') + done.length + ' finished' }));
            if (showFinished) kids.push(h('ul', { className: 'ca-list finished' }, ...done.map(actionRow)));
        }
        body.replaceChildren(...kids);
    }
    function track(state) {
        if (state === 'withdrawn') return h('span', { className: 'ca-track' }, h('b', { text: 'Withdrawn' }));
        const at = STEPS.indexOf(state);
        return h('ol', { className: 'ca-track', 'aria-label': 'Step: ' + A.stateWord(state) }, ...STEPS.map((s, i) =>
            h('li', { className: (i < at ? 'past' : '') + (i === at ? ' now' : ''), 'aria-current': i === at ? 'step' : null, text: A.stateWord(s) })));
    }
    function actionRow(a) {
        const steps = A.actionSteps(a.state);
        const meta = [A.whoWhen(a.opened_by, a.opened_at) ? 'Opened by ' + A.whoWhen(a.opened_by, a.opened_at) : '',
            a.assigned_to ? 'Assigned to ' + a.assigned_to : '', a.due_at ? 'due ' + R.dayIn(a.due_at) : '',
            a.priority && a.priority !== 'normal' ? a.priority.charAt(0).toUpperCase() + a.priority.slice(1) + ' priority' : '',
            a.test_name ? R.shortTest(a.test_name)[0] : ''].filter(Boolean);
        const latest = a.state === 'closed' ? (a.closed_note || a.verification || a.action_taken)
            : a.state === 'verified' ? a.verification || a.action_taken : a.action_taken;
        return h('li', { className: 'ca-row', 'data-action': a.uid, 'data-testid': 'ca-row' },
            h('div', { className: 'ca-main' },
                h('p', { className: 'ca-what', text: a.what_happened }),
                track(a.state),
                latest ? h('p', { className: 'caption ca-latest', text: (a.state === 'actioned' ? 'Done: ' : a.state === 'verified' ? 'Checked: ' : '') + latest }) : null,
                h('p', { className: 'caption' }, a.overdue ? h('span', { className: 'verdict s-ok_but' }, glyph('half'), h('span', { text: 'Overdue' })) : null,
                    a.overdue && meta.length ? ' · ' : '', meta.join(' · '))),
            h('div', { className: 'ca-act' },
                steps.next ? gated('ca-step', lower(A.stepButton(steps.next)).replace('…', ''), A.stepButton(steps.next), { 'data-uid': a.uid, 'data-step': steps.next, 'data-testid': 'ca-next' }) : null,
                ghost('ca-step', 'work on a corrective action', steps.next ? 'Other steps…' : 'Add a note…',
                    { 'data-uid': a.uid, 'data-step': steps.next ? steps.steps.find(x => x !== steps.next) : 'note', 'data-testid': 'ca-other' })));
    }
    let stepping = null;          // {action, step}
    function openStep(b) {
        const a = actionRows().find(x => x.uid === b.dataset.uid);
        if (!a) return;
        stepping = { action: a, step: b.dataset.step };
        $('step-title').textContent = 'Corrective action · ' + A.stateWord(a.state);
        $('step-what').textContent = a.what_happened + (a.opened_by ? ' (opened by ' + A.whoWhen(a.opened_by, a.opened_at) + ')' : '');
        const steps = A.actionSteps(a.state).steps;
        $('step-seg').replaceChildren(...steps.map(s => h('button', { type: 'button', role: 'radio', 'data-step': s, 'aria-checked': String(s === stepping.step), text: A.STEP_TAB[s] })));
        $('step-seg').hidden = steps.length < 2;
        paintStep();
        P.keepSection('actions');
        P.openSheet('step-sheet');
        const first = $('step-fields').querySelector('textarea, input, select');
        if (first) first.focus();
    }
    const STEP_FIELD = {
        record: ['What was done (required)', 'action_taken'], verify: ['How you checked that it worked (required)', ''],
        close: ['Closing note (optional)', ''], withdraw: ['Why it is withdrawn (required)', ''], note: ['Note (required)', ''],
    };
    function paintStep() {
        const s = stepping.step, a = stepping.action;
        for (const b of $('step-seg').querySelectorAll('button')) b.setAttribute('aria-checked', String(b.dataset.step === s));
        errOff('step-err');
        $('step-go').textContent = A.STEP_GO[s];
        if (s === 'assign') {
            $('step-fields').replaceChildren(
                h('label', { className: 'field' }, h('span', { text: 'Who owns it' }), h('input', { type: 'text', id: 'step-who', value: a.assigned_to || '', autocomplete: 'off' })),
                h('div', { className: 'sched-row' },
                    h('label', { className: 'field' }, h('span', { text: 'Due' }), h('input', { type: 'date', id: 'step-due', value: (a.due_at || '').slice(0, 10) })),
                    h('label', { className: 'field' }, h('span', { text: 'Priority' }), h('select', { id: 'step-priority' },
                        ...['low', 'normal', 'high', 'critical'].map(p => h('option', { value: p, text: p.charAt(0).toUpperCase() + p.slice(1) }))))));
            $('step-priority').value = a.priority || 'normal';
            return;
        }
        const [label, prefill] = STEP_FIELD[s];
        $('step-fields').replaceChildren(h('label', { className: 'field' }, h('span', { text: label }),
            h('textarea', { id: 'step-text', rows: '3' })));
        $('step-text').value = prefill ? (a[prefill] || '') : '';
    }
    $('step-seg').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-step]');
        if (!b || !stepping) return;
        stepping.step = b.dataset.step;
        paintStep();
    });
    function stepFields() {
        if (stepping.step === 'assign') return { who: $('step-who').value, due: $('step-due').value, priority: $('step-priority').value };
        return { text: $('step-text').value };
    }
    P.submit($('step-form'), $('step-err'), $('step-go'), {
        check: () => A.stepProblem(stepping.step, stepFields()),
        url: () => A.stepRequest(stepping.step, stepping.action.uid, stepFields())[0],
        body: () => A.stepRequest(stepping.step, stepping.action.uid, stepFields())[1],
        section: () => 'actions',
    }, () => { say(A.STEP_TOAST[stepping.step]); load('actions'); });

    // ── Documents ─────────────────────────────────────────────────────────
    const TYPE = { 'application/pdf': 'PDF', 'image/png': 'PNG', 'image/jpeg': 'JPEG' };
    function docRows() { return ((reads.documents || {}).body || {}).documents || []; }
    function drawDocuments() {
        const r = reads.documents;
        const body = $('doc-body');
        if (!r || (r.state === 'loading' && !r.body)) { body.replaceChildren(reading('Reading its documents…')); return; }
        if (r.state === 'err') { body.replaceChildren(failed('its documents', 'documents')); return; }
        const docs = docRows();
        if (!docs.length) { body.replaceChildren(none('No documents for this instrument yet.')); return; }
        body.replaceChildren(h('ul', { className: 'doc-list', 'data-testid': 'doc-list' }, ...docs.map(d => h('li', { className: 'doc-row', 'data-testid': 'doc-row' },
            h('span', { className: 'ico ico-file', 'aria-hidden': 'true' }),
            h('span', { className: 'doc-main' }, h('span', { className: 'doc-name', text: d.filename }),
                h('span', { className: 'caption', text: [TYPE[d.content_type] || '', A.sizeText(d.size_bytes), A.whoWhen(d.uploaded_by, d.uploaded_at)].filter(Boolean).join(' · ') })),
            h('a', { className: 'btn btn-ghost btn-sm', href: '/api/equipment/documents/' + enc(d.uid) + '/download', download: '', text: 'Download' }),
            ghost('doc-delete', 'delete a document', 'Delete…', { 'data-uid': d.uid, 'data-testid': 'doc-delete' })))));
    }
    function openUpload() {
        $('upload-file').value = '';
        errOff('upload-err');
        $('upload-title').textContent = 'Upload a document for ' + data().title;
        P.keepSection('documents');
        P.openSheet('upload-sheet');
        $('upload-file').focus();
    }
    const picked = () => ($('upload-file').files || [])[0] || null;
    P.submit($('upload-form'), $('upload-err'), $('upload-go'), {
        check: () => A.uploadProblem(picked()),
        url: () => '/api/equipment/' + enc(uid) + '/documents',
        form: () => { const f = new FormData(); f.append('file', picked()); return f; },
        section: () => 'documents',
    }, (b) => { say(((b.document || {}).filename || 'The document') + ' uploaded'); load('documents'); });
    function deleteDocument(b) {
        const d = docRows().find(x => x.uid === b.dataset.uid);
        if (!d) return;
        confirmSheet({
            title: 'Delete ' + d.filename,
            why: 'It is removed from ' + data().title + '\'s documents and from the documents folder. The deletion is audited.',
            go: 'Delete it', section: 'documents',
            url: '/api/equipment/documents/' + enc(d.uid), method: 'DELETE',
            toast: d.filename + ' deleted', after: () => load('documents'),
        });
    }

    // ── Placement ─────────────────────────────────────────────────────────
    function drawPlacement() {
        const p = data().placement;
        if (!p) return;
        const kv = (k, v) => [h('div', { className: 'k', text: k }), h('div', { className: 'v' }, v)];
        $('pl-body').replaceChildren(h('div', { className: 'kv', 'data-testid': 'pl-kv' },
            ...kv('Level', p.level ? p.level : h('span', { className: 'muted', text: p.levels.length ? 'Not on a level' : 'No levels are set up' })),
            ...kv('On the map', p.placed ? 'Placed' : h('span', {}, glyph('never'), ' Not on the map'))));
        const others = p.levels.filter(lv => lv.uid !== p.level_uid);
        $('pl-level').hidden = !others.length;
        $('pl-reset').hidden = !p.placed;
        $('pl-map').textContent = p.placed ? 'Move on the map' : 'Place it on the map';
        $('pl-map').setAttribute('href', p.map);
    }
    function openLevel() {
        const p = data().placement;
        $('level-pick').replaceChildren(...p.levels.map(lv => h('option', { value: lv.uid, text: lv.name + (lv.uid === p.level_uid ? ' (here now)' : '') })));
        const other = p.levels.find(lv => lv.uid !== p.level_uid);
        $('level-pick').value = other ? other.uid : (p.level_uid || '');
        $('level-title').textContent = 'Move ' + data().title + ' to another level';
        errOff('level-err');
        P.keepSection('placement');
        P.openSheet('level-sheet');
        $('level-pick').focus();
    }
    P.submit($('level-form'), $('level-err'), $('level-go'), {
        check: () => $('level-pick').value === (data().placement.level_uid || '') ? 'It is on that level already. Pick another, or Cancel.' : '',
        url: () => '/api/equipment/' + enc(uid) + '/level',
        body: () => ({ level_uid: $('level-pick').value }),
        section: () => 'placement',
    }, () => {
        const lv = data().placement.levels.find(x => x.uid === $('level-pick').value);
        say('Moved to ' + (lv ? lv.name : 'another level'));
    });
    function resetPosition() {
        const p = data().placement;
        confirmSheet({
            title: 'Reset ' + data().title + '\'s position',
            why: 'It comes off the plan and is listed as "Not on the map" until somebody places it again.' + (p.level ? ' It stays on ' + p.level + '.' : ''),
            go: 'Reset position', section: 'placement',
            url: '/api/machines/' + enc(uid) + '/position', method: 'DELETE',
            toast: 'Position reset',
        });
    }

    // ── the confirm sheet (delete a task, a document, a correction; reset) ─
    let cfg = null;
    function confirmSheet(c) {
        cfg = c;
        $('confirm-title').textContent = c.title;
        $('confirm-why').textContent = c.why;
        $('confirm-reason-field').hidden = !c.reason;
        $('confirm-reason').value = '';
        $('confirm-go').textContent = c.go;
        errOff('confirm-err');
        if (c.section) P.keepSection(c.section);
        P.openSheet('confirm-sheet');
        (c.reason ? $('confirm-reason') : $('confirm-go')).focus();
    }
    P.submit($('confirm-form'), $('confirm-err'), $('confirm-go'), {
        check: () => cfg.reason && !$('confirm-reason').value.trim() ? 'Say why. The reason is kept with the change (17025 §7.8.2).' : '',
        url: () => cfg.url,
        method: () => cfg.method || 'POST',
        body: () => (cfg.body ? cfg.body($('confirm-reason').value.trim()) : undefined),
        section: () => cfg.section || '',
    }, () => { say(cfg.toast); if (cfg.after) cfg.after(); });

    // ── Remove (typed name, password: the admin unlock) ───────────────────
    // The password is kept in this tab for 15 minutes in a closure (never
    // storage), so a second removal in that time asks only for the name.
    const unlock = window.LEMUi && window.LEMUi.makeAdminGate ? window.LEMUi.makeAdminGate({ ttlMs: 15 * 60 * 1000 }) : null;
    function openRemove() {
        const d = data();
        $('remove-title').textContent = 'Remove ' + d.title + ' from LEM';
        $('remove-why').textContent = 'This clears its QC assignments, schedule, placement, documents and the LabStation configuration it runs on. Its log stays in the record and the removal is audited. It cannot be undone from this page.';
        const live = $('remove-live');
        live.hidden = !(d.remove && d.remove.checking_in);
        live.lastElementChild.textContent = 'A LabStation module is running ' + d.title + ' right now. Removing it clears that module\'s configuration and stops it parsing.';
        $('remove-name-l').textContent = 'Type “' + d.title + '” to confirm';
        $('remove-name').value = '';
        $('remove-name').placeholder = d.title;
        const open = unlock && unlock.get();
        $('remove-pw-field').hidden = !!open;
        $('remove-pw').value = '';
        $('remove-unlocked').hidden = !open;
        if (open) $('remove-unlocked').textContent = 'Unlocked in this tab for ' + Math.max(1, Math.ceil(unlock.remainingMs() / 60000)) + ' more min; no password needed.';
        errOff('remove-err');
        P.keepSection('remove');
        P.openSheet('remove-sheet');
        $('remove-name').focus();
    }
    $('remove-form').addEventListener('submit', (ev) => {
        ev.preventDefault();
        const d = data();
        const kept = unlock && unlock.get();
        const pw = kept || $('remove-pw').value;
        const err = $('remove-err');
        const msg = A.removeProblem($('remove-name').value, d.title, !kept, pw);
        if (msg) { err.textContent = msg; err.hidden = false; return; }
        $('remove-go').disabled = true;
        err.hidden = true;
        P.send('/api/ui/instruments/' + enc(uid) + '/remove', { body: { name: $('remove-name').value.trim(), password: pw } })
            .then(() => {
                if (unlock) unlock.set(pw);
                $('remove-sheet').close();
                say(d.title + ' removed from LEM');
                removed(d);
            })
            .catch(e => {
                if (e && e.status === 403 && unlock) {
                    unlock.clear();
                    $('remove-pw-field').hidden = false;
                    $('remove-unlocked').hidden = true;
                }
                err.textContent = (e && e.message) || 'Nothing was removed.';
                err.hidden = false;
            })
            .finally(() => { $('remove-go').disabled = false; });
    });
    // the record is gone: the page says so where it stands, with a way home
    function removed(d) {
        const main = $('main');
        main.replaceChildren(h('header', { className: 'page-head' }, h('h1', { text: d.title })),
            h('section', { className: 'card checklist removed-card', 'data-testid': 'removed' },
                h('div', { className: 'bar' }, h('h2', { text: d.title + ' was removed from LEM' })),
                h('p', { text: 'Its log stays in the record and the removal is audited. If its LabStation module starts again, it registers as a new instrument.' }),
                h('a', { className: 'btn', href: '/', text: 'Back to Instruments' })));
        const t = $('topbar-online');
        if (t) t.hidden = true;
        document.title = d.title + ' removed · LEM';
    }

    // ── one page, many sections ───────────────────────────────────────────
    function render() {
        drawMaintenance();
        drawPlacement();
        // the section reads redraw from their own answers; a refreshed
        // record may change the checks a correction's units come from
        if (reads.corrections) drawCorrections();
    }
    P.register({
        render,
        reload: (name) => { if (ROUTES[name]) load(name); },
        acts: {
            schedule: () => openSchedule(null),
            'mt-done': markDone,
            'mt-edit': (b) => { const t = task(b.dataset.uid); if (t) openSchedule(t); },
            'corr-add': () => openCorrection(null),
            'corr-edit': openCorrection,
            'corr-remove': removeCorrection,
            'ca-step': openStep,
            'doc-upload': openUpload,
            'doc-delete': deleteDocument,
            level: openLevel,
            reset: resetPosition,
            remove: openRemove,
        },
    });
    for (const name of ['corrections', 'actions', 'documents', 'history']) load(name);
})();
