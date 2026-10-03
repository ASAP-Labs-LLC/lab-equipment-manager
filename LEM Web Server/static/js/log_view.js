/* log_view.js: the machine log's rows and its Log entry sheet, drawn the
   same way on the record (#log) and on /logs (ia-final §3.1 #6, §3.6).

   Every server string goes in through textContent. A row is one hit area
   that opens the sheet: its first cell holds a real <button> (keyboard and
   screen readers reach it), and a click anywhere else on the row presses it.
   The instrument's name on /logs is a link of its own, to the record.
   The sheet is a native <dialog>: focus is trapped, Escape closes it, and
   focus goes back to the row's button. */
(function (root) {
    'use strict';
    const G = root.LEMLog;

    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(props || {})) {
            if (v === null || v === undefined || v === false) continue;
            if (k === 'className') el.className = v;
            else if (k === 'text') el.textContent = String(v);
            else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
            else el.setAttribute(k, v === true ? '' : String(v));
        }
        for (const c of kids.flat()) {
            if (c === null || c === undefined || c === false) continue;
            el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
        }
        return el;
    }

    // what the status cell says: a shape and a word, never colour alone
    function mark(e) {
        const d = e.detail || {};
        if (e.kind === 'qc') {
            const bad = d.in_spec === false || /^(fail|out|red)/i.test(String(d.verdict || ''));
            const good = d.in_spec === true || /^(pass|in|green|ok)/i.test(String(d.verdict || ''));
            if (bad) return { glyph: 'error', word: 'Out of spec', cls: 's-not_ok' };
            if (good) return { glyph: 'final', word: 'In spec', cls: '' };
        }
        if (e.kind === 'result_conflict') return { glyph: 'held', word: 'Held back', cls: 's-ok_but' };
        if (e.kind === 'held_expired') return { glyph: 'never', word: 'Not filed', cls: '' };
        return null;
    }

    /* cols: 'when','inst','kind','lab','test','value','who' */
    function row(e, opts) {
        const o = opts || {};
        const cols = o.cols || ['when', 'kind', 'lab', 'test', 'value'];
        const m = mark(e);
        const open = h('button', { type: 'button', className: 'row-open', text: G.whenText(e.ts),
            'aria-haspopup': 'dialog', title: G.whenFull(e.ts) });
        const cells = {
            when: () => h('td', { className: 'c-when' }, open),
            inst: () => h('td', { className: 'c-inst' }, e.machine_uid
                ? h('a', { className: 'inst-link', href: '/instruments/' + encodeURIComponent(e.machine_uid), text: e.machine_title || e.machine_uid })
                : h('span', { className: 'tag', text: 'Lab-wide' })),
            kind: () => h('td', { className: 'c-kind' },
                h('span', { className: 'kind-word', text: G.kindWord(e.kind) }),
                m ? h('span', { className: 'verdict ' + m.cls }, h('span', { className: 'glyph ' + m.glyph, 'aria-hidden': 'true' }), h('span', { text: m.word })) : null),
            lab: () => h('td', { className: 'c-lab', text: e.lab_id || '' }),
            test: () => h('td', { className: 'c-test', text: G.rowWhat(e) || (e.kind === 'result_conflict' || e.kind === 'reread' ? '' : G.summary(e)), title: G.summary(e) || null }),
            value: () => h('td', { className: 'c-value', text: G.rowValue(e) }),
            who: () => h('td', { className: 'c-who', text: G.rowWho(e) }),
        };
        const tr = h('tr', { className: 'log-row', 'data-id': e.id === null || e.id === undefined ? null : String(e.id), 'data-kind': e.kind },
            ...cols.map(c => cells[c]()));
        open.addEventListener('click', () => openSheet(e, o, open));
        tr.addEventListener('click', (ev) => {
            if (ev.target.closest('a, button')) return;          // its own control
            if (String(root.getSelection ? root.getSelection() : '').length) return;   // copying text
            open.click();
        });
        return tr;
    }

    function openSheet(e, opts, opener) {
        const d = document.getElementById('log-sheet');
        if (!d) return;
        const s = G.sheet(e, opts);
        const $ = (id) => document.getElementById(id);
        $('log-sheet-title').textContent = s.title;
        $('log-sheet-sub').textContent = s.where + ' · ' + s.when;
        const say = $('log-sheet-say');
        say.textContent = s.summary;
        say.hidden = !s.summary;
        const kv = (k, v, muted) => [h('div', { className: 'k', text: k }), h('div', { className: 'v' + (muted ? ' muted' : ''), text: v })];
        $('log-sheet-kv').replaceChildren(...s.rows.flatMap(r => kv(r.k, r.v, r.muted)));
        $('log-sheet-kv').hidden = !s.rows.length;
        $('log-sheet-extra').replaceChildren(...s.extra.flatMap(r => kv(r.k, r.v)));
        $('log-sheet-extra').hidden = $('log-sheet-extra-h').hidden = !s.extra.length;
        $('log-sheet-id').textContent = s.id === null ? '' : 'Entry ' + s.id;
        const go = $('log-sheet-go');
        go.href = s.link.href;
        go.textContent = s.link.text;
        d.__opener = opener || null;
        if (!d.open) d.showModal();
    }

    function wire() {
        const d = document.getElementById('log-sheet');
        if (!d || d.__wired) return;
        d.__wired = true;
        d.addEventListener('click', (ev) => {
            if (ev.target === d || ev.target.closest('[data-close]')) d.close();
        });
        d.addEventListener('close', () => { if (d.__opener && d.__opener.isConnected) d.__opener.focus(); });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();

    root.LEMLogView = { row, openSheet, mark, h };
})(window);
