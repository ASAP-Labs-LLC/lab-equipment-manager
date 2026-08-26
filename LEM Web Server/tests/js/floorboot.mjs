/* Actually run the floor's script, and require it to reach the end.
 *
 * The failure this guards against is specific and was real: replacing the SVG
 * renderer with the 3D world removed a block of the page, and `esc()` and
 * `col()` had been declared inside it while being called from code that
 * stayed. The markup was perfect, the page served 200, and the script threw on
 * its first call.
 *
 * A top-level throw is not a partial failure. Everything registered after it
 * never runs — so sign-in, lab hours, the debug panel, the map lock and the
 * poll loop were all dead while the page looked completely normal.
 *
 * A static check cannot catch this. The first attempt at one passed happily
 * with `col` deleted, because `col` is *also* declared as a local inside
 * another function, and a scope-blind scan sees a declaration and is satisfied.
 * So this does the only thing that is actually conclusive: it executes the
 * script against a stub DOM and lets the engine decide. A missing global is a
 * ReferenceError, which is exactly what the browser would have said.
 */
import fs from 'fs';
import vm from 'vm';

const html = fs.readFileSync(
  new URL('../../templates/floor.html', import.meta.url), 'utf8');

/* The page has one classic script; the module that boots the world has its own
 * scope and its own imports, so it is not run here. */
const scripts = [...html.matchAll(/<script(?![^>]*\btype=)[^>]*>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).filter(s => s.length > 400);
if (!scripts.length) {
  console.log('FAIL: no inline classic script found in floor.html');
  process.exit(1);
}
const src = scripts.sort((a, b) => b.length - a.length)[0];

/* ---- a DOM that says yes to everything ---------------------------------
 * The point is not to simulate a browser. It is to let every statement in the
 * script execute so the engine can report anything genuinely undefined. So the
 * stub element answers any property with something harmless and chainable. */
const touched = new Set();

function makeElement(name = 'stub') {
  const attrs = {};
  const el = {
    tagName: name.toUpperCase(),
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    appendChild(c) { return c; }, removeChild(c) { return c; },
    insertBefore(c) { return c; }, remove() {}, focus() {}, blur() {}, click() {},
    /* Attributes are RECORDED, not swallowed. `setView()` shows and hides the
     * plan through the hidden ATTRIBUTE (an SVGElement has no `.hidden`
     * property — see the note beside it in floor.html), so a stub that drops
     * them cannot tell a shown plan from a hidden one. */
    attrs,
    setAttribute(k, v) { attrs[k] = String(v); },
    removeAttribute(k) { delete attrs[k]; },
    getAttribute(k) { return k in attrs ? attrs[k] : ''; },
    showModal() {}, close() {}, scrollIntoView() {}, select() {},
    getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600,
                                   right: 800, bottom: 600, x: 0, y: 0}),
    /* SVG geometry. Without this the proxy answers `getBBox` with a function
       returning an element, `bb.width > 1` is false, and `drawSimpleFloor`
       takes its no-geometry FALLBACK — so the branch that actually fits the
       drawing to the stage was never executed by this harness at all. A fixed
       box is enough: the fit is arithmetic on whatever it is handed. */
    getBBox: () => ({x: -600, y: -60, width: 1200, height: 610}),
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
    style: {}, dataset: {}, children: [], childNodes: [],
    innerHTML: '', textContent: '', value: '', checked: false, hidden: false,
    disabled: false, open: false, title: '', href: '',
    closest: () => null, querySelector: () => makeElement(),
    querySelectorAll: () => [],
    parentNode: null, files: [],
  };
  /* Anything the page reaches for that is not listed above still has to answer —
   * a missing DOM property must not be mistaken for a missing global. */
  return new Proxy(el, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (typeof prop === 'symbol') return undefined;
      touched.add(String(prop));
      return () => makeElement();
    },
    set(target, prop, value) { target[prop] = value; return true; },
  });
}

/* The same selector must give back the SAME element, every time.
 *
 * A stub that mints a fresh object per query can prove the script RUNS but
 * never what it DID: `$('#world').hidden = true` was written to an object
 * thrown away on the next line. Caching by selector is both closer to a real
 * document and the only way to ask afterwards what state the page settled in. */
const els = new Map();
const el = sel => {
  if (!els.has(sel)) els.set(sel, makeElement());
  return els.get(sel);
};
/* The plan starts hidden in the markup. Seed it, so "the plan is showing" is a
 * claim about something that had to be removed rather than about something
 * that was never there. */
el('#floorSimple').setAttribute('hidden', '');

const documentStub = {
  querySelector: el,
  querySelectorAll: () => [],
  getElementById: id => el('#' + id),
  createElement: name => makeElement(name),
  createElementNS: (ns, name) => makeElement(name),
  addEventListener() {}, removeEventListener() {},
  documentElement: makeElement('html'),
  body: makeElement('body'),
  head: makeElement('head'),
  hidden: false, visibilityState: 'visible',
};

const noop = () => {};
const timer = () => 0;

/* fetch resolves to something shaped like every response this page reads, so
 * the async tails run rather than rejecting into the void. */
const response = () => Promise.resolve({
  ok: true, status: 200,
  /* `authenticated: true` so the signed-in paths are the ones exercised.
   * Every write on this page is gated on `requireAuth()`, and a harness that
   * is permanently signed out can only ever prove the sign-in dialog opens. */
  json: () => Promise.resolve({machines: [], samples: [], events: [],
                               authenticated: true, user: 'ryan',
                               locked: true, days: [],
                               tasks: [], series: [], holidays: {}}),
  text: () => Promise.resolve(''),
});

