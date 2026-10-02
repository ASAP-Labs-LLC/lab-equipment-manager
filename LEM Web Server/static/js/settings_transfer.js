/* settings_transfer.js: Settings › Transfer (transfer §14).

   Reads GET /api/transfer/overview on arrival and every 30 s while visible
   (GET only). Draws four blocks:
     Benches   one row per instrument: module, road, last delivered, waiting,
               and the ONE action that row needs (Approve enrolment, Reset…,
               Retire…), each confirmed in a sheet first.
     Bridge    on or off, and every reason it may not be turned off yet, in
               the server's words; the switch asks in a sheet before freezing
               LabCore's copy.
     Journal   road C: choose a copied journal folder, see what it would add,
               then Import (the section's one ink button, only after a preview).
     Dedupe    the dry run per bench with its QC impact, the approver's
               decision (with his password, D7), and Apply.

   Every write goes through LEMTransfer.send and says what happened only
   after r.ok; a refusal keeps the sheet open with the server's sentence.
   Every string goes in through textContent. */
(function () {
    'use strict';
    const T = window.LEMTransfer;
    const $ = (id) => document.getElementById(id);

    function mount() {
        if (!T || !$('transfer') || !window.LEMShell) return;
        const S = window.LEMShell;
        const h = S.h;
        let readAt = Date.now();

        function say(id, text, kind, list) {
            const el = $(id);
            el.className = 'msg' + (kind ? ' ' + kind : '');
            el.replaceChildren(h('span', { text: text || '' }));
            if (list && list.length) el.append(h('ul', { className: 'tr-reasons' }, ...list.map(t => h('li', { text: String(t) }))));
        }

        // ── the sheet: one dialog for every confirmation ──────────────────
        const sheet = $('tr-sheet');
        let onGo = null;
        function ask(o) {
            $('tr-sheet-title').textContent = o.title;
            $('tr-sheet-why').textContent = o.why;
            $('tr-sheet-field').hidden = !o.field;
            $('tr-sheet-decision').hidden = !o.decision;
            const input = $('tr-sheet-input');
            input.value = '';
            input.type = o.password ? 'password' : 'text';
            $('tr-sheet-label').textContent = o.field || '';
            $('tr-sheet-go').textContent = o.go;
            $('tr-sheet-go').className = 'btn sheet-go' + (o.danger ? ' btn-danger' : '');
            $('tr-sheet-err').hidden = true;
            onGo = o.run;
            sheet.showModal();
            if (o.field) input.focus();
        }
        $('tr-sheet-form').addEventListener('submit', async (ev) => {
            ev.preventDefault();
            if (!onGo) return;
            const go = $('tr-sheet-go');
            go.disabled = true;
            const was = go.textContent;
            go.textContent = 'Saving…';
            const decision = (document.querySelector('input[name="tr-decision"]:checked') || {}).value;
            const out = await onGo($('tr-sheet-input').value, decision);
            go.disabled = false;
            go.textContent = was;
            if (out && out.ok === false) {
                const err = $('tr-sheet-err');
                err.hidden = false;
                err.textContent = out.text;
                return;
            }
            sheet.close();
            if (out && out.done) S.toast(out.done);
        });
        sheet.querySelector('[data-close]').addEventListener('click', () => sheet.close());

        // ── benches ────────────────────────────────────────────────────────
        function actButton(v) {
            if (!v.action) return null;
            return h('button', { type: 'button', className: 'btn btn-sm' + (v.action.kind === 'retire' ? ' btn-ghost' : ''),
                                 'data-act': v.action.kind, 'data-uid': v.uid,
                                 'data-gated': v.action.kind === 'approve' ? 'approve this bench' : v.action.kind === 'reset' ? 'reset this bench' : 'retire this instrument',
                                 text: v.action.label });
        }

        function benchRows(list) {
            const el = (readAt ? (Date.now() - readAt) / 1000 : 0);
            return list.map((b) => {
                const v = T.benchRow(b, el);
                const name = v.href ? h('a', { className: 'link', href: v.href, text: v.title }) : h('span', { text: v.title });
                const sub = [v.module, v.note].filter(Boolean).join(' · ');
                return h('tr', { 'data-uid': v.uid },
                    h('th', { scope: 'row' }, name, sub ? h('span', { className: 'caption', text: sub }) : null),
                    h('td', { className: 'tr-c-road', text: v.road }),
                    h('td', {}, h('span', { className: 'tr-line' }, h('span', { className: 'glyph ' + v.delivered.glyph, 'aria-hidden': 'true' }),
                        h('span', { text: v.delivered.text })),
                        // the phone has no Waiting column: what waits is said here
                        (v.waiting !== '—' && v.waiting !== '0')
                            ? h('span', { className: 'caption tr-wait-inline', text: v.waiting + ' waiting' }) : null),
                    h('td', { className: 'tr-c-wait num', text: v.waiting }),
                    h('td', { className: 'tr-c-act' }, actButton(v)));
            });
        }

        function olderRows(g) {
            const out = [];
            if (g.older.length) {
                out.push(h('div', { className: 'k', text: 'Older module (3.9)' }),
                    h('div', { className: 'v' },
                        h('span', { className: 'tr-line' }, h('span', { className: 'glyph final', 'aria-hidden': 'true' }),
                            h('span', { className: 'val', text: g.older.length + ' ' + (g.older.length === 1 ? 'bench' : 'benches') + ': ' + T.names(g.older) })),
                        h('span', { className: 'caption', text: 'Their readings reach LEM through LabCore and the bridge. Each moves to the table above when its LabStation gets module 4.' })));
            }
            if (g.stopped.length) {
                const n = g.stopped.length;
                out.push(h('div', { className: 'k', text: 'Stopped' }),
                    h('div', { className: 'v' },
                        h('span', { className: 'tr-line' }, h('span', { className: 'glyph never', 'aria-hidden': 'true' }),
                            h('span', { className: 'val', text: n + ' ' + (n === 1 ? 'bench is' : 'benches are') + ' on the older module and not checking in' })),
                        h('span', { className: 'caption', text: 'Retire one that is gone for good: the bridge waits for every bench that is not retired.' }),
                        h('ul', { className: 'tr-stopped' }, ...g.stopped.map((b) => {
                            const v = T.benchRow(b, 0);
                            return h('li', {},
                                v.href ? h('a', { className: 'link', href: v.href, text: v.title }) : h('span', { text: v.title }),
                                actButton(v));
                        }))));
            }
            return out;
        }

        function paintBridge(o) {
            const v = T.bridgeView(o);
            $('tr-bridge-state').textContent = v.state;
            $('tr-bridge-why').textContent = v.why;
            $('tr-bridge-reasons').replaceChildren(...v.reasons.map(r => h('li', { text: r })));
            const b = $('cust-bridge');
            if (b) {
                b.hidden = !v.canChange;
                b.dataset.on = (o.bridge && o.bridge.on) ? 'true' : 'false';
                b.textContent = (o.bridge && o.bridge.on) ? 'Turn the bridge off…' : 'Turn the bridge back on';
                // offered, never forced: with reasons outstanding the server
                // would refuse, so the button says why instead of failing
                b.disabled = !!(o.bridge && o.bridge.on && v.reasons.length);
                b.title = b.disabled ? 'Not yet: see the reasons beside it.' : '';
            }
        }

        function render(out) {
            const st = $('tr-state');
            if (out.error && !out.overview) {
                st.hidden = false;
                st.dataset.state = 'error';
                $('tr-state-text').textContent = out.error;
                $('tr-retry').hidden = false;
                $('tr-benches-block').hidden = true;
                $('tr-bridge-block').hidden = true;
                return;
            }
            if (out.error) {
                st.hidden = false;
                st.dataset.state = 'error';
                $('tr-state-text').textContent = 'Not current: ' + out.error;
                $('tr-retry').hidden = false;
            } else {
                st.hidden = true;
                $('tr-retry').hidden = true;
                if (!out.tick) readAt = Date.now();
            }
            const o = out.overview;
            $('tr-benches-block').hidden = false;
            $('tr-bridge-block').hidden = false;
            const g = T.benchGroups(o);
            $('tr-benches-body').replaceChildren(...benchRows(g.v4));
            $('tr-benches').hidden = !g.v4.length;
            $('tr-v4-empty').hidden = g.v4.length > 0;
            $('tr-older').replaceChildren(...olderRows(g));
            paintBridge(o);
        }

        const ctl = T.createSettingsTransfer({
            fetch: window.fetch.bind(window), timers: window, render,
            isVisible: () => document.visibilityState !== 'hidden',
        });
        $('tr-retry').addEventListener('click', () => ctl.load());

        $('tr-benches-block').addEventListener('click', (ev) => {
            const btn = ev.target.closest && ev.target.closest('button[data-act]');
            if (!btn) return;
            const uid = btn.dataset.uid;
            const row = ((ctl.overview() || {}).benches || []).find(b => b.machine_uid === uid) || { title: uid };
            if (btn.dataset.act === 'approve') {
                ask({ title: 'Let ' + row.title + ' enrol', go: 'Approve',
                      why: 'Its next enrolment gets a new token, and any token it held before stops working now. Approve only a bench you know asked.',
                      run: async () => { const r = await ctl.approve(uid); return r.ok ? { done: row.title + ' may enrol now.' } : r; } });
            } else if (btn.dataset.act === 'reset') {
                ask({ title: 'Reset ' + row.title + '\'s token', go: 'Reset the token', danger: true,
                      why: 'Its syncs are refused from now on, and its next enrolment waits for someone to approve it here. Its readings stay safe in its journal meanwhile.',
                      run: async () => { const r = await ctl.reset(uid); return r.ok ? { done: row.title + '\'s token was reset.' } : r; } });
            } else if (btn.dataset.act === 'retire') {
                ask({ title: 'Retire ' + row.title, go: 'Retire it', danger: true, field: 'Type its name to confirm',
                      why: 'It leaves every list and the bridge stops waiting for it. Its history is hidden, not deleted: un-retiring brings it back.',
                      run: async (typed) => {
                          if (typed.trim() !== String(row.title).trim()) return { ok: false, text: 'Type the name exactly: ' + row.title };
                          const r = await ctl.retire(uid);
                          return r.ok ? { done: row.title + ' was retired.' } : r;
                      } });
            }
        });

        const bridgeBtn = $('cust-bridge');
        if (bridgeBtn) {
            bridgeBtn.addEventListener('click', () => {
                const on = bridgeBtn.dataset.on === 'true';
                ask({ title: on ? 'Turn the bridge off' : 'Turn the bridge back on',
                      go: on ? 'Turn it off' : 'Turn it on', danger: on,
                      why: on ? 'LEM stops copying its record into LabCore, and LabCore\'s copy of LEM\'s tables is frozen as of now. Benches on the older module would stop seeing changes.'
                              : 'LEM copies its record into LabCore again. Nothing is lost either way.',
                      run: async () => {
                          const r = await ctl.bridge(!on);
                          if (r.status === 409 && r.body && r.body.refusals) {
                              return { ok: false, text: 'The bridge stays on: ' + r.body.refusals.join(' ') };
                          }
                          return r.ok ? { done: on ? 'The bridge is off.' : 'The bridge is on again.' } : r;
                      } });
            });
        }

        // ── road C: the journal folder ────────────────────────────────────
        let chosen = null;
        function form() {
            const fd = new FormData();
            for (const f of chosen || []) if (/\.jsonl$/i.test(f.name)) fd.append('files', f, f.name);
            return fd;
        }
        $('tr-journal-files').addEventListener('change', async (ev) => {
            chosen = Array.from(ev.target.files || []);
            $('tr-journal-go-row').hidden = true;
            const segs = chosen.filter(f => /\.jsonl$/i.test(f.name));
            if (!segs.length) { say('tr-journal-msg', 'No seg-*.jsonl files in that folder. Choose the bench\'s lem_journal folder for one instrument.', 'err'); return; }
            say('tr-journal-msg', 'Checking ' + segs.length + ' ' + (segs.length === 1 ? 'segment' : 'segments') + '…');
            const res = await ctl.journal(form(), true);
            if (!res.ok) { say('tr-journal-msg', res.text, 'err'); return; }
            const p = T.journalPreview(res.body);
            const out = $('tr-journal-out');
            out.hidden = false;
            out.replaceChildren(h('div', { className: 'kv' }, ...p.lines.flatMap(l => [
                h('div', { className: 'k', text: l.name + (l.epoch ? ' · ' + l.epoch : '') }),
                h('div', { className: 'v' }, h('span', { text: l.text }), l.note ? h('span', { className: 'caption', text: l.note }) : null)])));
            say('tr-journal-msg', p.label ? '' : 'Nothing to import: LEM already holds every record in the folder.');
            $('tr-journal-go').textContent = p.label || '';
            $('tr-journal-go-row').hidden = !p.label;
        });
        $('tr-journal-go').addEventListener('click', async () => {
            const go = $('tr-journal-go');
            go.disabled = true;
            const res = await ctl.journal(form(), false);
            go.disabled = false;
            if (!res.ok) { say('tr-journal-msg', res.text, 'err'); return; }
            const o = T.journalOutcome(res.body);
            say('tr-journal-msg', o.text, o.tone === 'done' ? 'ok' : 'err', o.notes);
            $('tr-journal-go-row').hidden = true;
        });
        $('tr-journal-cancel').addEventListener('click', () => {
            chosen = null;
            $('tr-journal-files').value = '';
            $('tr-journal-out').hidden = true;
            $('tr-journal-go-row').hidden = true;
            say('tr-journal-msg', '');
        });

        // ── dedupe ─────────────────────────────────────────────────────────
        async function drawApprovals(box) {
            const res = await ctl.approvals();
            if (!res.ok) { box.append(h('p', { className: 'msg err', text: res.text })); return; }
            const list = (res.body && res.body.approvals) || [];
            if (!list.length) return;
            box.append(h('h4', { className: 'sub-h', text: 'Decisions on record' }),
                h('div', { className: 'kv' }, ...list.flatMap(a => [
                    h('div', { className: 'k', text: a.title + ' · ' + a.candidates + ' rows' }),
                    h('div', { className: 'v' },
                        h('span', { text: (a.decision === 'approved' ? 'Approved' : 'Rejected') + ' by ' + a.approved_by + ', ' + String(a.approved_at || '').slice(0, 16).replace('T', ' ') }),
                        a.decision === 'approved' && !a.applied
                            ? h('button', { type: 'button', className: 'btn btn-sm', 'data-apply': String(a.id), 'data-gated': 'hide these rows', text: 'Apply' })
                            : h('span', { className: 'caption', text: a.applied ? 'applied: ' + a.applied + ' rows hidden' : 'nothing hidden' }))])));
        }
        $('tr-dedupe-run').addEventListener('click', async () => {
            const btn = $('tr-dedupe-run');
            btn.disabled = true;
            say('tr-dedupe-msg', 'Reading the whole record; this can take a minute…');
            const res = await ctl.dedupeRun();
            btn.disabled = false;
            const box = $('tr-dedupe-out');
            if (!res.ok) { say('tr-dedupe-msg', res.text, 'err'); return; }
            const titles = {};
            for (const b of ((ctl.overview() || {}).benches || [])) titles[b.machine_uid] = b.title;
            const rows = T.dedupeRows(res.body, titles);
            say('tr-dedupe-msg', rows.length ? '' : 'Nothing to hide: the dry run found no re-read rows.');
            box.replaceChildren(h('div', { className: 'kv' }, ...rows.flatMap(r => [
                h('div', { className: 'k', text: r.title }),
                h('div', { className: 'v' }, h('span', { text: r.text }), h('span', { className: 'caption', text: r.impact }),
                    h('button', { type: 'button', className: 'btn btn-sm', 'data-unit': r.unit, 'data-uid': r.machine_uid,
                                  'data-run': r.run_id, 'data-gated': 'decide on these rows', text: 'Decide…' }))])));
            await drawApprovals(box);
        });
        $('tr-dedupe-out').addEventListener('click', async (ev) => {
            const d = ev.target.closest && ev.target.closest('button[data-unit]');
            if (d) {
                ask({ title: 'Decide on ' + (d.closest('.v').previousElementSibling || {}).textContent,
                      go: 'Record the decision', field: 'Your password (approving is signed)', password: true, decision: true,
                      why: 'Approved rows stop counting in QC and the log once applied; every row stays in the record and can be brought back.',
                      run: async (pw, decision) => {
                          const r = await ctl.dedupeApprove({ machine_uid: d.dataset.uid, unit: d.dataset.unit,
                                                              run_id: d.dataset.run, password: pw, decision });
                          if (r.ok) $('tr-dedupe-run').click();
                          return r.ok ? { done: 'Decision recorded. Apply it to hide the rows.' } : r;
                      } });
                return;
            }
            const a = ev.target.closest && ev.target.closest('button[data-apply]');
            if (a) {
                a.disabled = true;
                const r = await ctl.dedupeApply(Number(a.dataset.apply));
                a.disabled = false;
                if (!r.ok) { say('tr-dedupe-msg', r.text, 'err'); return; }
                say('tr-dedupe-msg', 'Applied: ' + ((r.body && r.body.annotated) || 'the') + ' rows are hidden. Each can be brought back.', 'ok');
                $('tr-dedupe-run').click();
            }
        });

        ctl.load().then(() => ctl.start());
        // a hidden tab asks nothing; coming back into view, it re-reads at once
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') ctl.load();
        });
        // the ages tick on screen without asking anyone
        setInterval(() => { if (ctl.overview()) render({ overview: ctl.overview(), error: null, tick: true }); }, 5000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})();
