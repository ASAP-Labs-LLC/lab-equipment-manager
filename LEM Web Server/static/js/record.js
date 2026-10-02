/* record.js: the instrument record (ia-final §3.1), drawn from
   /api/ui/instruments/<uid> (the first paint's copy rides in #record-data).

   What it draws, and the one rule for each:
     the head        one pill (glyph + word, no tinted fill), then facts
     the card        the verdict, one sentence ending "Next: …", ONE primary
                     (the server's button, re-labelled here, never a second)
     the tiles       QC · Bench · On line · Maintenance (only when scheduled);
                     the current one gets an ink border, never red
     the QC table    one row per check; the selected row draws the chart
     the chart       24 runs · 90 days · All; certificate centre and limits;
                     ▲ outside them; a control finding is a caption marked
                     provisional, never a second verdict; one U line
   Every failed read has its own sentence, distinct from "none yet". Every
   server string goes in through textContent. GETs on the live feed only;
   the two sheets POST when a person presses their button. */
(function () {
    'use strict';
    const R = window.LEMRecord;
    const L = window.LEMInstruments;
    const S = window.LEMShell;
    const h = S.h;
    const $ = (id) => document.getElementById(id);
    const SVG = 'http://www.w3.org/2000/svg';
    const page = $('main');
    const uid = page.dataset.uid;
    const hasQuality = page.dataset.hasQuality === 'true';

    let data = null;
    try { data = JSON.parse(($('record-data') || {}).textContent || 'null'); } catch (_e) { data = null; }
    if (!data || data.state !== 'ready') return;

    let selected = data.qc.selected;
    let range = '24';
    const trend = {};          // range -> {state:'loading'|'ok'|'err', series, error}
    const uncert = {};         // test -> {ok, current, error} | 'loading'

    S.addRecent({ href: '/instruments/' + encodeURIComponent(uid), label: data.title });

    // ── glyphs (§4.1): a shape per state, never colour alone ──────────────
    function svg(tag, attrs) {
        const el = document.createElementNS(SVG, tag);
        for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
        return el;
    }
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
        } else {
            box.appendChild(svg('circle', { cx: 11, cy: 11, r: 9.25, class: 'ring dashed' }));
        }
        return box;
    }
    const glyph = (kind) => h('span', { className: 'glyph ' + kind, 'aria-hidden': 'true' });
    const VERDICT_CLASS = { out: 's-not_ok', due: 's-ok_but', none: '', in: '' };

    // ── the head ──────────────────────────────────────────────────────────
    function renderHead() {
        // No status pill and no bench state here: the card right below says
        // the verdict, and its Bench tile the bench, and saying either again
        // 80px above would be the "said twice" defect (§0.2). The meta line
        // is the facts no tile carries.
        const hd = data.head;
        const bits = [];
        const add = (node) => { if (bits.length) bits.push(h('span', { className: 'sep', 'aria-hidden': 'true' }, '·')); bits.push(node); };
        // the bench's state is the Bench tile's; the head carries what no tile does
        if (hd.last_result_at) add(h('span', { text: 'Last result ' + L.when(hd.last_result_at) }));
        if (hd.level) add(h('span', { text: hd.level }));
        add(h('span', { className: 'mono', text: hd.uid }));
        $('rec-meta').replaceChildren(...bits);
        $('topbar-online').hidden = !data.topbar.online;
    }

    // ── the card ──────────────────────────────────────────────────────────
    function renderCard() {
        const r = data.readiness;
        $('ready-word').textContent = r.word;
        $('ready-word').className = 's-' + r.state;
        $('ready-glyph').replaceChildren(circle(r.glyph === 'final' ? 'ok' : r.glyph));
        $('ready-cap').textContent = R.captionText(r.caption);
        const p = $('ready-primary');
        if (r.primary) {
            p.textContent = r.primary.label;
            p.dataset.act = r.primary.act;
            p.dataset.gated = r.primary.act === 'action' ? 'open a corrective action' : 'put it back on line';
            p.hidden = false;
        } else {
            p.hidden = true;
            delete p.dataset.act;
            delete p.dataset.gated;
        }
        $('ready-tiles').replaceChildren(...r.tiles.map(tile));
        $('ready-tiles').dataset.n = String(r.tiles.length);
    }
    function tile(t) {
        const kids = [circle(t.glyph), h('span', { className: 't-title', text: t.title }),
            h('span', { className: 't-word', text: t.word })];
        const detail = t.detail + (t.at && t.key === 'bench' ? ' · ' + L.when(t.at) : '');
        if (detail) kids.push(h('span', { className: 't-detail', text: detail }));
        const a = t.action;
        if (a && a.href) kids.push(h('a', { className: 't-link', href: a.href, text: a.label }));
        return h('div', { className: 'rtile' + (t.current ? ' current' : ''), 'data-key': t.key, 'data-testid': 'ready-tile' }, ...kids);
    }

    // ── QC: the table ─────────────────────────────────────────────────────
    function renderQcIntro() {
        const stds = data.qc.standards;
        const p = $('qc-intro');
        const kids = [];
        if (stds.length) {
            kids.push('Checked against ');
            stds.forEach((s, i) => {
                if (i) kids.push(i === stds.length - 1 ? ' and ' : ', ');
                kids.push(hasQuality ? h('a', { className: 'link', href: '/quality/standards/' + encodeURIComponent(s), text: s }) : h('b', { text: s }));
            });
            kids.push(' by assignment. ');
        }
        kids.push('The bench reads these off the instrument; nobody types QC here.');
        p.replaceChildren(...kids);
    }
    function track(c) {
        const pos = R.bandPos(c, c.value);
        if (!pos) return h('span', { className: 'track none', 'aria-hidden': 'true' });
        const el = h('span', { className: 'track', 'aria-hidden': 'true' },
            h('i', { className: 'band', style: 'left:' + pos.lowPct + '%;width:' + (pos.highPct - pos.lowPct) + '%' }),
            h('i', { className: 'mid', style: 'left:' + pos.midPct + '%' }));
        if (pos.valPct !== null) {
            const past = c.verdict && c.verdict.key === 'none' ? ' past' : '';
            el.appendChild(h('i', { className: 'dot' + (pos.outside ? ' out ' + pos.outside : past), style: 'left:' + pos.valPct + '%' }));
        }
        return el;
    }
    function bandCell(c) {
        if (c.low === null && c.high === null) return h('span', { className: 'muted', text: 'No certified values on file' });
        const f = (v) => R.fmtQC(v, c);
        return h('span', { className: 'bandtxt' }, f(c.low) + ' – ', h('b', { text: f(c.expected) }), ' – ' + f(c.high) + (c.units ? ' ' + c.units : ''));
    }
    function renderQc() {
        const rows = data.qc.checks;
        const none = $('qc-none');
        $('qc-tbl').hidden = !rows.length;
        none.hidden = !!rows.length;
        $('export-qc').hidden = !rows.length;     // a file of nothing is not offered
        if (!rows.length) {
            // the card already says "No QC assigned"; this says what it means
            none.replaceChildren(glyph('never'), h('span', { text: 'Nothing is checked on this instrument, so nothing can say whether it reads true.' }));
            $('qc-chart').hidden = true;
            return;
        }
        $('qc-rows').replaceChildren(...rows.map(c => {
            const sel = c.test === selected;
            const v = c.verdict;
            // the standard is named once, in the section's sentence, unless
            // the checks are against more than one
            const sub = [c.method, data.qc.standards.length > 1 ? c.sample_id : ''].filter(Boolean).join(' · ');
            return h('tr', { className: 'qrow' + (sel ? ' is-sel' : '') + (v.key === 'none' ? ' is-past' : ''), 'data-test': c.test, 'aria-selected': sel ? 'true' : 'false', 'data-testid': 'qc-row' },
                h('td', { className: 'c-check' },
                    h('button', { type: 'button', className: 'qc-pick', 'aria-pressed': sel ? 'true' : 'false', title: 'Show its chart' }, c.title),
                    sub ? h('span', { className: 'sub', text: sub }) : null,
                    h('span', { className: 'fold-band' },
                        h('span', { className: 'fold-last', text: 'Last ' + R.fmtQC(c.value, c) + (c.at ? ' · ' + L.when(c.at) : '') }),
                        bandCell(c), track(c))),
                h('td', { className: 'c-band' }, bandCell(c)),
                h('td', { className: 'c-track' }, track(c)),
                h('td', { className: 'c-last num', text: R.fmtQC(c.value, c) }),
                h('td', { className: 'c-verdict' }, h('span', { className: 'verdict ' + (VERDICT_CLASS[v.key] || '') }, glyph(v.glyph),
                    h('span', { text: v.word })), v.detail ? h('span', { className: 'sub', text: v.detail }) : null),
                h('td', { className: 'c-when', text: c.at ? L.when(c.at) : '' }));
        }));
    }
    $('qc-rows').addEventListener('click', (ev) => {
        const tr = ev.target.closest('tr.qrow');
        if (!tr || tr.dataset.test === selected) return;
        selected = tr.dataset.test;
        renderQc();
        renderChart();
        const btn = document.querySelector('tr.qrow.is-sel .qc-pick');
        if (btn && ev.target.closest('.qc-pick')) btn.focus();
    });

    // ── QC: the chart ─────────────────────────────────────────────────────
    // a network failure is "LEM did not answer", not the browser's own words;
    // a server sentence is passed on as it was said (without its full stop,
    // which the sentence around it supplies)
    function why(e) {
        if (!e || e.name === 'TypeError') return 'LEM did not answer';
        return String(e.message || 'no answer').replace(/[.\s]+$/, '');
    }
    function selCheck() { return data.qc.checks.find(c => c.test === selected) || null; }
    function loadTrend(rng) {
        trend[rng] = { state: 'loading' };
        renderChart();
        return fetch('/api/machines/' + encodeURIComponent(uid) + '/qc-trend?range=' + rng, { headers: { 'X-LEM-Background': '1' } })
            .then(r => r.json().catch(() => ({})).then(b => ({ r, b })))
            .then(({ r, b }) => {
                if (!r.ok || b.error) throw new Error(b.error || ('LEM answered ' + r.status));
                trend[rng] = { state: 'ok', series: b.series || [] };
            })
            .catch(e => { trend[rng] = { state: 'err', error: why(e) }; })
            .finally(renderChart);
    }
    function loadU(test) {
        uncert[test] = 'loading';
        fetch('/api/uncertainty/' + encodeURIComponent(uid) + '/' + encodeURIComponent(test), { headers: { 'X-LEM-Background': '1' } })
            .then(r => r.json().catch(() => ({})).then(b => ({ r, b })))
            .then(({ r, b }) => {
                if (!r.ok || b.error) throw new Error(b.error || ('LEM answered ' + r.status));
                uncert[test] = { ok: true, current: b.current || null };
            })
            .catch(e => { uncert[test] = { ok: false, error: why(e) }; })
            .finally(renderU);
    }
    function renderU() {
        const c = selCheck();
        const el = $('chart-u');
        if (!c) { el.textContent = ''; return; }
        const u = uncert[c.test];
        if (u === undefined) { loadU(c.test); return; }
        el.className = 'chart-line' + (u !== 'loading' && !u.ok ? ' err' : '');
        el.textContent = u === 'loading' ? 'Reading the uncertainty register…' : R.uLine(u, c);
    }
    function note(kind, text, retry) {
        return h('div', { className: 'chart-note' + (kind === 'err' ? ' err' : ''), role: 'status' },
            glyph(kind === 'err' ? 'error' : 'never'), h('span', { text }),
            retry ? h('button', { type: 'button', className: 'btn btn-ghost btn-sm', onclick: retry, text: 'Try again' }) : null);
    }
    function renderChart() {
        const c = selCheck();
        const card = $('qc-chart');
        card.hidden = !c;
        if (!c) return;
        $('chart-h').replaceChildren(h('span', { text: c.title }), c.sample_id ? h('span', { className: 'muted', text: ' · ' + c.sample_id }) : null);
        for (const b of $('chart-range').querySelectorAll('button')) b.setAttribute('aria-checked', String(b.dataset.range === range));
        const plot = $('chart-plot');
        const ctl = $('chart-control');
        const cap = $('chart-cap');
        renderU();
        ctl.hidden = true;
        // no certified values: the row already says so, and a chart with
        // nothing to draw against would say it a second time
        if (c.low === null && c.high === null) { card.hidden = true; return; }
        const t = trend[range];
        if (!t) { loadTrend(range); return; }
        if (t.state === 'loading') {
            cap.textContent = 'Reading its QC history…';
            plot.replaceChildren(h('div', { className: 'chart-skel', 'aria-busy': 'true' }, h('i'), h('i'), h('i')));
            return;
        }
        if (t.state === 'err') {
            cap.textContent = '';
            plot.replaceChildren(note('err', 'Couldn\'t read this check\'s history: ' + t.error + '. The table above is from the last reading.', () => loadTrend(range)));
            return;
        }
        // the series for this check on the standard it is on NOW
        const s = t.series.find(x => x.test_name === c.test && x.sample_id === c.sample_id && !x.superseded) ||
                  t.series.find(x => x.test_name === c.test && !x.superseded) || null;
        if (!s || !(s.points || []).length) {
            // said once, in the plot; the caption line under the title stays
            // empty rather than repeat it, and the empty plot is not 176px
            cap.textContent = '';
            plot.classList.add('is-empty');
            plot.replaceChildren(note('none', range === '90d' ? 'No runs of this check in the last 90 days.'
                : 'No runs of this check on file yet. Its first run draws here.'));
            return;
        }
        plot.classList.remove('is-empty');
        cap.textContent = R.rangeCaption(s, range) + ' · centre is the certificate value';
        plot.replaceChildren(drawChart(s, c));
        const cc = R.controlCaption(s);
        ctl.hidden = !cc;
        ctl.replaceChildren(cc ? h('span', { className: 'ctl-k', text: 'Control' }) : '', cc ? h('span', { text: cc }) : '');
    }
    function drawChart(s, c) {
        const w = Math.max(320, Math.round($('chart-plot').clientWidth || 640));
        const H = 176;
        const m = R.chartModel({ points: s.points, low: c.low, high: c.high, expected: c.expected }, { w, h: H });
        const box = svg('svg', { viewBox: '0 0 ' + w + ' ' + H, width: '100%', height: H, class: 'qchart', role: 'img',
            'aria-label': c.title + ': ' + R.rangeCaption(s, range) + '. Limits ' + R.bandText(c) + (c.units ? ' ' + c.units : '') + '.' });
        const P = m.plot;
        box.appendChild(svg('rect', { x: P.x0, y: m.lines.high, width: P.x1 - P.x0, height: Math.max(0, m.lines.low - m.lines.high), class: 'qband' }));
        const hline = (y, cls) => box.appendChild(svg('line', { x1: P.x0, x2: P.x1, y1: y, y2: y, class: cls }));
        hline(m.lines.high, 'qlim'); hline(m.lines.low, 'qlim'); if (m.lines.mid !== null) hline(m.lines.mid, 'qmid');
        const label = (y, k, v) => {
            const t = svg('text', { x: P.x1 + 10, y: y + 4, class: 'qlab' });
            t.appendChild(svg('tspan', { class: 'qk' })).textContent = k + ' ';
            t.appendChild(svg('tspan', {})).textContent = R.fmtQC(v, c);
            box.appendChild(t);
        };
        // labels that would overlap at a tight band: centre wins, limits move apart
        const gap = 13;
        let yh = m.lines.high, yl = m.lines.low;
        if (m.lines.mid !== null) { yh = Math.min(yh, m.lines.mid - gap); yl = Math.max(yl, m.lines.mid + gap); }
        label(yh, 'max', c.high); label(yl, 'min', c.low); if (m.lines.mid !== null) label(m.lines.mid, 'target', c.expected);
        if (m.points.length > 1) {
            box.appendChild(svg('polyline', { points: m.points.map(p => p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(' '), class: 'qline' }));
        }
        const lastI = m.points.length - 1;
        for (const p of m.points) {
            let el;
            if (p.outside) el = svg('path', { d: 'M' + p.x + ' ' + (p.y - 6) + 'l5.5 9.5h-11z', class: 'qpt out' });
            else el = svg('circle', { cx: p.x, cy: p.y, r: p.i === lastI ? 4 : 2.75, class: 'qpt' + (p.i === lastI ? ' last' : '') });
            const tip = svg('title', {});
            tip.textContent = L.when(p.ts) + ' · ' + R.fmtQC(p.value, c) + (c.units ? ' ' + c.units : '') + (p.outside ? ' · outside the limits' : '');
            el.appendChild(tip);
            box.appendChild(el);
        }
        // the runs the control caption is about, ringed (never recoloured: the
        // finding is provisional, the verdict is the bench's)
        const vs = (s.violations || []).slice().sort((a, b) => Math.max(...b.indices) - Math.max(...a.indices));
        for (const i of (vs[0] ? vs[0].indices : [])) {
            const p = m.points[i];
            if (p) box.appendChild(svg('circle', { cx: p.x, cy: p.y, r: 7.5, class: 'qring' }));
        }
        for (const t of m.ticks) {
            const tx = svg('text', { x: t.x, y: H - 6, class: 'qtick', 'text-anchor': 'middle' });
            tx.textContent = t.label;
            box.appendChild(tx);
        }
        return box;
    }
    $('chart-range').addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-range]');
        if (!b || b.dataset.range === range) return;
        range = b.dataset.range;
        renderChart();
    });
    let resizeT = null;
    window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(renderChart, 120); });

    // ── Maintenance and Bench (read-only; piece 6 and 7 add their actions) ─
    function day(iso) { return R.dayIn(iso); }
    function renderMaintenance() {
        const tasks = data.maintenance;
        const body = $('mt-body');
        if (!tasks.length) {
            body.replaceChildren(h('p', { className: 'empty-note' }, glyph('never'), h('span', { text: 'Nothing scheduled for this instrument.' })));
            return;
        }
        body.replaceChildren(h('div', { className: 'card table-card' }, h('table', { className: 'tbl mt' },
            h('thead', {}, h('tr', {}, ...[['Task', ''], ['Every', 'm-every'], ['Last done', 'm-last'], ['Due', ''], ['', '']]
                .map(([t, cls]) => h('th', { scope: 'col', className: cls, text: t })))),
            h('tbody', {}, ...tasks.map(t => h('tr', {},
                h('td', {}, h('b', { text: t.name })),
                h('td', { className: 'm-every', text: t.every ? t.every + ' days' : '—' }),
                h('td', { className: 'm-last', text: day(t.last_done) }),
                h('td', { text: day(t.next_due) }),
                h('td', {}, h('span', { className: 'verdict ' + (t.glyph === 'error' ? 's-not_ok' : t.glyph === 'half' ? 's-ok_but' : '') }, glyph(t.glyph), h('span', { text: t.word })))))))),
            h('p', { className: 'caption' }, 'To schedule a task or mark one done, use the ', h('a', { className: 'link', href: '/maintenance/classic', text: 'PM and calibration page' }), ' until it moves here.'));
    }
    function renderBench() {
        const b = data.bench;
        const kv = (k, ...v) => [h('div', { className: 'k', text: k }), h('div', { className: 'v' }, ...v)];
        $('bench-body').replaceChildren(h('div', { className: 'kv' },
            ...kv('Reads from', b.reads_from ? h('span', { className: 'mono', text: b.reads_from }) : h('span', { className: 'muted', text: 'Not reported' })),
            ...kv('Bench', glyph(b.glyph === 'final' ? 'final' : 'dashed'), ' ' + b.word + (b.at ? ' · last poll ' + L.when(b.at) : '')),
            ...kv('Live road', b.live_road ? 'Yes: it reports to LEM directly' : 'No: its readings reach LEM via LabCore'),
            ...kv('Replays not re-sent', h('span', { className: 'muted', text: b.replays_not_resent === null ? 'Not reported by this bench\'s module version' : String(b.replays_not_resent) }))),
            h('p', { className: 'caption', text: 'Instruments are added and configured in LabStation\'s LEM module.' }));
    }

    // ── the two sheets ────────────────────────────────────────────────────
    function openSheet(id) { const d = $(id); if (!d.open) d.showModal(); }
    for (const d of document.querySelectorAll('dialog.rec-sheet')) {
        d.addEventListener('click', (ev) => { if (ev.target.closest('[data-close]')) d.close(); });
    }
    function sheetOnline(act) {
        const off = act === 'offline';
        $('online-title').textContent = (off ? 'Take ' : 'Put ') + data.title + (off ? ' off line' : ' back on line');
        $('online-why').textContent = off
            ? 'The bench stops reporting results from it until somebody puts it back. Say why; your name and the time are kept with it.'
            : 'Results from it count again. Say what was done; your name and the time are kept with it.';
        $('online-kind').hidden = !off;
        $('online-go').textContent = off ? 'Take off line' : 'Put back on line';
        $('online-form').dataset.act = act;
        $('online-err').hidden = true;
        $('online-comment').value = '';
        openSheet('online-sheet');
        $('online-comment').focus();
    }
    function sheetAction() {
        const p = data.readiness.primary || {};
        const sel = $('action-test');
        const failing = data.qc.checks.filter(c => c.verdict.key === 'out');
        const opts = (failing.length ? failing : data.qc.checks);
        sel.replaceChildren(...opts.map(c => h('option', { value: c.test, text: c.title + (c.sample_id ? ' · ' + c.sample_id : '') })));
        sel.value = p.test || (opts[0] && opts[0].test) || '';
        const c = data.qc.checks.find(x => x.test === sel.value);
        $('action-what').value = c ? (c.title + ' read ' + R.fmtQC(c.value, c) + (c.units ? ' ' + c.units : '') + ' on ' + (c.sample_id || 'the standard') +
            ', outside ' + R.bandText(c) + (c.at ? ' (' + L.when(c.at) + ')' : '') + '.') : '';
        $('action-err').hidden = true;
        openSheet('action-sheet');
        $('action-what').focus();
    }
    document.addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-act]');
        if (!b || !page.parentNode.contains(b) || b.closest('dialog')) return;
        const act = b.dataset.act;
        const go = () => (act === 'action' ? sheetAction() : sheetOnline(act));
        if (window.LEMSignIn) window.LEMSignIn.need(b.dataset.gated || '', go); else go();
    });
    function post(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then(r => r.json().catch(() => ({})).then(b => {
                if (!r.ok || b.error || b.ok === false) throw new Error(b.error || ('LEM answered ' + r.status + ' and did not save it.'));
                return b;
            }));
    }
    function hm() { const d = new Date(); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
    function submit(form, err, go, send, done) {
        form.addEventListener('submit', (ev) => {
            ev.preventDefault();
            const msg = send.check();
            if (msg) { err.textContent = msg; err.hidden = false; return; }
            go.disabled = true;
            err.hidden = true;
            post(send.url(), send.body())
                .then(() => { form.closest('dialog').close(); done(); refetch(); })
                .catch(e => { err.textContent = (e && e.message) || 'It was not saved.'; err.hidden = false; })
                .finally(() => { go.disabled = false; });
        });
    }
    submit($('online-form'), $('online-err'), $('online-go'), {
        check: () => $('online-comment').value.trim() ? '' : 'Say why. A comment is kept with every change of state.',
        url: () => '/api/machines/' + encodeURIComponent(uid) + '/override',
        body: () => {
            const off = $('online-form').dataset.act === 'offline';
            const kind = (document.querySelector('#online-kind input:checked') || {}).value || 'SERVICE';
            return { override: off ? kind : '', comment: $('online-comment').value.trim() };
        },
    }, () => S.toast(($('online-form').dataset.act === 'offline' ? 'Taken off line' : 'Back on line') + ' · ' + (window.LEMSignIn ? window.LEMSignIn.user() : '') + ' · ' + hm()));
    submit($('action-form'), $('action-err'), $('action-go'), {
        check: () => $('action-what').value.trim() ? '' : 'Say what happened.',
        url: () => '/api/equipment/' + encodeURIComponent(uid) + '/actions',
        body: () => ({ what_happened: $('action-what').value.trim(), trigger_kind: 'qc_fail', test_name: $('action-test').value }),
    }, () => S.toast('Corrective action opened · ' + (window.LEMSignIn ? window.LEMSignIn.user() : '') + ' · ' + hm()));

    // ── live: refetch when this instrument or the snapshot changed ─────────
    let inflight = false;
    function refetch() {
        if (inflight || !window.LEMLive) return;
        inflight = true;
        window.LEMLive.bgFetch('/api/ui/instruments/' + encodeURIComponent(uid))
            .then(r => { if (r.status === 404) { location.reload(); throw new Error('gone'); } if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(d => {
                const before = JSON.stringify((data.qc.checks.find(c => c.test === selected) || {}).at);
                data = d;
                if (!data.qc.checks.some(c => c.test === selected)) selected = data.qc.selected;
                const after = JSON.stringify((data.qc.checks.find(c => c.test === selected) || {}).at);
                if (before !== after) { for (const k of Object.keys(trend)) delete trend[k]; }
                render();
            })
            .catch(() => {})
            .finally(() => { inflight = false; });
    }
    if (window.LEMLive) {
        let lastAt = data.built_at;
        window.LEMLive.subscribe((u) => {
            const changed = u.reset || (u.machines && u.machines.indexOf(uid) >= 0) ||
                (u.snapshot_at && u.snapshot_at !== lastAt);
            if (u.snapshot_at) lastAt = u.snapshot_at;
            if (changed) refetch();
        });
    }
    // times ("Wed 15:04") age on their own; no request
    setInterval(() => { if (!document.hidden) { renderHead(); renderCard(); renderQc(); } }, 60000);

    function render() {
        renderHead();
        renderCard();
        renderQcIntro();
        renderQc();
        renderChart();
        renderMaintenance();
        renderBench();
    }
    render();
})();
