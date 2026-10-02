// Settings › Backups (transfer §11). Every sentence is the server's
// (custody.Custody.view): this file places them and wires three buttons.
//
// * "Back up now" waits for the answer, which is the checked copy or the
//   reason there is none.
// * "Run the restore drill" starts it (it boots a second server and takes
//   seconds), then re-reads /api/custody every 2 s until it has finished —
//   only while a drill this page started is running; no timer otherwise.
// * The bridge switch asks twice before turning the bridge OFF, and the
//   server decides: a 409 lists its refusals here, in its own words.
(function () {
    'use strict';
    const S = window.LEMShell;
    const h = S.h;
    const $ = (id) => document.getElementById(id);

    async function call(method, url, payload) {
        try {
            const r = await fetch(url, {
                method, credentials: 'same-origin',
                headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
                body: payload === undefined ? undefined : JSON.stringify(payload),
            });
            let body = null;
            try { body = await r.json(); } catch (_e) { body = null; }
            return { status: r.status, ok: r.ok, body };
        } catch (_e) {
            return { status: 0, ok: false, body: null };
        }
    }

    function say(text, kind, list) {
        const el = $('cust-msg');
        el.className = 'msg' + (kind ? ' ' + kind : '');
        el.replaceChildren(h('span', { text: text || '' }));
        if (list && list.length) {
            el.append(h('ul', { className: 'cust-refusals' }, ...list.map(t => h('li', { text: String(t) }))));
        }
    }

    function why(res, what) {
        if (!res.status) return 'LEM did not answer, so ' + what + '. Try again.';
        if (res.status === 401) return 'You are signed out, so ' + what + '.';
        const e = res.body && res.body.error;
        return (e ? String(e).replace(/\.?$/, '. ') : 'The server answered ' + res.status + '. ') +
            what.charAt(0).toUpperCase() + what.slice(1) + '.';
    }

    function paint(view) {
        if (!view || !Array.isArray(view.rows)) return;
        $('cust-rows').replaceChildren(...view.rows.flatMap(r => [
            h('div', { className: 'k', text: r.label }),
            h('div', { className: 'v', 'data-key': r.key },
                h('span', { className: 'line' },
                    h('span', { className: 'glyph ' + (r.glyph || ''), 'aria-hidden': 'true' }),
                    h('span', { className: 'val', text: r.value })),
                r.note ? h('span', { className: 'caption', text: r.note }) : null)]));
        $('cust-drill').disabled = !!view.drill_running;
        const b = $('cust-bridge');
        b.dataset.on = view.bridge_on ? 'true' : 'false';
        b.textContent = view.bridge_on ? 'Turn the bridge off…' : 'Turn the bridge back on';
        disarm();
    }

    function busy(btn, label) {
        const was = btn.textContent;
        btn.disabled = true;
        btn.textContent = label;
        return () => { btn.textContent = was; btn.disabled = false; };
    }

    async function backup() {
        const done = busy($('cust-backup'), 'Backing up…');
        const res = await call('POST', '/api/custody/backup');
        done();
        if (res.body && res.body.view) paint(res.body.view);
        if (res.ok) say('Backed up, and the copy passed its checks.', 'ok');
        else say(why(res, 'no backup was taken'), 'err');
    }

    async function drill() {
        const btn = $('cust-drill');
        btn.disabled = true;
        const res = await call('POST', '/api/custody/drill');
        if (res.status !== 202) {
            btn.disabled = false;
            say(why(res, 'the drill did not start'), 'err');
            return;
        }
        say('The drill is running: the newest backup is being restored and booted on a scratch port.');
        const started = Date.now();
        for (;;) {
            await new Promise(r => setTimeout(r, 2000));
            const st = await call('GET', '/api/custody');
            if (st.ok && st.body && st.body.view && !st.body.view.drill_running) {
                paint(st.body.view);
                const row = st.body.view.rows.find(r => r.key === 'drill');
                const passed = row && row.glyph === 'final';
                say(passed ? 'The drill passed.' : 'The drill did not pass: ' + (row ? row.note : ''), passed ? 'ok' : 'err');
                return;
            }
            if (Date.now() - started > 180000) {
                say(st.ok ? 'The drill is still running after 3 minutes; its result will be on this page when it ends.'
                    : why(st, 'the drill\'s progress could not be read'), 'err');
                btn.disabled = false;
                return;
            }
        }
    }

    let armed = null;
    function disarm() {
        if (armed) { clearTimeout(armed); armed = null; }
        const b = $('cust-bridge');
        b.classList.remove('btn-danger');
        if (b.dataset.on === 'true') b.textContent = 'Turn the bridge off…';
    }

    async function bridge() {
        const b = $('cust-bridge');
        const on = b.dataset.on === 'true';
        if (on && !armed) {
            // asked twice: this freezes LabCore's copy of LEM's tables
            b.textContent = 'Press again to turn the bridge off';
            armed = setTimeout(disarm, 6000);
            say('Turning the bridge off stops copying LEM\'s record into LabCore. LEM checks first that the store has a recent off-host copy.');
            return;
        }
        disarm();
        const done = busy(b, on ? 'Turning off…' : 'Turning on…');
        const res = await call('POST', '/api/transfer/bridge', { on: !on });
        done();
        if (res.status === 409 && res.body && res.body.refusals) {
            say('The bridge stays on:', 'err', res.body.refusals);
            return;
        }
        if (!res.ok) { say(why(res, 'the bridge was not changed'), 'err'); return; }
        const st = await call('GET', '/api/custody');
        if (st.ok && st.body) paint(st.body.view);
        say(on ? 'The bridge is off.' : 'The bridge is on again.', 'ok');
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (!$('backups')) return;
        $('cust-backup').addEventListener('click', backup);
        $('cust-drill').addEventListener('click', drill);
        $('cust-bridge').addEventListener('click', bridge);
    });
})();
