// The data transfer's pages, in a harness (static/js/transfer_logic.js,
// LEMTransfer): the instrument's Data transfer section, /results/conflicts
// and Settings › Transfer.
//
// Why these rules, and why they are tested here rather than clicked through:
//
// * EVERY SAVE CHECKS r.ok. This codebase has twice told a person something
//   landed when it had not ("imported 3094" while nothing landed, notes.md).
//   On these pages the writes are a person's decision on a held result (D1),
//   resetting a bench's token, retiring an instrument, the bridge switch,
//   importing a journal folder and the dedupe approval. Each is driven here
//   against a fake server that answers 200, 409, 503, an HTML error page,
//   and a dropped connection, and only the 2xx may come back ok. The
//   harness also checks the 409's own sentence reaches the person.
// * NO POST ON A TIMER. /healthz idle_seconds is how the unattended updater
//   knows nobody is working; GETs leave it alone, writes reset it. A page
//   that wrote on a timer would pin it at zero for as long as a tablet sat
//   on it, and no release would ever install (constraints A.7). So every
//   controller is started with fake timers and run for a simulated ten
//   minutes, and every request it made on its own must be a GET.
// * A FAILED READ IS NEVER AN EMPTY RESULT. A refresh that fails keeps what
//   the page had and says it is not current; it never draws "nothing to
//   decide".
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/transfer_logic.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const T = root.LEMTransfer;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
const claim = (name, ok, note) => {
  if (ok) { console.log(`  ok   ${name}`); return; }
  fails++; console.log(`  FAIL ${name}${note ? `\n         ${note}` : ''}`);
};

if (!T) { console.log('  FAIL transfer_logic.js does not define window.LEMTransfer'); process.exit(1); }

// ── a fake server and fake timers ───────────────────────────────────────────

function response(status, body, opts) {
  return {
    ok: status >= 200 && status < 300, status,
    json: async () => {
      if (opts && opts.html) throw new SyntaxError('Unexpected token <');
      return body;
    },
  };
}

function server(answer) {
  const calls = [];
  const fetch = async (url, opts) => {
    const method = (opts && opts.method) || 'GET';
    calls.push({ url, method, body: opts && opts.body });
    const a = answer(method, url, opts && opts.body);
    if (a === 'drop') throw new TypeError('Failed to fetch');
    return a;
  };
  return { fetch, calls };
}

function timers() {
  let now = 0;
  let next = 1;
  const intervals = new Map();
  return {
    setInterval(fn, ms) { const id = next++; intervals.set(id, { fn, ms, at: now + ms }); return id; },
    clearInterval(id) { intervals.delete(id); },
    async run(ms) {
      const end = now + ms;
      for (;;) {
        let soonest = null;
        for (const [id, t] of intervals) if (!soonest || t.at < soonest.t.at) soonest = { id, t };
        if (!soonest || soonest.t.at > end) break;
        now = soonest.t.at;
        soonest.t.at += soonest.t.ms;
        await soonest.t.fn();
      }
      now = end;
    },
    count() { return intervals.size; },
  };
}

const OVERVIEW = { benches: [
  { machine_uid: 'a', title: 'Agilent GC 1', mode: 'v2', module: 'v4.0.0', road: 'Internet', delivered_s: 4,
    waiting: 0, checking_in: true, enrolment: 'enrolled', href: '/instruments/a#transfer' },
  { machine_uid: 'e', title: 'Eravap', mode: 'legacy', module: T.NOT_REPORTED, road: 'LabCore (older module)',
    delivered_s: null, waiting: null, checking_in: false, enrolment: 'none', href: '/instruments/e#transfer' },
  { machine_uid: 'n', title: 'Eraspec NIR', mode: 'v2', module: 'v4.0.0', road: 'LAN', delivered_s: 720,
    waiting: 148, checking_in: true, enrolment: 'pending', href: '/instruments/n#transfer' }],
  bridge: { on: true, can_change: true, refusals: ['1 bench still runs the older module (3.9): Eravap.'] } };
