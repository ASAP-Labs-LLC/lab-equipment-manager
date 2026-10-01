// static/js/live.js: the poller (LEMLive.createPoller), driven with fake
// timers, a fake fetch and a fake page visibility. Ported from GC hub's
// tests/js/live_poller.test.js; the scenarios are GC's, the URL and the
// background header are LEM's, and three are LEM's own (at the end): the
// server is killed, every request is a GET, and nothing is "Live" after 90 s.
//
// Why: the bar for the live feed is "kill the server and the live row says
// Reconnecting within 15 s, and never Live after 90 s", and "three tabs open
// for ten minutes leave /healthz idle_seconds rising". A poller that POSTed,
// or that kept its last "Live" while the timers stalled, would fail both
// without any test noticing.
import { createRequire } from 'module';
import assert from 'assert';
const require = createRequire(import.meta.url);
const L = require('../../static/js/live.js');
let n = 0;
const t = { eq(a, b) { assert.deepStrictEqual(a, b); n++; } };


function answer(over) {
    return Object.assign({ cursor: 'b:1', reset: false, machines: [], kinds: [],
        needs_you: 0, fleet: { checking_in: 1, total: 1, live_road: 1 }, round: null, qc_out: 0,
        notifications_unread: 0, jobs: [], version: 'v1' }, over || {});
}

/** A fake world: timers you advance, a fetch you answer by hand. */
function world() {
    const w = { now: 0, timers: [], calls: [], pending: [], visible: true, handlers: {},
                inFlight: 0, maxInFlight: 0, fail: false, next: [] };
    w.deps = {
        now: () => w.now,
        setTimeout: (fn, ms) => { const tm = { fn, at: w.now + ms, id: Symbol('t') }; w.timers.push(tm); return tm.id; },
        clearTimeout: (id) => { w.timers = w.timers.filter(tm => tm.id !== id); },
        isVisible: () => w.visible,
        on: (ev, fn) => { (w.handlers[ev] = w.handlers[ev] || []).push(fn); },
        off: (ev, fn) => { w.handlers[ev] = (w.handlers[ev] || []).filter(f => f !== fn); },
        fetch: (url, opts) => {
            w.calls.push({ url, opts });
            w.inFlight++;
            w.maxInFlight = Math.max(w.maxInFlight, w.inFlight);
            return new Promise((resolve, reject) => w.pending.push({ resolve, reject }));
        },
    };
    // answer the oldest open request
    w.reply = async (body) => {
        const p = w.pending.shift();
        w.inFlight--;
        if (body instanceof Error) p.reject(body);
        else p.resolve({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
        await flush();
    };
    w.replyStatus = async (status) => {
        const p = w.pending.shift();
        w.inFlight--;
        p.resolve({ ok: false, status, json: async () => ({}), text: async () => '{}' });
        await flush();
    };
    // the next timer's delay from now, and run it
    w.nextDelay = () => {
        const tm = w.timers.slice().sort((a, b) => a.at - b.at)[0];
        return tm ? tm.at - w.now : null;
    };
    w.fire = async () => {
        const tm = w.timers.slice().sort((a, b) => a.at - b.at)[0];
        w.timers = w.timers.filter(x => x !== tm);
        w.now = tm.at;
        tm.fn();
        await flush();
    };
    w.emit = async (ev) => { for (const fn of w.handlers[ev] || []) fn(); await flush(); };
    return w;
}

async function flush() {
    for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));
}

