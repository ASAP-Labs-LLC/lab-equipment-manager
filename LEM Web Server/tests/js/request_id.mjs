// LEM.send and X-Request-Id: the browser half of W2 (static/lem.js).
//
// The server dedupes a retry only if the retry carries the SAME id as the
// attempt whose answer was lost. Round one built the server half and no page
// ever sent the header (the critic grepped: 0 hits), so a supervisor whose
// "Save" timed out and who pressed it again got a second change in the
// §7.8.2 trail. What has to hold, and why each one is here:
//
// * every write carries an id, and a retry of the SAME change reuses it —
//   automatically once after a network failure, and on the person's next
//   press if that fails too;
// * a definitive answer (2xx, or a 4xx refusal) ends the id: the next press
//   is a NEW change, which is what the person means by pressing again after
//   being told it saved or was refused;
// * a 5xx keeps it: a 500 may be a commit whose answer was lost, and the
//   same id is what lets the server say "already done" instead of doing it
//   twice (a rolled-back 502/503 left nothing in the ledger, so reusing the
//   id there costs nothing);
// * a different change (other body or other URL) never shares an id;
// * an unreachable server is "not known whether it saved", never "nothing
//   was saved": after a lost response the second sentence is false.
import fs from 'fs';
const src = fs.readFileSync(new URL('../../static/lem.js', import.meta.url), 'utf8');

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

// script: list of 'throw' | {status, body, replayed}
function harness(script) {
  const store = new Map();
  const win = {sessionStorage: {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k)}};
  const sent = [];
  let i = 0;
  const fetch = (url, init) => {
    sent.push({url, method: init && init.method,
               id: init && init.headers && init.headers['X-Request-Id'],
               body: init && init.body});
    const step = script[Math.min(i++, script.length - 1)];
    if (step === 'throw') return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve({
      ok: step.status >= 200 && step.status < 300, status: step.status,
      headers: {get: h => (h.toLowerCase() === 'x-request-replayed' && step.replayed ? 'true' : null)},
      json: () => Promise.resolve(step.body || {})});
  };
  const fast = (fn) => { fn(); return 0; };          // no real waiting in a test
  const fn = new Function('window', 'sessionStorage', 'fetch', 'requestIdleCallback', 'setTimeout',
                          src + '; return window.LEM;');
  return {LEM: fn(win, win.sessionStorage, fetch, undefined, fast), sent, store};
}

const URL1 = '/api/machines/m1/corrections';
const BODY = {test_name: 'Flash', correction: '0.5'};

// 1. a save carries an id
{
  const h = harness([{status: 200, body: {ok: true}}]);
  const r = await h.LEM.send(URL1, {body: BODY});
  check('a write carries X-Request-Id', typeof h.sent[0].id === 'string' && h.sent[0].id.length >= 16, true);
  check('and succeeds', r.ok, true);
}

// 2. network failure: one automatic retry with the SAME id
{
  const h = harness(['throw', {status: 200, body: {ok: true}, replayed: true}]);
  const r = await h.LEM.send(URL1, {body: BODY});
  check('retried once after a network failure', h.sent.length, 2);
  check('with the same id', h.sent[0].id === h.sent[1].id, true);
  check('the replay is reported as a success', [r.ok, r.replayed], [true, true]);
}

// 3. both attempts fail: the NEXT press reuses the id, and the words are honest
{
  const h = harness(['throw', 'throw', {status: 200, body: {ok: true}, replayed: true}]);
  const r1 = await h.LEM.send(URL1, {body: BODY});
  check('two failures: not ok', r1.ok, false);
  check('never claims nothing was saved', /nothing was saved/i.test(r1.error), false);
  check('says it is not known', /not known/i.test(r1.error), true);
  const r2 = await h.LEM.send(URL1, {body: BODY});
  check('the next press of the same change reuses the id', h.sent[2].id, h.sent[0].id);
  check('and lands', r2.ok, true);
}

// 4. a 500 keeps the id (the answer may be a lost commit)
{
  const h = harness([{status: 500, body: {}}, {status: 200, body: {ok: true}, replayed: true}]);
  await h.LEM.send(URL1, {body: BODY});
  check('a 5xx is not retried automatically', h.sent.length, 1);
  await h.LEM.send(URL1, {body: BODY});
  check('a press after a 5xx reuses the id', h.sent[1].id, h.sent[0].id);
}

// 5. a definitive answer ends the id
{
  const h = harness([{status: 200, body: {ok: true}}, {status: 200, body: {ok: true}}]);
  await h.LEM.send(URL1, {body: BODY});
  await h.LEM.send(URL1, {body: BODY});
  check('after a 200 the same change pressed again is a NEW request', h.sent[1].id !== h.sent[0].id, true);
}
{
  const h = harness([{status: 400, body: {error: 'x'}}, {status: 200, body: {ok: true}}]);
  await h.LEM.send(URL1, {body: BODY});
  await h.LEM.send(URL1, {body: BODY});
  check('after a 4xx refusal the next press is a NEW request', h.sent[1].id !== h.sent[0].id, true);
}

// 6. a different change never shares an id, even while one is pending
{
  const h = harness([{status: 503, body: {}}, {status: 200, body: {ok: true}},
                     {status: 200, body: {ok: true}}]);
  await h.LEM.send(URL1, {body: BODY});
  await h.LEM.send(URL1, {body: {test_name: 'Flash', correction: '0.7'}});
  await h.LEM.send(URL1 + '/Flash', {method: 'DELETE'});
  const ids = new Set(h.sent.map(s => s.id));
  check('other body, other URL: three ids', ids.size, 3);
}

// 7. GETs are not this function's business; reads carry no id
{
  const h = harness([{status: 200, body: {}}]);
  await h.LEM.send('/api/x', {method: 'DELETE'});
  check('DELETE carries an id too', !!h.sent[0].id, true);
}

// 8. clearing the read cache after a write must not forget a pending change
{
  const h = harness(['throw', 'throw', {status: 200, body: {ok: true}, replayed: true}]);
  await h.LEM.send(URL1, {body: BODY});
  h.LEM.bust();
  await h.LEM.send(URL1, {body: BODY});
  check('bust() keeps the pending id', h.sent[2].id, h.sent[0].id);
}

console.log(fails ? `\n${fails} FAILED` : '\nall ok');
process.exit(fails ? 1 : 0);
