/* checkcard.js: one instrument's checks, each result against its band, in a
   card that opens on hover, focus or tap (2026-10-07).

   Ryan: "I want to look at the machine on the grid and see the status, what
   the result was and the target range, (maybe on a hover, because I
   understand GC is hard because it has 5 of them)". The list's chips and
   the map's bays both open this, from the `checks` ui_instruments.checks
   puts on every instrument: name, result, units, min – target – max, when,
   and the verdict ui_live judged it by.

   A scale per check: the band (min to max) as a bar, the target as a tick,
   the result as a dot, red when the check is out. Every string goes in
   through textContent. GETs nothing. */
(function (root) {
    'use strict';

    function h(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }
    const num = (v) => (v === null || v === undefined || v === '' || !isFinite(Number(v))) ? null : Number(v);
    const unit = (u) => (u === 'C' ? '°C' : u === 'F' ? '°F' : (u || ''));

    /** Where on a 0–100 scale a value sits, with a quarter of the band's
        width either side so a result just outside is still drawn. */
    function place(v, lo, hi) {
        const span = hi - lo || Math.abs(hi) || 1;
        const a = lo - span * 0.25, b = hi + span * 0.25;
        return Math.max(2, Math.min(98, 100 * (v - a) / (b - a)));
    }

    function row(c, when) {
        const r = h('div', 'cc-row cc-' + (c.key || 'none'));
        const l1 = h('div', 'cc-l1');
        const v = num(c.value);
        l1.append(h('span', 'cc-name', c.name),
                  h('span', 'cc-val', v === null ? (c.word || 'No result') : v + (unit(c.units) ? ' ' + unit(c.units) : '')));
        r.append(l1);
        const lo = num(c.low), hi = num(c.high), exp = num(c.expected);
        if (lo !== null && hi !== null && hi > lo) {
            const sc = h('div', 'cc-scale');
            sc.setAttribute('aria-hidden', 'true');
            const band = h('i', 'cc-band');
            band.style.left = place(lo, lo, hi) + '%';
            band.style.right = (100 - place(hi, lo, hi)) + '%';
            sc.append(band);
            if (exp !== null) { const t = h('i', 'cc-tgt'); t.style.left = place(exp, lo, hi) + '%'; sc.append(t); }
            if (v !== null) { const d = h('i', 'cc-dot'); d.style.left = place(v, lo, hi) + '%'; sc.append(d); }
            r.append(sc);
            const l2 = h('div', 'cc-l2');
            l2.append(h('span', '', lo + ' – ' + (exp !== null ? exp + ' – ' : '') + hi + (unit(c.units) ? ' ' + unit(c.units) : '')),
                      h('span', '', c.at && when ? when(c.at) : ''));
            r.append(l2);
        } else if (c.at && when) {
            const l2 = h('div', 'cc-l2');
            l2.append(h('span', '', 'No band published'), h('span', '', when(c.at)));
            r.append(l2);
        }
        return r;
    }

    let pop = null, owner = null;
    function ensure() {
        if (pop) return pop;
        pop = h('div', 'checkcard');
        pop.id = 'checkcard';
        pop.setAttribute('role', 'tooltip');
        pop.hidden = true;
        document.body.appendChild(pop);
        document.addEventListener('click', (e) => { if (owner && !owner.contains(e.target)) hide(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
        window.addEventListener('scroll', hide, { passive: true, capture: true });
        return pop;
    }
    function hide() {
        if (!pop) return;
        pop.hidden = true;
        if (owner) owner.removeAttribute('aria-describedby');
        owner = null;
    }
    /** Open the card for `el`: {title, word, glyph, checks, note}. */
    function show(el, o, when) {
        ensure();
        owner = el;
        pop.replaceChildren();
        const head = h('div', 'cc-head');
        if (o.glyph) { const g = h('span', 'glyph ' + o.glyph); g.setAttribute('aria-hidden', 'true'); head.append(g); }
        head.append(h('b', '', o.title), h('span', 'cc-word', o.word || ''));
        pop.append(head);
        const list = o.checks || [];
        if (!list.length) pop.append(h('div', 'cc-l2', o.note || 'No QC assigned'));
        for (const c of list) pop.append(row(c, when));
        pop.hidden = false;
        el.setAttribute('aria-describedby', 'checkcard');
        const r = el.getBoundingClientRect();
        const pw = pop.offsetWidth, ph = pop.offsetHeight;
        const x = Math.min(window.innerWidth - pw - 12, Math.max(12, r.left));
        let y = r.bottom + 8;
        if (y + ph > window.innerHeight - 8) y = Math.max(8, r.top - ph - 8);
        pop.style.left = x + 'px';
        pop.style.top = y + 'px';
    }
    /** Hover and focus open it; a tap toggles it (a phone has no hover). A
        tap on a link inside `el` still follows the link. */
    function attach(el, get, when) {
        let viaPointer = false;
        el.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') show(el, get(), when); });
        el.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse' && owner === el) hide(); });
        el.addEventListener('pointerdown', () => { viaPointer = true; });
        el.addEventListener('focus', () => { if (!viaPointer) show(el, get(), when); viaPointer = false; });
        el.addEventListener('blur', () => { if (owner === el) hide(); });
        el.addEventListener('click', (e) => {
            if (e.target.closest && e.target.closest('a')) return;
            e.preventDefault();
            e.stopPropagation();
            if (owner === el && !pop.hidden) hide(); else show(el, get(), when);
        });
    }

    root.LEMCheckCard = { attach, show, hide, place };
})(typeof window !== 'undefined' ? window : globalThis);
