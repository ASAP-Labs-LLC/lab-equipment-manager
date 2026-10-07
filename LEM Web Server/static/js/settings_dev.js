// Settings › Developer (only under --dev; this file is not even referenced
// otherwise). The four Simulate tools that used to sit in the floor's
// right-click menu, now aimed at an instrument picked here. The server side is
// POST /api/dev/simulate, registered only when web_app.dev_tools_allowed.
(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    document.addEventListener('DOMContentLoaded', async () => {
        const T = window.LEMSettings;
        const S = window.LEMShell;
        const pick = $('dev-machine'), msg = $('dev-msg');
        if (!T || !pick) return;
        const status = T.seg($('dev-status-seg'), 'data-status');

        const fleet = await T.call('GET', '/api/machines');
        const machines = (fleet.ok && fleet.body && fleet.body.machines) || [];
        if (!machines.length) {
            pick.replaceChildren(S.h('option', { value: '', text: fleet.ok ? 'No instruments in the record yet' : 'The record could not be read' }));
        } else {
            pick.replaceChildren(...machines.slice().sort((a, b) => String(a.title).localeCompare(String(b.title)))
                .map(m => S.h('option', { value: m.machine_uid, text: m.title + ' · ' + m.status })));
        }

        const name = () => (pick.selectedOptions[0] ? pick.selectedOptions[0].textContent.split(' · ')[0] : '');
        document.querySelectorAll('#developer [data-sim]').forEach(btn => btn.addEventListener('click', async () => {
            const act = btn.dataset.sim;
            if (act === 'status-open') {
                const open = $('dev-status').hidden;
                $('dev-status').hidden = !open;
                btn.setAttribute('aria-expanded', open ? 'true' : 'false');
                return;
            }
            if (!pick.value) { T.say(msg, 'Pick an instrument first.', 'err'); return; }
            const body = { machine_uid: pick.value, action: act };
            if (act === 'status') body.status = status();
            const done = T.busy(btn, 'Working…');
            const res = await T.call('POST', '/api/dev/simulate', body);
            done();
            if (!res.ok) {
                T.say(msg, (res.body && res.body.error) || ('The simulation was refused (' + res.status + ').'), 'err');
                return;
            }
            const b = res.body || {};
            T.say(msg, act === 'status' ? name() + ' now shows ' + b.simulated + ' on every screen, marked Simulated.'
                : act === 'clear' ? (b.cleared ? name() + ' shows its record again.' : name() + ' had no simulated status.')
                : b.landed + (b.landed === 1 ? ' simulated run' : ' simulated runs') + ' landed in the dev log for ' + name() + '.', 'ok');
        }));
    });
})();