const sandbox = {
  /* `window` is the sandbox itself, so window-level listeners land here. */
  addEventListener: noop, removeEventListener: noop, dispatchEvent: noop,
  matchMedia: () => ({matches: false, addEventListener: noop,
                      addListener: noop}),
  getComputedStyle: () => ({getPropertyValue: () => ''}),
  scrollTo: noop, open: noop, close: noop, print: noop,
  innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1,
  console: {log: noop, warn: noop, error: noop, info: noop, debug: noop},
  document: documentStub,
  navigator: {userAgent: 'node', clipboard: {writeText: noop}},
  location: {search: '', href: 'http://localhost/floor', pathname: '/floor',
             hash: '', reload: noop, assign: noop},
  history: {pushState: noop, replaceState: noop},
  localStorage: {getItem: () => null, setItem: noop, removeItem: noop},
  sessionStorage: {getItem: () => null, setItem: noop, removeItem: noop},
  fetch: response,
  setTimeout: timer, setInterval: timer,
  clearTimeout: noop, clearInterval: noop,
  requestAnimationFrame: timer, cancelAnimationFrame: noop,
  performance: {now: () => 0, getEntriesByType: () => []},
  alert: noop, confirm: () => true, prompt: () => null,
  /* lem.js's global. `bust` is here because the page drops the cached floor
   * after every write that changes it — a stub missing it turns a successful
   * save into a TypeError, which is the harness lying about the page. */
  LEM: {get: () => Promise.resolve(null), fresh: () => Promise.resolve(null),
        prefetch: noop, live: noop, bust: noop},
  URLSearchParams, URL, JSON, Math, Date, Set, Map, WeakMap, Promise, Error,
  TypeError, RegExp, Intl, Array, Object, String, Number, Boolean, Symbol,
  parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent,
  structuredClone: v => v, queueMicrotask: noop,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

const context = vm.createContext(sandbox);

let failed = false;
const report = (what, err) => {
  failed = true;
  console.log(`FAIL: ${what}`);
  console.log('  ' + String(err && (err.stack || err)).split('\n')
    .slice(0, 3).join('\n  ') + '\n');
};

/* `load()` runs at the bottom of the page and everything it does happens after
 * an await, so a throw in there surfaces as a rejected promise rather than a
 * thrown error. Swallowing those would let exactly the bug this test exists
 * for slip past. */
process.on('unhandledRejection', err =>
  report('a promise rejected during load — the floor would paint nothing', err));

try {
  new vm.Script(src, {filename: 'floor.html <script>'}).runInContext(context);
  console.log('  ok   the floor script runs to completion');
} catch (err) {
  report('the floor script threw while loading — every listener registered '
         + 'after this point would be dead', err);
}

/* The bridge the world module talks to has to survive the load, or the map
 * silently never attaches. */
if (!failed) {
  const bridge = sandbox.window.__floorBridge;
  const REQUIRED = ['attach', 'onSelect', 'onContext', 'onHover', 'onMove',
                    'canDrag'];
  if (!bridge) {
    failed = true;
    console.log('FAIL: window.__floorBridge was never defined — the 3D world '
                + 'has nothing to attach to');
  } else {
    for (const name of REQUIRED) {
      if (typeof bridge[name] !== 'function') {
        failed = true;
        console.log(`FAIL: __floorBridge.${name} is missing`);
      }
    }
    if (!failed) {
      console.log(`  ok   __floorBridge exposes all ${REQUIRED.length} hooks `
                  + 'the world needs');
    }
  }
}

/* ---- and now actually draw something ------------------------------------
 *
 * Booting is not enough. `esc()` and `col()` are only reached from the render
 * functions, and those are callbacks — registered at load, invoked later. The
 * first version of this test passed happily with both deleted for exactly that
 * reason. So put a representative instrument through every render path the
 * floor takes and require none of them to throw.
 *
 * Only `function` declarations become properties of the vm context (a top-level
 * `const` stays in the script's own lexical scope), which is why these are the
 * ones reachable — and it is enough, because every one of them writes markup
 * built from esc() and col(). */
const machine = {
  machine_uid: 'optimpp-1', title: 'OptiMPP 1', status: 'RED',
  reason: 'Cloud Point -9.8 C — outside 63.7 ± 2.10.',
  status_color: '#f85b5b', updated_at: '2026-08-06 17:20:00',
  last_activity: '2026-08-06 17:20:00', last_poll: '2026-08-06 17:20:00',
  watching: 'C:/LabData/optimpp-1', module_running: true,
  module_state: 'running', closed_reason: '', maintenance_due: 1,
  sub_statuses: {qc: 'RED', pm: 'GREEN', calibration: 'YELLOW'},
  /* EXACTLY the columns snapshot_service's `spec` arm selects out of
   * lem_qc_specs (snapshot_service.py:113) — machine_uid, test_name,
   * sample_id, expected, std_dev, k, units. No `low`, no `high`.
   *
   * This fixture used to carry `low: -16, high: -12` here. Nothing in the
   * server has ever emitted those on qc_specs: the band lives on
   * effective_specs, below, because it is computed by the MODULE as
   * expected ± k·std_dev and published to lem_machine_specs. An invented
   * field on a fixture is not a harmless extra — it is the only reason
   * showTip's `Number(spec.low)` looked fine here while every real
   * instrument rendered NaN…NaN on the floor. */
  qc_specs: [{test_name: 'Cloud Point', sample_id: 'STD-1', expected: -14,
              std_dev: 1, k: 2, units: 'C'}],
  effective_specs: [{test_name: 'Cloud Point', low: -16, high: -12,
                     expected: -14, units: 'C', sample_id: 'STD-1',
                     last_qc_value: -9.8, correction: 0,
                     last_qc_at: '2026-08-06 15:00:00', last_qc_in_spec: false}],
  qc_targets: [{sample: 'Diesel - AO25', test: 'Cloud Point'}],
  maintenance: [{uid: 'm1', name: 'Monthly PM', kind: 'pm', status: 'RED',
                 due: '2026-08-01', last_done: '2026-07-01'}],
  pos: [4.1, 0],
};

const PATHS = [
  ['showTip', [machine, {x: 400, y: 300}]],
  ['hideTip', []],
  ['diagnosis', [machine]],
  ['qcChecksHtml', [machine]],
  ['moduleStateText', [machine]],
  ['panelSignature', [machine]],
  ['lastQcAt', [machine]],
  ['silentFor', [machine]],
  ['ago', ['2026-08-06 15:00:00']],
  ['feedSignature', [[{machine_uid: 'optimpp-1', ts: '2026-08-06 17:20:00',
                       kind: 'run', lab_id: 'L-3001'}]]],
  ['renderTally', []],
  ['renderOverview', []],
  ['drawFloor', []],
  ['spawnBlip', ['optimpp-1', 'L-3001']],
  ['paintTools', []],
  ['paintDebug', []],
  ['simOn', []],
  ['applySim', [[machine]]],
];

/* These two are the contract with the 3D world, and both are called from
 * places that would fail silently: `drawFloor()` from eighteen sites meaning
 * "the floor changed", `spawnBlip()` from the run poller meaning "a print was
 * parsed". Rename either and the map simply stops responding, with no error
 * anywhere. Everything else in PATHS is allowed to have been refactored away. */
const REQUIRED_FNS = ['drawFloor', 'spawnBlip'];

if (!failed) {
  for (const name of REQUIRED_FNS) {
    if (typeof sandbox[name] !== 'function') {
      failed = true;
      console.log(`FAIL: ${name}() is gone — the page still expects it, and `
                  + 'nothing would report its absence at runtime');
    }
  }
}

if (!failed) {
  let exercised = 0;
  for (const [name, args] of PATHS) {
    const fn = sandbox[name];
    if (typeof fn !== 'function') continue;   // refactored away: not a fault
    try {
      fn(...args);
      exercised++;
    } catch (err) {
      report(`${name}() threw — the floor cannot paint a piece of equipment`,
             err);
    }
  }
  if (!failed) {
    console.log(`  ok   ${exercised} render paths run against a live-shaped `
                + 'piece of equipment');
  }
}

/* ---- and now RUN the reader every write on this page goes through --------
 *
 * `failure(r)` is what turns a 503 or a 502 into words on the dialog. Every
 * `await failure(...)` call site is correct only if this function is, and until
 * now the only thing testing it was a Python test grepping the template for
 * the string "if (r.ok) return null;". That test would pass with the body
 * replaced by `return null` — i.e. with the whole point of the branch gutted —
 * as long as the line still appeared somewhere in the file.
 *
 * So it is executed here, against response objects shaped like the ones the
 * routes actually send. The engine decides, the way the browser would.
 */
if (!failed) {
  const fn = sandbox.failure;
  if (typeof fn !== 'function') {
    failed = true;
    console.log('FAIL: failure() is gone — every write on the floor would '
                + 'close its dialog on "Saved" regardless of the answer');
  } else {
    const res = (ok, status, body) => ({
      ok, status, json: () => Promise.resolve(body),
    });
    const cases = [
      ['a 200 is not a failure', res(true, 200, {ok: true}), v => v === null],
      /* what _labcore_failed sends for a refused write */
      ['a 502 carries the route\'s own words',
       res(false, 502, {error: 'The QC band was NOT saved. LabCore is busy.',
                        saved: false, retry: true}),
       v => typeof v === 'string' && v.includes('NOT saved')],
      /* what _labcore_unreadable sends */
      ['a 503 is a failure too',
       res(false, 503, {error: 'That checklist could not be read.'}),
       v => typeof v === 'string' && v.includes('could not be read')],
      /* a proxy or a dev-server error page: not JSON at all */
      ['a failure with an unreadable body still fails',
       {ok: false, status: 500, json: () => Promise.reject(new Error('html'))},
       v => typeof v === 'string' && v.length > 0],
    ];
    for (const [what, response, ok] of cases) {
      let got;
      try {
        got = await fn(response);
      } catch (err) {
        report(`failure() threw on ${what}`, err);
        continue;
      }
      if (!ok(got)) {
        failed = true;
        console.log(`FAIL: ${what} — failure() answered ${JSON.stringify(got)}`);
      }
    }
    if (!failed) {
      console.log(`  ok   failure() judges all ${cases.length} answer shapes`);
    }
  }
}

/* ---- the 3D site is severed; the SVG plan is the floor -----------------
 *
 * Ryan, 2026-08-24: "just dont have it render trains in 3d okay? We are going
 * to focus on the SVG rendering."
 *
 * The switch is `SITE_VIEW` at the top of the page. What makes this worth a
 * test rather than a comment is the boot order: the remembered view is applied
 * inside `__floorBridge.attach(world)`, and with the world severed NOTHING EVER
 * CALLS ATTACH. Miss that and the page is exactly as broken as a black canvas —
 * `VIEW` stays 'site', the plan keeps the `hidden` it was served with, and the
 * floor is a blank stage with no error anywhere to say why.
 *
 * So this asks the settled page what it is showing, rather than reading the
 * source and believing it. */
if (!failed) {
  const bridge = sandbox.window.__floorBridge;
  const claim = (what, ok) => {
    if (ok) { console.log(`  ok   ${what}`); return; }
    failed = true;
    console.log(`FAIL: ${what}`);
  };

  claim('the site view is severed (__floorBridge.siteView is false)',
        bridge && bridge.siteView === false);
  claim('the plan is showing — #floorSimple lost its hidden attribute',
        !('hidden' in el('#floorSimple').attrs));
  claim('the 3D canvas is hidden', el('#world').hidden === true);

  /* Controls with nothing behind them. Every one of these reaches for `WORLD`,
   * which never arrives: View toggles between two views when there is only
   * one, Quality tunes a renderer that is not running, and Arrange's whole-floor
   * buttons return early on `!WORLD` — a button that silently does nothing is
   * worse than one that is not there. */
  for (const sel of ['#btnView', '#btnQuality', '#btnArrange']) {
    claim(`${sel} is hidden while the world is severed`,
          el(sel).hidden === true);
  }
}

/* ---- levels: the ladder, the picker, and one level at a time -----------
 *
 * Ryan: "I want vertical layers like levels if you will. These 'Levels' can be
 * renamed, in the UI you can cycle through them."
 *
 * Everything below RUNS the shipped functions. The two claims that matter most
 * cannot be read off the source at all:
 *
 *   - the plan draws ONE level, and which one the page settles on;
 *   - switching level costs NO REQUEST. The floor repaints every two seconds
 *     from every screen in the lab; a fetch hidden inside a level switch is a
 *     LabCore read behind a gesture people make all day, and the only honest
 *     way to test for it is to count fetches across the switch.
 */
const claim = (what, ok) => {
  if (ok) { console.log(`  ok   ${what}`); return true; }
  failed = true;
  console.log(`FAIL: ${what}`);
  return false;
};

/* The wire contract shows up INSIDE rendered markup — `data-pick="gc-1"` is
 * fine, but `/api/machines` in an href and `machine_uid` in a data attribute
 * are not prose. Take them out, and judge what a person could actually read. */
const prose = html => String(html || '')
  /* HTML comments ride along in `innerHTML` and nobody reads them on screen —
   * the same thing `visible_text()` strips in the Python half of this check. */
  .replace(/<!--[\s\S]*?-->/g, ' ')
  .replace(/machine_uid/g, '')
  .replace(/\/api\/machines/g, '');
const OLD_NOUNS = /\bmachines?\b|\binstruments?\b/i;
const saysOldNoun = html => OLD_NOUNS.exec(prose(html));

/* "Equipment" is UNCOUNTABLE. "4 equipments" and "4 equipment" are both wrong,
 * and "8 equipment in the lab across 3 levels" on a floor plan reads as a
 * broken translation rather than as a lab — so a number may never sit against
 * it. This is the check that catches a mechanical find-and-replace. */
const MISCOUNTED = /\b\d+\s+equipments?\b|\bequipments\b/i;
const miscounts = html => MISCOUNTED.exec(String(html || ''));

const readsWell = (what, html) => {
  const old = saysOldNoun(html);
  if (old) {
    failed = true;
    console.log(`FAIL: ${what} still says "${old[0]}" where a person reads it`);
    return false;
  }
  const bad = miscounts(html);
  if (bad) {
    failed = true;
    console.log(`FAIL: ${what} reads ungrammatically — "${bad[0]}"`);
    return false;
  }
  console.log(`  ok   ${what} reads in the one noun, and counts grammatically`);
  return true;
};

/* Rank deliberately out of array order: the page must sort the ladder itself,
 * because nothing in LabCore guarantees the rows arrive in any order and a
 * floor that reorders on every poll is the bug this repo has already fixed
 * once. */
const LADDER = [
  {uid: 'l2', name: 'Mezzanine', rank: 2},
  {uid: 'l1', name: 'Ground', rank: 1},
  {uid: 'l3', name: 'Roof', rank: 3},
];

if (!failed) {
  const sorted = sandbox.sortedLevels;
  const resolve = sandbox.resolveLevelView;
  const cycle = sandbox.cycleLevel;
  if (typeof sorted !== 'function' || typeof resolve !== 'function'
      || typeof cycle !== 'function') {
    failed = true;
    console.log('FAIL: the level functions are gone — sortedLevels / '
                + 'resolveLevelView / cycleLevel');
  } else {
    claim('the ladder sorts by rank, not by the order the rows arrived',
          sorted(LADDER).map(l => l.uid).join() === 'l1,l2,l3');
    claim('the screen\'s own stored level wins',
          resolve(LADDER, 'l3', 'l1') === 'l3');
    claim('a stored level that has been deleted falls back to the default',
          resolve(LADDER, 'gone', 'l2') === 'l2');
    claim('a deleted default falls back to the ground, never to nothing',
          resolve(LADDER, 'gone', 'also-gone') === 'l1');
    claim('a lab with no levels resolves to "" — flat, which is a real state',
          resolve([], 'l1', 'l1') === '');
    /* WRAPS. levels.cycle() on the server does the same and says why: this is
     * the viewer's gesture. MOVING equipment clamps instead, because a wrap
     * there looks exactly like the instrument falling into the basement. */
    claim('cycling up off the top wraps to the ground',
          cycle('l3', LADDER, 1) === 'l1');
    claim('cycling down off the bottom wraps to the top',
          cycle('l1', LADDER, -1) === 'l3');
    claim('cycling from a level that is gone lands on the ladder',
          cycle('gone', LADDER, 1) === 'l1');
    claim('cycling a flat lab answers ""', cycle('', [], 1) === '');
  }
}

/* ---- and now drive the real load path with a real payload -------------- */

const onLevel = (uid, over) => Object.assign({}, machine, {
  machine_uid: uid, title: uid.toUpperCase(), level_uid: '',
  level_moved_at: '', level_moved_by: '',
}, over || {});

const FLEET = {
  machines: [
    onLevel('gc-1', {title: 'GC 1', level_uid: 'l1',
                     level_moved_at: '2026-08-20T09:00:00',
                     level_moved_by: 'ryan'}),
    onLevel('gc-2', {title: 'GC 2', status: 'GREEN', level_uid: 'l2',
                     reason: 'All checks in spec.'}),
    /* No level_uid at all — a payload from a fallback read, or from before
     * levels shipped. It must stand on the GROUND and be drawn, not vanish. */
    onLevel('gc-3', {title: 'GC 3', status: 'YELLOW',
                     reason: 'No QC assigned.'}),
  ],
  levels: LADDER,
  default_level: 'l1',
  ground_level: 'l1',
  labcore_online: true,
  age_seconds: 3,
};

/* Everything the page can reach the network through, counted in one place.
 *
 * `fetch` is not enough: the floor's own poll goes through `LEM.get` /
 * `LEM.fresh`, so a level switch that quietly called `load()` would be
 * invisible to a counter that only wrapped `fetch` — which is exactly what a
 * first version of this harness missed. */
let NET = 0;
const PAYLOADS = {
  '/api/machines': null,
  '/api/qc-samples': {samples: []},
};
const rawFetch = sandbox.fetch;
sandbox.fetch = (...args) => { NET++; return rawFetch(...args); };
sandbox.LEM.get = url => { NET++; return Promise.resolve(PAYLOADS[url] || null); };
sandbox.LEM.fresh = sandbox.LEM.get;
/* Node's own timer, not the sandbox's stub: draining the microtask queue is
 * how an async `load()` fired inside a synchronous call becomes visible. */
const settle = () => new Promise(r => setTimeout(r, 0));

/* ---- the empty states, before there is a fleet to hide them ------------
 *
 * The floor refuses to blank a fleet it already has, so the "no instruments"
 * rail can only be drawn while MACHINES is still empty — which is why this
 * runs first. It is also the branch a rename escapes through: prose in a
 * branch nothing ever renders is prose nothing ever checks. */
if (!failed && typeof sandbox.load === 'function') {
  for (const online of [true, false]) {
    PAYLOADS['/api/machines'] = {machines: [], levels: [], default_level: '',
                                 ground_level: '', labcore_online: online,
                                 age_seconds: 1};
    try {
      await sandbox.load();
    } catch (err) {
      report('load() threw on an empty lab', err);
    }
    const rail = String(el('#railL').innerHTML || '');
    readsWell(`the ${online ? 'empty' : 'offline'} floor rail`, rail);
  }
}

PAYLOADS['/api/machines'] = FLEET;

if (!failed && typeof sandbox.load === 'function') {
  try {
    await sandbox.load();
  } catch (err) {
    report('load() threw with a real payload', err);
  }
}

if (!failed) {
  const vis = sandbox.visibleMachines;
  if (typeof vis !== 'function') {
    failed = true;
    console.log('FAIL: visibleMachines() is gone — the plan would draw every '
                + 'level at once and the level switcher would do nothing');
  } else {
    claim('the page opens on the floor-wide default level',
          el('#levelName').textContent === 'Ground');
    /* The picker's own label IS the current-level readout — one control, two
     * jobs, so no second caption can disagree with it. */
    claim('the picker button is labelled with the level, not "Level"',
          typeof sandbox.levelButtonLabel === 'function'
          && sandbox.levelButtonLabel() === 'Ground');
    claim('the plan draws ONE level, not the whole fleet — 2 of 3 here',
          FLEET.machines.length === 3 && vis().length === 2);
    claim('equipment with no placement stands on the ground and is drawn',
          vis().some(m => m.machine_uid === 'gc-3'));
    claim('equipment on another level is not drawn here',
          !vis().some(m => m.machine_uid === 'gc-2'));
    claim('a populated level is not the empty state',
          el('#levelEmpty').hidden === true);
    /* THE PICKER AND THE STEPPERS BOTH SHIP. `levels.cycle()`'s docstring:
       a stepper costs one press per level and stops working as a lab grows;
       the picker costs an open and a pick however tall the building. Read off
       the controls after a render, because a template grep for the ids passes
       with every one of them permanently hidden. */
    claim('the picker is offered on a lab that has levels',
          el('#btnLevel').hidden === false);
    claim('…and so are both steppers, enabled, on a ladder of three',
          el('#btnLevelUp').hidden === false
          && el('#btnLevelDown').hidden === false
          && el('#btnLevelUp').disabled === false
          && el('#btnLevelDown').disabled === false);
    claim('…and each stepper names where it goes',
          /Mezzanine/.test(String(el('#btnLevelUp').title))
          && /Roof/.test(String(el('#btnLevelDown').title)));
    claim('the picker says which level it is showing',
          /Ground/.test(String(el('#btnLevel').title)));
    claim('the plan carries the level name as its own watermark',
          el('#levelMark').textContent === 'Ground');
  }
}

/* THE zero-request claim. Counted across the switch, because a fetch hidden
 * inside `setLevelView` is invisible to any amount of reading. */
if (!failed && typeof sandbox.setLevelView === 'function') {
  const before = NET;
  sandbox.setLevelView('l2');
  sandbox.setLevelView('l3');
  /* Drained, or an async `load()` fired inside the switch lands after the
   * count is read and the whole claim passes on a page that refetches the
   * floor on every level change. */
  await settle();
  claim('switching level fires NO request — the level rides the payload the '
        + 'floor is already polling', NET === before);
  claim('the picker relabels itself to the level it switched to',
        el('#levelName').textContent === 'Roof');
  claim('the plan is now empty of equipment on Roof',
        sandbox.visibleMachines().length === 0);
  /* An empty level is a first-class state: a heading that names it, a sentence
   * of orientation, a sentence saying where everything else is, and one
   * primary action — never a blank canvas, and the chrome stays. */
  claim('an empty level shows its designed panel',
        el('#levelEmpty').hidden === false);
  claim('the panel names the level it is talking about',
        String(el('#levelEmptyHead').textContent).includes('Roof'));
  claim('the panel says where the equipment actually is',
        /Ground/.test(String(el('#levelEmptyWhere').textContent))
        && /Mezzanine/.test(String(el('#levelEmptyWhere').textContent)));
  claim('the level is still named on the plan itself, for a maximal-map wall '
        + 'display with no tool row', el('#levelMark').textContent === 'Roof');
  /* THE DESIGNED STATE, PART BY PART. A test that only greps five ids passes
     with every one of them left permanently empty and hidden — which is what
     the Python side used to do, and why this lives here now. */
  claim('…and the panel gives a sentence of orientation, not just a heading',
        String(el('#levelEmptyBody').textContent).length > 60);
  claim('…and one primary action, which is offered and not disabled',
        el('#levelEmptyMove').hidden === false
        && el('#levelEmptyMove').disabled === false);
  /* THE HINT IS INSTRUCTIONS. On an empty level "drag to rearrange ·
     right-click for actions" names two things that need a piece of equipment
     to do them, on a level that has none. */
  claim('the floor hint does not tell you to drag equipment that is not there',
        !/drag|right-click/.test(String(el('#hint').textContent)));
  claim('…and says what CAN be done instead',
        /move equipment here|switch level/i
          .test(String(el('#hint').textContent)));

  sandbox.setLevelView('l1');
  claim('going back to a populated level takes the empty panel away',
        el('#levelEmpty').hidden === true);
}

/* ---- the plan is FITTED TO THE STAGE, not to its own content -----------
 *
 * At yaw 45 the projected deck's aspect is exactly 1/sin(tilt) and does NOT
 * depend on how the bays are laid out — a 1x8 rank and a 3x3 block both come
 * out 2:1 at tilt 30. The stage is nothing like 2:1, so `meet` fitted the
 * drawing to the width and threw the rest of the height away: measured in a
 * browser before this, the plan used 53% of the stage at 1600px and 43% at
 * 1280px, at every viewport and on every level.
 *
 * So the tilt is SOLVED from the shape of the box the drawing is going into.
 * That is a pure function of the stage's rectangle, which is the part worth
 * pinning here — the pixels themselves were measured in Chromium. */
if (!failed && typeof sandbox.planFitTilt === 'function') {
  const stage = el('#stage');
  const real = stage.getBoundingClientRect;
  const box = (w, h) => { stage.getBoundingClientRect =
    () => ({left: 0, top: 0, width: w, height: h, right: w, bottom: h,
            x: 0, y: 0}); };
  const SPAN = 14;                        // bays across + down, a real floor

  box(1600, 420);
  const wide = sandbox.planFitTilt(SPAN);
  box(700, 1000);
  const tall = sandbox.planFitTilt(SPAN);
  claim('a wide short stage gets a shallow, side-on floor',
        wide < 35);
  claim('a tall narrow stage gets a steeper, more overhead one',
        tall > wide + 15);

  /* MONOTONIC IN BETWEEN, never a step between two presets. The tilt the
     solve WANTS is asin(1/A) for a stage of aspect A; the number it returns
     also folds in the plate overshoot measured off the last draw, which is
     not readable from here — so the shape of the answer is what is pinned
     here, and the arithmetic is checked end-to-end on the viewBox below. */
  box(1500, 1000);
  const mid = sandbox.planFitTilt(SPAN);
  claim('…and a stage in between gets an angle in between',
        mid > wide && mid < tall);
  box(1200, 1000);
  claim('…moving with the stage, not stepping between two presets',
        sandbox.planFitTilt(SPAN) > mid);

  /* CLAMPED AT BOTH ENDS. Past about 68 degrees the deck stops being a floor;
     below about 22 it reads as a line. Neither may be exceeded whatever
     ratio a window is dragged to. */
  box(4000, 200);
  const silly = sandbox.planFitTilt(SPAN);
  box(200, 4000);
  const sillier = sandbox.planFitTilt(SPAN);
  claim('an absurdly wide stage is clamped, not flattened to nothing',
        silly >= 22 && silly <= 68);
  claim('an absurdly tall one is clamped too',
        sillier >= 22 && sillier <= 68);
  claim('…and the clamp is what stopped it, not a coincidence',
        sillier > 60 && silly < 26);

  /* A stage that has not been laid out yet must not produce NaN — a NaN tilt
     is a viewBox of NaN and a blank floor with nothing saying why. */
  box(0, 0);
  claim('a stage with no size falls back to a real angle',
        Number.isFinite(sandbox.planFitTilt(SPAN)));
  claim('…and so does a floor with no extent',
        Number.isFinite(sandbox.planFitTilt(0)));
  stage.getBoundingClientRect = real;
}

/* The other half of the same fix, read off the attribute the browser reads.
 *
 * `preserveAspectRatio="xMidYMid meet"` fits the more demanding axis and
 * CENTRES the other — so a viewBox left the shape of its own content
 * letterboxes the difference away. Padded out to the stage's shape (never
 * cropped to it: the whole floor has to stay visible), there is nothing left
 * to letterbox. */
if (!failed && typeof sandbox.drawSimpleFloor === 'function') {
  const stage = el('#stage');
  const realRect = stage.getBoundingClientRect;
  const box = (w, h) => { stage.getBoundingClientRect =
    () => ({left: 0, top: 0, width: w, height: h, right: w, bottom: h,
            x: 0, y: 0}); };
  const svg = el('#floorSimple');
  const vb = () => String(svg.getAttribute('viewBox') || '')
    .trim().split(/\s+/).map(Number);

  for (const [w, h] of [[1400, 900], [900, 1200], [700, 700]]) {
    box(w, h);
    sandbox.PLAN_SIG = '';                 // force a rebuild at the new shape
    sandbox.drawSimpleFloor(false);
    const [, , vw, vh] = vb();
    claim(`the plan's viewBox is the shape of a ${w}x${h} stage`,
          Number.isFinite(vw) && Number.isFinite(vh) && vh > 0
          && Math.abs((vw / vh) - (w / h)) < 0.02);
  }

  /* AND IT NEVER CROPS. The box has to still contain the drawing it was
     measured from — padding out is the fix, cutting down is a floor with
     equipment off the edge of it. */
  box(1400, 900);
  sandbox.PLAN_SIG = '';
  sandbox.drawSimpleFloor(false);
  const [x, y, vw, vh] = vb();
  claim('…and still contains the whole drawing rather than cropping to fit',
        x <= -600 && y <= -60 && x + vw >= 600 && y + vh >= 550);
  stage.getBoundingClientRect = realRect;
}

/* ---- the padlock agrees with the word next to it ----------------------- */
if (!failed && typeof sandbox.lockGlyph === 'function') {
  const open = String(sandbox.lockGlyph(true));
  const shut = String(sandbox.lockGlyph(false));
  claim('the padlock is drawn, not an emoji whose shackle the font decides',
        open.includes('<svg') && shut.includes('<svg')
        && !/[\u{1F512}\u{1F513}]/u.test(open + shut));
  claim('…and the open one is a different drawing from the shut one',
        open !== shut);
  if (typeof sandbox.paintTools === 'function') {
    sandbox.paintTools();
    const label = String(el('#btnLock').innerHTML || '');
    claim('the lock button carries that drawing beside its word',
          label.includes('<svg') && /Locked|Unlocked/.test(label));
    claim('…and no emoji padlock at all',
          !/[\u{1F512}\u{1F513}]/u.test(label));
  }
}

/* ---- the ladder as a thing you can rebuild ----------------------------- */
if (!failed) {
  const consequence = sandbox.deleteConsequence;
  if (typeof consequence !== 'function') {
    failed = true;
    console.log('FAIL: deleteConsequence() is gone — deleting a level would '
                + 'not say what happens to the equipment standing on it');
  } else {
    const words = consequence('l2');
    /* Deleting a level that holds equipment MUST say what happens to it. The
     * store drops the placements and `levels.placements` stands everything
     * unplaced on the ground — true, and useless if it only lives in a
     * docstring. */
    claim('deleting a level says how much equipment stands on it',
          /1 piece/.test(words));
    claim('deleting a level says the equipment is not deleted',
          /not deleted/i.test(words));
    claim('deleting a level names where the equipment lands',
          /Ground/.test(words));
    claim('deleting an empty level says so instead',
          /Nothing stands on it/.test(consequence('l3')));
  }
  if (typeof sandbox.levelsListHtml === 'function') {
    const html = sandbox.levelsListHtml();
    claim('the levels dialog lists every level',
          html.includes('Ground') && html.includes('Mezzanine')
          && html.includes('Roof'));
    /* The default is a MARK ON THE ROW, not a hidden preference — and it is
     * the only level wearing it. */
    claim('the default level is marked on its row, exactly once',
          (html.match(/class="dflt"/g) || []).length === 1);
    /* Rename, make-default and delete are OFFERED ON EVERY ROW — and the row
       that is already the default does not offer to be made it again. */
    claim('every level row offers a rename and a delete',
          (html.match(/data-lvlrename/g) || []).length === 3
          && (html.match(/data-lvldelete/g) || []).length === 3);
    claim('…and make-default only on the levels that are not it',
          (html.match(/data-lvldefault/g) || []).length === 2);
    claim('every row says how much equipment stands on that level',
          (html.match(/piece(s)? of\s*\n?\s*equipment/g) || []).length === 3);
  }
  if (typeof sandbox.levelMenuHtml === 'function') {
    const menu = sandbox.levelMenuHtml();
    claim('the open list ticks the current level rather than relying on '
          + 'colour alone', menu.includes('✓'));
    claim('the open list carries a per-level roll-up — which level has RED '
          + 'equipment is why a lab switches level at all',
          menu.includes('lvlroll'));
    /* COLOUR IS NEVER THE ONLY CARRIER. The roll-up used to be a coloured dot
       and a number, on the row a lab reads to decide which level to walk to. */
    claim('every roll-up entry says its status in words, not only in colour',
          /<em>red<\/em>/.test(menu) && /<em>green<\/em>/.test(menu));
    claim('…and the row as a whole is announced with the same words',
          /aria-label="[^"]*\d+ red/.test(menu));
    claim('a level with nothing on it says so in a sentence',
          menu.includes('no equipment on this level'));
    claim('…and its row is announced that way too',
          /aria-label="[^"]*no equipment/.test(menu));
  }
}

