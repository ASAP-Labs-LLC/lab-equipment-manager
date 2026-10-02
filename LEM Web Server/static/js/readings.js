/* readings.js: the Readings page (/checklists/trends, piece 10).

   One chart per reading, drawn from the answer the server put in the page.
   Limits are drawn only where someone set them; with none there is no band
   and no verdict (A.6). Each chart has a one-line text summary and a "Show
   as a table" button (ia-final §9.1 rule 5). `?item=<uid>` scrolls to the
   chart that item feeds and marks it with the ink border GC uses for "Now".

   The pure half (LEMReadingsLogic: scales, the summary, which card an item
   lands on) is node-tested in tests/js/readings.mjs. No colour is chosen
   here; lem.css draws with chart tokens. */
(function (root) {
    'use strict';

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function dayWords(iso) {
        const p = String(iso || '').split('-').map(Number);
        return p.length === 3 && p[1] ? p[2] + ' ' + MONTHS[p[1] - 1] : String(iso || '');
    }

    /** The y range: the readings and any limit, padded, never zero-height. */
    function yRange(values, lo, hi) {
        const all = values.slice();
        if (lo !== null && lo !== undefined) all.push(lo);
        if (hi !== null && hi !== undefined) all.push(hi);
        let min = Math.min.apply(null, all), max = Math.max.apply(null, all);
        if (!isFinite(min) || !isFinite(max)) return [0, 1];
        if (min === max) { const d = Math.abs(min) * 0.1 || 1; min -= d; max += d; }
        const pad = (max - min) * 0.12;
        return [min - pad, max + pad];
    }

    function outside(v, lo, hi) {
        return (lo !== null && lo !== undefined && v < lo) || (hi !== null && hi !== undefined && v > hi);
    }

    /** "24 readings, 1 outside the limits, last 2900 PSI on 30 Sep." */
    function summary(t) {
        const pts = t.points || [];
        if (!pts.length) return 'Never written.';
        const lo = t.min === undefined ? null : t.min, hi = t.max === undefined ? null : t.max;
        const out = (lo !== null || hi !== null) ? pts.filter(p => outside(p.value, lo, hi)).length : null;
        const last = pts[pts.length - 1];
        return pts.length + ' reading' + (pts.length === 1 ? '' : 's')
            + (out === null ? '' : ', ' + out + ' outside the limits')
            + ', last ' + last.value + (t.units ? ' ' + t.units : '') + ' on ' + dayWords(last.day) + '.';
    }

    /** Which card `?item=<uid>` lands on: the card whose items include it,
        or whose own key is it (a tracked thing's uid works too). */
    function target(trends, uid) {
        if (!uid) return '';
        for (const t of trends || []) {
            const key = t.tracked_uid || t.item_uid;
            if (key === uid || (t.item_uids || []).indexOf(uid) >= 0) return key;
        }
        return '';
    }

    const logic = { yRange, summary, target, outside };
    root.LEMReadingsLogic = logic;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = logic;
    if (typeof document === 'undefined') return;

    // ── the page ─────────────────────────────────────────────────────────────
    const main = document.getElementById('main');
    if (!main || main.dataset.testid !== 'readings' || !main.dataset.trends) return;
    let data = null;
    try { data = JSON.parse(main.dataset.trends); } catch (_e) { return; }
    const trends = data.trends || [];
    const NS = 'http://www.w3.org/2000/svg';

    function svg(tag, attrs) {
        const e = document.createElementNS(NS, tag);
        for (const k in attrs) e.setAttribute(k, attrs[k]);
        return e;
    }

    function chart(t, host) {
        const pts = t.points || [];
        const W = 320, H = 120, L = 34, R = 8, TOP = 8, B = 18;
        const lo = t.min === undefined ? null : t.min, hi = t.max === undefined ? null : t.max;
        const [y0, y1] = yRange(pts.map(p => p.value), lo, hi);
        const x = (i) => L + (pts.length === 1 ? (W - L - R) / 2 : i * (W - L - R) / (pts.length - 1));
        const y = (v) => TOP + (H - TOP - B) * (1 - (v - y0) / (y1 - y0));
        const s = svg('svg', { class: 'rd-chart', viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', role: 'img' });
        s.setAttribute('aria-label', t.text + ': ' + summary(t));
        // grid: top and bottom value
        for (const v of [y0 + (y1 - y0) * 0.12 / 1.24, y1 - (y1 - y0) * 0.12 / 1.24]) {
            s.append(svg('line', { class: 'grid', x1: L, x2: W - R, y1: y(v), y2: y(v) }));
            const lab = svg('text', { x: L - 4, y: y(v) + 3, 'text-anchor': 'end' });
            lab.textContent = String(+v.toPrecision(4));
            s.append(lab);
        }
        if (lo !== null && hi !== null) s.append(svg('rect', { class: 'band', x: L, width: W - L - R, y: y(hi), height: Math.max(0, y(lo) - y(hi)) }));
        for (const v of [lo, hi]) if (v !== null) s.append(svg('line', { class: 'lim', x1: L, x2: W - R, y1: y(v), y2: y(v) }));
        if (pts.length > 1) {
            s.append(svg('polyline', { class: 'ln', points: pts.map((p, i) => x(i).toFixed(1) + ',' + y(p.value).toFixed(1)).join(' ') }));
        }
        pts.forEach((p, i) => {
            const out = outside(p.value, lo, hi);
            // outside the limits is a triangle, not only a colour (§9.1 rule 2)
            const m = out
                ? svg('path', { class: 'pt out', d: 'M' + x(i) + ' ' + (y(p.value) - 4) + 'l4 7h-8z' })
                : svg('circle', { class: 'pt', cx: x(i), cy: y(p.value), r: pts.length > 60 ? 1.6 : 2.4 });
            const tip = svg('title', {});
            tip.textContent = dayWords(p.day) + ': ' + p.value + (t.units ? ' ' + t.units : '') + (p.user ? ' · ' + p.user : '') + (p.round ? ' · ' + p.round : '');
            m.append(tip);
            s.append(m);
        });
        const first = svg('text', { x: L, y: H - 4 }); first.textContent = dayWords(pts[0].day);
        const last = svg('text', { x: W - R, y: H - 4, 'text-anchor': 'end' }); last.textContent = dayWords(pts[pts.length - 1].day);
        s.append(first, last);
        host.replaceChildren(s);
        const cap = document.createElement('p');
        cap.className = 'caption';
        cap.style.margin = '0';
        cap.textContent = summary(t) + ' ';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'linkbtn';
        btn.textContent = 'Show as a table';
        btn.setAttribute('aria-expanded', 'false');
        btn.addEventListener('click', () => {
            const open = btn.getAttribute('aria-expanded') === 'true';
            btn.setAttribute('aria-expanded', open ? 'false' : 'true');
            btn.textContent = open ? 'Show as a table' : 'Show as a chart';
            if (open) { host.replaceChildren(s); return; }
            const tbl = document.createElement('table');
            tbl.className = 'tbl rd-table';
            const head = tbl.createTHead().insertRow();
            for (const h of ['Day', 'Reading', 'By']) { const th = document.createElement('th'); th.scope = 'col'; th.textContent = h; head.append(th); }
            const tb = tbl.createTBody();
            for (const p of pts.slice().reverse()) {
                const r = tb.insertRow();
                r.insertCell().textContent = p.day + (p.round ? ' · ' + p.round : '');
                r.insertCell().textContent = p.value + (t.units ? ' ' + t.units : '') + (outside(p.value, lo, hi) ? ' (outside)' : '');
                r.insertCell().textContent = p.user || '';
            }
            host.replaceChildren(tbl);
        });
        cap.append(btn);
        host.after(cap);
    }

    for (const t of trends) {
        const key = t.tracked_uid || t.item_uid;
        const host = document.querySelector('.rd-plot[data-chart="' + (root.CSS && CSS.escape ? CSS.escape(key) : key) + '"]');
        if (host && (t.points || []).length) chart(t, host);
    }

    // ?item=<uid>: land on that reading
    const want = new URLSearchParams(location.search).get('item') || '';
    if (want) {
        const key = target(trends, want);
        const note = document.getElementById('rd-target-note');
        if (key) {
            const card = document.getElementById('rd-' + key);
            if (card) {
                card.classList.add('is-target');
                card.setAttribute('tabindex', '-1');
                card.scrollIntoView({ block: 'center' });
                card.focus({ preventScroll: true });
            }
        } else if (note) {
            note.textContent = 'That item does not record a number any more, so it has no chart. Every reading is below.';
            note.hidden = false;
        }
    }
})(typeof window !== 'undefined' ? window : globalThis);
