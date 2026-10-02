/* round_archive.js: the archive of recorded days, in a sheet (piece 10).

   Opened by #archive-chip: "Archived (n)" on the round list (every round),
   "Open the archive" in an editor (data-round: that round only). One square
   per day a round was recorded, filled by how much of it was done, a month
   to a card, newest first; choose a day to read who ticked and read what.

   Four sentences, never confused: reading, the calendar, "nothing recorded
   yet", and "could not be read", which is NOT an empty archive. This is the
   record an auditor asks for, and "no rounds recorded" about three years of
   ticks is the worst thing it could say. Every server string goes in
   through textContent; no colour is chosen here (the fill is lem.css). */
(function (root) {
    'use strict';
    if (typeof document === 'undefined') return;
    const $ = (id) => document.getElementById(id);
    const chip = $('archive-chip');
    const sheet = $('archive-sheet');
    if (!chip || !sheet) return;
    const body = $('archive-body');
    const dayOut = $('archive-day');
    const only = chip.dataset.round || '';
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function dayWords(iso) {
        const [y, m, d] = String(iso).split('-').map(Number);
        return d + ' ' + MONTHS[m - 1] + ' ' + y;
    }
    function band(pct) { return pct >= 100 ? 4 : pct >= 67 ? 3 : pct >= 34 ? 2 : pct > 0 ? 1 : 0; }

    function say(kind, text, retry) {
        body.replaceChildren();
        const p = el('p', kind === 'failed' ? 'warnline' : kind === 'loading' ? 'load-line' : 'empty-note');
        const g = el('span', 'glyph ' + (kind === 'failed' ? 'held' : kind === 'loading' ? 'working' : 'never'));
        g.setAttribute('aria-hidden', 'true');
        p.append(g, document.createTextNode(' ' + text));
        body.append(p);
        if (retry) {
            const b = el('button', 'btn btn-sm', 'Try again');
            b.type = 'button';
            b.addEventListener('click', load);
            body.append(b);
        }
    }

    async function load() {
        say('loading', 'Reading the archive…');
        dayOut.replaceChildren();
        const url = '/api/checklists/history?limit=3660' + (only ? '&checklist=' + encodeURIComponent(only) : '');
        let b = null;
        try {
            const r = await fetch(url, { headers: { Accept: 'application/json' } });
            b = r.ok ? await r.json() : null;
        } catch (_e) { b = null; }
        if (!b || !Array.isArray(b.days)) {
            say('failed', 'The archive could not be read. This is not an empty archive: the days are on file, and LEM could not reach them just now.', true);
            return;
        }
        if (!b.days.length) {
            say('empty', only ? 'Nothing recorded on this round yet. The first tick starts its archive.'
                : 'No rounds recorded yet. If the old LEM has history, bring it across in Settings › Imports.');
            return;
        }
        draw(b.days);
    }

    function draw(days) {
        const byDay = new Map(days.map(d => [d.day, d]));
        const sorted = Array.from(byDay.keys()).sort();
        const first = sorted[0], last = sorted[sorted.length - 1];
        body.replaceChildren();
        const sum = el('p', 'arc-sum');
        // "of what was recorded", never "fully done": the archive counts the
        // items somebody touched that day, not the round as it was defined
        sum.textContent = days.length + ' day' + (days.length === 1 ? '' : 's') + ' recorded · '
            + dayWords(first) + (days.length > 1 ? ' to ' + dayWords(last) : '');
        body.append(sum);

        // every month from the first recorded day to the last, so a month
        // the rounds stopped shows as an empty card, not a gap nobody sees
        const months = [];
        let [y, m] = first.slice(0, 7).split('-').map(Number);
        const [ly, lm] = last.slice(0, 7).split('-').map(Number);
        while (y < ly || (y === ly && m <= lm)) { months.push([y, m]); m += 1; if (m > 12) { m = 1; y += 1; } }

        const wrap = el('div', 'arc-months');
        for (const [yy, mm] of months.reverse()) {
            const key = yy + '-' + String(mm).padStart(2, '0');
            const card = el('section', 'arc-month');
            const mine = days.filter(d => d.day.startsWith(key));
            const h = el('h3', null, MONTHS[mm - 1] + ' ' + yy);
            const cap = el('span', 'caption', ' · ' + mine.length + ' recorded');
            h.append(cap);
            card.append(h);
            const grid = el('div', 'arc-grid');
            grid.setAttribute('role', 'group');
            grid.setAttribute('aria-label', MONTHS[mm - 1] + ' ' + yy);
            for (const w of ['M', 'T', 'W', 'T', 'F', 'S', 'S']) {
                const dw = el('span', 'arc-dow', w); dw.setAttribute('aria-hidden', 'true'); grid.append(dw);
            }
            const lead = (new Date(yy, mm - 1, 1).getDay() + 6) % 7;
            for (let i = 0; i < lead; i++) grid.append(el('span', 'arc-pad'));
            const dim = new Date(yy, mm, 0).getDate();
            for (let dn = 1; dn <= dim; dn++) {
                const iso = key + '-' + String(dn).padStart(2, '0');
                const d = byDay.get(iso);
                if (!d) {
                    const s = el('span', 'arc-day none', String(dn));
                    s.title = dayWords(iso) + ': nothing recorded';
                    grid.append(s);
                    continue;
                }
                const btn = el('button', 'arc-day b' + band(d.pct), String(dn));
                btn.type = 'button';
                btn.dataset.day = iso;
                const words = dayWords(iso) + ': ' + d.checked + ' of ' + d.total + ' recorded item' + (d.total === 1 ? '' : 's') + ' ticked';
                btn.setAttribute('aria-label', words);
                btn.title = words;
                grid.append(btn);
            }
            card.append(grid);
            wrap.append(card);
        }
        body.append(wrap);
        const legend = el('p', 'arc-legend caption');
        legend.append(document.createTextNode('Fewer ticked '));
        for (let i = 0; i <= 4; i++) { const sw = el('span', 'arc-day b' + i); sw.setAttribute('aria-hidden', 'true'); legend.append(sw); }
        legend.append(document.createTextNode(' every recorded item ticked'));
        body.append(legend);
    }

    async function readDay(iso) {
        body.querySelectorAll('.arc-day[aria-pressed]').forEach(b => b.removeAttribute('aria-pressed'));
        const btn = body.querySelector('.arc-day[data-day="' + iso + '"]');
        if (btn) btn.setAttribute('aria-pressed', 'true');
        dayOut.replaceChildren(el('p', 'load-line', 'Reading ' + dayWords(iso) + '…'));
        let b = null;
        try {
            const r = await fetch('/api/checklists?day=' + encodeURIComponent(iso), { headers: { Accept: 'application/json' } });
            b = r.ok ? await r.json() : null;
        } catch (_e) { b = null; }
        dayOut.replaceChildren();
        const h = el('h3', null, dayWords(iso));
        dayOut.append(h);
        if (!b) { dayOut.append(el('p', 'warnline', 'That day could not be read. It is on file; try again in a moment.')); return; }
        const lists = (b.checklists || []).filter(cl => (!only || cl.uid === only));
        let any = false;
        for (const cl of lists) {
            const st = (b.state || {})[cl.uid] || {};
            const touched = (cl.items || []).filter(i => st[i.uid]);
            if (!touched.length && only === '') continue;
            any = true;
            const sec = el('div', 'arc-round');
            const t = el('p', 'arc-round-h');
            t.append(el('b', null, cl.name), el('span', 'caption', ' · ' + (cl.checked || 0) + ' of ' + (cl.total || 0) + ' done'));
            sec.append(t);
            if (!touched.length) { sec.append(el('p', 'caption', 'Nothing was recorded on this round that day.')); dayOut.append(sec); continue; }
            const ul = el('ul', 'arc-items');
            for (const i of touched) {
                const e = st[i.uid] || {};
                const li = el('li');
                const g = el('span', 'glyph ' + (e.checked ? 'final' : 'never')); g.setAttribute('aria-hidden', 'true');
                const label = el('span', 'arc-label', i.text);
                li.append(g, label);
                if (e.value) li.append(el('b', 'arc-val', e.value + (i.units ? ' ' + i.units : '')));
                li.append(el('span', 'caption arc-by', (e.checked ? '' : 'Undone · ') + (e.user || 'Unknown') + (e.at ? ' · ' + String(e.at).slice(11, 16) : '')));
                ul.append(li);
            }
            sec.append(ul);
            dayOut.append(sec);
        }
        if (!any) dayOut.append(el('p', 'caption', 'Nothing recorded that day.'));
    }

    body.addEventListener('click', (ev) => {
        const b = ev.target.closest('button.arc-day[data-day]');
        if (b) readDay(b.dataset.day);
    });
    chip.addEventListener('click', () => {
        if (!sheet.open) sheet.showModal();
        load();
    });
    $('archive-close').addEventListener('click', () => sheet.close());
})(typeof window !== 'undefined' ? window : globalThis);
