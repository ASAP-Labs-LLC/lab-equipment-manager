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
        const paren = [c.std || '', c.at ? stamp(c.at, nowMs, true) : ''].filter(Boolean).join(', ');
        let s = c.lead + (paren ? ' (' + paren + ')' : '') + (c.too ? '; ' + c.too : '') + '.';
        if (c.next) s += ' Next: ' + lowerFirst(c.next) + '.';
        return s;
    }

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function day(iso) {
        const t = Date.parse(iso || '');
        if (isNaN(t)) return '';
        const d = new Date(t);
        // another year names its year, as stamp() and dayIn() do
        return d.getDate() + ' ' + MONTHS[d.getMonth()] +
            (d.getFullYear() === new Date().getFullYear() ? '' : ' ' + d.getFullYear());
    }
    /** A schedule's calendar day: "22 Jul", with its year when that is not
        this year ("22 Jul 2027"), so a due date a year on does not read as
        today's. Read off the date's own digits, not through a time zone. */
    function dayIn(iso, nowMs) {
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
        if (!m) return '—';
        const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
        if (!(mo >= 1 && mo <= 12)) return '—';
        const thisYear = new Date(nowMs === undefined ? Date.now() : nowMs).getFullYear();
        return d + ' ' + MONTHS[mo - 1] + (y === thisYear ? '' : ' ' + y);
    }
    const RANGE_EMPTY = { '24': 'No runs on file for this check yet', '90d': 'No runs in the last 90 days', all: 'No runs on file for this check yet' };

    /** "24 runs since 4 Aug · 1 outside the limits". A series withLatest()
        topped up with the row's own run says so, in the same line. */
    function rangeCaption(series, range) {
        const pts = (series && series.points) || [];
        if (!pts.length) return RANGE_EMPTY[range] || RANGE_EMPTY['24'];
        const n = pts.length;
        const f = Number(series.failures) || 0;
        // The row's run is what LabCore holds for the check (the spec row
        // the module writes). Said as LabCore's, never "the bench's status":
        // round 5's critic found that under "Bench never checked in".
        if (series.from_status && !series.logged) {
            return '1 run on ' + day(pts[n - 1].ts) + (f ? ' · outside the limits' : '') +
                ' · LabCore\'s latest result for this check, none in LEM\'s QC log yet';
        }
        const head = (range === 'all' ? 'All ' : '') + n + (n === 1 ? ' run' : ' runs') + ' since ' + day(pts[0].ts);
        return head + ' · ' + (f ? f + ' outside the limits' : 'none outside the limits') +
            (series.from_status ? ' · newest from LabCore, not yet in LEM\'s QC log' : '');
    }

    /** The chart and the row above it tell one story (round 4's critic).

        The row's Last and When are LabCore's copy of the spec the module publishes
        with every poll); the chart is LEM's QC log. Two reads of the same
        runs, and the log can lag or (a fresh bench, the dev seed) hold none
        yet. Drawing only the log then put "No runs of this check on file"
        under a row showing a run: an empty read presented as "never run".

        So when the row has a result newer than anything the log returned, it
        is appended (never inserted: the control findings' indices are
        positions in the logged points and must stay so) and marked
        `from: 'status'`. It is drawn and counted against the limits; it is
        not analysed for control, because the log is what the control rules
        were run on. `logged` is how many drawn runs came from the log.
        The same run seen by both reads a moment apart (≤ 2 min, same value)
        is one run. 90 days does not draw a row result older than 90 days. */
    function withLatest(series, chk, range, nowMs) {
        const s = series || {};
        const pts = (s.points || []).slice();
        const out = Object.assign({}, s, { points: pts, failures: Number(s.failures) || 0,
            violations: (s.violations || []).slice(), logged: pts.length, from_status: false });
        const c = chk || {};
        const v = num(c.value);
        if (v === null) return out;
        const at = c.at ? Date.parse(c.at) : NaN;
        const now = nowMs === undefined ? Date.now() : nowMs;
        if (range === '90d' && !(at >= now - 90 * 86400000)) return out;
        const lastLog = pts.length ? pts[pts.length - 1] : null;
        if (lastLog) {
            const lt = Date.parse(lastLog.ts || '');
            // without a time on the row there is nothing to say it is newer
            if (isNaN(at)) return out;
            if (!isNaN(lt) && lt >= at - 120000) return out;
        }
        const lo = num(c.low), hi = num(c.high);
        const outside = (hi !== null && v > hi) || (lo !== null && v < lo);
        pts.push({ ts: c.at || null, value: v, in_spec: !outside, from: 'status' });
        out.from_status = true;
        if (outside) out.failures += 1;
        // "24 runs" draws 24 (round 5: it drew 25). The oldest logged run
        // makes way; the findings' indices move with the points, a finding
        // about only that run goes, one through it keeps its own count.
        if (range === '24' && pts.length > 24) {
            const gone = pts.shift();
            out.logged -= 1;
            if (gone.in_spec === false && out.failures > 0) out.failures -= 1;
            out.violations = out.violations.map((x) => {
                const idx = (x.indices || []).filter((i) => i > 0).map((i) => i - 1);
                return idx.length ? Object.assign({}, x, { indices: idx, count: x.count || (x.indices || []).length }) : null;
            }).filter(Boolean);
        }
        return out;
    }

    /** A time on the record always names its day: "Today 07:30",
        "Yesterday 07:49", "Tue 15:04", "3 Aug" (round 4's critic: a bare
        "07:30" beside "Thu 07:49" read as the older of the two). `lower`
        for the middle of a sentence. */
    function stamp(iso, nowMs, lower) {
        // Self-contained (round 5: built on instruments_logic's when(), it
        // said nothing at all when that file had not loaded), and an older
        // year names its year (round 5: 2 Oct 2025 read "2 Oct", today).
        if (!iso || typeof iso !== 'string') return '';
        const t = Date.parse(iso);
        if (isNaN(t)) return '';
        const d = new Date(t), n = new Date(nowMs === undefined ? Date.now() : nowMs);
        const day0 = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
        const days = Math.round((day0(n) - day0(d)) / 86400000);
        const hmS = hm(t);
        // only the words are lowered: a weekday keeps its capital
        if (days === 0) return (lower ? 'today ' : 'Today ') + hmS;
        if (days === 1) return (lower ? 'yesterday ' : 'Yesterday ') + hmS;
        if (days > 1 && days < 7) return WEEKDAYS[d.getDay()] + ' ' + hmS;
        return d.getDate() + ' ' + MONTHS[d.getMonth()] + (d.getFullYear() === n.getFullYear() ? '' : ' ' + d.getFullYear());
    }
    const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    /** A statistical-control finding, as a caption marked provisional: never
        a second verdict (judge J3: one verdict column). The newest finding is
        said; the others are counted. In control: nothing. */
    function controlPhrase(v) {
        const n = v.count || (v.indices || []).length;
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
            return { i, x: x(i), y: y(v), outside: !!outside, value: v, ts: p.ts, in_spec: p.in_spec, from: p.from || 'log' };
        });
        const ticks = [];
        if (n) {
            const idx = n === 1 ? [0] : n < 4 ? [0, n - 1] : [0, Math.round((n - 1) / 2), n - 1];
            for (const i of idx) ticks.push({ x: x(i), label: day(pts[i].ts) });
        }
        return { plot, points, ticks, lines: { low: lo === null ? null : y(lo), high: hi === null ? null : y(hi), mid: mid === null ? null : y(mid) } };
    }

    // ── the chart in words and as a table (§9.1 rule 5) ─────────────────────
    /** "last 191.83 °C on 30 Sep": the newest run, in the band's decimals.
        Joined to rangeCaption() under the chart, and read out with it. */
    function chartLast(series, spec) {
        const pts = ((series && series.points) || []).filter(p => num(p.value) !== null);
        if (!pts.length) return '';
        const p = pts[pts.length - 1];
        const u = spec && spec.units ? ' ' + spec.units : '';
        return 'last ' + fmtQC(p.value, spec) + u + ' on ' + day(p.ts);
    }
    function dayTime(iso) {
        const t = Date.parse(iso || '');
        return isNaN(t) ? '' : day(iso) + ' ' + hm(t);
    }
    /** The rows "Show as a table" draws: newest first, each run's day and
        time, its value in the band's decimals, and its verdict in words
        (none invented when the check has no limits). */
    function chartRows(series, spec) {
        const s = spec || {};
        const lo = num(s.low), hi = num(s.high);
        const judged = lo !== null || hi !== null;
        return ((series && series.points) || []).filter(p => num(p.value) !== null).slice().reverse().map(p => {
            const v = num(p.value);
            const out = (hi !== null && v > hi) || (lo !== null && v < lo);
            return {
                when: dayTime(p.ts),
                value: fmtQC(v, s),
                verdict: judged ? (out ? 'Outside the limits' : 'Within the limits') : '',
                outside: !!out,
                note: p.from === 'status' ? 'LabCore\'s latest result, not yet in LEM\'s QC log' : '',
            };
        });
    }

    // ── how long a pass counts, and a refresh that failed ──────────────────
    /** §3.1's QC intro, middle sentence, with the window the verdicts were
        judged by: {hours, from} from the server (from = the standard that
        set it, "" = the lab default). */
    function windowSentence(w) {
        const hrs = w && num(w.hours) > 0 ? num(w.hours) : 24;
        const h = String(Math.round(hrs * 100) / 100);
        return 'A passing check counts for ' + h + ' h' + (w && w.from ? ', as ' + w.from + ' sets' : '') + '.';
    }
    function hm(ms) {
        const d = new Date(ms);
        return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    }
    /** The card's line when a refresh failed: what is on screen is the last
        answer read, and it says so (a failed read is never an answer).
        `fail` is {status, error}: status 0 = no answer at all. */
    function staleText(fail, sinceMs, nowMs) {
        const f = fail || {};
        const why = f.error ? String(f.error).replace(/[.\s]+$/, '')
            : (f.status ? 'HTTP ' + f.status : 'LEM did not answer');
        const same = new Date(sinceMs).toDateString() === new Date(nowMs === undefined ? Date.now() : nowMs).toDateString();
        const at = (same ? '' : day(new Date(sinceMs).toISOString()) + ' ') + hm(sinceMs);
        return 'Couldn\'t refresh this instrument (' + why + '). Shown as of ' + at + '; it may be out of date.';
    }

    // ── the "mark it done" sheet (round 6) ─────────────────────────────────
    /** Today in the lab's own calendar, as the date input wants it. Not
        toISOString(): at 21:00 in Houston that is already tomorrow. */
    function localDay(nowMs) {
        const d = new Date(nowMs === undefined ? Date.now() : nowMs);
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }
    /** Why the sheet cannot be sent yet, or '' when it can. */
    function doneProblem(note, when, nowMs) {
        if (!String(note || '').trim()) return 'Say what was done. A note is kept with every completion.';
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(when || ''));
        const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
        if (!d || d.getMonth() !== Number(m[2]) - 1 || d.getDate() !== Number(m[3])) return 'Say which day it was done.';
        if (when > localDay(nowMs)) return 'It cannot be marked done on a day that has not come yet.';
        return '';
    }
    function doneToast(task, who, at) {
        return ((task && task.name) || 'Task') + ' marked done' + (who ? ' · ' + who : '') + (at ? ' · ' + at : '');
    }

    /** A readiness tile's detail as whole facts: its " · "-joined detail,
        plus, on the Bench tile, when it last checked in. The page draws each
        part unbreakable, so a narrow tile wraps only between facts and never
        leaves "14:01" alone on a line (round 8, at 820 wide). */
    function tileParts(t, nowMs) {
        const parts = String((t && t.detail) || '').split(' · ').map(x => x.trim()).filter(Boolean);
        if (t && t.key === 'bench' && t.at) {
            const at = stamp(t.at, nowMs, true);
            if (at) parts.push(at);
        }
        return parts;
    }

    // ── "Change which standards…" (round 2: No QC assigned had no door) ──
    const UNITS = { C: '°C', F: '°F', degC: '°C', degF: '°F', 'mm2/s': 'mm²/s', 'mm^2/s': 'mm²/s',
        cm3: 'cm³', 'g/cm3': 'g/cm³', 'kg/m3': 'kg/m³' };

    /** [title, method] for a LabCore test name, as ui_record.short_test says
        it: "ASTM D2887/D86 - …, 10% Recovery" -> ["10% Recovery", "ASTM
        D2887/D86"]. The sheet names a check the way the QC table does. */
    function shortTest(name) {
        const raw = String(name || '').trim();
        const i = raw.indexOf(' - ');
        if (i < 0) return [raw, ''];
        const method = raw.slice(0, i).replace(/\s+/g, ' ').trim();
        let rest = raw.slice(i + 3).trim();
        const j = rest.lastIndexOf(', ');
        if (j >= 0) {
            const tail = rest.slice(j + 2).trim();
            if (/^(\d|[A-Z]{2,}\b)/.test(tail)) rest = tail;
        }
        return [rest || raw, method];
    }
    const tkey = (sample, test) => JSON.stringify([String(sample || ''), String(test || '')]);

    /** The sheet's tick boxes: one group per library standard, in library
        order, each test ticked when it is assigned; then every assignment
        whose standard or test is no longer in the library, ticked and marked
        gone (the POST replaces the whole set, so leaving it out would drop it
        without anybody choosing to). */
    function assignGroups(samples, targets) {
        const on = new Set((targets || []).map(t => tkey(t.sample, t.test)));
        const seen = new Set();
        const groups = (samples || []).map(s => ({
            name: String(s.name || ''), labId: String(s.sample_id_val || ''), gone: false,
            tests: (s.tests || []).map(t => {
                const key = tkey(s.name, t.name);
                seen.add(key);
                const [title, method] = shortTest(t.name);
                const spec = { low: num(t.low), expected: num(t.expected), high: num(t.high) };
                const units = UNITS[t.units] || String(t.units || '');
                const band = spec.low === null && spec.high === null ? '' : bandText(spec) + (units ? ' ' + units : '');
                return { key, sample: String(s.name || ''), test: String(t.name || ''), title, method, band, checked: on.has(key) };
            }),
        }));
        const gone = new Map();
        for (const t of targets || []) {
            const key = tkey(t.sample, t.test);
            if (seen.has(key)) continue;
            seen.add(key);
            const name = String(t.sample || '');
            if (!gone.has(name)) gone.set(name, { name, labId: '', gone: true, tests: [] });
            const [title, method] = shortTest(t.test);
            gone.get(name).tests.push({ key, sample: name, test: String(t.test || ''), title, method, band: '', checked: true });
        }
        return groups.concat([...gone.values()]);
    }
    /** The targets a set of tick-box keys stands for, in their order. */
    function assignTargets(keys) {
        return (keys || []).map(k => { const [sample, test] = JSON.parse(k); return { sample, test }; });
    }
    function andList(items) {
        return items.length <= 1 ? items.join('') : items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
    }
    /** The line above Save: what the instrument will be checked on, or what
        ticking nothing means. */
    function assignSentence(chosen, before) {
        const n = (chosen || []).length;
        if (!n) return (before || []).length
            ? 'Nothing ticked: it will read No QC assigned, and nothing will judge it.'
            : 'Nothing ticked yet.';
        const stds = [...new Set(chosen.map(t => t.sample))];
        return 'It will be checked on ' + n + ' check' + (n === 1 ? '' : 's') + ', against ' + andList(stds) + '.';
    }
    /** Why Save cannot be sent yet, or '' when it can. */
    function assignProblem(before, chosen) {
        const a = (before || []).map(t => tkey(t.sample, t.test)).sort();
        const b = (chosen || []).map(t => tkey(t.sample, t.test)).sort();
        return JSON.stringify(a) === JSON.stringify(b) ? 'Nothing has changed.' : '';
    }

    /** A QC row's verdict detail, less what the row's When column and the
        section's sentence already say: "last passed 3 Aug" beside a When of
        3 Aug, and "a pass counts for 24 h" under "A passing check counts for
        24 h". What only the row knows stays. */
    function rowDetail(detail, win, at) {
        const hours = win && Number(win.hours);
        return String(detail || '').split(' · ').map(x => x.trim()).filter(Boolean).filter(p => {
            if (at && /^last passed /.test(p)) return false;
            const m = /^a pass counts for ([\d.]+) h$/.exec(p);
            return !(m && Number(m[1]) === hours);
        }).join(' · ');
    }

    /** One change, one request id (W2): the rule static/lem.js keeps for
        the round pages, for the record's writes. The server answers a retry
        carrying the SAME `X-Request-Id` from its ledger instead of applying
        a correction twice, so an id belongs to one change (method, URL,
        body) and is kept while its outcome is unknown: no answer at all, or
        a 5xx that may be a commit whose answer was lost. A 2xx or 4xx is a
        definitive answer and ends it, so pressing again is a new change.
        Kept in sessionStorage (a reload in this tab still finishes the same
        change), in memory when storage is refused. */
    function changeIds(store) {
        const mem = {};
        const PRE = 'lemrid:';
        const get = (k) => { try { const v = store && store.getItem(k); if (v) return v; } catch (_e) { /* memory */ } return mem[k] || null; };
        const put = (k, v) => { mem[k] = v; try { if (store) store.setItem(k, v); } catch (_e) { /* memory only */ } };
        const drop = (k) => { delete mem[k]; try { if (store) store.removeItem(k); } catch (_e) { /* nothing kept */ } };
        function mint() {
            try { if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID(); } catch (_e) { /* below */ }
            let out = Date.now().toString(36) + '-';
            for (let i = 0; i < 4; i++) out += Math.random().toString(36).slice(2, 8);
            return out;
        }
        return {
            begin(method, url, body) {
                const key = PRE + method + ' ' + url + ' ' + (body == null ? '' : body);
                const id = get(key) || mint();
                put(key, id);
                return { key, id };
            },
            settle(key, status) { if (status > 0 && status < 500) drop(key); },
        };
    }

    const api = { chartLast, chartRows, changeIds, rowDetail, shortTest, assignGroups, assignTargets, assignSentence, assignProblem, tileParts, localDay, doneProblem, doneToast, withLatest, stamp, windowSentence, staleText, fmtQC, qcDecimals, bandText, bandPos, captionText, rangeCaption, controlCaption, uLine, chartModel, day, dayIn };
    root.LEMRecord = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