/* ---- a lab with no levels at all --------------------------------------- */
if (!failed && typeof sandbox.applyLevels === 'function') {
  sandbox.applyLevels({levels: [], default_level: '', ground_level: ''});
  claim('a flat lab hides the picker — there is nothing to pick',
        el('#btnLevel').hidden === true);
  claim('…and hides the steppers with it',
        el('#btnLevelUp').hidden === true
        && el('#btnLevelDown').hidden === true);
  /* But "Levels…" stays, or a flat lab could never make its first level. */
  claim('…and keeps the way to create the first one',
        el('#btnLevels').hidden === false);
  claim('a flat lab draws the whole fleet rather than nothing',
        sandbox.visibleMachines().length === 3);
  // Put the ladder back for anything below.
  sandbox.applyLevels(FLEET);
  claim('the picker comes back when the lab has levels again',
        el('#btnLevel').hidden === false);
}

/* ---- documents: the limits are known BEFORE the upload fails ----------- */
if (!failed) {
  const problem = sandbox.docLimitProblem;
  const html = sandbox.documentsHtml;
  if (typeof problem !== 'function' || typeof html !== 'function') {
    failed = true;
    console.log('FAIL: the documents tab lost docLimitProblem() or '
                + 'documentsHtml()');
  } else {
    claim('a PDF under the ceiling is accepted',
          problem({name: 'cert.pdf', size: 900000,
                   type: 'application/pdf'}) === null);
    claim('a photo of a nameplate is accepted too',
          problem({name: 'plate.JPG', size: 400000, type: ''}) === null);
    const wrong = problem({name: 'dump.exe', size: 10, type: ''});
    claim('the wrong kind of file is refused by name, before it is sent',
          typeof wrong === 'string' && /PDF, PNG or JPEG/.test(wrong));
    const big = problem({name: 'scan.pdf', size: 40 * 1024 * 1024,
                         type: 'application/pdf'});
    claim('an oversized file is refused with its size and the limit',
          typeof big === 'string' && /40\.0 MB/.test(big)
          && /25 MB/.test(big));
    claim('an empty file is refused',
          /empty/.test(String(problem({name: 'x.pdf', size: 0,
                                       type: 'application/pdf'}))));
    claim('no file at all is refused rather than uploaded',
          typeof problem(null) === 'string');
    claim('an equipment record with no documents says so plainly',
          /No documents/.test(html([])));
    const listed = html([{uid: 'd1', filename: 'Prüfzertifikat.pdf',
                          size_bytes: 1536000, uploaded_at: '2026-08-20T10:00',
                          uploaded_by: 'ryan'}]);
    claim('a listed document offers a download and a delete',
          listed.includes('/api/equipment/documents/d1/download')
          && listed.includes('data-docdel="d1"'));
    claim('a listed document shows its size in words a person reads',
          listed.includes('1.5 MB'));
  }
}

