/* running_now.js: what LEM is doing right now, ported from GC hub's
   static/js/running_now.js (v4.0 lane E).

   From LEMLive's answers (`jobs`, ia-final §5) it keeps the sidebar foot's
   #running-now current without a reload: one line, most urgent first; click
   for the list, titled "Running now" while something runs, else "Recent
   work". A running job shows its title and progress; an ended one ONE outcome
   line from the server ("Log copy filled · 41,903 rows"), who, and when it
   finished. Open / Download / Dismiss; Dismiss keeps the list open. Ended jobs
   stay 30 minutes (the server drops them) or until dismissed in this browser
   (localStorage, keyed "<boot id>:<job id>": job ids restart with LEM).

   What is not ported: GC's agent strip (#gc-strip) and its processing-paused
   banner. LEM's benches are counted by the fleet line, and its banner says
   what is degraded about the DATA (status.js), not about a processing queue
   LEM does not have.

   The pure helpers are module.exports for tests/js/running_now.mjs; the DOM
   part mounts itself when the page has #running-now and LEMLive. Every
   string is set with textContent. */
(function (root) {
    'use strict';

    const DISMISS_KEY = 'lem.running-dismissed';
    const DISMISS_MAX = 200;
    const GLYPHS = { running: 'spinner', done: 'dot', failed: 'triangle', stopped: 'ring',
                     interrupted: 'ring' };
    const VERBS = { done: 'finished', failed: 'failed', stopped: 'stopped',
                    interrupted: 'interrupted' };

    function fmtNum(n) {
        return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    function _num(v) {
        return typeof v === 'number' && isFinite(v) ? v : null;
    }

    /** A running job's progress in words. */
    function progressText(task) {
        const p = (task && task.progress) || {};
        const done = _num(p.done);
        const total = _num(p.total);
        const count = total !== null ? fmtNum(done || 0) + ' of ' + fmtNum(total) : null;
        if (p.text && count) return p.text + ' · ' + count;
        if (p.text) return p.text;
        if (count) return count;
        return 'Starting…';
    }

    /** The job's one headline: its title while running, the server's
        outcome line once it ended (none: title + state). */
    function headline(task) {
        if (task.state === 'running') return task.title;
        if (task.outcome) return task.outcome;
        return task.title + ' ' + (VERBS[task.state] || 'ended');
    }

    function detailLine(task) {
        return task.state === 'running' ? progressText(task) : null;
    }

    function _hm(iso) {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return null;
        return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    }

    /** "ryan · started 14:02" / "ryan · finished 14:05" (local time). */
    function metaLine(task) {
        const running = task.state === 'running';
        const at = _hm(running ? task.started_at : task.ended_at);
        const when = at ? (running ? 'started ' : task.state === 'done' ? 'finished ' : 'ended ') + at
            : null;
        return [task.by, when].filter(Boolean).join(' · ');
    }

    function popoverTitle(tasks) {
        return (tasks || []).some(t => t.state === 'running') ? 'Running now' : 'Recent work';
    }

    /** The collapsed indicator ({text, glyph, state, count, pct}), or null. */
    function summary(tasks) {
        const list = tasks || [];
        if (!list.length) return null;
        const running = list.filter(t => t.state === 'running');
        if (running.length > 1) {
            return { text: running.length + ' running', glyph: 'spinner', state: 'running',
                     count: list.length, pct: null };
        }
        if (running.length === 1) {
            const t = running[0];
            const p = t.progress || {};
            const total = _num(p.total);
            const count = total !== null ? fmtNum(_num(p.done) || 0) + '/' + fmtNum(total) : '';
            const parts = [p.text, count].filter(Boolean).join(' ');
            const pct = total ? Math.min(100, Math.round(100 * (_num(p.done) || 0) / total)) : null;
            return { text: t.title + (parts ? ' · ' + parts : ''), glyph: 'spinner', state: 'running',
                     count: list.length, pct };
        }
        const t = list[0];                     // the server sends the most recent first
        return { text: headline(t), glyph: GLYPHS[t.state] || 'ring', state: t.state,
                 count: list.length, pct: null };
    }

    function dismissKey(boot, task) {
        return String(boot || '') + ':' + task.id;
    }

    /** The jobs this browser has not dismissed (running ones always show). */
    function visibleTasks(tasks, dismissed, boot) {
        return (tasks || []).filter(t => t.state === 'running' || !dismissed.has(dismissKey(boot, t)));
    }

    function _storage(s) {
        if (s !== undefined) return s;
        try { return root.localStorage || null; } catch (_) { return null; }
    }

    function loadDismissed(storage) {
        try {
            const st = _storage(storage);
            const raw = st ? st.getItem(DISMISS_KEY) : null;
            const list = raw ? JSON.parse(raw) : [];
            return new Set(Array.isArray(list) ? list.filter(x => typeof x === 'string') : []);
        } catch (_) {
            return new Set();
        }
    }

    function saveDismissed(set, storage) {
        try {
            const st = _storage(storage);
            if (st) st.setItem(DISMISS_KEY, JSON.stringify([...set].slice(-DISMISS_MAX)));
        } catch (_) { /* not remembered: private window, blocked storage */ }
    }

    const pure = { DISMISS_KEY, DISMISS_MAX, fmtNum, progressText, headline, detailLine, metaLine,
                   popoverTitle, summary, dismissKey, visibleTasks, loadDismissed, saveDismissed };
    root.LEMRunningNow = pure;
    if (typeof module !== 'undefined' && module.exports) module.exports = pure;
    if (typeof document === 'undefined') return;

    // ── the page ──────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    let last = { tasks: [], boot: null };
    let dismissed = loadDismissed();
    let open = false;

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined && text !== null) e.textContent = String(text);
        return e;
    }

    function glyph(kind) {
        const g = el('span', 'rn-glyph rn-glyph-' + kind);
        g.setAttribute('aria-hidden', 'true');
        g.textContent = { spinner: '', dot: '●', triangle: '▲', ring: '○' }[kind] || '';
        return g;
    }

    function shown() {
        return visibleTasks(last.tasks, dismissed, last.boot);
    }

    function renderIndicator() {
        const btn = $('running-now');
        if (!btn) return;
        const tasks = shown();
        const s = summary(tasks);
        btn.hidden = !s;
        btn.replaceChildren();
        // the words strip (rail mode) says the same line
        if (root.LEMShell && root.LEMShell.setStatus) root.LEMShell.setStatus({ job: s ? s.text : null });
        if (!s) { if (open) setOpen(false); return; }
        btn.dataset.state = s.state;
        btn.append(glyph(s.glyph), el('span', 'rn-text', s.text));
        if (s.pct !== null) {
            const bar = el('span', 'rn-bar');
            const fill = el('span', 'rn-bar-fill');
            fill.style.width = s.pct + '%';
            bar.appendChild(fill);
            btn.appendChild(bar);
        }
        btn.setAttribute('aria-label', (s.state === 'running' ? 'Running now: ' : 'Recent work: ') + s.text);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) renderPopover();
    }

    function action(label, cls, attrs) {
        const a = el(attrs.href ? 'a' : 'button', 'rn-action ' + cls, label);
        for (const [k, v] of Object.entries(attrs)) {
            if (k === 'onclick') a.addEventListener('click', v);
            else a.setAttribute(k, v);
        }
        return a;
    }

    function renderPopover() {
        const pop = $('running-now-popover');
        if (!pop) return;
        const tasks = shown();
        const list = el('ul', 'rn-list');
        for (const t of tasks) {
            const li = el('li', 'rn-task rn-' + t.state);
            li.dataset.taskId = t.id;
            const head = el('div', 'rn-task-title');
            head.append(glyph(GLYPHS[t.state] || 'ring'), el('span', null, headline(t)));
            li.appendChild(head);
            const detail = detailLine(t);
            if (detail) li.appendChild(el('div', 'rn-task-line', detail));
            const meta = metaLine(t);
            if (meta) li.appendChild(el('div', 'rn-task-meta', meta));
            const acts = el('div', 'rn-actions');
            if (t.open_url) acts.appendChild(action('Open', 'rn-open', { href: t.open_url }));
            if (t.download_url) {
                acts.appendChild(action('Download', 'rn-download', { href: t.download_url, download: '' }));
            }
            if (t.state !== 'running') {
                acts.appendChild(action('Dismiss', 'rn-dismiss', { type: 'button', onclick: () => {
                    dismissed.add(dismissKey(last.boot, t));
                    saveDismissed(dismissed);
                    renderIndicator();
                } }));
            }
            if (acts.childNodes.length) li.appendChild(acts);
            list.appendChild(li);
        }
        pop.replaceChildren(el('div', 'rn-pop-title', popoverTitle(tasks)), list);
    }

    function setOpen(which) {
        open = !!which;
        const pop = $('running-now-popover');
        const btn = $('running-now');
        if (pop) pop.hidden = !open;
        if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) renderPopover();
    }

    function onUpdate(u) {
        last = { tasks: Array.isArray(u.jobs) ? u.jobs : last.tasks, boot: u.boot || last.boot };
        renderIndicator();
    }

    function mount() {
        if (!root.LEMLive || !$('running-now')) return;
        $('running-now').addEventListener('click', (e) => { e.stopPropagation(); setOpen(!open); });
        const pop = $('running-now-popover');
        if (pop) pop.addEventListener('click', (e) => e.stopPropagation());
        document.addEventListener('click', () => { if (open) setOpen(false); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && open) setOpen(false); });
        root.LEMLive.subscribe(onUpdate);
        root.LEMLive.start();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})(typeof window !== 'undefined' ? window : globalThis);