const CONFLICTS = { open: [{ ref: 'ep-1:2', machine_uid: 'a', title: 'Agilent GC 1', lab_id: '38214',
  test_name: 'IBP', ours: '151.9', theirs: '151.6', their_operator: 'dana', their_at: '09:10',
  opened_at: '09:41', print_at: '09:40' }], decided: [], rejected: [] };

// GETs answer the page's data; writes answer what `write` says
function lab(write) {
  return server((method, url, body) => {
    if (method === 'GET') {
      if (url.startsWith('/api/transfer/overview')) return response(200, OVERVIEW);
      if (url.startsWith('/api/results/conflicts')) return response(200, CONFLICTS);
      if (url.startsWith('/api/ui/transfer/')) return response(200, { mode: 'v2', rows: [
        { key: 'delivered', label: 'Last delivered', value: '4 s ago', age_s: 4, glyph: 'final' }] });
      return response(200, {});
    }
    return write(method, url, body);
  });
}

// ── 1. every save checks r.ok ───────────────────────────────────────────────

const REFUSALS = [
  ['a 409 with its sentence', () => response(409, { error: 'It was already decided: keep by dana at 09:12.' })],
  ['a 503', () => response(503, { error: "LEM's store did not record the decision" })],
  ['an HTML error page', () => response(502, null, { html: true })],
  ['a dropped connection', () => 'drop'],
  ['a 401 (signed out meanwhile)', () => response(401, { error: 'Sign in to do that.' })],
];

const WRITES = [
  ['decide (Keep)', (ctl) => ctl.conflicts.decide('ep-1:2', 'keep', '151.6')],
  ['decide (Send)', (ctl) => ctl.conflicts.decide('ep-1:2', 'send', '151.6')],
  ['approve enrolment', (ctl) => ctl.settings.approve('n')],
  ['reset a token', (ctl) => ctl.settings.reset('a')],
  ['retire an instrument', (ctl) => ctl.settings.retire('e')],
  ['the bridge switch', (ctl) => ctl.settings.bridge(false)],
  ['import a journal folder', (ctl) => ctl.settings.journal(undefined, false)],
  ['record a dedupe decision', (ctl) => ctl.settings.dedupeApprove({ machine_uid: 'a', unit: 'replay_duplicate', run_id: 'r', password: 'x' })],
  ['apply a dedupe decision', (ctl) => ctl.settings.dedupeApply(3)],
];

function controllers(fetch) {
  const t = timers();
  const renders = [];
  const render = (x) => renders.push(x);
  return {
    t, renders,
    conflicts: T.createConflicts({ fetch, timers: t, render }),
    settings: T.createSettingsTransfer({ fetch, timers: t, render }),
  };
}

for (const [wname, act] of WRITES) {
  for (const [rname, answer] of REFUSALS) {
    const s = lab(() => answer());
    const res = await act(controllers(s.fetch));
    claim(`${wname}: ${rname} is not ok`, res.ok === false, JSON.stringify(res));
    claim(`${wname}: ${rname} says something`, typeof res.text === 'string' && res.text.length > 10, res.text);
    if (rname.startsWith('a 409')) {
      claim(`${wname}: the 409's own words reach the person`, /already decided/.test(res.text), res.text);
    }
    if (rname.startsWith('a dropped')) {
      claim(`${wname}: a dropped connection says LEM did not answer`, /did not answer/.test(res.text), res.text);
    }
  }
  const ok = lab(() => response(200, { ok: true }));
  const res = await act(controllers(ok.fetch));
  claim(`${wname}: a 200 is ok`, res.ok === true, JSON.stringify(res));
  const writes = ok.calls.filter(c => c.method !== 'GET');
  check(`${wname}: exactly one write`, writes.length, 1);
}