/* ---- history: one ordered list, and honest about being cut short ------- */
if (!failed) {
  const tl = sandbox.timelineHtml, acts = sandbox.actionsHtml;
  if (typeof tl !== 'function' || typeof acts !== 'function') {
    failed = true;
    console.log('FAIL: the history tab lost timelineHtml() or actionsHtml()');
  } else {
    claim('an empty history says nothing has been recorded',
          /Nothing recorded/.test(tl({entries: []})));
    const body = {
      entries: [
        {at: '2026-08-20T10:00:00', uid: 'a1', source: 'corrective_action',
         kind: 'opened', machine_uid: 'gc-1', who: 'ryan',
         summary: 'Cloud Point QC failed twice', caused_by: '', detail: {}},
        {at: '2026-08-19T10:00:00', uid: 'c1', source: 'correction_factor',
         kind: 'changed', machine_uid: 'gc-1', who: 'ryan',
         summary: 'Cloud Point correction 0.00 → -3.00', caused_by: '',
         detail: {}},
      ],
      truncated: true, note: 'Showing the most recent 200 entries.',
      count: 2, limit: 200,
    };
    const drawn = tl(body);
    /* Corrective actions AND correction-factor changes in ONE ordered list —
     * the question is "what happened to this equipment", not "what happened in
     * each of six tables". */
    claim('corrective actions and correction-factor changes share one list',
          drawn.includes('Cloud Point QC failed twice')
          && drawn.includes('0.00 → -3.00'));
    /* `truncated` is a claim about completeness. A history that quietly stops
     * is read as "that is everything that happened", which about a compliance
     * record is a lie. */
    claim('a truncated history says it is truncated',
          drawn.includes('most recent 200'));
    claim('an action entry is clickable back to the action',
          drawn.includes('data-action="a1"'));
    claim('nothing open reads as nothing open',
          /Nothing open/.test(acts([])));
    const late = acts([{uid: 'a1', what_happened: 'QC failed', state: 'open',
                        priority: 'high', overdue: true, assigned_to: 'ryan',
                        due_at: '2026-08-01', opened_at: '2026-07-20T09:00'}]);
    claim('an overdue action wears an overdue badge',
          late.includes('badge over') && late.includes('overdue'));
    claim('an action shows who owns it and when it is due',
          late.includes('ryan') && late.includes('2026-08-01'));

    /* AND IT HAS TO FIT THE RAIL IT IS DRAWN IN. This is the card the review
       called the most important one on the History tab and unreadable: the
       sentence and three badges in one `.n` with the badges FLOATED RIGHT,
       then owner, due date and opened date in one no-wrap flex row under it.
       In a rail that is the identical collision the attention card above was
       rebuilt to fix — the sentence squeezed to min-content behind the float,
       and dates broken mid-token.

       Same shape as that fix, and checked the same way: the sentence on a
       full-width line of its own, the facts in rows that WRAP, and no row
       carrying both. */
    claim('the corrective-action card gives the sentence its own line',
          /<div class="why">\s*QC failed\s*<\/div>/.test(late));
    claim('…and does NOT float the badges into it',
          !/class="n"/.test(late) && !/class="val"/.test(late));
    const rows = late.match(/<div class="attnmeta">[\s\S]*?<\/div>/g) || [];
    claim('…and puts the facts in rows that are allowed to wrap',
          rows.length === 2);
    claim('…with the sentence in neither of them',
          !rows.some(r => /QC failed/.test(r)));
    claim('…and the badges in exactly one of them',
          rows.filter(r => /class="badge/.test(r)).length === 1);
    claim('…and the dates in the other',
          /2026-08-01/.test(rows[1]) && /2026-07-20/.test(rows[1]));
  }
}

/* ---- the rename, judged on what the page actually RENDERS --------------
 *
 * Ryan: "all terms that say 'machine' will change to equipment" — and,
 * 2026-08-25, confirmed for "instrument" too, which was this page's own older
 * word for the same thing. ONE NOUN ON SCREEN.
 *
 * DISPLAY ONLY — `machine_uid` is the wire contract every bench writes its
 * rows on, so it stays in code, in tables, in JSON keys and in every existing
 * route, and it is stripped below before the words are judged.
 *
 * Read off the rendered markup rather than grepped out of the template: a
 * template grep passes while the function that builds the words is gutted, and
 * this repo has caught that twice. */


/* The counter itself, before anything renders it. Every place on this page
 * that prints a number of them comes through here, so if this is right they
 * cannot drift apart, and if it is wrong every one of them is wrong at once. */
if (!failed && typeof sandbox.equipmentCount === 'function') {
  const n = sandbox.equipmentCount;
  claim('one of them is "1 piece of equipment"', n(1) === '1 piece of equipment');
  claim('four are "4 pieces of equipment", never "4 equipment"',
        n(4) === '4 pieces of equipment');
  claim('none is "0 pieces of equipment", not "0 piece"',
        n(0) === '0 pieces of equipment');
  claim('nothing it can be handed produces a number against the bare noun',
        [0, 1, 2, 7, 60].every(k => !MISCOUNTED.test(n(k))));
} else if (!failed) {
  failed = true;
  console.log('FAIL: equipmentCount() is gone — every count on this page went '
              + 'back to putting a number against an uncountable noun');
}

if (!failed && typeof sandbox.renderOverview === 'function') {
  sandbox.renderOverview();
  const rail = String(el('#railL').innerHTML || '');
  claim('the floor rail actually renders something to judge', rail.length > 40);
  readsWell('the floor rail', rail);
  claim('the rail names the level each flagged piece of equipment is on',
        /Ground|Mezzanine|Roof/.test(rail));
  /* NEWLY ARRIVED EQUIPMENT CAN BE PLACED. Equipment is not created on this
     page — it registers itself the first time its module polls — so "choose
     its level when it is created" lands as: the first time it appears,
     unplaced, the floor says so and offers to place it. `gc-3` in the fixture
     is exactly that piece. */
  claim('the rail says which equipment has never been placed on a level',
        rail.includes('Not placed on a level'));
  claim('…names it', /Gas Chromatograph 3|gc-3/.test(rail));
  claim('…and offers to place it', rail.includes('id="placeUnplaced"'));
  claim('the rail heads itself with a grammatical count',
        /\d+ pieces? of equipment/.test(rail));
}

/* ---- the record itself: four tabs, and the limits before the upload ----
 *
 * `select()` builds the whole equipment record, so this is the only place the
 * documents tab exists at all — it is not in the served markup, and a test
 * that grepped the template's <script> for "25 MB" would pass with the tab
 * deleted. Draw the record and read what it says. */
if (!failed && typeof sandbox.select === 'function') {
  try {
    await sandbox.select(FLEET.machines[0]);
  } catch (err) {
    report('select() threw building the equipment record', err);
  }
}

if (!failed) {
  const rail = String(el('#railL').innerHTML || '');
  claim('the record has a Documents tab and a History tab',
        rail.includes('data-tab="docs"') && rail.includes('data-tab="hist"'));
  claim('the SOP placeholder is gone', !rail.includes('data-tab="sop"'));
  /* The limits are printed BEFORE a file is chosen. The alternative is
   * uploading 40 MB over lab wifi to be told it was the wrong kind of file. */
  claim('the documents tab states its limits up front',
        rail.includes('PDF, PNG or JPEG') && rail.includes('25 MB'));
  claim('the history tab offers opening a corrective action',
        rail.includes('id="actNew"'));
  /* Every named piece of equipment carries its level. */
  claim('the open record names the level the equipment stands on',
        String(el('#panelLevel').innerHTML || '').includes('Ground'));
  claim('…and says who placed it there, which rides the same payload',
        /ryan/.test(String(el('#panelLevel').innerHTML || '')));
  readsWell('the equipment record', rail);

  /* ---- the record's own controls, as RENDERED controls ----------------
   *
   * All of this used to be greps in tests/test_equipment_ui.py: `'data-tab=
   * "docs"' in floor`, `'.pdf' in block`, `'/history?limit=' in floor`. Every
   * one of them passes with the tab left empty, the input left out of the DOM
   * or the fetch deleted — the file itself said so. Read off the record the
   * page draws. */
  claim('the file picker offers only what the server accepts',
        /accept="[^"]*\.pdf[^"]*\.png[^"]*\.jpe?g/.test(rail));
  /* A RAW <input type=file> IS THE ONE CONTROL NOBODY DESIGNED: every browser
     draws its own grey button in its own system font and none of them can be
     styled. The input is still the input — it is what the upload reads and
     what takes focus — driven by a real <label>. */
  claim('the file input is not shipped raw',
        rail.includes('class="filepick"')
        && /<label class="tool" for="docFile"/.test(rail));
  claim('…and says what has been chosen, before Upload is pressed',
        rail.includes('id="docChosen"') && rail.includes('No file chosen'));

  /* THE HISTORY TAB HAS FILTERS AND STATES ITS ORDER. The lab-wide /logs page
     has both; this asked the same question about one instrument with neither,
     so a long record was a scroll and the direction was a guess. */
  claim('the history tab offers a kind filter and a search',
        rail.includes('id="histKind"') && rail.includes('id="histQ"'));
  claim('…and states the order it is in',
        /Newest first/.test(rail));
}

/* ---- the routes are actually REACHED, not merely mentioned -------------
 *
 * `assert "at + '/record'" in floor` is satisfied by the string existing
 * anywhere in the file, including inside a comment. Drive the page and read
 * back what it asked the server for. */
if (!failed) {
  const asked = [];
  const realFetch = sandbox.fetch;
  /* `authenticated` rides every stubbed answer. The page re-reads /api/me
     through the same fetch, and a spy that omits it signs the operator out
     mid-run — after which every write returns early at `requireAuth()` and
     the rest of this file tests a signed-out page without saying so. */
  const spy = body => (...args) => {
    asked.push({url: String(args[0]), opts: args[1] || {}});
    return Promise.resolve({ok: true, status: 200,
      json: () => Promise.resolve({ok: true, authenticated: true,
                                   user: 'ryan', machines: [], events: [],
                                   ...(body || {})})});
  };

  /* THE FIVE LIFECYCLE TRANSITIONS. One dialog each, then the sheet's own
     submit — which is the only path that reaches the route now. */
  if (typeof sandbox.actOnAction === 'function') {
    const STEPS = [['record', '/record', 'action_taken'],
                   ['verify', '/verify', 'note'],
                   ['close', '/close', 'note'],
                   ['withdraw', '/withdraw', 'reason'],
                   ['note', '/note', 'note']];
    for (const [step, path, field] of STEPS) {
      asked.length = 0;
      sandbox.fetch = spy({ok: true, action: {}, events: [], entries: []});
      sandbox.actOnAction('ca-1', step);
      el('#askText').value = 'Something a person typed.';
      await sandbox.askSubmit();
      await settle();
      sandbox.fetch = realFetch;
      const post = asked.find(a => a.url.includes(path));
      claim(`${step} posts to ${path}`, !!post
            && String(post.opts.method).toUpperCase() === 'POST');
      claim(`…carrying the operator's words as ${field}`,
            !!post && JSON.parse(String(post.opts.body || '{}'))[field]
                      === 'Something a person typed.');
    }
  }

  /* Moving a level, the history read and the documents read: three more
     routes that were greps. */
  if (typeof sandbox.act === 'function') {
    asked.length = 0;
    sandbox.fetch = spy({ok: true, machines: []});
    await sandbox.act('lvl-up', FLEET.machines[0]);
    await settle();
    sandbox.fetch = realFetch;
    claim('stepping a level up posts to the equipment’s level route',
          asked.some(a => /\/api\/equipment\/[^/]+\/level\/up$/.test(a.url)
                          && String(a.opts.method).toUpperCase() === 'POST'));
  }
  if (typeof sandbox.loadHistory === 'function') {
    asked.length = 0;
    sandbox.fetch = spy({entries: [], actions: []});
    await sandbox.loadHistory(FLEET.machines[0]);
    await settle();
    sandbox.fetch = realFetch;
    claim('opening History reads the merged timeline, with a limit',
          asked.some(a => /\/history\?limit=\d+/.test(a.url)));
    claim('…and this equipment’s corrective actions alongside it',
          asked.some(a => /\/actions$/.test(a.url)));
  }
  if (typeof sandbox.loadDocuments === 'function') {
    asked.length = 0;
    sandbox.fetch = spy({documents: []});
    await sandbox.loadDocuments(FLEET.machines[0]);
    await settle();
    sandbox.fetch = realFetch;
    claim('opening Documents reads this equipment’s documents',
          asked.some(a => /\/api\/equipment\/[^/]+\/documents$/.test(a.url)));
  }
}

/* ---- the history filters actually narrow the list --------------------- */
if (!failed && typeof sandbox.paintTimeline === 'function'
    && typeof sandbox.fillHistoryKinds === 'function') {
  const realFetch = sandbox.fetch;
  const ENTRIES = [
    {at: '2026-08-24T10:00', uid: 'e1', source: 'log', kind: 'qc',
     summary: 'QC Cloud Point -10.6 outside the band', who: ''},
    {at: '2026-08-23T10:00', uid: 'e2', source: 'log', kind: 'config',
     summary: 'Level moved — Moved from Ground Floor to Second Floor.',
     who: 'kaden'},
    {at: '2026-08-22T10:00', uid: 'e3', source: 'maintenance', kind: 'pm',
     summary: 'PM completed — Monthly clean', who: 'ryan'},
  ];
  sandbox.fetch = () => Promise.resolve({ok: true, status: 200,
    json: () => Promise.resolve({entries: ENTRIES, truncated: false,
                                 actions: [], authenticated: true,
                                 user: 'ryan'})});
  await sandbox.loadHistory(FLEET.machines[0]);
  await settle();
  sandbox.fetch = realFetch;
  const shown = () => String(el('#tlList').innerHTML || '');
  claim('the history draws everything it was given', shown().includes('e1')
        || /Cloud Point/.test(shown()));
  claim('the kind filter is built from what this equipment actually has',
        /value="qc"/.test(String(el('#histKind').innerHTML))
        && /value="pm"/.test(String(el('#histKind').innerHTML))
        && !/value="override"/.test(String(el('#histKind').innerHTML)));
  el('#histKind').value = 'pm';
  sandbox.paintTimeline();
  claim('choosing a kind narrows the list to it',
        /Monthly clean/.test(shown()) && !/Cloud Point/.test(shown()));
  claim('…and the order line says how much of the record is on screen',
        /1 of 3/.test(String(el('#histOrder').textContent)));
  el('#histKind').value = '';
  el('#histQ').value = 'kaden';
  sandbox.paintTimeline();
  claim('searching narrows it too, across who as well as what',
        /Second Floor/.test(shown()) && !/Monthly clean/.test(shown()));
  el('#histQ').value = 'nothing matches this';
  sandbox.paintTimeline();
  claim('a filter that matches nothing says so, and does not read as an '
        + 'empty history', /matches that/.test(shown()));
  el('#histQ').value = '';
  sandbox.paintTimeline();
  claim('clearing the filters says the order and nothing else',
        String(el('#histOrder').textContent).trim() === 'Newest first.');
  /* THE STORED CONSTANT IS NEVER PRINTED. `level_move` is what the store
     writes into `test_name`; the row above it reads "level created". */
  claim('a level move reads in English, not as the stored constant',
        !/level_move/.test(shown()) && /Level moved/.test(shown()));
  claim('…and names the level rather than its uid',
        /Second Floor/.test(shown()));
}

if (!failed && typeof sandbox.deselect === 'function') sandbox.deselect();

/* ---- a lab-wide event names no equipment, and must say so --------------
 *
 * `level created` is written with an empty machine_uid. The activity rail
 * rendered that as a blank where a name goes, which reads as a row whose
 * equipment nobody recorded. */
if (!failed && typeof sandbox.renderFeed === 'function') {
  const realFetch = sandbox.fetch;
  sandbox.fetch = () => Promise.resolve({ok: true, status: 200,
    json: () => Promise.resolve({authenticated: true, user: 'ryan', events: [
      {machine_uid: '', ts: '2026-08-24T09:00:00', kind: 'config', lab_id: '',
       test_name: 'level created', value: '',
       detail: '{"action":"level created","by":"ryan"}'},
      {machine_uid: 'gc-1', ts: '2026-08-24T09:05:00', kind: 'qc',
       lab_id: 'L1', test_name: 'Cloud Point', value: '-9.8',
       detail: '{"in_spec": false}'},
    ]})});
  await sandbox.renderFeed();
  await settle();
  const feed = String(el('#railR').innerHTML || '');
  sandbox.fetch = realFetch;
  claim('the activity rail draws a lab-wide event',
        /config/.test(feed));
  claim('…and names it Lab-wide rather than leaving the name blank',
        /Lab-wide/.test(feed));
  claim('…while equipment events still carry their own title',
        /GC 1|gc-1/.test(feed));
}

/* ---- the rail may not contradict the dots above it either --------------
 *
 * "Awaiting QC setup" listed everything with no standard assigned and then
 * said "so they read UNKNOWN" — which a bench reporting GREEN off its own
 * module can perfectly well not. Same rule as `diagnosis()`, in the other
 * place on this page that states a conclusion about a status it never looked
 * at. Driven with a fleet that has both, because the fixture above gives every
 * piece its QC and so never reaches this branch at all. */
if (!failed && typeof sandbox.load === 'function') {
  const bare = {qc_specs: [], effective_specs: [], qc_targets: []};
  const kept = PAYLOADS['/api/machines'];
  PAYLOADS['/api/machines'] = Object.assign({}, FLEET, {machines: [
    onLevel('nq-1', Object.assign({title: 'NQ 1', status: 'GREEN',
                                   level_uid: 'l1', reason: ''}, bare)),
    onLevel('nq-2', Object.assign({title: 'NQ 2', status: 'UNKNOWN',
                                   level_uid: 'l1', reason: ''}, bare)),
  ]});
  await sandbox.load();
  await settle();
  const rail = String(el('#railL').innerHTML || '');
  const line = (rail.match(/Awaiting QC setup<\/div>[\s\S]*?<\/p>/) || [''])[0];
  claim('a fleet with no QC assigned reaches the "Awaiting QC setup" line',
        line.length > 40);
  claim('…which names both pieces', /NQ 1/.test(line) && /NQ 2/.test(line));
  claim('…and does NOT tell the GREEN one it reads UNKNOWN',
        !/so they read UNKNOWN/.test(line)
        && !/which is why they read UNKNOWN/.test(line));
  claim('…while still saying the one that DOES reads it for that reason',
        /reads UNKNOWN for that reason/.test(line));
  claim('…and still says the useful half about the rest',
        /nothing here is checking/.test(line));
  readsWell('the awaiting-QC line', line);

  /* All of them grey: then the original sentence is the true one and must
     still be said. */
  PAYLOADS['/api/machines'] = Object.assign({}, FLEET, {machines: [
    onLevel('nq-2', Object.assign({title: 'NQ 2', status: 'UNKNOWN',
                                   level_uid: 'l1', reason: ''}, bare)),
  ]});
  await sandbox.load();
  await settle();
  const all = String(el('#railL').innerHTML || '')
    .match(/Awaiting QC setup<\/div>[\s\S]*?<\/p>/);
  claim('a bench that really does read UNKNOWN is still told why',
        !!all && /why it reads UNKNOWN/.test(all[0]));

  PAYLOADS['/api/machines'] = kept;
  await sandbox.load();
  await settle();
}

/* ---- the rest of the words a person actually reads ---------------------
 *
 * Prose in a branch nothing renders is prose nothing checks: the rename first
 * escaped this harness through the "no equipment" rail, which only draws
 * before there is a fleet. The right-click menu and the delete confirmation
 * are the other two places this page writes sentences from JavaScript. */
if (!failed && typeof sandbox.openMenu === 'function') {
  sandbox.openMenu(FLEET.machines[0], {clientX: 40, clientY: 40});
  const menu = String(el('#menu').innerHTML || '');
  claim('the right-click menu renders', menu.length > 40);
  readsWell('the right-click menu', menu);
  claim('the right-click menu offers moving up and down a level',
        menu.includes('data-act="lvl-up"')
        && menu.includes('data-act="lvl-down"'));
  claim('…and names the level the equipment is on', menu.includes('Ground'));
  claim('the menu reaches the documents and history tabs',
        menu.includes('data-act="docs"') && menu.includes('data-act="hist"'));
  /* The steppers are one press per level; this is the "straight to that one"
     path, and the only way to place several pieces at once. */
  claim('…and offers going straight to a chosen level',
        menu.includes('data-act="lvl-pick"'));
}

/* ---- NOTHING ON THIS PAGE ASKS THROUGH A NATIVE BOX --------------------
 *
 * `window.prompt` and `window.confirm` cannot show what is being changed,
 * cannot show a refusal, and chain. Every one of them is now the page's own
 * sheet, so the stubs are booby-trapped: reaching either is a failure, not a
 * silently-answered question. */
const nativeAsks = [];
sandbox.confirm = msg => { nativeAsks.push('confirm: ' + msg); return false; };
sandbox.prompt = msg => { nativeAsks.push('prompt: ' + msg); return null; };

if (!failed && typeof sandbox.act === 'function') {
  try {
    await sandbox.act('delete', FLEET.machines[0]);
  } catch (err) {
    report('act("delete") threw', err);
  }
  claim('retiring equipment opens the page’s own sheet, not a native box',
        nativeAsks.length === 0);
  const title = String(el('#askTitle').textContent || '');
  const body = String(el('#askRule').textContent || '');
  claim('…which names what is being retired',
        String(el('#askWhatText').textContent || '')
          .includes(FLEET.machines[0].title));
  claim('…and says what retiring costs, in the words the confirm used',
        /status and QC assignments are removed/.test(body)
        && /re-appear on the next poll/.test(body));
  /* The second confirm read "OK = erase, Cancel = keep" — a box whose Cancel
     button did not cancel. It is a tick inside the one sheet now. */
  claim('the history question is a tick in the same sheet, not a second box',
        el('#askCheckWrap').hidden === false
        && /erase its history/.test(String(el('#askCheckLabel').textContent)));
  claim('…and leaving it untouched keeps the history',
        /history stays/.test(String(el('#askCheckLabel').textContent)));
  readsWell('the retire sheet', title + ' ' + body
            + ' ' + String(el('#askCheckLabel').textContent));

  /* AND THE TICK HAS TO REACH THE REQUEST. It replaced a second confirm()
     whose answer decided whether the history was erased; a tick that is drawn
     and then not read is worse than the box it replaced, because it looks
     answered. Driven both ways. */
  const realFetch2 = sandbox.fetch;
  const sent = [];
  sandbox.fetch = (...args) => {
    sent.push({url: String(args[0]), body: (args[1] || {}).body});
    return Promise.resolve({ok: true, status: 200,
      json: () => Promise.resolve({ok: true, authenticated: true,
                                   user: 'ryan', machines: []})});
  };
  for (const ticked of [true, false]) {
    sent.length = 0;
    await sandbox.act('delete', FLEET.machines[0]);
    el('#askCheck').checked = ticked;
    await sandbox.askSubmit();
    await settle();
    const del = sent.find(r => /\/api\/machines\//.test(r.url));
    claim(`retiring with the tick ${ticked ? 'set' : 'clear'} sends `
          + `purge_history: ${ticked}`,
          !!del && JSON.parse(String(del.body || '{}')).purge_history === ticked);
  }
  sandbox.fetch = realFetch2;
}

/* ---- the corrective-action lifecycle, which was five chained prompts ----
 *
 * The five transitions the store enforces were `prompt()` boxes: no way to see
 * WHICH action was about to be withdrawn, no way to state the rule, and — the
 * one that cost work — no way to show the 409 that came back after the box had
 * already closed and taken the typed sentence with it. */
if (!failed && typeof sandbox.actOnAction === 'function') {
  const STEPS = ['record', 'verify', 'close', 'withdraw', 'note'];
  /* The action on screen, put there the way the page puts it there: by
     rendering it. `renderAction` is what fills the record AND what the
     lifecycle sheets quote from, so driving it is the only way to prove the
     sheet shows the action rather than a blank. */
  const ACTION = {
    uid: 'ca-1', state: 'actioned', priority: 'high',
    what_happened: 'Cloud Point drifted below the lower band on two runs.',
    assigned_to: 'ryan', due_at: '2026-08-24', opened_at: '2026-08-22T09:10:00',
    opened_by: 'kaden', action_taken: 'Replaced the cooling bath fluid.',
  };
  const realActionFetch = sandbox.fetch;
  sandbox.fetch = () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({action: ACTION, events: []}),
  });
  await sandbox.renderAction('ca-1');
  sandbox.fetch = realActionFetch;
  claim('the corrective-action record renders what it was given',
        String(el('#actionBody').innerHTML || '')
          .includes('Cloud Point drifted'));
  /* THE BUTTON THAT WOULD BE REFUSED IS DISABLED. `actioned` cannot be closed,
     so Close is off; it can be verified, so Verify is on. Same table the
     server enforces, read off the RENDERED buttons. */
  const railHtml = String(el('#actionBody').innerHTML || '');
  const btn = what => (railHtml.match(
    new RegExp('data-do="' + what + '"([^>]*)>')) || [, ''])[1];
  claim('…and disables the move the lifecycle refuses from this state',
        /disabled/.test(btn('close')));
  claim('…while offering the ones it allows',
        !/disabled/.test(btn('verify')) && !/disabled/.test(btn('withdraw'))
        && !/disabled/.test(btn('record')));
  const seen = {};
  for (const step of STEPS) {
    el('#askTitle').textContent = '';
    el('#askRule').textContent = '';
    el('#askLabel').textContent = '';
    el('#askField').hidden = true;
    sandbox.actOnAction('ca-1', step);
    seen[step] = {
      title: String(el('#askTitle').textContent || ''),
      rule: String(el('#askRule').textContent || ''),
      label: String(el('#askLabel').textContent || ''),
      what: String(el('#askWhatText').textContent || ''),
      field: el('#askField').hidden === false,
      submit: String(el('#askGo').textContent || ''),
    };
  }
  claim('all five lifecycle transitions open a dialog, none a prompt',
        nativeAsks.length === 0);
  /* AND NONE OF THEM CARRIES THE RETIRE SHEET'S TICK. One dialog reused means
     a control left over from the last question is a control in this one — and
     `.askcheck` has `display:flex`, which beats the UA's `[hidden]` rule at
     equal specificity unless the stylesheet says otherwise. */
  claim('…and none of them shows the retire sheet’s leftover tick',
        el('#askCheckWrap').hidden === true);
  claim('…each with its own heading',
        new Set(STEPS.map(k => seen[k].title)).size === 5
        && STEPS.every(k => seen[k].title.length > 8));
  claim('…each showing the action’s own words',
        STEPS.every(k => seen[k].what.includes('Cloud Point drifted')));
  claim('…each with a real labelled field, not a bare box',
        STEPS.every(k => seen[k].field && seen[k].label.length > 3));
  claim('…each with a button that names the move',
        STEPS.every(k => seen[k].submit.length > 4
                         && seen[k].submit !== 'OK'));
  /* THE RULE IS STATED BEFORE THE BUTTON IS PRESSED. These are the rules
     `equipment_history.LIFECYCLE` enforces, not rules invented here. */
  claim('closing states that only a verified action may be closed',
        /verified/i.test(seen.close.rule));
  claim('verifying states that it needs a recorded action, and happens once',
        /once/i.test(seen.verify.rule) && /what was done/i.test(seen.verify.rule));
  claim('withdrawing states that the row stays and says who withdrew it',
        /stays/i.test(seen.withdraw.rule));
  claim('a note states that it is allowed in every state and rewrites nothing',
        /every state/i.test(seen.note.rule)
        && /never rewrites/i.test(seen.note.rule));
  claim('recording states that it keeps what it replaced',
        /keeps what it replaced/i.test(seen.record.rule));
  STEPS.forEach(k => readsWell('the ' + k + ' sheet',
                               seen[k].title + ' ' + seen[k].rule));

  /* A REFUSAL IS SHOWN IN THE SHEET, AND THE SHEET STAYS OPEN. The store
     refuses a close that skipped verification by ANSWERING 409 — this is the
     whole reason a prompt could not carry these. */
  const realFetch = sandbox.fetch;
  sandbox.fetch = () => Promise.resolve({
    ok: false, status: 409,
    json: () => Promise.resolve({error: 'Corrective action is actioned; it '
      + 'cannot be closed from there. Verify that it worked before closing '
      + 'it, or withdraw it.'}),
  });
  el('#askDlg').open = true;
  el('#askErr').textContent = '';
  sandbox.actOnAction('ca-1', 'close');
  el('#askText').value = 'Back in service.';
  await sandbox.askSubmit();
  sandbox.fetch = realFetch;
  claim('a refused transition prints the server’s refusal in the sheet',
        /cannot be closed from there/.test(String(el('#askErr').textContent)));
  claim('…and the sheet keeps the sentence that was typed',
        String(el('#askText').value) === 'Back in service.');
  claim('…and the button is usable again rather than left disabled',
        el('#askGo').disabled === false);

  /* An empty answer where one is required is refused HERE, with the sheet
     open, rather than as a round trip that loses the box. */
  sandbox.actOnAction('ca-1', 'withdraw');
  el('#askText').value = '   ';
  await sandbox.askSubmit();
  claim('a required answer left blank is refused in the sheet',
        String(el('#askErr').textContent).length > 10);
}

