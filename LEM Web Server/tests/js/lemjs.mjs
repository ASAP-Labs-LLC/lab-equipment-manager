import fs from 'fs';
const src = fs.readFileSync(new URL('../../static/lem.js', import.meta.url), 'utf8');

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

function harness(responses) {
  const store = new Map();
  const win = {
    sessionStorage: {
      getItem: k => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
      removeItem: k => store.delete(k),
    },
  };
  let calls = 0;
  const fetch = () => {
    const body = responses[Math.min(calls++, responses.length - 1)];
    return Promise.resolve({ok: true, json: () => Promise.resolve(body)});
  };
  const g = {window: win, sessionStorage: win.sessionStorage, fetch,
             Date, JSON, Object, Promise, setTimeout};
  const fn = new Function('window', 'sessionStorage', 'fetch', 'requestIdleCallback',
                          src + '; return window.LEM;');
  return {LEM: fn(win, win.sessionStorage, fetch, undefined), fetches: () => calls,
          store};
}

// ── 1. a second identical answer must not repaint ─────────────────────────
{
  const payload = {machines: [{machine_uid: 'm1', title: 'A'}]};
  const h = harness([payload, payload]);
  const paints = [];
  await h.LEM.live('/api/x', (d, meta) => paints.push(meta.cached));
  await h.LEM.live('/api/x', (d, meta) => paints.push(meta.cached));
  check('identical answer paints from cache only', paints, [false, true]);
}

// ── 2. the bug: a field that changes every request forces a repaint ───────
{
  const a = {machines: [{machine_uid: 'm1'}], age_seconds: 1.2};
  const b = {machines: [{machine_uid: 'm1'}], age_seconds: 3.4};
  const h = harness([a, b]);
  const paints = [];
  await h.LEM.live('/api/machines', (d, m) => paints.push(m.cached));
  await h.LEM.live('/api/machines', (d, m) => paints.push(m.cached));
  // Without a signature the clock alone triggers a full repaint.
  check('age_seconds alone repaints without a signature', paints, [false, true, false]);
}

// ── 3. with a signature, only real change repaints ────────────────────────
{
  const a = {machines: [{machine_uid: 'm1', status: 'GREEN'}], age_seconds: 1.2};
  const b = {machines: [{machine_uid: 'm1', status: 'GREEN'}], age_seconds: 9.9};
  const h = harness([a, b]);
  const sig = d => JSON.stringify((d && d.machines) || null);
  const paints = [];
  await h.LEM.live('/api/machines', (d, m) => paints.push(m.cached), {signature: sig});
  await h.LEM.live('/api/machines', (d, m) => paints.push(m.cached), {signature: sig});
  check('a moved clock does NOT repaint', paints, [false, true]);
}

{
  const a = {machines: [{machine_uid: 'm1', status: 'GREEN'}], age_seconds: 1};
  const b = {machines: [{machine_uid: 'm1', status: 'RED'}], age_seconds: 2};
  const h = harness([a, b]);
  const sig = d => JSON.stringify((d && d.machines) || null);
  const paints = [];
  await h.LEM.live('/api/machines', (d, m) => paints.push(m.cached), {signature: sig});
  await h.LEM.live('/api/machines', (d, m) => paints.push(m.cached), {signature: sig});
  check('a real change DOES repaint', paints, [false, true, false]);
}

// ── 4. the fresh answer is always stored, repaint or not ──────────────────
{
  const a = {machines: [{machine_uid: 'm1', status: 'GREEN'}], age_seconds: 1};
  const b = {machines: [{machine_uid: 'm1', status: 'GREEN'}], age_seconds: 50};
  const h = harness([a, b]);
  const sig = d => JSON.stringify((d && d.machines) || null);
  await h.LEM.live('/api/machines', () => {}, {signature: sig});
  await h.LEM.live('/api/machines', () => {}, {signature: sig});
  const box = JSON.parse(h.store.get('lem:/api/machines'));
  check('the newest answer is what is cached', box.data.age_seconds, 50);
}

// ── 5. fresh() bypasses the cache entirely ────────────────────────────────
{
  const a = {v: 1}, b = {v: 2};
  const h = harness([a, b]);
  await h.LEM.get('/api/x');                 // warms the cache
  const got = await h.LEM.fresh('/api/x');
  check('fresh() returns the network answer, not the cached one', got.v, 2);
  const box = JSON.parse(h.store.get('lem:/api/x'));
  check('fresh() still updates the cache', box.data.v, 2);
}

// ── 6. a network failure falls back to the last good answer ──────────────
{
  const store = new Map();
  const win = {sessionStorage: {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k)}};
  let fail = false;
  const fetchFn = () => fail
    ? Promise.reject(new Error('offline'))
    : Promise.resolve({ok: true, json: () => Promise.resolve({v: 1})});
  const LEM = new Function('window', 'sessionStorage', 'fetch',
                           'requestIdleCallback',
                           src + '; return window.LEM;')(
    win, win.sessionStorage, fetchFn, undefined);
  await LEM.get('/api/x');
  fail = true;
  check('fresh() falls back to the cached answer when offline',
        (await LEM.fresh('/api/x')).v, 1);
}

console.log(fails ? `\n${fails} FAILED` : '\nall lem.js cases pass');
process.exit(fails ? 1 : 0);
