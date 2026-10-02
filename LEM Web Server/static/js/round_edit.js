/* round_edit.js: the round editor (ia-final §3.4, piece 10).

   P2, the bug this page ends: on an empty slot, typing three items and
   pressing Enter after each made THREE checklists, one item each, because
   every Add posted a new checklist. Here Enter adds a ROW, in the page, and
   nothing is sent until Save. Save posts the whole round once, with every
   item in items[], under a uid minted when the page was drawn: pressing it
   twice (or again because the answer was lost) upserts the same round.

   The pure half (LEMRoundEditLogic: limits, the checks, the payload, the
   uid, the reorder) is node-tested in tests/js/round_edit.mjs; the pages
   and the one-POST save in tests/test_round_editor.py. Every server string
   goes in through textContent. No timer posts anything (constraints A.7). */
(function (root) {
    'use strict';

    // ── pure ─────────────────────────────────────────────────────────────────
    const NUM = /^[-+]?(\d+\.?\d*|\.\d+)$/;
    const HHMM = /^([01]?\d|2[0-3]):([0-5]\d)$/;

    /** A limit as typed: {ok, value} with value null for blank (no limit,
        never 0), or {ok:false}. Commas are refused, not guessed at. */
    function parseLimit(raw) {
        const t = String(raw == null ? '' : raw).trim();
        if (!t) return { ok: true, value: null };
        if (!NUM.test(t)) return { ok: false };
        const v = Number(t);
        return isFinite(v) ? { ok: true, value: v } : { ok: false };
    }

    /** A due time: '' or "HH:MM" (7:30 becomes 07:30). null when it is not one. */
    function parseDue(raw) {
        const t = String(raw == null ? '' : raw).trim();
        if (!t) return '';
        const m = HHMM.exec(t);
        return m ? (m[1].length === 1 ? '0' + m[1] : m[1]) + ':' + m[2] : null;
    }

    /** Twelve hex characters, the shape the server mints. From
        crypto.getRandomValues, which (unlike randomUUID) works on a plain
        http:// lab address too. */
    function mintUid(rand) {
        const bytes = new Uint8Array(6);
        if (rand) rand(bytes);
        else if (root.crypto && root.crypto.getRandomValues) root.crypto.getRandomValues(bytes);
        else for (let i = 0; i < 6; i++) bytes[i] = Math.floor(Math.random() * 256);
        return Array.from(bytes, b => (b < 16 ? '0' : '') + b.toString(16)).join('');
    }

    function _blank(it) {
        return !String(it.text || '').trim() && !String(it.units || '').trim()
            && !String(it.min || '').trim() && !String(it.max || '').trim();
    }

    /** What is wrong with the round as it stands, each problem placed:
        [{index, field, error}], index -1 for the round's own fields. The
        server checks the same things; checking here means the sentence lands
        on the row before anything is sent. A row with nothing in it at all is
        not a problem: it is dropped. */
    function problems(def) {
        const out = [];
        if (!String(def.name || '').trim()) out.push({ index: -1, field: 'name', error: 'Give the round a name.' });
        if (parseDue(def.due_time) === null) {
            out.push({ index: -1, field: 'due', error: 'Write the due time as HH:MM, like 09:30, or leave it blank.' });
        }
        (def.items || []).forEach((it, n) => {
            if (_blank(it)) return;
            const label = String(it.text || '').trim();
            if (!label) { out.push({ index: n, field: 'text', error: 'This item needs a label.' }); return; }
            if (it.entry_type !== 'number' || it.item_type === 'header' || it.limits_unknown) return;
            const lo = parseLimit(it.min), hi = parseLimit(it.max);
            if (!lo.ok) out.push({ index: n, field: 'min', error: 'The minimum has to be a number, like 500, or left blank.' });
            if (!hi.ok) out.push({ index: n, field: 'max', error: 'The maximum has to be a number, like 3000, or left blank.' });
            if (lo.ok && hi.ok && lo.value !== null && hi.value !== null && lo.value > hi.value) {
                out.push({ index: n, field: 'min', error: 'The minimum (' + lo.value + ') is above the maximum ('
                    + hi.value + '). Every reading would be out of range.' });
            }
        });
        return out;
    }

    /** The one request: the round with every item, blank rows dropped.
        Fields the editor does not show (heading, subtask, weekdays) ride
        along untouched, so a save never loses them. A tracked reading whose
        limits could not be read sends no limits, so the server keeps them. */
    function payload(def) {
        const items = [];
        for (const it of (def.items || [])) {
            if (_blank(it)) continue;
            const kind = it.entry_type === 'number' || it.entry_type === 'text' ? it.entry_type : 'none';
            const out = {
                uid: it.uid, text: String(it.text || '').trim(), item_type: it.item_type || 'item',
                parent_uid: it.parent_uid || null, days_active: it.days_active || [],
                entry_type: it.item_type === 'header' ? 'none' : kind,
                units: kind === 'number' ? String(it.units || '').trim() : '',
                track_uid: it.track_uid || '',
            };
            if (kind === 'number' && it.item_type !== 'header') {
                out.track = !!it.track;
                if (!it.limits_unknown) {
                    out.min = String(it.min == null ? '' : it.min).trim();
                    out.max = String(it.max == null ? '' : it.max).trim();
                }
            } else {
                out.track = false;
            }
            items.push(out);
        }
        return { uid: def.uid, name: String(def.name || '').trim(), slot: def.slot || 'other',
                 due_time: parseDue(def.due_time) || '', items };
    }

    /** "3 items · 1 reading" (headings are not work, so not counted). */
    function countWords(items) {
        const work = (items || []).filter(i => !_blank(i) && i.item_type !== 'header');
        const reads = work.filter(i => i.entry_type === 'number').length;
        return work.length + ' item' + (work.length === 1 ? '' : 's')
            + (reads ? ' · ' + reads + ' reading' + (reads === 1 ? '' : 's') : '');
    }

    /** Where Enter goes from row `n` of `rows` (labels as strings):
        {add: true, at} to insert an empty row at `at`, or {focus: at}. A
        row with no label keeps the caret (an empty row is not "the next
        item"); an empty row already below is reused rather than stacked. */
    function enterFrom(labels, n) {
        if (!String(labels[n] || '').trim()) return { focus: n };
        if (n + 1 < labels.length && !String(labels[n + 1] || '').trim()) return { focus: n + 1 };
        return { add: true, at: n + 1 };
    }

    /** Move row `from` to `to` (clamped) in a list of {uid, item_type,
        parent_uid}; returns the new list. A subtask hangs off a row ABOVE it,
        so a move that strands one under a parent now below it (or gone)
        detaches it rather than saving a loop. Ryan: "let me click and drag to
        rearrange, moving an arrow a million times is tedious." */
    function moveItem(items, from, to) {
        const out = items.map(i => Object.assign({}, i));
        if (from === to || from < 0 || from >= out.length) return out;
        to = Math.max(0, Math.min(out.length - 1, to));
        const [row] = out.splice(from, 1);
        out.splice(to, 0, row);
        out.forEach((it, i) => {
            if (it.item_type !== 'subtask' || !it.parent_uid) return;
            const at = out.findIndex(o => o.uid && o.uid === it.parent_uid);
            if (at === -1 || at > i) it.parent_uid = null;
        });
        return out;
    }

    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    /** The quiet line under a row: what it does that the row cannot show. */
    function caption(it, parentText) {
        const parts = [];
        if (it.limits_unknown) parts.push('The limits this reading shares could not be read, so they are left as they are.');
        if (it.shared && it.track && it.entry_type === 'number') parts.push('One series with ' + it.shared + '; the limits are shared.');
        if (it.item_type === 'subtask') parts.push(parentText ? 'Under “' + parentText + '”; ticking it ticks this.' : 'A subtask with nothing above to hang off.');
        const days = (it.days_active || []).map(Number).filter(d => d >= 0 && d <= 6).sort();
        if (days.length && days.length < 7) parts.push('Only on ' + days.map(d => DAYS[d]).join(', ') + '.');
        return parts.join(' ');
    }

    const SLOT_WORD = { opening: 'Opening', closing: 'Closing', other: 'Other' };

    const logic = { parseLimit, parseDue, mintUid, problems, payload, countWords, enterFrom, moveItem, caption };
    root.LEMRoundEditLogic = logic;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = logic;
    if (typeof document === 'undefined') return;

    // ── the page ─────────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const main = $('main');
    if (!main || main.dataset.testid !== 'round-editor') return;
    const list = $('ed-items');
    const tpl = $('ed-item-tpl');
    const saveBtn = $('ed-save');
    const stateEl = $('ed-state');
    const errEl = $('ed-error');
    let isNew = main.dataset.new === '1';
    let saving = false;

    function rows() { return Array.from(list.querySelectorAll(':scope > li.ed-item')); }

    function kindOf(li) {
        const on = li.querySelector('.ed-kinds [aria-checked="true"]');
        return on ? on.dataset.kind : (li.dataset.kind || 'none');
    }

    function readRow(li) {
        let days = [];
        try { days = JSON.parse(li.dataset.days || '[]'); } catch (_e) { days = []; }
        const v = (cls) => { const el = li.querySelector(cls); return el ? el.value : ''; };
        return {
            uid: li.dataset.uid, text: v('.ed-label'), item_type: li.dataset.type || 'item',
            parent_uid: li.dataset.parent || null, days_active: days, entry_type: kindOf(li),
            units: v('.ed-units'), min: v('.ed-min'), max: v('.ed-max'),
            track: !!(li.querySelector('.ed-track') || {}).checked,
            track_uid: li.dataset.trackUid || '', limits_unknown: li.dataset.limitsUnknown === '1',
            shared: li.dataset.shared || '',
        };
    }

    function rowByUid(uid) { return rows().find(li => li.dataset.uid === uid) || null; }

    /** A row's type decides what it shows: a heading has no kind and no
        reading; the caption says what a row does that it cannot show. */
    function paintRow(li) {
        const it = readRow(li);
        const head = it.item_type === 'header';
        li.querySelector('.ed-tag').hidden = !head;
        li.querySelector('.ed-kinds').hidden = head;
        li.querySelector('.ed-number').hidden = head || kindOf(li) !== 'number';
        const lab = li.querySelector('.ed-label');
        lab.placeholder = head ? 'Heading' : 'What to do, e.g. Check the nitrogen generator';
        const parent = it.parent_uid ? rowByUid(it.parent_uid) : null;
        const cap = li.querySelector('.ed-cap');
        const words = caption(it, parent ? parent.querySelector('.ed-label').value.trim() : '');
        if (cap.textContent !== words) cap.textContent = words;
    }

    function current() {
        const slotOn = document.querySelector('#ed-slot [aria-checked="true"]');
        return {
            uid: main.dataset.uid, name: $('ed-name').value, due_time: $('ed-due').value,
            slot: slotOn ? slotOn.dataset.slot : 'other', items: rows().map(readRow),
        };
    }

    // what is on the server, or for a new round what the page was drawn
    // with: leaving an untouched new round asks nothing
    let savedSig = JSON.stringify(payload(current()));
    function dirty() { return JSON.stringify(payload(current())) !== savedSig; }

    function setState(text) { if (stateEl.textContent !== text) stateEl.textContent = text; }

    function paintHead() {
        const def = current();
        const name = String(def.name || '').trim();
        $('ed-title').textContent = name || 'New round';
        document.title = (name || 'New round') + ' · LEM';
        if (!isNew) $('crumb-name').textContent = name || 'Untitled round';
        $('ed-meta-slot').textContent = SLOT_WORD[def.slot] || 'Other';
        const due = parseDue(def.due_time);
        $('ed-meta-due').textContent = due ? 'Due ' + due : 'No due time';
        $('ed-meta-items').textContent = countWords(def.items);
        rows().forEach((li, n) => { li.querySelector('.ed-n').textContent = String(n + 1); paintRow(li); });
        if (!saving) {
            if (isNew) setState('Not saved yet');
            else if (dirty()) setState('Unsaved changes');
            else if (!/^Saved/.test(stateEl.textContent)) setState('Saved');
        }
    }

    function clearErrors() {
        errEl.hidden = true; errEl.textContent = '';
        $('ed-due-err').hidden = true;
        document.querySelectorAll('[aria-invalid="true"]').forEach(el => el.removeAttribute('aria-invalid'));
        rows().forEach(li => { const e = li.querySelector('.ed-err'); e.hidden = true; e.textContent = ''; });
    }

    const FIELD = { text: '.ed-label', min: '.ed-min', max: '.ed-max', units: '.ed-units' };

    /** Put each problem on its row and field; focus the first. */
    function showProblems(list_) {
        let first = null;
        for (const p of list_) {
            if (p.index === -1) {
                const el = p.field === 'name' ? $('ed-name') : $('ed-due');
                el.setAttribute('aria-invalid', 'true');
                if (p.field === 'due') { $('ed-due-err').textContent = p.error; $('ed-due-err').hidden = false; }
                else { errEl.textContent = p.error; errEl.hidden = false; }
                first = first || el;
                continue;
            }
            const li = rowsWithContent()[p.index];
            if (!li) continue;
            const e = li.querySelector('.ed-err');
            e.textContent = e.textContent ? e.textContent + ' ' + p.error : p.error;
            e.hidden = false;
            const f = li.querySelector(FIELD[p.field] || '.ed-label');
            if (f) { f.setAttribute('aria-invalid', 'true'); first = first || f; }
        }
        if (first) first.focus();
    }

    // problems() and the server index the rows as the page has them; the
    // server only sees rows with content, so its index maps through this.
    function rowsWithContent() { return rows(); }
    function postedRows() { return rows().filter(li => !_blank(readRow(li))); }

    function newRow(after) {
        const frag = tpl.content.cloneNode(true);
        const li = frag.querySelector('li');
        li.dataset.uid = mintUid();
        if (after && after.parentNode === list) after.after(li); else list.appendChild(li);
        paintHead();
        return li;
    }

    function setKind(li, kind) {
        li.dataset.kind = kind;
        li.querySelectorAll('.ed-kinds [role="radio"]').forEach(b => {
            b.setAttribute('aria-checked', b.dataset.kind === kind ? 'true' : 'false');
        });
        const num = li.querySelector('.ed-number');
        if (num) num.hidden = kind !== 'number';
        paintHead();
    }

    // Enter adds the next item (T4a: "item 1, Enter"), from the label or from
    // any of a number's fields, so "PSI", Enter goes straight on.
    list.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' || ev.isComposing) return;
        const field = ev.target.closest('.ed-label, .ed-units, .ed-min, .ed-max');
        if (!field) return;
        ev.preventDefault();
        const all = rows();
        const li = field.closest('li.ed-item');
        const n = all.indexOf(li);
        const go = enterFrom(all.map(r => r.querySelector('.ed-label').value), n);
        if (go.add) newRow(li).querySelector('.ed-label').focus();
        else all[go.focus].querySelector('.ed-label').focus();
    });

    list.addEventListener('click', (ev) => {
        const kindBtn = ev.target.closest('.ed-kinds [role="radio"]');
        const li = ev.target.closest('li.ed-item');
        if (!li) return;
        if (kindBtn) {
            setKind(li, kindBtn.dataset.kind);
            // a Number goes straight to its units: "Number, PSI, Enter"
            if (kindBtn.dataset.kind === 'number') li.querySelector('.ed-units').focus();
            return;
        }
        if (ev.target.closest('.ed-more')) { openOptions(li); return; }
        if (ev.target.closest('.ed-up') || ev.target.closest('.ed-down')) {
            const up = !!ev.target.closest('.ed-up');
            const n = rows().indexOf(li);
            moveRow(n, n + (up ? -1 : 1));
            const btn = li.querySelector(up ? '.ed-up' : '.ed-down');
            (btn && getComputedStyle(btn).visibility !== 'hidden' ? btn : li.querySelector('.ed-label')).focus();
            return;
        }
        if (ev.target.closest('.ed-remove')) {
            const to = li.nextElementSibling || li.previousElementSibling;
            li.remove();
            if (!rows().length) newRow();
            (to && to.isConnected ? to : rows()[0]).querySelector('.ed-label').focus();
            paintHead();
        }
    });

    // arrow keys move through a kind seg, as in any radiogroup
    list.addEventListener('keydown', (ev) => {
        const b = ev.target.closest('.ed-kinds [role="radio"]');
        if (!b || !/^Arrow(Left|Right)$/.test(ev.key)) return;
        ev.preventDefault();
        const all = Array.from(b.parentNode.children);
        const nxt = all[(all.indexOf(b) + (ev.key === 'ArrowRight' ? 1 : all.length - 1)) % all.length];
        setKind(b.closest('li.ed-item'), nxt.dataset.kind);
        nxt.focus();
    });

    // ── reorder: arrows for keyboards, drag by the number for everyone ─────
    function applyOrder(next) {
        const byUid = new Map(rows().map(li => [li.dataset.uid, li]));
        for (const it of next) {
            const li = byUid.get(it.uid);
            if (!li) continue;
            li.dataset.parent = it.parent_uid || '';
            list.appendChild(li);
        }
        paintHead();
    }
    function moveRow(from, to) {
        applyOrder(moveItem(rows().map(readRow), from, to));
    }
    let dragFrom = -1;
    list.addEventListener('dragstart', (ev) => {
        const grip = ev.target.closest && ev.target.closest('.ed-n');
        if (!grip) return;
        const li = grip.closest('li.ed-item');
        dragFrom = rows().indexOf(li);
        li.classList.add('dragging');
        try { ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', li.dataset.uid); } catch (_e) { /* old browser */ }
    });
    list.addEventListener('dragover', (ev) => {
        if (dragFrom < 0) return;
        const li = ev.target.closest('li.ed-item');
        if (!li) return;
        ev.preventDefault();
        rows().forEach(r => r.classList.toggle('drop-here', r === li));
    });
    list.addEventListener('drop', (ev) => {
        if (dragFrom < 0) return;
        ev.preventDefault();
        const li = ev.target.closest('li.ed-item');
        const to = li ? rows().indexOf(li) : rows().length - 1;
        const from = dragFrom;
        dragFrom = -1;
        rows().forEach(r => r.classList.remove('drop-here', 'dragging'));
        moveRow(from, to);
    });
    list.addEventListener('dragend', () => {
        dragFrom = -1;
        rows().forEach(r => r.classList.remove('drop-here', 'dragging'));
    });

    // ── options: heading, subtask, days (a sheet, so the row stays short) ──
    const sheet = $('item-sheet');
    let optRow = null;
    function optType(t) {
        $('opt-type').querySelectorAll('[role="radio"]').forEach(b => b.setAttribute('aria-checked', b.dataset.type === t ? 'true' : 'false'));
        $('opt-parent-row').hidden = t !== 'subtask';
        $('opt-days-row').hidden = t === 'header';
    }
    function openOptions(li) {
        optRow = li;
        const it = readRow(li);
        $('item-sheet-label').textContent = it.text.trim() || 'This item has no label yet.';
        const sel = $('opt-parent');
        sel.replaceChildren();
        const above = rows().slice(0, rows().indexOf(li)).filter(r => (r.dataset.type || 'item') !== 'subtask');
        for (const r of above) {
            const o = document.createElement('option');
            o.value = r.dataset.uid;
            o.textContent = r.querySelector('.ed-label').value.trim() || '(no label yet)';
            sel.append(o);
        }
        if (it.parent_uid) sel.value = it.parent_uid;
        else if (above.length) sel.value = above[above.length - 1].dataset.uid;
        const subtaskBtn = $('opt-type').querySelector('[data-type="subtask"]');
        subtaskBtn.disabled = !above.length;
        subtaskBtn.title = above.length ? '' : 'A subtask needs an item above it';
        optType(it.item_type || 'item');
        const days = new Set((it.days_active || []).map(Number));
        $('opt-days').querySelectorAll('.opt-day').forEach(b => b.setAttribute('aria-pressed', days.has(Number(b.dataset.day)) ? 'true' : 'false'));
        sheet.showModal();
    }
    $('opt-type').addEventListener('click', (ev) => {
        const b = ev.target.closest('[role="radio"]');
        if (b && !b.disabled) optType(b.dataset.type);
    });
    $('opt-days').addEventListener('click', (ev) => {
        const b = ev.target.closest('.opt-day');
        if (b) b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    });
    $('item-sheet-cancel').addEventListener('click', () => sheet.close());
    $('item-sheet-done').addEventListener('click', () => {
        const li = optRow;
        if (!li || !li.isConnected) { sheet.close(); return; }
        const t = ($('opt-type').querySelector('[aria-checked="true"]') || {}).dataset;
        const type = (t && t.type) || 'item';
        li.dataset.type = type;
        li.dataset.parent = type === 'subtask' ? $('opt-parent').value : '';
        const days = Array.from($('opt-days').querySelectorAll('.opt-day[aria-pressed="true"]')).map(b => Number(b.dataset.day));
        li.dataset.days = JSON.stringify(type === 'header' || days.length === 7 ? [] : days);
        if (type === 'header') setKind(li, 'none');
        sheet.close();
        paintHead();
        li.querySelector('.ed-more').focus();
    });

    $('ed-add').addEventListener('click', () => {
        const last = rows()[rows().length - 1];
        const reuse = last && !_blank(readRow(last)) ? null : last;
        (reuse || newRow(last)).querySelector('.ed-label').focus();
    });

    $('ed-slot').addEventListener('click', (ev) => {
        const b = ev.target.closest('[role="radio"]');
        if (!b) return;
        $('ed-slot').querySelectorAll('[role="radio"]').forEach(x => {
            x.setAttribute('aria-checked', x === b ? 'true' : 'false');
        });
        paintHead();
    });

    main.addEventListener('input', () => { clearErrorsSoft(); paintHead(); });
    main.addEventListener('change', paintHead);
    // the field you are fixing loses its mark as you type; the rest stay
    function clearErrorsSoft() {
        const el = document.activeElement;
        if (el && el.getAttribute('aria-invalid') === 'true') {
            el.removeAttribute('aria-invalid');
            const li = el.closest('li.ed-item');
            if (li && !li.querySelector('[aria-invalid="true"]')) li.querySelector('.ed-err').hidden = true;
            if (el.id === 'ed-due') $('ed-due-err').hidden = true;
        }
    }

    function toast(msg, kind) { if (root.LEMShell && root.LEMShell.toast) root.LEMShell.toast(msg, kind); }

    function hhmm() { const d = new Date(); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }

    async function save() {
        if (saving) return;
        clearErrors();
        const def = current();
        const bad = problems(def);
        if (bad.length) { showProblems(bad); setState('Not saved: fix what is marked'); return; }
        const body = payload(def);
        if (!body.items.length) {
            showProblems([{ index: 0, field: 'text', error: 'Add at least one item.' }]);
            return;
        }
        saving = true;
        saveBtn.disabled = true;
        setState('Saving…');
        const LEMjs = root.LEM;
        const r = await LEMjs.send('/api/checklists', { body, fallback: 'The round did not save.' });
        saving = false;
        saveBtn.disabled = false;
        if (r.status === 401) {
            document.body.dataset.user = '';
            document.body.classList.add('anon');
            setState('Not saved: sign in to save');
            if (root.LEMSignIn) root.LEMSignIn.need('save the round', save);
            return;
        }
        if (!r.ok) {
            const b = r.body || {};
            if (r.status === 400 && typeof b.item === 'number') {
                // the server's index counts the rows it was sent
                const li = postedRows()[b.item];
                const n = li ? rows().indexOf(li) : -1;
                showProblems([{ index: n, field: b.field || 'text', error: b.error || 'This item was refused.' }]);
            } else if (r.status === 400 && b.field === 'name') {
                showProblems([{ index: -1, field: 'name', error: b.error }]);
            } else {
                errEl.textContent = r.error || 'The round did not save.';
                errEl.hidden = false;
            }
            setState('Not saved');
            return;
        }
        savedSig = JSON.stringify(payload(current()));
        if (LEMjs && LEMjs.bust) LEMjs.bust('/api/checklists');
        const saved = (r.body && r.body.checklist) || body;
        // a tracked reading now names its thing
        const byUid = new Map((saved.items || []).map(i => [i.uid, i]));
        rows().forEach(li => {
            const s = byUid.get(li.dataset.uid);
            if (s) li.dataset.trackUid = s.track_uid || '';
        });
        if (isNew) {
            isNew = false;
            main.dataset.new = '';
            try { history.replaceState(null, '', '/checklists/edit/' + encodeURIComponent(body.uid)); } catch (_e) { /* old browser */ }
            const pill = $('ed-meta-new');
            if (pill) { pill.previousElementSibling.remove(); pill.remove(); }
            $('crumb-name').textContent = body.name;
            const arch = $('archive-new');
            if (arch) arch.textContent = 'No days yet. The first tick starts it.';
        }
        const open = $('open-round');
        if (open) {
            if (body.slot === 'opening' || body.slot === 'closing') {
                open.href = '/checklists/' + body.slot;
                open.hidden = false;
            } else open.hidden = true;
        }
        setState('Saved ' + hhmm());
        paintHead();
        toast(body.name + ' saved · ' + countWords(body.items));
    }
    saveBtn.addEventListener('click', save);
    // Ctrl/Cmd+S saves, as it does everywhere else
    document.addEventListener('keydown', (ev) => {
        if ((ev.ctrlKey || ev.metaKey) && (ev.key === 's' || ev.key === 'S')) { ev.preventDefault(); saveBtn.click(); }
    });

    window.addEventListener('beforeunload', (ev) => {
        if (dirty() && !saving) { ev.preventDefault(); ev.returnValue = ''; }
    });

    // ── delete ──────────────────────────────────────────────────────────────
    const del = $('ed-delete');
    if (del) {
        const sheet = $('delete-sheet');
        del.addEventListener('click', () => { $('delete-err').hidden = true; sheet.showModal(); });
        $('delete-cancel').addEventListener('click', () => sheet.close());
        $('delete-go').addEventListener('click', async () => {
            const go = $('delete-go');
            go.disabled = true;
            const r = await root.LEM.send('/api/checklists/' + encodeURIComponent(main.dataset.uid),
                { method: 'DELETE', fallback: 'The round was not deleted.' });
            go.disabled = false;
            if (r.status === 401) { sheet.close(); if (root.LEMSignIn) root.LEMSignIn.need('delete the round', () => del.click()); return; }
            if (!r.ok) { $('delete-err').textContent = r.error; $('delete-err').hidden = false; return; }
            savedSig = JSON.stringify(payload(current()));     // nothing left to lose
            if (root.LEM.bust) root.LEM.bust('/api/checklists');
            location.assign('/checklists/edit');
        });
    }

    paintHead();
    // The new round's first item has the caret (autofocus), so T4a types
    // straight away. Some browsers drop autofocus on a restored page.
    if (isNew) {
        const first = rows()[0];
        if (first && document.activeElement !== first.querySelector('.ed-label')) first.querySelector('.ed-label').focus();
    }
})(typeof window !== 'undefined' ? window : globalThis);
