/* conflicts.js: /results/conflicts (templates/conflicts.html).

   The two buttons on each held result are the only writes, and each one
   goes through LEMTransfer.createConflicts().decide, which paints the row
   as decided only after the server's r.ok, and then re-reads the list so the
   row moves to Decided from the server's own word. A refusal stays on the
   row, in the server's sentence, with both buttons back. Signed out, the
   buttons are gated (signin.js): the sheet opens, and the click goes ahead
   after sign-in.

   The list re-reads every 15 s while the tab is visible (GET only), so a
   decision another person made elsewhere, or a bench taking one, shows up
   without a reload. Every server string goes in through textContent. */
(function () {
    'use strict';
    const T = window.LEMTransfer;
    const $ = (id) => document.getElementById(id);

    function mount() {
        const page = document.querySelector('[data-testid="conflicts-page"]');
        if (!T || !page || !window.LEMShell) return;
        const S = window.LEMShell;
        const h = S.h;
        let initial = null;
        try { initial = JSON.parse($('cf-data').textContent || 'null'); } catch (_e) { initial = null; }
        if (initial && !Object.keys(initial).length) initial = null;
        const busy = new Set();          // refs whose decision is in flight

        function row(c) {
            const keep = h('button', { type: 'button', className: 'btn btn-sm', 'data-choice': 'keep',
                                       'data-ref': c.ref, 'data-gated': 'decide this result', text: 'Keep LabCore value' });
            const send = h('button', { type: 'button', className: 'btn btn-sm', 'data-choice': 'send',
                                       'data-ref': c.ref, 'data-gated': 'decide this result', text: 'Send instrument value' });
            if (busy.has(c.ref)) { keep.disabled = true; send.disabled = true; }
            return h('li', { className: 'cf-row', 'data-ref': c.ref, 'data-theirs': c.theirs, 'data-testid': 'conflict' },
                h('div', { className: 'cf-what' },
                    h('p', { className: 'cf-title' }, h('b', { text: c.lab_id }),
                        h('span', { className: 'sep', 'aria-hidden': 'true', text: '·' }), c.test_name,
                        h('span', { className: 'sep', 'aria-hidden': 'true', text: '·' }),
                        h('a', { className: 'link', href: '/instruments/' + encodeURIComponent(c.machine_uid) + '#transfer', text: c.title })),
                    h('dl', { className: 'cf-vals' },
                        h('dt', { text: 'In LabCore' }),
                        h('dd', {}, h('b', { className: 'num', text: c.theirs }), ' ',
                            h('span', { className: 'caption', text: 'entered by ' + (c.their_operator || 'someone') + (c.their_at ? ', ' + c.their_at : '') })),
                        h('dt', { text: 'From the instrument' }),
                        h('dd', {}, h('b', { className: 'num', text: c.ours }), ' ',
                            h('span', { className: 'caption', text: c.print_at ? 'the print of ' + c.print_at : 'held ' + c.opened_at })))),
                h('div', { className: 'cf-acts', role: 'group', 'aria-label': 'Decide ' + c.lab_id + ' ' + c.test_name }, keep, send),
                h('p', { className: 'msg cf-msg', role: 'status' }));
        }

        function decidedRows(list) {
            return list.flatMap((c) => [
                h('div', { className: 'k' }, c.lab_id + ' · ' + c.test_name, h('span', { className: 'caption', text: c.title })),
                h('div', { className: 'v' },
                    h('span', { className: 'tr-line' },
                        h('span', { className: 'glyph ' + (c.delivered ? 'final' : 'working'), 'aria-hidden': 'true' }),
                        h('span', { className: 'val', text: c.choice === 'keep' ? "Kept LabCore's " + c.theirs : "Sent the instrument's " + c.ours })),
                    h('span', { className: 'caption', text: c.by + ', ' + c.decided_at + ' · ' +
                        (c.delivered ? 'taken by the bench ' + c.delivered_at : 'waiting for the bench to take it') }))]);
        }

        function rejectedRows(list) {
            return list.flatMap((b) => (b.cells && b.cells.length ? b.cells : [null]).flatMap((c) => c ? [
                h('div', { className: 'k' }, c.lab_id + ' · ' + c.test_name,
                    h('span', { className: 'caption' }, h('a', { className: 'link', href: b.href, text: b.title }))),
                h('div', { className: 'v' },
                    h('span', { className: 'tr-line' }, h('span', { className: 'glyph error', 'aria-hidden': 'true' }),
                        h('span', { className: 'val', text: c.value + ' not filed' })),
                    h('span', { className: 'caption', text: 'LabCore said: ' + (c.error || 'no reason given') + (c.at ? ' · ' + c.at : '') }))
            ] : [
                h('div', { className: 'k', text: b.title }),
                h('div', { className: 'v' },
                    h('span', { className: 'tr-line' }, h('span', { className: 'glyph error', 'aria-hidden': 'true' }),
                        h('span', { className: 'val', text: b.count + ' rejected' })),
                    h('span', { className: 'caption', text: 'The bench reported them; their details have not reached LEM yet.' }))]));
        }

        function paint(view) {
            const pill = T.conflictPill(view);
            const p = $('cf-pill');
            if (p) {
                p.className = 'pill ' + pill.tone;
                p.querySelector('.glyph').className = 'glyph ' + pill.glyph;
                $('cf-pill-text').textContent = pill.text;
            }
            if (!view) return;
            const open = view.open || [];
            $('cf-open').replaceChildren(...open.map(row));
            $('cf-empty').hidden = open.length > 0;
            const dec = view.decided || [];
            $('cf-decided').replaceChildren(...decidedRows(dec));
            $('cf-decided-empty').hidden = dec.length > 0;
            const rej = view.rejected || [];
            $('cf-rejected').replaceChildren(...rejectedRows(rej));
            $('cf-rejected-empty').hidden = rej.length > 0;
            const read = $('cf-read');
            if (read) read.textContent = 'Read ' + new Date().toTimeString().slice(0, 5);
        }

        function render(out) {
            const err = $('cf-error');
            if (out.error) {
                err.hidden = false;
                $('cf-error-text').textContent = out.view
                    ? 'Not current: ' + out.error + ' What is below is from the last read.'
                    : out.error;
                if (!out.view) paint(null);
                return;
            }
            err.hidden = true;
            paint(out.view);
        }

        const ctl = T.createConflicts({
            fetch: window.fetch.bind(window), timers: window, render, initial,
            machine: page.dataset.machine || '',
            isVisible: () => document.visibilityState !== 'hidden',
        });

        page.addEventListener('click', async (ev) => {
            const btn = ev.target.closest && ev.target.closest('button[data-choice]');
            if (!btn) return;
            const li = btn.closest('.cf-row');
            const ref = li.dataset.ref;
            if (busy.has(ref)) return;
            busy.add(ref);
            li.querySelectorAll('button').forEach(b => { b.disabled = true; });
            const msg = li.querySelector('.cf-msg');
            msg.className = 'msg cf-msg';
            msg.textContent = 'Saving…';
            const res = await ctl.decide(ref, btn.dataset.choice, li.dataset.theirs);
            busy.delete(ref);
            if (!res.ok) {
                const still = document.querySelector('.cf-row[data-ref="' + (window.CSS && CSS.escape ? CSS.escape(ref) : ref) + '"]');
                const m = still ? still.querySelector('.cf-msg') : msg;
                if (still) still.querySelectorAll('button').forEach(b => { b.disabled = false; });
                m.className = 'msg cf-msg err';
                m.textContent = res.text;
                return;
            }
            S.toast(res.text);
        });
        $('cf-retry').addEventListener('click', () => ctl.load());
        ctl.start();
        // a hidden tab asks nothing; coming back into view, it re-reads at once
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') ctl.load();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})();