/* ---- a refused write must SAY so, and the dialog must stay open --------
 *
 * LabCore's queue refuses past ~100 pending by ANSWERING, not by raising, and
 * the routes turn that into a 502/503 carrying the sentence. Nothing throws.
 * So the only thing standing between "LabCore said no" and a dialog closing on
 * "Saved" is that every write reads `failure(r)` and stops. */
if (!failed && typeof sandbox.levelWrite === 'function') {
  const real = sandbox.fetch;
  const answer = (ok, status, body) => () => Promise.resolve({
    ok, status, json: () => Promise.resolve(body)});

  sandbox.fetch = answer(false, 502, {
    error: 'The new level was NOT saved. LabCore is busy (retry_after=4).',
    saved: false, retry: true});
  let out = await sandbox.levelWrite('/api/equipment/levels', {}, 'The level');
  claim('a refused level write answers false, so the caller cannot carry on',
        out === false);
  claim('…and the refusal is put on screen in LabCore\'s own words',
        /NOT saved/.test(String(el('#levelsErr').textContent)));

  sandbox.fetch = () => Promise.reject(new Error('offline'));
  out = await sandbox.levelWrite('/api/equipment/levels', {}, 'The level');
  claim('a server that cannot be reached is a failure too, not a success',
        out === false
        && /not be reached/.test(String(el('#levelsErr').textContent)));

  sandbox.fetch = answer(true, 200, {ok: true});
  out = await sandbox.levelWrite('/api/equipment/levels', {}, 'The level');
  claim('a write that landed answers true and clears the line',
        out === true && String(el('#levelsErr').textContent) === '');
  sandbox.fetch = real;
}

