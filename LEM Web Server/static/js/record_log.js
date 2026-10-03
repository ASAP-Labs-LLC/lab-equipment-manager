/* record_log.js: the record's Log section and Bench and results' counters
   (ia-final §3.1 #6 and #7, piece 7).

   Log: the last 20 entries from /api/logs?machine=<uid> (the same question
   "Open in the full log" asks, so the section and the page agree). The chips
   (All · Results · QC · Status · Setup) are view filters; each re-asks and
   re-points "Open in the full log" and "Export history" at the same group.
   "Load older" appends the next 20 by the server's keyset cursor. A row opens
   the Log entry sheet; on the record its button goes to the full log, since
   the record is already here.

   Bench and results: Filed to LabCore today · Held, waiting for the sample ·
   Replays not re-sent, from /api/ui/instruments/<uid>/bench (LEM's store and
   memory, never LabCore). A counter the bench never reported reads "Not
   reported by this bench's module version", never 0; when two or more read
   that, they share one row (one fact, said once). A read that failed says
   so in each place it would have answered.

   GETs only. The first read is the one a person pays for by opening the
   record; live ticks for this instrument re-ask in the background. */
(function () {
    'use strict';
    const G = window.LEMLog;
    const V = window.LEMLogView;
    const h = V.h;
    const $ = (id) => document.getElementById(id);
    const page = $('main');
    if (!page || !$('log')) return;
    const uid = page.dataset.uid;
    const PAGE = 20;

    let group = 'all';
    let shown = [];
    let next = null;
    let seq = 0;

    function st() { return { equipment: uid, kind: group === 'all' ? '' : group, since: '', until: '', q: '' }; }
    function links() {
        const q = G.toQuery(st());
        $('log-full').href = '/logs' + q;
        $('log-export').href = '/api/logs.csv' + q + (q ? '&' : '?') + 'limit=all';
    }
    function label() { const g = G.GROUPS.find(x => x.key === group); return g ? g.label : ''; }

    function stateLine(kind, text) {
        const el = $('log-state');
        el.hidden = kind === 'none';
        el.dataset.state = kind;
        el.querySelector('.glyph').className = 'glyph ' + ({ loading: 'working', error: 'error' }[kind] || 'never');
        $('log-state-text').textContent = text || '';
        $('log-retry').hidden = kind !== 'error';
    }

    function ask(extra, background) {
        const url = '/api/logs?' + G.apiQuery(st(), Object.assign({ limit: PAGE }, extra || {}));
        const f = background && window.LEMLive ? window.LEMLive.bgFetch(url) : fetch(url, { headers: { Accept: 'application/json' } });
        return f.then(r => r.json().catch(() => ({})).then(b => {
            if (!r.ok) throw Object.assign(new Error('HTTP ' + r.status), { said: b && b.error, status: r.status });
            return b;
        }));
    }

    function paint(events, append) {
        const rows = events.map(e => V.row(e, { onRecord: true }));
        const body = $('log-rows');
        if (append) rows.forEach(r => body.appendChild(r)); else body.replaceChildren(...rows);
        $('log-older').hidden = !next;
    }

    function settle(b) {
        if (shown.length) { stateLine('none'); return; }
        if (b && b.error) { stateLine('error', 'Couldn\'t read this bench\'s log: ' + b.error + ' This is not an empty log.'); return; }
        stateLine('empty', group === 'all'
            ? 'Nothing recorded for this instrument yet. Its bench writes here as it reads results.'
            : 'No ' + (group === 'qc' ? 'QC' : label().toLowerCase()) + ' entries for this instrument. All shows everything it recorded.');
    }

    function load(background) {
        const my = ++seq;
        $('log-tbl').classList.add('is-reading');
        if (!shown.length && !background) stateLine('loading', 'Reading this bench\'s log…');
        return ask(null, background).then(b => {
            if (my !== seq) return;
            shown = b.events || [];
            next = b.next || null;
            paint(shown, false);
            settle(b);
        }).catch(e => {
            if (my !== seq) return;
            if (background && shown.length) return;      // what is on screen stays; the next tick asks again
            shown = []; next = null;
            paint([], false);
            stateLine('error', 'Couldn\'t read this bench\'s log' + (e && e.said ? ': ' + e.said : (e && e.status ? ' (LEM answered ' + e.status + ').' : ': LEM did not answer.')) + ' This is not an empty log.');
        }).finally(() => { if (my === seq) $('log-tbl').classList.remove('is-reading'); });
    }

    $('log-chips').addEventListener('click', (ev) => {
        const b = ev.target.closest('[data-group]');
        if (!b || b.dataset.group === group) return;
        group = b.dataset.group;
        for (const c of $('log-chips').querySelectorAll('[data-group]')) c.setAttribute('aria-pressed', String(c === b));
        shown = []; next = null;
        links();
        load();
    });
    $('log-retry').addEventListener('click', () => load());
    $('log-older').addEventListener('click', () => {
        if (!next) return;
        const btn = $('log-older');
        btn.disabled = true;
        btn.textContent = 'Reading…';
        const my = seq;
        ask({ before: next }).then(b => {
            if (my !== seq) return;
            const evs = b.events || [];
            shown = shown.concat(evs);
            next = b.next || null;
            paint(evs, true);
        }).catch(e => {
            if (window.LEMShell) window.LEMShell.toast('Couldn\'t read older entries' + (e && e.said ? ': ' + e.said : '') + '. Try again.', 'err');
        }).finally(() => { btn.disabled = false; btn.textContent = 'Load older'; });
    });
    const z = G.zoneName();
    if (z) $('log-th-when').textContent = 'When (' + z + ')';

    // ── Bench and results' counters ───────────────────────────────────────
    let counts = null;        // the last answer, or {error}
    const NR = "Not reported by this bench's module version";
    const SHORT = { filed_today: 'results filed to LabCore today', held: 'readings held for their sample', replays_not_resent: 'replays not re-sent' };
    const NAMES = { filed_today: 'Filed to LabCore today', held: 'Held, waiting for the sample', replays_not_resent: 'Replays not re-sent' };
    function fillBench() {
        const box = $('bench-counts');
        if (!box || !counts) return;
        const kv = (k, v, cls) => [h('div', { className: 'k', text: k }), h('div', { className: 'v' + (cls ? ' ' + cls : '') }, v)];
        if (counts.error) {
            box.replaceChildren(...Object.keys(NAMES).flatMap(key =>
                kv(NAMES[key], h('span', {}, h('span', { className: 'glyph error', 'aria-hidden': 'true' }), ' Could not be read: ' + counts.error))));
            return;
        }
        const keys = Object.keys(NAMES);
        const unrep = keys.filter(k => counts[k] && counts[k].n === null && counts[k].text === NR);
        const out = [];
        for (const k of keys) {
            const c = counts[k] || { n: null, text: NR };
            if (unrep.length >= 2 && unrep.indexOf(k) >= 0) {
                if (k !== unrep[0]) continue;
                const words = unrep.map(x => SHORT[x]);
                const list = words.length === 2 ? words.join(' and ') : words.slice(0, -1).join(', ') + ' and ' + words[words.length - 1];
                out.push(...kv('Filing counts', [h('b', { text: NR }),
                    h('span', { className: 'caption', text: list.charAt(0).toUpperCase() + list.slice(1) + ' are unknown here: this bench\'s module does not count them.' })],
                    'bench-unrep'));
                continue;
            }
            let v;
            if (c.n === null) v = h('span', { className: 'muted', text: c.text });
            else if (k === 'held' && c.n > 0) {
                v = [h('span', { text: c.text + ' ' }), h('a', { className: 'link', href: '/logs' + G.toQuery({ equipment: uid, kind: 'results' }), text: 'Results in the log' })];
            } else v = h('span', { text: c.text });
            out.push(...kv(NAMES[k], v, 'bench-count'));
        }
        box.replaceChildren(...out);
    }
    document.addEventListener('lem:bench-drawn', fillBench);
    function loadBench(background) {
        const url = '/api/ui/instruments/' + encodeURIComponent(uid) + '/bench';
        const f = background && window.LEMLive ? window.LEMLive.bgFetch(url) : fetch(url, { headers: { Accept: 'application/json' } });
        return f.then(r => r.json().catch(() => ({})).then(b => {
            if (!r.ok) throw Object.assign(new Error('HTTP ' + r.status), { said: b && b.error });
            counts = b;
            fillBench();
        })).catch(e => {
            if (background && counts && !counts.error) return;
            counts = { error: (e && e.said) || 'LEM did not answer' };
            fillBench();
        });
    }

    // ── live: this instrument wrote something ─────────────────────────────
    if (window.LEMLive) {
        let lastLog = 0, lastBench = 0;
        window.LEMLive.subscribe((u) => {
            const mine = u.reset || (u.machines && u.machines.indexOf(uid) >= 0);
            if (!mine || document.hidden) return;
            const now = Date.now();
            if (now - lastLog > 10000 && shown.length <= PAGE && !$('log-sheet').open) { lastLog = now; load(true); }
            if (now - lastBench > 30000) { lastBench = now; loadBench(true); }
        });
    }

    links();
    load();
    loadBench();
})();
