/* transfer_section.js: keeps the instrument's Data transfer section
   (templates/_transfer_section.html) current. The server drew it; this
   re-reads GET /api/ui/transfer/<uid> every 15 s while the tab is visible
   and ages "Last delivered" every second from the answer's own number.
   GETs only: it never writes, so an open record page never pins
   /healthz idle_seconds. A refresh that fails keeps the rows it has and
   says, above them, that they are not current. Every string goes in through
   textContent. */
(function () {
    'use strict';
    const T = window.LEMTransfer;
    const $ = (id) => document.getElementById(id);

    function mount() {
        const sec = $('transfer');
        if (!T || !sec || !sec.dataset.uid || !window.LEMShell) return;
        const h = window.LEMShell.h;
        const rowsEl = $('transfer-rows');
        const state = $('transfer-state');
        const stateText = $('transfer-state-text');
        const retry = $('transfer-retry');

        function paintRows() {
            const rows = T.displayRows ? T.displayRows(ctl.rows()) : ctl.rows();
            if (!rows.length) return;
            rowsEl.replaceChildren(...rows.flatMap((r) => [
                h('div', { className: 'k', 'data-key': r.key, text: r.label }),
                h('div', { className: 'v', 'data-key': r.key },
                    h('span', { className: 'tr-line' },
                        h('span', { className: 'glyph ' + (r.glyph || ''), 'aria-hidden': 'true' }),
                        h('span', { className: 'val', text: r.value }),
                        r.href && r.action ? h('a', { className: 'tr-act', href: r.href, text: r.action }) : null),
                    r.note ? h('span', { className: 'caption', text: r.note }) : null)]));
        }

        function render(out) {
            if (out.error) {
                state.hidden = false;
                state.dataset.state = 'error';
                stateText.textContent = 'Not current: ' + out.error;
                retry.hidden = false;
                rowsEl.classList.add('stale');
                return;
            }
            const err = out.data && out.data.error;
            state.hidden = !err;
            retry.hidden = true;
            if (err) {
                state.dataset.state = 'error';
                stateText.textContent = 'Part of this could not be read from LEM\'s store (' + err + '). Rows that depend on it say so.';
            }
            rowsEl.classList.remove('stale');
            paintRows();
        }

        const ctl = T.createSection({
            uid: sec.dataset.uid, fetch: window.fetch.bind(window), render,
            timers: window, now: () => Date.now(),
            isVisible: () => document.visibilityState !== 'hidden',
        });
        retry.addEventListener('click', () => ctl.load());
        ctl.load().then(() => ctl.start());
        // a hidden tab asks nothing; coming back into view, it re-reads at once
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') ctl.load();
        });
        // the age ticks on screen without asking anyone
        setInterval(() => {
            const v = rowsEl.querySelector('.v[data-key="delivered"] .val');
            const row = ctl.rows().find(r => r.key === 'delivered');
            if (v && row && v.textContent !== row.value) v.textContent = row.value;
        }, 1000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})();