/* ---- the corrective-action lifecycle, as the record allows it ----------
 *
 * `equipment_history.LIFECYCLE` refuses closing something nobody verified, and
 * the route turns that into a 409 with the sentence saying what to do instead.
 * The server stays the authority — but a UI that offers a move the record will
 * refuse teaches people to ignore what it says, so the button is disabled. */
if (!failed && typeof sandbox.renderAction === 'function') {
  const real = sandbox.fetch;
  const asAction = over => () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({
      action: Object.assign({
        uid: 'a1', machine_uid: 'gc-1', what_happened: 'QC failed twice',
        state: 'open', priority: 'high', overdue: false, opened_at: '2026-08-01',
        opened_by: 'ryan', action_taken: '', verified_at: '', assigned_to: '',
        due_at: '', closed_at: '', outcome: '',
      }, over),
      events: [{kind: 'note', note: 'chased the vendor', by: 'ryan',
                at: '2026-08-02T09:00'}],
    })});

  sandbox.fetch = asAction({state: 'open'});
  await sandbox.renderAction('a1');
  let body = String(el('#actionBody').innerHTML || '');
  const enabled = what => new RegExp(`data-do="${what}"(?![^>]*disabled)`)
    .test(body);
  claim('an open action can have what was done recorded', enabled('record'));
  claim('…but cannot be verified before anything is recorded',
        !enabled('verify'));
  claim('…and cannot be closed at all', !enabled('close'));
  claim('…and can always be withdrawn — it is the only way to say the record '
        + 'should not exist', enabled('withdraw'));
  claim('the action shows what was said about it', body.includes('chased the'));

  sandbox.fetch = asAction({state: 'verified', action_taken: 'replaced lamp',
                            verified_at: '2026-08-03', verified_by: 'kaden',
                            overdue: true, due_at: '2026-08-02'});
  await sandbox.renderAction('a1');
  body = String(el('#actionBody').innerHTML || '');
  claim('a verified action can be closed', enabled('close'));
  claim('…and an overdue one says so on the record itself',
        body.includes('badge over'));

  sandbox.fetch = asAction({state: 'closed', action_taken: 'replaced lamp',
                            verified_at: '2026-08-03', closed_at: '2026-08-04'});
  await sandbox.renderAction('a1');
  body = String(el('#actionBody').innerHTML || '');
  claim('a closed action is terminal — nothing is offered on it',
        !enabled('close') && !enabled('verify') && !enabled('record')
        && !enabled('withdraw'));
  /* A note is legal at any point in an action's life, including after it is
   * finished — a pointer to a recurrence belongs there. */
  claim('…except a note, which is legal for ever', enabled('note'));

  sandbox.fetch = () => Promise.resolve({
    ok: false, status: 503,
    json: () => Promise.resolve({error: 'This corrective action could not be '
                                        + 'read.'})});
  await sandbox.renderAction('a1');
  body = String(el('#actionBody').innerHTML || '');
  claim('a blip on an action reads as a blip, not as an empty action',
        /could not be read/.test(body));
  sandbox.fetch = real;
}