// A decision is drawn as decided only from the server's own re-read
{
  const s = lab(() => response(200, { ok: true }));
  const c = controllers(s.fetch);
  const res = await c.conflicts.decide('ep-1:2', 'keep', '151.6');
  check('after Keep: the list is re-read (POST then GET)',
        s.calls.map(x => x.method), ['POST', 'GET']);
  claim('after Keep: the toast says the bench is told, not that it is filed', /bench is told/.test(res.text), res.text);
}
// a refused decision does not re-read as if it had landed
{
  const s = lab(() => response(409, { error: 'LabCore\'s value is 151.7 now, not the 151.6 this page showed.' }));
  const c = controllers(s.fetch);
  await c.conflicts.decide('ep-1:2', 'send', '151.6');
  check('after a refusal: no re-read paints it decided', s.calls.map(x => x.method), ['POST']);
}
// the decision carries what the person saw, so a moved cell is refused
{
  const s = lab(() => response(200, { ok: true }));
  await controllers(s.fetch).conflicts.decide('ep-1:2', 'send', '151.6');
  check('the decision names the value the person saw',
        JSON.parse(s.calls[0].body), { ref: 'ep-1:2', choice: 'send', seen: '151.6' });
}
// a choice that is neither is refused before any request
{
  const s = lab(() => response(200, { ok: true }));
  const res = await controllers(s.fetch).conflicts.decide('ep-1:2', 'both');
  check('neither Keep nor Send: no request', s.calls.length, 0);
  check('neither Keep nor Send: not ok', res.ok, false);
}

// ── 2. no POST on a timer ───────────────────────────────────────────────────

{
  const s = lab(() => response(200, { ok: true }));
  const t = timers();
  const section = T.createSection({ uid: 'a', fetch: s.fetch, timers: t, render: () => {}, now: () => 0 });
  const conflicts = T.createConflicts({ fetch: s.fetch, timers: t, render: () => {} });
  const settings = T.createSettingsTransfer({ fetch: s.fetch, timers: t, render: () => {} });
  section.start(); conflicts.start(); settings.start();
  await t.run(10 * 60 * 1000);
  const methods = [...new Set(s.calls.map(c => c.method))];
  check('ten minutes open on all three pages: only GETs', methods, ['GET']);
  check('the section polls every 15 s', s.calls.filter(c => c.url.startsWith('/api/ui/transfer/')).length, 40);
  check('the conflicts page polls every 15 s', s.calls.filter(c => c.url.startsWith('/api/results/conflicts')).length, 40);
  check('Settings polls every 30 s', s.calls.filter(c => c.url.startsWith('/api/transfer/overview')).length, 20);
  section.stop(); conflicts.stop(); settings.stop();
  check('stop clears every timer', t.count(), 0);
}
{
  // a hidden tab costs nothing
  const s = lab(() => response(200, { ok: true }));
  const t = timers();
  const c = T.createConflicts({ fetch: s.fetch, timers: t, render: () => {}, isVisible: () => false });
  c.start();
  await t.run(5 * 60 * 1000);
  check('a hidden tab asks nothing', s.calls.length, 0);
}
{
  let threw = false;
  try { T.createPoller({ method: 'POST', timers: timers(), every: 1000, run: () => {} }); } catch (_e) { threw = true; }
  claim('a poller refuses to be built for a write', threw);
}
// the page scripts do their timed work only through LEMTransfer's pollers:
// a setInterval in them may repaint, never fetch
for (const f of ['transfer_section.js', 'conflicts.js', 'settings_transfer.js']) {
  const code = fs.readFileSync(new URL('../../static/js/' + f, import.meta.url), 'utf8');
  const timed = [...code.matchAll(/setInterval\(([\s\S]*?)\n\s*\}?,?\s*\d+\)/g)].map(m => m[1]);
  claim(`${f}: no fetch inside a setInterval`, timed.every(b => !/fetch|\.decide|\.reset|\.approve|\.retire|\.bridge|\.journal|dedupe/.test(b)),
        timed.join(' | '));
  claim(`${f}: never calls fetch directly (every request goes through LEMTransfer.send)`,
        !/[^.]\bfetch\(/.test(code.replace(/window\.fetch\.bind\(window\)/g, '')));
}