const run = async () => {
    // ── start: one request, resolves after the first answer, idempotent ──
    {
        const w = world();
        const p = L.createPoller(w.deps);
        const got = [];
        p.subscribe(u => got.push(u));
        let started = false;
        const s1 = p.start().then(() => { started = true; });
        const s2 = p.start();
        await flush();
        t.eq(w.calls.length, 1);
        t.eq(w.calls[0].url, '/api/ui/live');
        t.eq(w.calls[0].opts.headers['X-LEM-Background'], '1');
        t.eq(started, false);
        await w.reply(answer({ reset: true }));
        await s1; await s2;
        t.eq(started, true);
        t.eq(got.length, 1);
        t.eq(got[0].reset, true);
        t.eq(p.status().connected, true);
        // the next poll carries the cursor
        t.eq(w.nextDelay(), 3000);
        await w.fire();
        t.eq(w.calls[1].url, '/api/ui/live?since=b%3A1');
        p.stop();
    }

    // ── start resolves after a failed first answer too (the page loads anyway) ──
    {
        const w = world();
        const p = L.createPoller(w.deps);
        let started = false;
        p.start().then(() => { started = true; });
        await flush();
        await w.replyStatus(503);
        t.eq(started, true);
        t.eq(p.status().connected, false);
        t.eq(p.status().error, 'HTTP 503');
        p.stop();
    }

    // ── cadence: 3 s visible, 30 s hidden, at once on becoming visible / focus ──
    {
        const w = world();
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        await w.reply(answer());
        t.eq(w.nextDelay(), 3000);
        await w.fire();
        w.visible = false;
        await w.reply(answer());
        t.eq(w.nextDelay(), 30000);
        // becoming visible polls at once (and reschedules)
        w.visible = true;
        const before = w.calls.length;
        await w.emit('visibilitychange');
        t.eq(w.calls.length, before + 1);
        await w.reply(answer());
        t.eq(w.nextDelay(), 3000);
        // a hidden tab's visibilitychange does not poll
        w.visible = false;
        await w.emit('visibilitychange');
        t.eq(w.calls.length, before + 1);
        // focus polls at once
        w.visible = true;
        await w.emit('focus');
        t.eq(w.calls.length, before + 2);
        p.stop();
    }

    // ── backoff with a cap; recovery resets it ──
    {
        const w = world();
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        const delays = [];
        for (let i = 0; i < 5; i++) {
            await w.reply(new Error('network down'));
            delays.push(w.nextDelay());
            await w.fire();
        }
        t.eq(delays, [6000, 12000, 12000, 12000, 12000]);
        t.eq(p.status().connected, false);
        t.eq(p.status().error, 'network down');
        await w.reply(answer());
        t.eq(w.nextDelay(), 3000);
        t.eq(p.status().connected, true);
        // hidden: 30 s, backing off to 60 s
        w.visible = false;
        await w.fire();
        await w.reply(new Error('x'));
        t.eq(w.nextDelay(), 60000);
        await w.fire();
        await w.reply(new Error('x'));
        t.eq(w.nextDelay(), 60000);
        p.stop();
    }

    // ── overlapping triggers: never two requests at once, one follow-up ──
    {
        const w = world();
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        p.pollNow(); p.pollNow();
        await w.emit('focus');
        t.eq(w.calls.length, 1);
        await w.reply(answer());
        t.eq(w.calls.length, 2);               // the queued follow-up, at once
        t.eq(w.nextDelay(), L.POLL_TIMEOUT_MS); // no next poll while it is in flight: only its timeout
        await w.reply(answer());
        t.eq(w.calls.length, 2);
        t.eq(w.maxInFlight, 1);
        t.eq(w.nextDelay(), 3000);
        p.stop();
        // stopped: nothing more
        await w.fire().catch(() => {});
        t.eq(w.calls.length, 2);
    }

    // ── a late subscriber gets its own reset with the snapshot; unsubscribe ──
    {
        const w = world();
        const p = L.createPoller(w.deps);
        const early = [];
        p.subscribe(u => early.push(u));
        p.start();
        await flush();
        await w.reply(answer({ reset: true, needs_you: 6, notifications_unread: 4 }));
        const late = [];
        const off = p.subscribe(u => late.push(u));
        await flush();
        t.eq(late.length, 1);
        t.eq(late[0].reset, true);
        t.eq(late[0].needs_you, 6);
        t.eq(late[0].notifications_unread, 4);
        t.eq(early.length, 1);                  // the early one got no second reset
        t.eq(p.last().needs_you, 6);
        off();
        await w.fire();
        await w.reply(answer({ cursor: 'b:2', machines: ['gc-7'] }));
        t.eq(late.length, 1);
        t.eq(early.length, 2);
        t.eq(early[1].machines, ['gc-7']);
        // a subscriber that throws doesn't stop the others
        p.subscribe(() => { throw new Error('boom'); });
        const after = [];
        p.subscribe(u => after.push(u));
        await flush();
        await w.fire();
        await w.reply(answer({ cursor: 'b:3', machines: ['gc-8'] }));
        t.eq(after.some(u => (u.machines || []).includes('gc-8')), true);
        p.stop();
    }

    // ── a hung hub (connection accepted, no reply): the poll gives up, status says so, polling goes on ──
    {
        const w = world();
        w.now = 1_000_000;                           // a real clock (last_ok_at 0 reads as "never")
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        await w.reply(answer({ reset: true }));
        t.eq(p.status().connected, true);
        await w.fire();                              // the next poll goes out ...
        t.eq(w.calls.length, 2);
        t.eq(!!(w.calls[1].opts && w.calls[1].opts.signal), true);   // abortable
        // ... and never comes back: the timeout fires
        t.eq(w.nextDelay(), L.POLL_TIMEOUT_MS);
        await w.fire();
        const st = p.status();
        t.eq(st.connected, false);
        t.eq(/Reconnecting/.test(L.statusText(st, w.now)), true);
        t.eq(w.calls[1].opts.signal.aborted, true);
        // the backoff after one failure, then a second request goes out (not blocked by the hung one)
        t.eq(w.nextDelay(), L.nextDelay(true, 1));
        await w.fire();
        t.eq(w.calls.length, 3);
        // the hung request answering late changes nothing
        const late = w.pending.shift(); w.inFlight--;
        late.resolve({ ok: true, status: 200, json: async () => answer({ cursor: 'zz:9' }),
                       text: async () => JSON.stringify(answer({ cursor: 'zz:9' })) });
        await flush();
        t.eq(p.status().connected, false);
        // the hub is back: the third request answers and all is live again
        await w.reply(answer({ cursor: 'b:2' }));
        t.eq(p.status().connected, true);
        t.eq(w.calls.length, 3);
        p.stop();
    }

    // ── backstop: no answer for a long time is never reported as connected ──
    {
        const w = world();
        w.now = 1_000_000;
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        await w.reply(answer({ reset: true }));
        t.eq(p.status().connected, true);
        w.now += 10 * 60 * 1000;                    // the timers stalled (a frozen tab), ten minutes on
        t.eq(p.status().connected, false);
        t.eq(/Reconnecting/.test(L.statusText(p.status(), w.now)), true);
        p.stop();
    }

    // ── bgFetch marks a request as background ──
    {
        const seen = [];
        const f = (url, opts) => { seen.push({ url, opts }); return Promise.resolve('ok'); };
        await L.bgFetch('/api/machines', { headers: { Accept: 'application/json' } }, f);
        t.eq(seen[0].url, '/api/machines');
        t.eq(seen[0].opts.headers['X-LEM-Background'], '1');
        t.eq(seen[0].opts.headers.Accept, 'application/json');
        t.eq(seen[0].opts.credentials, 'same-origin');
        // never on a write
        let threw = false;
        try { await L.bgFetch('/api/x', { method: 'POST' }, f); } catch (_) { threw = true; }
        t.eq(threw, true);
    }
    // ── LEM: the server is killed. Connection refused on the next poll; the
    //    words say Reconnecting within 15 s and never Live again ──
    {
        const w = world();
        w.now = 1_000_000;
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        await w.reply(answer({ reset: true }));
        const okAt = w.now;
        t.eq(L.statusText(p.status(), w.now), 'Live · updated just now');
        // killed: every request from here on is refused at once
        let sawReconnecting = null;
        for (let i = 0; i < 40 && w.now - okAt <= 120_000; i++) {
            await w.fire();                               // the next poll goes out
            if (w.pending.length) await w.reply(new TypeError('Failed to fetch'));
            const text = L.statusText(p.status(), w.now);
            if (sawReconnecting === null && /^Reconnecting… · last update /.test(text)) sawReconnecting = w.now - okAt;
            if (w.now - okAt > 90_000) t.eq(/^Live/.test(text), false);
        }
        t.eq(sawReconnecting !== null && sawReconnecting <= 15_000, true);
        // and between polls (the words tick every second on the page) it never says Live either
        for (let s = 0; s <= 120; s++) {
            const text = L.statusText(p.status(), okAt + s * 1000 + 3000);
            t.eq(/^Live/.test(text), false);
        }
        p.stop();
    }

    // ── LEM: a hidden tab that stalls for 90 s is not Live (the backstop) ──
    {
        const w = world();
        w.now = 1_000_000;
        w.visible = false;
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        await w.reply(answer({ reset: true }));
        w.now += 90_001;                               // no timer fired: a frozen tab
        t.eq(/^Reconnecting/.test(L.statusText(p.status(), w.now)), true);
        p.stop();
    }

    // ── LEM: every request the poller makes is a GET (idle-gated deploys, A.7) ──
    {
        const w = world();
        const p = L.createPoller(w.deps);
        p.start();
        await flush();
        for (let i = 0; i < 20; i++) {
            await w.reply(i % 3 ? answer({ cursor: 'b:' + i }) : new Error('blip'));
            await w.fire();
        }
        t.eq(w.calls.length >= 20, true);
        t.eq(w.calls.every(c => (c.opts.method || 'GET') === 'GET'), true);
        t.eq(w.calls.every(c => c.url.startsWith('/api/ui/live')), true);
        t.eq(w.calls.every(c => c.opts.headers['X-LEM-Background'] === '1'), true);
        p.stop();
    }
};
run().then(() => console.log('live_poller.mjs: ' + n + ' checks passed'),
           (e) => { console.error('FAIL live_poller.mjs\n', e); process.exit(1); });