/* ---- the fleet's open actions: one read, and never a confident zero ----
 *
 * `open_by_machine()` answers the whole lab at once, and it is read off the
 * polled path entirely. What matters here is the failure: a read that could
 * not be made must not badge every piece of equipment as having nothing open, because
 * "nobody is on this" is the sentence that decides whether somebody walks over
 * to a RED bench. */
if (!failed && typeof sandbox.loadOpenActions === 'function'
    && typeof sandbox.openActionsFor === 'function') {
  const real = sandbox.fetch;
  sandbox.fetch = () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({
      by_machine: {'gc-1': [{uid: 'a1', overdue: true, priority: 'high',
                             state: 'open', what_happened: 'QC failed'}]},
      counts: {'gc-1': 1}, overdue: {'gc-1': 1}, total: 1})});
  await sandbox.loadOpenActions();
  claim('a piece of equipment with an open action reports it',
        (sandbox.openActionsFor('gc-1') || []).length === 1);
  claim('one with none reports none — the read succeeded',
        (sandbox.openActionsFor('gc-2') || []).length === 0);
  sandbox.setLevelView('l1');
  sandbox.renderOverview();
  let rail = String(el('#railL').innerHTML || '');
  claim('the rail badges the open action and says it is overdue',
        rail.includes('1 open') && rail.includes('overdue'));
  claim('…and totals them for the lab',
        /1<\/b>\s*open\s*across the lab/.test(rail));

  sandbox.fetch = () => Promise.resolve({
    ok: false, status: 503,
    json: () => Promise.resolve({error: 'The open corrective actions could '
                                        + 'not be read.'})});
  await sandbox.loadOpenActions();
  claim('a failed fleet read answers "unknown", never "none open"',
        sandbox.openActionsFor('gc-1') === null);
  sandbox.renderOverview();
  rail = String(el('#railL').innerHTML || '');
  claim('…so the rail shows no badge rather than a confident zero',
        !rail.includes('1 open') && !rail.includes('0 open'));
  sandbox.fetch = real;
}

/* ---- assign / due / priority: one dialog, prefilled ---------------------
 *
 * `CorrectiveActionStore.assign` rewrites all three together, so a dialog that
 * opens on blank fields silently unassigns an action the moment somebody
 * presses Save to change only the due date. And a refusal has to keep the
 * dialog open, like every other write on this page. */
if (!failed && typeof sandbox.openAssign === 'function') {
  const real = sandbox.fetch;
  sandbox.fetch = () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({
      action: {uid: 'a1', machine_uid: 'gc-1', state: 'actioned',
               what_happened: 'QC failed twice', priority: 'critical',
               assigned_to: 'kaden', due_at: '2026-09-01', overdue: false,
               opened_at: '2026-08-01', action_taken: 'swapped the lamp',
               verified_at: '', closed_at: '', outcome: ''},
      events: []})});
  await sandbox.renderAction('a1');
  sandbox.openAssign('a1');
  claim('the assign dialog opens on who the action is already with',
        el('#asWho').value === 'kaden');
  claim('…and on the due date it already has',
        el('#asDue').value === '2026-09-01');
  claim('…and on its current priority', el('#asPriority').value === 'critical');

  sandbox.fetch = () => Promise.resolve({
    ok: false, status: 502,
    json: () => Promise.resolve({
      error: 'This corrective action was NOT saved. LabCore is busy.'})});
  const saved = await sandbox.saveAssignment();
  claim('a refused assignment answers false rather than reporting a save',
        saved === false);
  claim('…and says so on the dialog, which stays open',
        /NOT saved/.test(String(el('#asErr').textContent)));
  sandbox.fetch = real;
}

/* ---- a blip reads as a blip, never as an empty tab ---------------------
 *
 * "This equipment has no documents" and "nothing is open against it" are
 * sentences an operator acts on, and inventing either out of a timed-out read
 * is the failure `labcore_result` was extracted to end. The stores raise
 * rather than degrading and the routes answer 503; this is the last leg, where
 * a `.then(r => r.json())` with no `r.ok` check would turn the 503's body into
 * an empty list. */
if (!failed && typeof sandbox.loadDocuments === 'function') {
  const real = sandbox.fetch;
  sandbox.fetch = () => Promise.resolve({
    ok: false, status: 503,
    json: () => Promise.resolve({
      error: 'This equipment\'s documents could not be read.'})});
  await sandbox.loadDocuments(FLEET.machines[0]);
  const docs = String(el('#docList').innerHTML || '');
  claim('an unreadable documents tab says so',
        /could not be read/.test(docs));
  claim('…and does NOT claim the equipment has no documents',
        !/No documents/.test(docs));

  if (typeof sandbox.loadHistory === 'function') {
    await sandbox.loadHistory(FLEET.machines[0]);
    const tl = String(el('#tlList').innerHTML || '');
    const open = String(el('#actOpen').innerHTML || '');
    claim('an unreadable history says so rather than showing nothing',
          /could not be read|did not answer/.test(tl)
          && !/Nothing recorded/.test(tl));
    claim('…and unreadable corrective actions do not read as none open',
          /could not be read|did not answer/.test(open)
          && !/Nothing open/.test(open));
  }
  sandbox.fetch = real;
}

/* ---- who the move dialog would offer ----------------------------------- */
if (!failed && typeof sandbox.moveCandidates === 'function') {
  sandbox.setLevelView('l1');
  const bringing = sandbox.moveCandidates(null).map(m => m.machine_uid);
  claim('bringing equipment onto a level offers what is not already there',
        bringing.length === 1 && bringing[0] === 'gc-2');
  const one = sandbox.moveCandidates(['gc-3']).map(m => m.machine_uid);
  claim('moving one piece offers exactly that one',
        one.length === 1 && one[0] === 'gc-3');
}

