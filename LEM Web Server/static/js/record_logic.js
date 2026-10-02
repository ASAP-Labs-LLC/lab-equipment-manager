/* record_logic.js: the instrument record's pure half (ia-final §3.1, §4.2).
   No DOM. Node-tested in tests/js/record_logic.mjs; drawn by record.js.

   fmtQC is the ONE QC number format on the page: its decimals are the most the
   standard's own low / expected / high use, at least 1, so a band never mixes
   them ("331.48 – 334.90 – 338.32", never "334.9"). A value below 0.01 that the
   band's decimals would round away falls back to two significant figures, so
   a sulfur result is never printed as 0.00. */
(function (root) {
    'use strict';
    const L = root.LEMInstruments || {};

    // ── numbers (§4.2) ─────────────────────────────────────────────────────
    function decimalsOf(x) {
        if (typeof x !== 'number' || !isFinite(x)) return 0;
        // the shortest form that reads back as the same number, at most 6
        // places: 0.30000000000000004 is 0.3, not seventeen decimals
        for (let d = 0; d <= 6; d++) {
            if (Math.abs(Number(x.toFixed(d)) - x) < 1e-9 * Math.max(1, Math.abs(x))) return d;
        }
        return 6;
    }
    function num(v) {
        if (v === null || v === undefined || v === '') return null;
        const n = Number(v);
        return isFinite(n) ? n : null;
    }
    function qcDecimals(spec) {
        const s = spec || {};
        const d = Math.max(decimalsOf(num(s.low)), decimalsOf(num(s.expected)), decimalsOf(num(s.high)));
        return Math.min(6, Math.max(1, d));
    }
    function fmtQC(v, spec) {
        const n = num(v);
        if (n === null) return '—';
        const d = qcDecimals(spec);
        const fixed = n.toFixed(d);
        if (n !== 0 && Math.abs(n) < 0.01) {
            const sig = Number(n.toPrecision(2));
            if (decimalsOf(sig) > d) return String(sig);
        }
        return fixed;
    }
    function bandText(spec) {
        const s = spec || {};
        if (num(s.low) === null && num(s.expected) === null && num(s.high) === null) return '';
        return [s.low, s.expected, s.high].map(v => fmtQC(v, s)).join(' – ');
    }

    // ── the band track ─────────────────────────────────────────────────────
    /** Where min, target, max and the last result sit on a track, in %.
        The track runs a quarter-band past each limit; a result outside the
        band is pinned to the edge it left by (`outside`), drawn as a
        triangle, never off the track. */
    function bandPos(spec, value) {
        const s = spec || {};
        const lo = num(s.low), hi = num(s.high);
        if (lo === null || hi === null || !(hi > lo)) return null;
        const mid = num(s.expected) === null ? (lo + hi) / 2 : num(s.expected);
        const pad = (hi - lo) * 0.25;
        const a = lo - pad, b = hi + pad;
        const pct = (x) => Math.round(((x - a) / (b - a)) * 1000) / 10;
        const v = num(value);
        let outside = null, valPct = null;
        if (v !== null) {
            if (v > hi) { outside = 'high'; valPct = 100; }
            else if (v < lo) { outside = 'low'; valPct = 0; }
            else valPct = pct(v);
        }
        return { lowPct: pct(lo), midPct: pct(mid), highPct: pct(hi), valPct, outside };
    }

    // ── words ──────────────────────────────────────────────────────────────
    const lowerFirst = (s) => s ? s.charAt(0).toLowerCase() + s.slice(1) : s;
    const when = (iso, nowMs) => (L.when ? L.when(iso, nowMs) : '');

    /** The readiness card's one sentence (§3.1 bar). */
    function captionText(c, nowMs) {
        if (!c || !c.lead) return '';
        const paren = [c.std || '', c.at ? when(c.at, nowMs) : ''].filter(Boolean).join(', ');
        let s = c.lead + (paren ? ' (' + paren + ')' : '') + (c.too ? '; ' + c.too : '') + '.';
        if (c.next) s += ' Next: ' + lowerFirst(c.next) + '.';
        return s;
    }

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function day(iso) {
        const t = Date.parse(iso || '');
        if (isNaN(t)) return '';
        const d = new Date(t);
        return d.getDate() + ' ' + MONTHS[d.getMonth()];
    }
    const RANGE_EMPTY = { '24': 'No runs on file for this check yet', '90d': 'No runs in the last 90 days', all: 'No runs on file for this check yet' };

    /** "24 runs since 4 Aug · 1 outside the limits" */
    function rangeCaption(series, range) {
        const pts = (series && series.points) || [];
        if (!pts.length) return RANGE_EMPTY[range] || RANGE_EMPTY['24'];
        const n = pts.length;
        const head = (range === 'all' ? 'All ' : '') + n + (n === 1 ? ' run' : ' runs') + ' since ' + day(pts[0].ts);
        const f = Number(series.failures) || 0;
        return head + ' · ' + (f ? f + ' outside the limits' : 'none outside the limits');
    }

    /** A statistical-control finding, as a caption marked provisional: never
        a second verdict (judge J3: one verdict column). The newest finding is
        said; the others are counted. In control: nothing. */
    function controlPhrase(v) {
        const n = (v.indices || []).length;
        const side = v.side === 'above' ? 'above' : v.side === 'below' ? 'below' : '';
        switch (v.rule) {
            case '1_3s': return n + (n === 1 ? ' run' : ' runs') + ' beyond 3s' + (side ? ', ' + side + ' the mean' : '');
            case '2of3_2s': return n + ' of 3 in a row beyond 2s' + (side ? ', ' + side + ' the mean' : '');
            case '4of5_1s': return n + ' of 5 in a row beyond 1s' + (side ? ', ' + side + ' the mean' : '');
            case 'shift': return n + ' in a row ' + (side || 'on one side of') + ' the mean';
            case 'trend': return n + ' in a row ' + (side === 'above' ? 'rising' : side === 'below' ? 'falling' : 'trending');
            default: return v.message ? String(v.message).split('.')[0] : 'A control finding';
        }
    }
    function controlCaption(series) {
        const vs = ((series && series.violations) || []).slice();
        if (!vs.length) return '';
        const last = (v) => Math.max.apply(null, (v.indices || [0]).concat([-1]));
        vs.sort((a, b) => last(b) - last(a));
        const top = vs[0];
        let s = controlPhrase(top);
        if (vs.length > 1) s += ' · ' + (vs.length - 1) + (vs.length === 2 ? ' more finding' : ' more findings');
        if (top.provisional) s += ' · provisional';
        return s;
    }

    /** One uncertainty line. `res` is {ok:true, current} or {ok:false, error}. */
    function uLine(res, spec, nowMs) {
        if (!res) return '';
        if (!res.ok) return 'Couldn\'t read the uncertainty register' + (res.error ? ': ' + res.error : '');
        const e = res.current;
        if (!e || num(e.u_expanded) === null) return 'No approved uncertainty estimate yet';
        const units = (spec && spec.units) ? ' ' + spec.units : '';
        const k = num(e.k);
        const bits = [];
        if (k !== null) bits.push('k = ' + String(Number(k.toPrecision(3))));
        if (e.approved_at) bits.push('approved ' + day(e.approved_at));
        return 'U = ±' + fmtQC(e.u_expanded, spec) + units + (bits.length ? ' (' + bits.join(', ') + ')' : '');
    }

    // ── the chart ──────────────────────────────────────────────────────────
    /** Geometry for one check's runs against its certificate band. Evenly
        spaced by run (a control chart is in run order); y spans the band and
        every result, padded. -> {plot, y(v), points[{x,y,outside,i}], lines,
        ticks:[{x,label}]} */
    function chartModel(series, box) {
        const w = (box && box.w) || 600, h = (box && box.h) || 200;
        const plot = { x0: 12, x1: w - 96, y0: 12, y1: h - 26 };
        const pts = ((series && series.points) || []).filter(p => num(p.value) !== null);
        const lo = num(series && series.low), hi = num(series && series.high);
        const mid = num(series && series.expected);
        const vals = pts.map(p => num(p.value)).concat([lo, hi, mid].filter(v => v !== null));
        if (!vals.length) return null;
        let min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
        if (max === min) { max += 1; min -= 1; }
        const pad = (max - min) * 0.12;
        min -= pad; max += pad;
        const y = (v) => plot.y1 - ((v - min) / (max - min)) * (plot.y1 - plot.y0);
        const n = pts.length;
        const x = (i) => n <= 1 ? (plot.x0 + plot.x1) / 2 : plot.x0 + (i / (n - 1)) * (plot.x1 - plot.x0);
        const points = pts.map((p, i) => {
            const v = num(p.value);
            const outside = (hi !== null && v > hi) || (lo !== null && v < lo);
            return { i, x: x(i), y: y(v), outside: !!outside, value: v, ts: p.ts, in_spec: p.in_spec };
        });
        const ticks = [];
        if (n) {
            const idx = n === 1 ? [0] : n < 4 ? [0, n - 1] : [0, Math.round((n - 1) / 2), n - 1];
            for (const i of idx) ticks.push({ x: x(i), label: day(pts[i].ts) });
        }
        return { plot, points, ticks, lines: { low: lo === null ? null : y(lo), high: hi === null ? null : y(hi), mid: mid === null ? null : y(mid) } };
    }

    const api = { fmtQC, qcDecimals, bandText, bandPos, captionText, rangeCaption, controlCaption, uLine, chartModel, day };
    root.LEMRecord = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