// ── 3. a failed read is never an empty result ───────────────────────────────

{
  let fail = false;
  const s = server((m, url) => fail ? response(503, { error: 'database is locked' }) : response(200, CONFLICTS));
  const t = timers();
  const renders = [];
  const c = T.createConflicts({ fetch: s.fetch, timers: t, render: (x) => renders.push(x) });
  await c.load();
  fail = true;
  await c.load();
  const last = renders[renders.length - 1];
  claim('a failed refresh keeps the list it had', last.view && last.view.open.length === 1);
  claim('and says it is not current', /database is locked/.test(last.error), last.error);
  check('the pill never says "Nothing waiting" for an unread list', T.conflictPill(null).text, 'Could not be read');
}

// ── 4. the words ────────────────────────────────────────────────────────────

check('ago 4 s', T.ago(4), '4 s');
check('ago 12 min', T.ago(725), '12 min');
check('ago unknown is null, never "0 s"', T.ago(null), null);
{
  const rows = T.sectionRows({ rows: [{ key: 'delivered', label: 'Last delivered', value: '4 s ago', age_s: 100, glyph: 'final' }] }, 30);
  check('Last delivered ages on screen', rows[0].value, '2 min ago');
  check('and turns held past 2 min', rows[0].glyph, 'held');
}
{
  const [a, e, n] = OVERVIEW.benches.map(b => T.benchRow(b, 0));
  check('a v2 bench: Reset… is its one action', a.action, { kind: 'reset', label: 'Reset…' });
  check('an older bench that stopped: Retire…', e.action, { kind: 'retire', label: 'Retire…' });
  check('an older bench waits on nothing LEM can count: a dash, never 0', e.waiting, '—');
  check('a bench asking to enrol: Approve', n.action, { kind: 'approve', label: 'Approve enrolment' });
  check('148 waiting is said', n.waiting, '148');
  check('12 min since it reached LEM is held', n.delivered, { text: '12 min ago', glyph: 'held' });
}
{
  const v = T.bridgeView(OVERVIEW);
  check('the bridge with a reason outstanding offers no switch', v.label, null);
  check('and lists the reason', v.reasons, ['1 bench still runs the older module (3.9): Eravap.']);
  check('no bridge on this server', T.bridgeView({ bridge: { on: null } }).state, 'No bridge on this server');
}
{
  const p = T.journalPreview({ benches: [{ machine_uid: 'a', title: 'Agilent GC 1', epoch: 'ep-9', new: 5, held: 2, damaged: 1, note: '' },
                                         { machine_uid: 'x', epoch: 'ep-1', new: 3, held: 0, damaged: 0, note: 'LEM has no instrument x; nothing imported' }] });
  check('the import button counts only what can land', p.label, 'Import 5 records');
  check('a damaged line is said', p.lines[0].text, '5 new · 2 already in LEM · 1 damaged line skipped');
  check('nothing new: no button', T.journalPreview({ benches: [{ machine_uid: 'a', new: 0, held: 5, damaged: 0 }] }).label, null);
}
check('the foot line from /api/ui/live', T.footLine({ text: 'Data · 2 of 17 reporting', glyph: 'held', href: '/settings#transfer',
                                                       full: 'Data: 2 of 17 benches reporting, 0 readings waiting at the benches.' }),
      { text: 'Data · 2 of 17 reporting', full: 'Data: 2 of 17 benches reporting, 0 readings waiting at the benches.',
        glyph: 'held', href: '/settings#transfer' });
{
  const g = T.benchGroups(OVERVIEW);
  check('module-4 benches go in the table', g.v4.map(b => b.machine_uid), ['a', 'n']);
  check('a stopped bench on the older module is listed to retire', g.stopped.map(b => b.machine_uid), ['e']);
  check('nine running older benches are one line, not nine rows',
        T.names(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I'].map(t => ({ title: t }))), 'A, B, C and 6 more');
}
check('no line when the server has none', T.footLine(null), null);

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall passed');