/* ---- the "Needs attention" card, which used to collide with itself ------
 *
 * Reported off a screenshot, 2026-08-25: Multitek NS — the most prominent card
 * on the page — rendered as
 *
 *     Cloud   Second
 *     Point   Floor
 *     -9.8 C
 *     -
 *     outside
 *     63.7
 *
 * with the red "1 OPEN · 1 OVERDUE" badge painted as a block across it. The
 * reason, the level name and the badge were three items in ONE `.lim` flex
 * row inside a 220px rail; the badge carries `white-space:nowrap` and so
 * could not give width back, the two text items were squeezed to their
 * min-content width and wrapped a word per line, and the badge — a flex item
 * under the default `align-items:stretch` — grew to the full height of the
 * result. Its sibling card looked fine only because its reason was short.
 *
 * The fix is STRUCTURAL, so this is checkable on rendered markup rather than
 * only by eye: the reason gets a line of its own, and the level and the badge
 * share a row beneath it. A card that puts them back in one row fails here. */
if (!failed && typeof sandbox.renderOverview === 'function'
    && typeof sandbox.loadOpenActions === 'function') {
  const real = sandbox.fetch;
  /* A long reason AND a badge on the same card — the exact pair that collided.
   * With a short reason the old markup laid out fine, which is why this uses
   * one that cannot fit on a line. */
  const LONG = 'Cloud Point -9.8 C — outside 63.7 ± 2.10, and the bath fluid '
             + 'was replaced on Tuesday without moving it back into band.';
  FLEET.machines[0].reason = LONG;
  sandbox.fetch = () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({
      by_machine: {'gc-1': [{uid: 'a1', overdue: true, priority: 'high',
                             state: 'open', what_happened: 'QC failed'}]},
      counts: {'gc-1': 1}, overdue: {'gc-1': 1}, total: 1})});
  await sandbox.loadOpenActions();
  sandbox.fetch = real;
  sandbox.setLevelView('l1');
  sandbox.renderOverview();
  const rail = String(el('#railL').innerHTML || '');

  claim('the card still renders both the reason and the badge',
        /Cloud Point/.test(rail) && /1 open/.test(rail)
        && /overdue/.test(rail));

  /* The reason's own element, and everything inside it. */
  const why = /<div class="why">([\s\S]*?)<\/div>/.exec(rail);
  claim('the reason has an element of its own, not a slot in a shared row',
        !!why);
  if (why) {
    claim('…and the badge is NOT inside it — that pairing is the collision',
          !/badge/.test(why[1]));
    claim('…and neither is the level name',
          !/Ground|Mezzanine|Roof/.test(why[1]));
  }

  /* The row underneath: the level and the badge, and NOT the reason. */
  const meta = /<div class="attnmeta">([\s\S]*?)<\/div>\s*<\/div>/.exec(rail);
  claim('the level and the badge share a row of their own', !!meta);
  if (meta) {
    claim('…which holds the badge', /class="badge/.test(meta[1]));
    claim('…and the level', /Ground/.test(meta[1]));
    claim('…and NOT the reason, which would put the collision back',
          !/Cloud Point/.test(meta[1]));
  }

  /* The three-in-a-row markup by name. `.lim` is still the right element for
   * a QC band's low/high pair; what it must never again hold is this card. */
  claim('no flagged card packs reason, level and badge into one `.lim` row',
        !/<div class="lim"><span>Cloud Point/.test(rail));

  readsWell('the flagged-equipment card', rail);

  /* A card with nothing to say must not leave an EMPTY row behind it — that is
   * a gap under the title that looks like something failed to load. gc-3 is
   * flagged YELLOW; take its reason away and it must simply lose the line. */
  const hadReason = FLEET.machines[2].reason;
  FLEET.machines[2].reason = '';
  sandbox.renderOverview();
  const quiet = String(el('#railL').innerHTML || '');
  claim('a flagged card with no reason drops the line instead of leaving it '
        + 'blank', !/<div class="why">\s*<\/div>/.test(quiet));
  claim('…and still renders the card itself',
        quiet.includes('data-pick="gc-3"'));
  FLEET.machines[2].reason = hadReason;
  FLEET.machines[0].reason = machine.reason;
}

/* The reason's own trimming. It has a full-width line now, so it can afford
 * far more than the 44 characters it had while sharing a flex row — and when
 * it IS cut it has to say so, because a QC band silently losing its upper
 * limit reads as a complete sentence meaning something else. */
if (!failed && typeof sandbox.attentionReason === 'function') {
  const r = sandbox.attentionReason;
  const long = 'Cloud Point -9.8 C — outside 63.7 ± 2.10, and the bath fluid '
             + 'was replaced on Tuesday without moving it back into band.';
  claim('a reason that fits is left exactly alone',
        r({reason: 'QC due in 4 hours.'}) === 'QC due in 4 hours.');
  claim('a reason too long to fit is cut', r({reason: long}).length < long.length);
  claim('…and says it was cut rather than ending mid-sentence',
        /…$/.test(r({reason: long})));
  /* Cut at a word boundary. "…outside 63.7 ± 2.10, and the bath flu…" is worse
   * than a shorter sentence that ends on a whole word. */
  const body = r({reason: long}).replace(/…$/, '');
  claim('…at a word boundary rather than mid-word',
        long.startsWith(body) && body.length > 0
        && (long.length === body.length || /\s/.test(long[body.length])));
  claim('no reason at all is empty, never the word "undefined"',
        r({}) === '' && r({reason: null}) === '');
} else if (!failed) {
  failed = true;
  console.log('FAIL: attentionReason() is gone — the flagged card would print '
              + 'whatever length of reason it was handed');
}

/* ---- every other sentence this page writes from JavaScript --------------
 *
 * The rename escaped this harness twice through branches nothing renders in
 * the happy path. So: sweep the render functions that RETURN markup, and put
 * every one of them through the same two judgements. A function that has been
 * refactored away is skipped, not failed — but a function that is still here
 * and still says "instrument" is caught, wherever on the page it lives. */
if (!failed) {
  const SWEEP = [
    ['the hover tip', () => { sandbox.showTip(FLEET.machines[0], {x: 4, y: 4});
                              return el('#tip').innerHTML; }],
    ['the diagnosis line', () => sandbox.diagnosis(FLEET.machines[0])],
    ['the QC checks list', () => sandbox.qcChecksHtml(FLEET.machines[0])],
    ['the module-state line', () => sandbox.moduleStateText(FLEET.machines[0])],
    ['an empty documents tab', () => sandbox.documentsHtml([])],
    ['an empty history', () => sandbox.timelineHtml({entries: []})],
    ['nothing open', () => sandbox.actionsHtml([])],
    ['the levels dialog', () => sandbox.levelsListHtml()],
    ['the level menu', () => sandbox.levelMenuHtml()],
    ['deleting a populated level', () => sandbox.deleteConsequence('l2')],
    ['deleting an empty level', () => sandbox.deleteConsequence('l3')],
    ['the floor hint', () => { sandbox.paintTools();
                               return el('#hint').textContent; }],
  ];
  let swept = 0;
  for (const [what, produce] of SWEEP) {
    let out;
    try {
      out = produce();
    } catch (err) {
      /* A stub DOM can only take these so far; a throw here is the harness's
       * limit, not the page's, EXCEPT that every one of these is already
       * driven elsewhere in this file. So report it — silently skipping is how
       * a sweep becomes decoration. */
      report(`${what} threw while being swept for the rename`, err);
      continue;
    }
    if (out === undefined || out === null) continue;   // refactored away
    if (readsWell(what, out)) swept++;
  }
  claim(`the sweep actually judged something — ${swept} of ${SWEEP.length} `
        + 'render paths', swept >= 8);
}

/* The module-state sentences are a branch each, and every one of them used to
 * name the thing. Drive all four rather than only the healthy one. */
if (!failed && typeof sandbox.diagnosis === 'function') {
  const states = [
    ['a stopped module', {module_state: 'stopped'}],
    ['a module that never checked in', {module_state: 'unknown',
                                        last_activity: ''}],
    ['a module running but parsing nothing',
     {module_state: 'running', module_running: true,
      last_activity: '2026-07-01 09:00:00'}],
    ['equipment with no QC assigned',
     {qc_targets: [], qc_specs: [], effective_specs: []}],
  ];
  for (const [what, over] of states) {
    const m = Object.assign({}, FLEET.machines[0], over);
    let out;
    try {
      out = sandbox.diagnosis(m);
    } catch (err) {
      report(`diagnosis() threw for ${what}`, err);
      continue;
    }
    readsWell(`the diagnosis for ${what}`, out);
  }

  /* ---- and it may never contradict the badge above it ------------------
   *
   * The no-QC branch asserted "that is why it reads UNKNOWN" without ever
   * looking at the status, so a bench reporting GREEN off its own module, with
   * no standard assigned here, was told three lines under a GREEN badge that
   * it read UNKNOWN. */
  /* Everything BEFORE the QC branch answered: the module is running and it
     parsed a minute ago, so the only thing left to say about this bench is
     whether anything is checking it. */
  const noQc = over => Object.assign({}, FLEET.machines[0], {
    module_state: 'running', module_running: true,
    last_poll: new Date().toISOString(),
    last_activity: new Date().toISOString(),
    qc_targets: [], qc_specs: [], effective_specs: [],
  }, over);
  const grey = sandbox.diagnosis(noQc({status: 'UNKNOWN'}));
  const green = sandbox.diagnosis(noQc({status: 'GREEN'}));
  claim('an UNKNOWN bench with no QC is told that is why',
        /reads UNKNOWN/.test(grey));
  claim('a GREEN bench with no QC is NOT told it reads UNKNOWN',
        !/UNKNOWN/.test(green));
  claim('…and is still told the QC is missing, which is the useful half',
        /no QC standard is assigned/.test(green));
  claim('…and is told what the badge actually says',
        /GREEN/.test(green));
  /* Assigned QC reaches a bench three ways and only one of them is
     `qc_targets`; judging on that alone told actively-judged benches they had
     none. */
  claim('a bench judged through a resolved effective spec is not called '
        + 'unassigned',
        sandbox.diagnosis(noQc({status: 'GREEN',
          effective_specs: [{test_name: 'Cloud Point', low: -9.8, high: -8.2}]}))
          === '');
}

/* ---- the hover tip draws a band, not NaN ------------------------------
 *
 * `showTip` reached for `m.qc_specs[0]` and printed `spec.low`/`spec.high`
 * off it. Those columns are not on that list and never have been —
 * lem_qc_specs holds expected/std_dev/k, and the band lives on
 * effective_specs, published by the module as expected ± k·std_dev
 * (lem_station_module.spec_band). Every instrument with a QC standard drew
 * `NaN…NaN`.
 *
 * It survived because this harness's fixture invented `low`/`high` on
 * qc_specs, and because the only assertion pointed at the tip asked whether
 * it said "machine" — a wrong number reads as clean prose. So this looks at
 * the number.
 *
 * Note which list this test does NOT name: it asserts the band that is
 * RENDERED, so any correct source passes and reintroducing the wrong one
 * fails, whichever field the fix chooses to read. */
if (!failed) {
  sandbox.showTip(machine, {x: 400, y: 300});
  const tip = el('#tip').innerHTML;
  claim('the hover tip never renders NaN', !/NaN/.test(tip));
  claim('…and draws the effective band the module published',
        /-16\.0\s*…\s*-12\.0/.test(tip));

  /* A machine whose only QC is an override — qc_specs populated,
   * effective_specs empty, which is exactly what a bench that has not
   * reported since the assignment looks like. The row must be absent or
   * numeric; it may never be NaN. */
  const overrideOnly = Object.assign({}, machine, {effective_specs: []});
  sandbox.showTip(overrideOnly, {x: 400, y: 300});
  claim('…and never NaN when only a per-machine override exists',
        !/NaN/.test(el('#tip').innerHTML));
}

console.log(failed ? '\nthe floor does not boot' : '\nthe floor boots');
process.exit(failed ? 1 : 0);
