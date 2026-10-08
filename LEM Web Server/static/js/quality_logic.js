/* quality_logic.js: QC's pure half (ia-final §3.5, piece 8). No DOM.
   Node-tested in tests/js/quality_logic.mjs; drawn by quality.js and
   standard.js.

   What it decides:
     chipOrder     "Check it on": the instruments that already report the
                   test first (by name), then the rest; null reporting is
                   "could not ask", never "nobody reports it"
     matchTests    the test type-ahead over LabCore's catalogue
     bandPreview   expected ± k·s, in fmtQC's decimals (§4.2)
     newProblem    why Save cannot be sent yet, in words, or ''
     newSentence   the line above Save
     assignChange  the standard page's Check it on: who is added, who is off
     certLine      one certificate's dates in words */
(function (root) {
    'use strict';
    const R = root.LEMRecord || {};
    const norm = (s) => String(s || '').split(/\s+/).filter(Boolean).join(' ').toLowerCase();
    const byTitle = (a, b) => a.title.toLowerCase() < b.title.toLowerCase() ? -1 : a.title.toLowerCase() > b.title.toLowerCase() ? 1 : (a.uid < b.uid ? -1 : 1);
    const UNITS = { C: '°C', F: '°F', degC: '°C', degF: '°F', 'mm2/s': 'mm²/s', 'mm^2/s': 'mm²/s', 'g/cm3': 'g/cm³', 'kg/m3': 'kg/m³' };
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    function andList(items) {
        items = (items || []).filter(Boolean);
        return items.length <= 1 ? items.join('') : items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
    }

    /** {first, rest, unknown}: who reports `test` first. `reporting` is
        /api/ui/quality/reporting's `tests` (normalised test -> [uid]), or
        null when it could not be read: then everyone is in `rest` and
        `unknown` says the order is not a claim. */
    function chipOrder(instruments, reporting, test) {
        const all = (instruments || []).slice().sort(byTitle);
        if (!reporting) return { first: [], rest: all, unknown: true };
        const who = new Set(reporting[norm(test)] || []);
        return { first: all.filter(m => who.has(m.uid)), rest: all.filter(m => !who.has(m.uid)), unknown: false };
    }

    /** Catalogue names matching `q`: the name itself first, then a word
        that starts with it, then anywhere; ties by length (the plainest
        name first), then alphabetically. At most eight. */
    function matchTests(catalogue, q) {
        const k = norm(q);
        if (!k) return [];
        const scored = [];
        for (const name of catalogue || []) {
            const n = norm(name);
            const i = n.indexOf(k);
            if (i < 0) continue;
            const score = n === k ? 0 : (i === 0 ? 1 : (/[\s\-/(,]/.test(n.charAt(i - 1)) ? 2 : 3));
            scored.push([score, n.length, n, name]);
        }
        scored.sort((a, b) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : 1));
        return scored.slice(0, 8).map(x => x[3]);
    }

    function num(v) {
        if (v === null || v === undefined || String(v).trim() === '') return null;
        const n = Number(String(v).trim());
        return isFinite(n) ? n : NaN;
    }

    /** "Passes 58.0 – 60.0 – 62.0 °C", or '' until it can be said. The
        decimals are the certificate's own (expected and s as typed). */
    function bandPreview(f) {
        const e = num(f.expected), s = num(f.sd);
        const k = num(f.k) === null ? 2 : num(f.k);
        if (e === null || s === null || isNaN(e) || isNaN(s) || isNaN(k) || s < 0 || !(k > 0)) return '';
        const places = Math.max(decimals(String(f.expected).trim()), decimals(String(f.sd).trim()), 1);
        const spec = { low: Number((e - k * s).toFixed(places)), expected: e, high: Number((e + k * s).toFixed(places)) };
        const fmt = (v) => (R.fmtQC ? R.fmtQC(v, spec) : String(v));
        const u = String(f.units || '').trim();
        return 'Passes ' + fmt(spec.low) + ' – ' + fmt(spec.expected) + ' – ' + fmt(spec.high) + (u ? ' ' + (UNITS[u] || u) : '');
    }
    function decimals(text) {
        const m = /\.(\d+)$/.exec(text);
        return m ? m[1].length : 0;
    }

    /** Why Save cannot be sent, or ''. `lib` is the library as read (null:
        not read, and the server's own check stands). */
    function newProblem(f, lib) {
        const name = String(f.name || '').split(/\s+/).filter(Boolean).join(' ');
        const lab = String(f.labId || '').trim();
        if (!name) return 'Give the standard a name.';
        if (!lab) return 'Enter the Lab ID it runs under.';
        if (!f.picked || !String(f.test || '').trim()) return 'Pick the test from LabCore\'s list.';
        const e = num(f.expected);
        if (e === null) return 'Enter the expected value.';
        if (isNaN(e)) return 'The expected value has to be a number, like 63.7.';
        const s = num(f.sd);
        if (s === null) return 'Enter the standard deviation.';
        if (isNaN(s)) return 'The standard deviation has to be a number, like 1.05.';
        if (s < 0) return 'The standard deviation cannot be negative.';
        const k = num(f.k);
        if (k !== null && (isNaN(k) || !(k > 0))) return 'k has to be greater than zero.';
        const hrs = num(f.hours);
        if (hrs !== null && (isNaN(hrs) || hrs < 0)) return 'The window is a number of hours, like 24.';
        for (const s0 of lib || []) {
            if (norm(s0.name) === name.toLowerCase()) return 'There is already a standard called ' + s0.name + '.';
            if (String(s0.sample_id_val || '').trim().toLowerCase() === lab.toLowerCase()) return String(s0.sample_id_val).trim() + ' is already the Lab ID of ' + s0.name + '.';
        }
        return '';
    }

    function shortName(test) {
        return R.shortTest ? R.shortTest(test)[0] || String(test || '') : String(test || '');
    }

    /** The line above Save. */
    function newSentence(f, titles) {
        const name = String(f.name || '').split(/\s+/).filter(Boolean).join(' ') || 'the standard';
        if (!(titles || []).length) return 'Saves ' + name + '. Nothing is checked on it until you pick an instrument here or on its page.';
        return 'Saves ' + name + ' and checks ' + shortName(f.test) + ' on ' + andList(titles) + '.';
    }

    /** The standard page's Check it on: before -> after, in words. */
    function assignChange(before, after, titles) {
        const b = new Set(before || []), a = new Set(after || []);
        const name = (u) => (titles && titles[u]) || u;
        const add = [...a].filter(u => !b.has(u)).map(name).sort();
        const off = [...b].filter(u => !a.has(u)).map(name).sort();
        if (!add.length && !off.length) return 'Nothing has changed.';
        return [add.length ? 'Adds ' + andList(add) + '.' : '', off.length ? 'Takes ' + andList(off) + ' off it.' : ''].filter(Boolean).join(' ');
    }

    function day(iso) {
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
        return m ? Number(m[3]) + ' ' + MONTHS[Number(m[2]) - 1] + ' ' + m[1] : '';
    }

    /** One certificate's facts: issued, valid to, who filed it. */
    function certLine(c) {
        const parts = [];
        if (day(c.issued_at)) parts.push('Issued ' + day(c.issued_at));
        parts.push(day(c.expires_at) ? (parts.length ? 'valid to ' : 'Valid to ') + day(c.expires_at) : (parts.length ? 'no expiry stated' : 'No expiry stated'));
        if (c.uploaded_by) parts.push('filed by ' + c.uploaded_by);
        return parts.join(' · ');
    }

    // ── Trends (2026-10-08): every check's chart, by instrument ───────────
    const RANK = { out: 0, due: 1, never: 2, in: 3 };
    /** Where a check sits among its instrument's others: a distillation
        reads IBP, the recoveries by their percent, then FBP; anything else
        follows, A to Z. */
    function checkKey(c) {
        const t = String((c && (c.check || c.test)) || '');
        if (/^IBP\b/i.test(t)) return [0, 0, t.toLowerCase()];
        const pct = t.match(/^(\d+(?:\.\d+)?)\s*%/);
        if (pct) return [1, Number(pct[1]), t.toLowerCase()];
        if (/^FBP\b/i.test(t)) return [2, 0, t.toLowerCase()];
        return [3, 0, t.toLowerCase()];
    }
    function byKey(a, b) {
        const x = checkKey(a), y = checkKey(b);
        for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
        return 0;
    }
    /** The QC wall's cards as one group per instrument: worst instrument
        first (its worst check decides), then A to Z; each group's checks in
        reading order, with its verdict counts and its record's link. */
    function trendGroups(cards) {
        const by = new Map();
        for (const c of cards || []) {
            if (!c || !c.uid) continue;
            let g = by.get(c.uid);
            if (!g) {
                g = { uid: c.uid, title: c.title || c.uid, href: String(c.href || '').split('#')[0] || '/instruments/' + encodeURIComponent(c.uid),
                      cards: [], counts: {}, worst: 'in', rank: Infinity };
                by.set(c.uid, g);
            }
            const k = (c.verdict && c.verdict.key) || 'never';
            g.cards.push(c);
            g.counts[k] = (g.counts[k] || 0) + 1;
            const r = typeof c.rank === 'number' ? c.rank : (RANK[k] !== undefined ? RANK[k] : 9);
            if (r < g.rank || (r === g.rank && (RANK[k] || 0) < (RANK[g.worst] || 0))) { g.rank = r; g.worst = k; }
        }
        const out = [...by.values()];
        for (const g of out) {
            g.cards.sort(byKey);
            // counts in the order the page says them: worst first
            const ordered = {};
            for (const k of Object.keys(RANK)) if (g.counts[k]) ordered[k] = g.counts[k];
            g.counts = ordered;
        }
        out.sort((a, b) => a.rank - b.rank || (a.title.toLowerCase() < b.title.toLowerCase() ? -1 : a.title.toLowerCase() > b.title.toLowerCase() ? 1 : 0));
        return out.map(g => { delete g.rank; return g; });
    }
    /** A card's points on a shared time axis: t is 0 at `fromMs` and 1 at
        `toMs`. A point with no band (z null) or no time cannot be placed and
        is left out, as is one outside the window. */
    function trendX(points, fromMs, toMs) {
        const span = toMs - fromMs;
        const out = [];
        for (const p of points || []) {
            if (!p || typeof p.z !== 'number' || !Number.isFinite(p.z) || !p.at) continue;
            const ms = Date.parse(p.at);
            if (!Number.isFinite(ms) || ms < fromMs || ms > toMs) continue;
            out.push({ t: span > 0 ? (ms - fromMs) / span : 1, z: p.z, in_spec: p.in_spec, at: p.at });
        }
        return out;
    }
    /** ?range= as days: 30, 90 or 180 (the record copy holds 180). */
    function trendRange(v) {
        const n = Number(v);
        return n === 30 || n === 90 || n === 180 ? n : 90;
    }

    /** A tile's chart height for its width: 88px at the minimum, growing
        with the tile, never a poster. */
    function trendChartH(tileW) { return Math.round(Math.max(88, Math.min(300, tileW * 0.34))); }
    const TILE_REST = 112, GROUP_HEAD = 34;   // a tile without its chart; a group's name line
    /** How many columns: the FEWEST (so the biggest tiles) whose packing of
        every group fits `height`; when none does, the most that keep tiles
        at least `min` wide, and the page scrolls. `sizes` is each group's
        number of checks, in page order; groups pack left to right as the
        page's flex rows do, each as wide as its checks up to a row. */
    function trendFit(o) {
        const W = o.width, gap = o.gap || 16, rowGap = o.rowGap || 24, min = o.min || 280;
        const sizes = (o.sizes || []).filter(n => n > 0);
        const maxCols = Math.max(1, Math.floor((W + gap) / (min + gap)));
        let best = null;
        for (let cols = 1; cols <= maxCols; cols++) {
            const tileW = Math.floor((W - gap * (cols - 1)) / cols);
            const tileH = TILE_REST + trendChartH(tileW);
            let x = 0, rowH = 0, total = 0;
            for (const n of sizes) {
                const span = Math.min(cols, n);
                const h = GROUP_HEAD + Math.ceil(n / span) * tileH + (Math.ceil(n / span) - 1) * gap;
                if (x && x + span > cols) { total += rowH + rowGap; x = 0; rowH = 0; }
                x += span; rowH = Math.max(rowH, h);
            }
            total += rowH;
            best = { cols, tileW, chartH: trendChartH(tileW), total };
            if (total <= o.height) return best;
        }
        return best;
    }

    const api = { trendFit, trendChartH, trendGroups, trendX, trendRange, chipOrder, matchTests, bandPreview, newProblem, newSentence, assignChange, certLine, andList, norm, day, UNITS };
    root.LEMQuality = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
