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
  const el = {
    tagName: name.toUpperCase(),
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    appendChild(c) { return c; }, removeChild(c) { return c; },
    insertBefore(c) { return c; }, remove() {}, focus() {}, blur() {}, click() {},
    setAttribute() {}, removeAttribute() {}, getAttribute() { return ''; },
    showModal() {}, close() {}, scrollIntoView() {}, select() {},
    getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 600,
                                   right: 800, bottom: 600, x: 0, y: 0}),
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

const documentStub = {
  querySelector: () => makeElement(),
  querySelectorAll: () => [],
  getElementById: () => makeElement(),
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
  json: () => Promise.resolve({machines: [], samples: [], events: [],
                               authenticated: false, locked: true, days: [],
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
  /* lem.js's global. The floor calls all three at load. */
  LEM: {get: () => Promise.resolve(null), fresh: () => Promise.resolve(null),
        prefetch: noop, live: noop},
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
  qc_specs: [{test_name: 'Cloud Point', sample_id: 'STD-1', expected: -14,
              std_dev: 1, k: 2, units: 'C', low: -16, high: -12}],
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
      report(`${name}() threw — the floor cannot paint an instrument`, err);
    }
  }
  if (!failed) {
    console.log(`  ok   ${exercised} render paths run against a live-shaped `
                + 'instrument');
  }
}

console.log(failed ? '\nthe floor does not boot' : '\nthe floor boots');
process.exit(failed ? 1 : 0);
