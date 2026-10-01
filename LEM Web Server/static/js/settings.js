// Settings (ia-final §3.7, piece 11): the page. The sentences come from
// LEMSettingsLogic (settings_logic.js, node-tested); this file fetches, places
// them, and wires the controls. Every server string goes in through
// textContent (LEMShell.h). No timer asks the server anything: the only
// interval re-words the record's age from numbers already on the page.
//
// Rules this page keeps:
// * A failed read is a sentence and a Retry, never an empty list. "This lab
//   is flat" and "LabCore did not answer" are different sentences.
// * A write is judged by the answer: r.ok AND, for imports, landed /
//   not_landed (importOutcome). Nothing short of that says "saved".
// * Signed out, every control that writes is data-gated: signin.js opens
//   "Sign in to <act>" and presses it again once you are in.
(function () {
    'use strict';
    const L = window.LEMSettingsLogic;
    const S = window.LEMShell;
    const h = S.h;
    const $ = (id) => document.getElementById(id);

    // One road to the server. Never throws: {status, body}; status 0 is "LEM
    // did not answer", body null is "the answer was not JSON".
    async function call(method, url, payload) {
        try {
            const r = await fetch(url, {
                method, headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
                body: payload === undefined ? undefined : JSON.stringify(payload), credentials: 'same-origin',
            });
            let body = null;
            try { body = await r.json(); } catch (_e) { body = null; }
            return { status: r.status, ok: r.ok, body };
        } catch (_e) {
            return { status: 0, ok: false, body: null };
        }
    }
    const errText = (res, what) => {
        if (!res.status) return 'LEM did not answer, so ' + what + '. Try again.';
        if (res.status === 401) return 'You are signed out, so ' + what + '.';
        const e = res.body && res.body.error;
        return (e ? String(e).replace(/\.?$/, '.') : 'The server answered ' + res.status + '.') + ' ' +
            (what.charAt(0).toUpperCase() + what.slice(1)) + '.';
    };

    // replaceChildren / append, minus the absent ones (DOM would print "null")
    const put = (el, ...kids) => el.replaceChildren(...kids.flat().filter(k => k !== null && k !== undefined && k !== false));
    const add = (el, ...kids) => el.append(...kids.flat().filter(k => k !== null && k !== undefined && k !== false));

    function say(el, text, kind) {
        if (!el) return;
        el.textContent = text || '';
        el.className = el.className.replace(/\s*\b(ok|err)\b/g, '') + (kind ? ' ' + kind : '');
    }

    // a load-state line: loading | error | empty | (hidden when there is an answer)
    function state(el, kind, text, retry) {
        el.hidden = !kind;
        if (!kind) return;
        el.dataset.state = kind;
        put(el,
            S.glyph(kind === 'loading' ? 'working' : kind === 'error' ? 'error' : 'never'),
            h('span', { text }),
            retry ? h('button', { type: 'button', className: 'btn btn-sm', text: 'Retry', onclick: retry }) : null);
    }

    function busy(btn, label) {
        if (!btn) return () => {};
        const was = btn.textContent, wasDisabled = btn.disabled;
        const kids = Array.from(btn.childNodes);
        btn.disabled = true;
        btn.textContent = label;
        return () => { btn.replaceChildren(...kids); if (!kids.length) btn.textContent = was; btn.disabled = wasDisabled; };
    }

    // ── This browser ────────────────────────────────────────────────────────
    function wireChoice(groupId, get, set, eventName) {
        const group = $(groupId);
        if (!group) return;
        const buttons = Array.from(group.querySelectorAll('[data-choice]'));
        const sync = () => {
            const now = get();
            buttons.forEach(b => b.setAttribute('aria-checked', b.dataset.choice === now ? 'true' : 'false'));
        };
        buttons.forEach(b => b.addEventListener('click', () => {
            set(b.dataset.choice);
            sync();
            say($('saved-state'), 'Saved on this computer');
        }));
        radioKeys(group, buttons);
        document.addEventListener(eventName, sync);
        sync();
    }
    // a radio group: arrow keys move the choice
    function radioKeys(group, buttons) {
        group.addEventListener('keydown', (ev) => {
            const i = buttons.findIndex(b => b.getAttribute('aria-checked') === 'true');
            const step = ev.key === 'ArrowRight' || ev.key === 'ArrowDown' ? 1
                : ev.key === 'ArrowLeft' || ev.key === 'ArrowUp' ? -1 : 0;
            if (!step) return;
            ev.preventDefault();
            const next = buttons[(i + step + buttons.length) % buttons.length];
            next.click();
            next.focus();
        });
    }
    function seg(group, attr) {
        const buttons = Array.from(group.querySelectorAll('[' + attr + ']'));
        buttons.forEach(b => b.addEventListener('click', () =>
            buttons.forEach(x => x.setAttribute('aria-checked', x === b ? 'true' : 'false'))));
        radioKeys(group, buttons);
        return () => (buttons.find(b => b.getAttribute('aria-checked') === 'true') || buttons[0]).getAttribute(attr);
    }

    // ── Floor and levels ────────────────────────────────────────────────────
    const levels = { renaming: '' };
    async function loadLevels() {
        state($('levels-state'), 'loading', 'Reading the levels from LabCore…');
        const [lv, fleet] = await Promise.all([call('GET', '/api/equipment/levels'), call('GET', '/api/machines')]);
        if (!lv.ok || !lv.body || !Array.isArray(lv.body.levels)) {
            $('levels-table').hidden = true;
            state($('levels-state'), 'error', errText(lv, 'the levels could not be read'), loadLevels);
            return;
        }
        const machines = fleet.ok && fleet.body && !fleet.body.warming ? fleet.body.machines : null;
        const rows = L.levelRows(lv.body, machines);
        if (!rows.length) {
            $('levels-table').hidden = true;
            state($('levels-state'), 'empty', 'This lab is flat: no levels yet. Every instrument stands on one floor until you add one.');
            return;
        }
        state($('levels-state'), '');
        $('levels-table').hidden = false;
        $('levels-body').replaceChildren(...rows.map(levelRow));
    }

    function levelRow(r) {
        const nameCell = h('td', { className: 'lv-name' });
        if (levels.renaming === r.uid) {
            const input = h('input', { type: 'text', value: r.name, maxlength: '40', 'aria-label': 'New name for ' + r.name });
            const save = h('button', { type: 'submit', className: 'btn btn-sm', text: 'Save name', 'data-gated': 'rename the level' });
            const form = h('form', { className: 'rename-form', autocomplete: 'off' }, input, save,
                h('button', { type: 'button', className: 'btn btn-sm btn-ghost', text: 'Cancel',
                    onclick: () => { levels.renaming = ''; loadLevels(); } }));
            form.addEventListener('submit', async (ev) => {
                ev.preventDefault();
                const name = input.value.trim();
                if (!name) { say($('levels-msg'), 'A level needs a name.', 'err'); input.focus(); return; }
                const done = busy(save, 'Saving…');
                const res = await call('POST', '/api/equipment/levels/' + encodeURIComponent(r.uid) + '/rename', { name });
                done();
                if (!res.ok) { say($('levels-msg'), errText(res, 'the name was not changed'), 'err'); return; }
                levels.renaming = '';
                say($('levels-msg'), 'Renamed: ' + r.name + ' is now ' + name + '.', 'ok');
                loadLevels();
            });
            nameCell.appendChild(form);
            setTimeout(() => input.focus(), 0);
        } else {
            add(nameCell, h('b', { text: r.name }),
                r.isGround ? h('span', { className: 'caption lv-tag', text: 'Ground' }) : null);
        }
        const act = h('td', { className: 'lv-act' });
        if (levels.renaming !== r.uid) {
            add(act,
                r.isDefault
                    ? h('span', { className: 'pill', title: 'Level pickers open on this one', text: 'Default view' })
                    : h('button', { type: 'button', className: 'btn btn-sm btn-ghost', text: 'Make default',
                        'data-gated': 'change the default level', onclick: () => makeDefault(r) }),
                h('button', { type: 'button', className: 'btn btn-sm btn-ghost', text: 'Rename', 'data-gated': 'rename a level',
                    'aria-label': 'Rename ' + r.name,
                    onclick: () => { levels.renaming = r.uid; say($('levels-msg'), ''); loadLevels(); } }));
        }
        return h('tr', { 'data-level': r.uid }, nameCell,
            h('td', { className: r.n ? '' : 'muted', text: r.count }), act);
    }

    async function makeDefault(r) {
        const res = await call('POST', '/api/equipment/default-level', { level_uid: r.uid });
        if (!res.ok) { say($('levels-msg'), errText(res, 'the default did not change'), 'err'); return; }
        say($('levels-msg'), 'Level pickers now open on ' + r.name + '.', 'ok');
        loadLevels();
    }

    function wireLevels() {
        const where = seg($('level-where'), 'data-where');
        $('level-form').addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const name = $('level-name').value.trim();
            if (!name) { say($('levels-msg'), 'Give the new level a name first.', 'err'); $('level-name').focus(); return; }
            const done = busy($('level-add'), 'Adding…');
            const body = where() === 'bottom' ? { name, rank: 0 } : { name };
            const res = await call('POST', '/api/equipment/levels', body);
            done();
            if (!res.ok) { say($('levels-msg'), errText(res, name + ' was not added'), 'err'); return; }
            $('level-name').value = '';
            say($('levels-msg'), 'Added ' + name + (where() === 'bottom' ? ', below the ground: it is the ground now.' : ', on top.'), 'ok');
            loadLevels();
        });
        loadLevels();
    }

    // ── Lab hours and holidays ──────────────────────────────────────────────
    const hours = { saved: null };
    function formDays() {
        return Array.from(document.querySelectorAll('#day-chips [data-day]'))
            .filter(b => b.getAttribute('aria-pressed') === 'true').map(b => Number(b.dataset.day));
    }
    function hoursChanged() {
        const s = hours.saved;
        if (!s) return;
        const days = formDays(), opens = $('hours-opens').value, closes = $('hours-closes').value;
        const dirty = JSON.stringify(days) !== JSON.stringify(s.working_days.slice().sort((a, b) => a - b)) ||
            opens !== s.opens || closes !== s.closes;
        const problem = dirty ? L.hoursProblem(opens, closes, days) : '';
        $('days-summary').textContent = L.daysText(days);
        $('hours-save').disabled = !dirty || !!problem;
        say($('hours-msg'), problem || (dirty ? 'Not saved yet.' : ''), problem ? 'err' : '');
    }
    async function loadHours() {
        state($('hours-state'), 'loading', 'Reading the lab\u2019s hours from LabCore…');
        const res = await call('GET', '/api/schedule');
        if (!res.ok || !res.body || res.body.known === false) {
            $('hours-form').hidden = true;
            state($('hours-state'), 'error', res.ok
                ? 'LabCore did not answer, so the lab\u2019s hours are not known. Screens are assuming Mon–Fri until it does; nothing here can be saved over a guess.'
                : errText(res, 'the lab\u2019s hours could not be read'), loadHours);
            return;
        }
        state($('hours-state'), '');
        $('hours-form').hidden = false;
        paintHours(res.body);
    }
    function paintHours(b) {
        hours.saved = { working_days: (b.working_days || []).slice(), opens: b.opens || '', closes: b.closes || '' };
        document.querySelectorAll('#day-chips [data-day]').forEach(btn =>
            btn.setAttribute('aria-pressed', hours.saved.working_days.includes(Number(btn.dataset.day)) ? 'true' : 'false'));
        $('hours-opens').value = hours.saved.opens;
        $('hours-closes').value = hours.saved.closes;
        hoursChanged();
        const hol = Object.entries(b.holidays || {}).sort((a, c) => a[0].localeCompare(c[0]));
        const today = new Date().toISOString().slice(0, 10);
        $('hol-list').replaceChildren(...(hol.length ? hol.map(([day, name]) => {
            const d = new Date(day + 'T12:00:00');
            const when = isNaN(d) ? day : d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
            return h('li', { className: day < today ? 'past' : '' },
                h('span', { className: 'hol-day', text: when }), h('span', { className: 'hol-name', text: name || 'Holiday' }),
                h('button', { type: 'button', className: 'btn btn-sm btn-ghost', text: 'Remove', 'data-gated': 'remove a holiday',
                    'aria-label': 'Remove ' + (name || 'the holiday') + ' on ' + when, onclick: () => removeHoliday(day, name) }));
        }) : [h('li', { className: 'empty', text: 'No holidays set: the lab counts as open on every open day.' })]));
    }
    async function removeHoliday(day, name) {
        const res = await call('DELETE', '/api/holidays/' + encodeURIComponent(day));
        if (!res.ok) { say($('hol-msg'), errText(res, (name || 'the holiday') + ' was not removed'), 'err'); return; }
        say($('hol-msg'), 'Removed ' + (name || day) + '.', 'ok');
        loadHours();
    }
    function wireHours() {
        document.querySelectorAll('#day-chips [data-day]').forEach(btn => btn.addEventListener('click', () => {
            btn.setAttribute('aria-pressed', btn.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
            hoursChanged();
        }));
        ['hours-opens', 'hours-closes'].forEach(id => $(id).addEventListener('input', hoursChanged));
        $('hours-save').addEventListener('click', async () => {
            const days = formDays(), opens = $('hours-opens').value, closes = $('hours-closes').value;
            const problem = L.hoursProblem(opens, closes, days);
            if (problem) { say($('hours-msg'), problem, 'err'); return; }
            const done = busy($('hours-save'), 'Saving…');
            const res = await call('POST', '/api/schedule', { working_days: days, opens, closes });
            done();
            if (!res.ok || !res.body || !res.body.schedule) { say($('hours-msg'), errText(res, 'the hours were not saved'), 'err'); hoursChanged(); return; }
            paintHours(res.body.schedule);
            say($('hours-msg'), 'Saved: open ' + L.daysText(days) + ', ' + opens + ' to ' + closes + '.', 'ok');
        });
        $('holiday-form').addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const day = $('holiday-day').value, name = $('holiday-name').value.trim();
            if (!day) { say($('hol-msg'), 'Pick the day first.', 'err'); $('holiday-day').focus(); return; }
            const done = busy($('holiday-add'), 'Adding…');
            const res = await call('POST', '/api/holidays', { day, name });
            done();
            if (!res.ok) { say($('hol-msg'), errText(res, 'the holiday was not added'), 'err'); return; }
            $('holiday-day').value = ''; $('holiday-name').value = '';
            say($('hol-msg'), 'Added ' + (name || day) + '. The lab counts as closed that day.', 'ok');
            loadHours();
        });
        loadHours();
    }

    // ── Imports ─────────────────────────────────────────────────────────────
    function readFile(input) {
        return new Promise((resolve) => {
            const f = input.files && input.files[0];
            if (!f) return resolve(null);
            const rd = new FileReader();
            rd.onload = () => resolve({ name: f.name, text: String(rd.result || '') });
            rd.onerror = () => resolve({ name: f.name, text: null });
            rd.readAsText(f);
        });
    }
    function outcome(el, o) {
        el.hidden = false;
        el.dataset.tone = o.tone;
        el.replaceChildren(S.glyph(o.glyph), h('div', {}, h('b', { text: o.title }),
            o.detail ? h('p', { className: 'caption', text: o.detail }) : null));
    }
    function previewOut(el, lines, table) {
        el.hidden = false;
        put(el, h('ul', { className: 'pv-lines' }, ...lines.map(t => h('li', { text: t }))), table);
    }
    function previewTable(heads, rows) {
        if (!rows.length) return null;
        return h('div', { className: 'tablewrap' }, h('table', { className: 'tbl pv-tbl' },
            h('thead', {}, h('tr', {}, ...heads.map(t => h('th', { scope: 'col', text: t })))),
            h('tbody', {}, ...rows.map(r => h('tr', {}, ...r.map(c => h('td', { text: c })))))));
    }

    function importer(cfg) {
        // cfg: {key, files:[{input, label, empty}], previewUrl, importUrl, body(files), unit, preview(body) -> {lines, go, table}}
        const st = { files: {}, ready: false };
        const reset = () => {
            $(cfg.key + '-preview-out').hidden = true;
            $(cfg.key + '-go-row').hidden = true;
        };
        const preview = async () => {
            if (!cfg.complete(st.files)) return;
            reset();
            $(cfg.key + '-outcome').hidden = true;
            const done = busy($(cfg.key + '-preview'), 'Reading…');
            const res = await call('POST', cfg.url + '?dry_run=1', cfg.body(st.files));
            done();
            if (!res.ok || !res.body) {
                previewOut($(cfg.key + '-preview-out'), [errText(res, 'nothing was previewed or imported')]);
                $(cfg.key + '-preview-out').classList.add('err');
                return;
            }
            $(cfg.key + '-preview-out').classList.remove('err');
            const p = cfg.preview(res.body);
            previewOut($(cfg.key + '-preview-out'), p.lines, p.table);
            if (p.go) {
                $(cfg.key + '-import-go').textContent = p.go;
                $(cfg.key + '-go-row').hidden = false;
            }
        };
        cfg.files.forEach(f => $(f.input).addEventListener('change', async () => {
            const got = await readFile($(f.input));
            st.files[f.input] = got;
            $(f.input + '-name').textContent = got ? got.name : f.empty;
            if (got && got.text === null) {
                previewOut($(cfg.key + '-preview-out'), [got.name + ' could not be read by this browser.']);
                return;
            }
            const can = cfg.complete(st.files);
            $(cfg.key + '-preview').hidden = !can;
            $(cfg.key + '-outcome').hidden = true;
            reset();
            if (can && window.LEMSignIn && window.LEMSignIn.user()) preview();
        }));
        $(cfg.key + '-preview').addEventListener('click', preview);
        $(cfg.key + '-cancel').addEventListener('click', () => {
            reset();
            cfg.files.forEach(f => { $(f.input).value = ''; st.files[f.input] = null; $(f.input + '-name').textContent = f.empty; });
            $(cfg.key + '-preview').hidden = true;
            $(cfg.key + '-outcome').hidden = true;
        });
        $(cfg.key + '-import-go').addEventListener('click', async () => {
            const btn = $(cfg.key + '-import-go');
            const done = busy(btn, 'Importing… keep this page open');
            const res = await call('POST', cfg.url, cfg.body(st.files));
            done();
            const o = L.importOutcome(res.status, res.body, cfg.unit);
            outcome($(cfg.key + '-outcome'), o);
            if (o.tone === 'done') {
                $(cfg.key + '-go-row').hidden = true;
                $(cfg.key + '-preview-out').hidden = true;
            } else {
                // the same file again is the advice, so the button says it
                btn.textContent = 'Run the same import again';
            }
        });
    }

    function wireImports() {
        importer({
            key: 'pm', url: '/api/maintenance-import', unit: { one: 'completion', many: 'completions' },
            files: [{ input: 'pm-file', empty: 'No file chosen' }],
            complete: (f) => !!(f['pm-file'] && f['pm-file'].text !== null),
            body: (f) => ({ csv: f['pm-file'].text }),
            preview: (b) => {
                const p = L.pmPreview(b);
                p.table = previewTable(['Instrument', 'Task', 'Kind', 'Done on'],
                    (b.preview || []).slice(0, 6).map(r => [r.machine_title, r.task, r.kind === 'calibration' ? 'Calibration' : 'PM', r.completed]));
                if (b.create_count > 6 && p.table) p.lines.push('The first 6 are shown below.');
                return p;
            },
        });
        importer({
            key: 'v4', url: '/api/checklists/import-v4', unit: { one: 'round', many: 'rounds' },
            files: [{ input: 'v4-config', empty: 'No file chosen' }, { input: 'v4-state', empty: 'No history file' }],
            complete: (f) => !!(f['v4-config'] && f['v4-config'].text !== null),
            body: (f) => ({ json: f['v4-config'].text, state: f['v4-state'] && f['v4-state'].text ? f['v4-state'].text : undefined }),
            preview: (b) => {
                const n = b.count || 0, ticks = b.history_rows || 0;
                const lines = [n + (n === 1 ? ' round' : ' rounds') + ' found' +
                    (ticks ? ', with ' + ticks.toLocaleString('en-US') + ' ticks of history across ' + (b.history_days || 0).toLocaleString('en-US') + ' days' : ', no history file')];
                lines.push('Rounds already here with the same name are updated, not doubled.');
                const table = previewTable(['Round', 'When', 'Due', 'Items'],
                    (b.checklists || []).map(c => [c.name, c.slot === 'closing' ? 'Closing' : c.slot === 'opening' ? 'Opening' : (c.slot || '—'), c.due_time || '—', String(c.items)]));
                return { lines, table, go: n ? 'Import ' + n + (n === 1 ? ' round' : ' rounds') + (ticks ? ' and ' + ticks.toLocaleString('en-US') + ' ticks' : '') : '' };
            },
        });
    }

    // ── Records and exports ─────────────────────────────────────────────────
    function wireExports() {
        document.querySelectorAll('[data-export-link]').forEach(a => a.addEventListener('click', async (ev) => {
            ev.preventDefault();
            const row = a.closest('.ex-row'), msg = row.querySelector('.ex-msg');
            const done = busy(a, 'Reading…');
            a.setAttribute('aria-disabled', 'true');
            let res;
            try { res = await fetch(a.getAttribute('href'), { credentials: 'same-origin' }); } catch (_e) { res = null; }
            done();
            a.removeAttribute('aria-disabled');
            if (!res || !res.ok) {
                let body = null;
                try { body = res ? await res.json() : null; } catch (_e) { body = null; }
                say(msg, errText({ status: res ? res.status : 0, body }, 'no file was made'), 'err');
                return;
            }
            const blob = await res.blob();
            const m = /filename="?([^";]+)"?/i.exec(res.headers.get('Content-Disposition') || '');
            const link = h('a', { href: URL.createObjectURL(blob), download: m ? m[1] : 'export.csv' });
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(link.href), 4000);
            const at = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
            say(msg, 'Downloaded at ' + at + '.', 'ok');
        }));
    }

    // ── Diagnostics ─────────────────────────────────────────────────────────
    const diag = { loadedAt: Date.now() };
    function tickAges() {
        const el = document.querySelector('#diag-rows [data-key="record"][data-age]');
        if (!el) return;
        const age = Number(el.dataset.age) + (Date.now() - diag.loadedAt) / 1000;
        const at = el.dataset.at || '';
        el.querySelector('.val').textContent = 'Read ' + L.ageText(age) + (at ? ' (at ' + at + ')' : '');
    }
    function paintDiag(d) {
        diag.loadedAt = Date.now();
        $('diag-summary').replaceChildren(S.glyph(d.summary.glyph), h('span', { id: 'diag-summary-text', text: d.summary.text }));
        const ng = $('diag-nav-glyph');
        if (ng) ng.className = 'glyph nav-glyph ' + (['error', 'held'].includes(d.summary.glyph) ? d.summary.glyph : '');
        $('diag-rows').replaceChildren(...d.rows.flatMap(r => [
            h('div', { className: 'k', text: r.label }),
            h('div', { className: 'v', 'data-key': r.key, 'data-age': r.age_seconds === null || r.age_seconds === undefined ? null : String(r.age_seconds),
                'data-at': r.at ? String(r.at).slice(11, 19) : null },
                h('span', { className: 'glyph ' + (r.glyph || ''), 'aria-hidden': 'true' }), h('span', { className: 'val', text: r.value }),
                r.note ? h('span', { className: 'caption', text: r.note }) : null)]));
        $('diag-health').replaceChildren(...Object.entries(d.healthz).flatMap(([k, v]) => [
            h('div', { className: 'k', text: k }), h('div', { className: 'v mono', text: v === '' || v === null ? '—' : String(v) })]));
        tickAges();
    }
    function wireDiagnostics() {
        const rec = document.querySelector('#diag-rows [data-key="record"]');
        if (rec) {
            const m = /(\d\d:\d\d:\d\d)/.exec(rec.textContent);
            if (m) rec.dataset.at = m[1];
        }
        tickAges();
        setInterval(tickAges, 1000);
        $('diag-refresh').addEventListener('click', async () => {
            const done = busy($('diag-refresh'), 'Refreshing…');
            // one read of the record, asked for by a person; then the facts, from memory
            const fresh = await call('GET', '/api/machines?fresh=1');
            const res = await call('GET', '/api/ui/diagnostics');
            done();
            if (!res.ok || !res.body || !Array.isArray(res.body.rows)) {
                $('diag-summary').replaceChildren(S.glyph('error'), h('span', { id: 'diag-summary-text',
                    text: 'Could not re-read: ' + errText(res, 'these are the facts from when the page opened') }));
                return;
            }
            paintDiag(res.body);
            if (!fresh.ok) say($('saved-state'), 'The record could not be re-read from LabCore; showing what is held.');
        });
    }

    // ── the sub-nav marks the section in view ───────────────────────────────
    function wireSubNav() {
        const links = Array.from(document.querySelectorAll('#set-nav a'));
        const mark = (id) => links.forEach(a => {
            const on = a.getAttribute('href') === '#' + id;
            a.classList.toggle('active', on);
            if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
        });
        // the last section whose title has reached the upper third; the
        // first one at the top of the page, the last one at the bottom
        const sections = links.map(a => document.querySelector(a.getAttribute('href'))).filter(Boolean);
        const pick = () => {
            const line = window.innerHeight / 3;
            let current = sections[0];
            for (const s of sections) if (s.getBoundingClientRect().top <= line) current = s;
            if (window.scrollY < 8) current = sections[0];
            else if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) current = sections[sections.length - 1];
            if (current) mark(current.id);
        };
        window.addEventListener('scroll', pick, { passive: true });
        pick();
        links.forEach(a => a.addEventListener('click', () => setTimeout(() => mark(a.getAttribute('href').slice(1)), 0)));
    }

    document.addEventListener('DOMContentLoaded', () => {
        wireChoice('theme-choice', () => window.LEMTheme.get().choice, (c) => window.LEMTheme.set(c), 'lem:theme');
        wireChoice('sidebar-choice', () => window.LEMSidebar.get(), (c) => window.LEMSidebar.set(c), 'lem:sidebar');
        wireLevels();
        wireHours();
        wireImports();
        wireExports();
        wireDiagnostics();
        wireSubNav();
        window.LEMSettings = { call, say, busy, seg };
    });
})();
