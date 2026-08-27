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

/* ---- REAL SVG GEOMETRY, computed from the attributes the page sets -------
 *
 * `getBBox` used to answer a FIXED box here. That was enough to stop
 * `drawSimpleFloor` taking its no-geometry fallback, and no more: the fit was
 * arithmetic on a number the harness made up, so "the plan fills the stage"
 * could only ever be a claim about the arithmetic, never about the drawing.
 *
 * It matters now. The floor is an exploded STACK of level planes, its height
 * grows with the ladder, and the number the review actually asks for — what
 * fraction of the canvas the EQUIPMENT occupies — cannot be derived from a
 * constant. So every element computes its own box out of the attributes the
 * renderer gave it, groups roll their children up, and `translate()` is
 * honoured. It is the same arithmetic a browser does, and it is the only way
 * this file can measure the drawing rather than believe it.
 *
 * Deliberately NOT a full SVG implementation: these are the shapes the plan
 * actually draws. An element it cannot measure contributes nothing rather than
 * guessing, so a bbox here is a LOWER bound on the real one. */
const NUMS = s => String(s).trim().split(/[\s,]+/).map(Number);
const box = (xs, ys) => (xs.length ? {x0: Math.min(...xs), y0: Math.min(...ys),
                                      x1: Math.max(...xs), y1: Math.max(...ys)}
                                   : null);

function ownBox(el) {
  const a = el.attrs, n = k => Number(a[k]);
  const fin = (...v) => v.every(Number.isFinite);
  switch (el.tagName) {
    case 'POLYGON': case 'POLYLINE': {
      const xs = [], ys = [];
      for (const p of String(a.points || '').trim().split(/\s+/)) {
        const [x, y] = p.split(',').map(Number);
        if (fin(x, y)) { xs.push(x); ys.push(y); }
      }
      return box(xs, ys);
    }
    case 'RECT': {
      if (!fin(n('x'), n('y'), n('width'), n('height'))) return null;
      return {x0: n('x'), y0: n('y'),
              x1: n('x') + n('width'), y1: n('y') + n('height')};
    }
    case 'LINE':
      return fin(n('x1'), n('y1'), n('x2'), n('y2'))
        ? box([n('x1'), n('x2')], [n('y1'), n('y2')]) : null;
    case 'CIRCLE':
      return fin(n('cx'), n('cy'), n('r'))
        ? {x0: n('cx') - n('r'), y0: n('cy') - n('r'),
           x1: n('cx') + n('r'), y1: n('cy') + n('r')} : null;
    case 'PATH': {
      const xs = [], ys = [];
      const v = String(a.d || '').replace(/[A-Za-z]/g, ' ').trim();
      const nn = v ? NUMS(v) : [];
      for (let i = 0; i + 1 < nn.length; i += 2) {
        if (fin(nn[i], nn[i + 1])) { xs.push(nn[i]); ys.push(nn[i + 1]); }
      }
      return box(xs, ys);
    }
    case 'TEXT': {
      /* Approximate, and it does not have to be better: every piece of text
       * the plan draws that MATTERS to the extent has a rect or a polygon
       * behind it that is wider than it is. */
      const size = Number(a['font-size']) || 12;
      const track = Number(a['letter-spacing']) || 0;
      const chars = String(el.textContent || '').length;
      const w = chars * (size * 0.6 + track);
      if (!fin(n('x'), n('y'))) return null;
      const anchor = a['text-anchor'] === 'middle' ? w / 2
                   : a['text-anchor'] === 'end' ? w : 0;
      return {x0: n('x') - anchor, y0: n('y') - size,
              x1: n('x') - anchor + w, y1: n('y') + size * 0.28};
    }
    default: return null;      // defs, filters, gradients, patterns: no extent
  }
}

/* Only `translate(x, y)` — it is the only transform this page uses, and a
 * silently-ignored one would make the stack measure as a single plane. */
function shift(el) {
  const t = /translate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)\s*\)/.exec(el.attrs.transform || '');
  return t ? [Number(t[1]), Number(t[2])] : [0, 0];
}

const NO_EXTENT = new Set(['DEFS', 'FILTER', 'LINEARGRADIENT', 'RADIALGRADIENT',
                           'PATTERN', 'STOP', 'FEGAUSSIANBLUR']);

function treeBox(el) {
  if (!el || NO_EXTENT.has(el.tagName)) return null;
  if (el.attrs && el.attrs.display === 'none') return null;
  let b = ownBox(el);
  for (const kid of el.children || []) {
    const kb = treeBox(kid);
    if (!kb) continue;
    b = b ? {x0: Math.min(b.x0, kb.x0), y0: Math.min(b.y0, kb.y0),
             x1: Math.max(b.x1, kb.x1), y1: Math.max(b.y1, kb.y1)} : kb;
  }
  if (!b) return null;
  const [dx, dy] = shift(el);
  return {x0: b.x0 + dx, y0: b.y0 + dy, x1: b.x1 + dx, y1: b.y1 + dy};
}

/* The same box, as the browser's `getBBox()` reports it: local to the element,
 * so its OWN transform is not applied. */
function bboxOf(el) {
  let b = ownBox(el);
  for (const kid of el.children || []) {
    const kb = treeBox(kid);
    if (!kb) continue;
    b = b ? {x0: Math.min(b.x0, kb.x0), y0: Math.min(b.y0, kb.y0),
             x1: Math.max(b.x1, kb.x1), y1: Math.max(b.y1, kb.y1)} : kb;
  }
  return b ? {x: b.x0, y: b.y0, width: b.x1 - b.x0, height: b.y1 - b.y0}
           : {x: 0, y: 0, width: 0, height: 0};
}

function makeElement(name = 'stub') {
  const attrs = {};
  const el = {
    tagName: name.toUpperCase(),
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    /* Children are RECORDED. Without a tree there is no geometry, and without
     * geometry the fit can only be asserted against numbers this file made up
     * itself. `appendChild` on something already here MOVES it, exactly as the
     * DOM does — `drawSimpleFloor` re-appends its plate layer to lift it above
     * the equipment, and a stub that duplicated it would report every name
     * plate twice. */
    appendChild(c) {
      const at = el.children.indexOf(c);
      if (at !== -1) el.children.splice(at, 1);
      el.children.push(c);
      if (c && typeof c === 'object') { try { c.parentNode = el; } catch (_) {} }
      return c;
    },
    removeChild(c) {
      const at = el.children.indexOf(c);
      if (at !== -1) el.children.splice(at, 1);
      return c;
    },
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
    /* The real box, out of the real tree. See the block above. */
    getBBox() { return bboxOf(el); },
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
      /* `drawSimpleFloor` asks whether it has drawn anything yet. A proxy that
       * answered with a function said "yes" before the first draw. */
      if (prop === 'firstChild') return target.children[0] || null;
      if (prop === 'lastChild') return target.children[target.children.length - 1] || null;
      if (prop in target) return target[prop];
      if (typeof prop === 'symbol') return undefined;
      touched.add(String(prop));
      return () => makeElement();
    },
    set(target, prop, value) {
      /* Emptying an element empties it. `svg.innerHTML = ''` is how the plan
       * starts every redraw, and a stub that kept the old tree would measure
       * every level twice by the third poll. */
      if (prop === 'innerHTML') target.children.length = 0;
      target[prop] = value;
      return true;
    },
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
  /* `failure` formats a refused write for a person: the server's own sentence,
   * which statements of a multi-statement save landed, and — only when the
   * refusal is worth retrying — how long to wait. This page's local `failure()`
   * delegates to it, so a stub without it turns every refusal path into a
   * TypeError. Modelled on the real one in static/lem.js rather than stubbed to
   * a constant, because three of this file's own assertions read the sentence
   * that comes back. */
  LEM: {get: () => Promise.resolve(null), fresh: () => Promise.resolve(null),
        prefetch: noop, live: noop, bust: noop,
        failure: (response, body, fallback) => {
          body = body || {};
          let text = body.error || fallback || 'That did not save.';
          if (body.not_landed && body.not_landed.length) {
            if (body.landed && body.landed.length) {
              text += ' Saved: ' + body.landed.join(', ') + '.';
            }
            text += ' NOT saved: ' + body.not_landed.join(', ') + '.';
          }
          if (body.retryable && body.retry_after > 0) {
            text += ' Try again in ' + Math.ceil(body.retry_after) + 's.';
          } else if (body.retryable) {
            text += ' Try again shortly.';
          }
          return text;
        }},
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
     equipment off the edge of it. Measured against the drawing the page
     actually built, not against a box this file invented. */
  box(1400, 900);
  sandbox.PLAN_SIG = '';
  sandbox.drawSimpleFloor(false);
  const [x, y, vw, vh] = vb();
  const drawn = svg.getBBox();
  claim('…and still contains the whole drawing rather than cropping to fit',
        drawn.width > 1 && drawn.height > 1
        && x <= drawn.x && y <= drawn.y
        && x + vw >= drawn.x + drawn.width
        && y + vh >= drawn.y + drawn.height);
  stage.getBoundingClientRect = realRect;
}

/* ---- HOW MUCH OF THE CANVAS IS ACTUALLY THE FLOOR ---------------------
 *
 * The open defect this work inherited: the drawing is fitted with
 * `preserveAspectRatio="xMidYMid meet"`, which fits the more demanding axis
 * and centres the other, so a viewBox that is not the stage's shape gives the
 * difference away as empty canvas.
 *
 * Two different measurements, and confusing them is how a previous round
 * reported 86.5% while a reviewer measured 41.2% off the same screen:
 *
 *   DRAWING FILL   the whole drawing's box — decks, pipework, name plates and
 *                  all — against the canvas. This is what "the plan fills the
 *                  stage" means, and it is the one the aspect solve controls.
 *   EQUIPMENT FILL the union of the INSTRUMENTS' own boxes against the same
 *                  canvas. This is what an operator is actually looking at,
 *                  it is always far smaller, and it is the one that gets
 *                  quietly given away to margin, empty deck and plate gutter.
 *
 * The union is rasterised rather than summed: overlapping boxes summed would
 * report more than 100% and flatter the result. */
const walk = (el, out = []) => {
  if (!el) return out;
  out.push(el);
  for (const kid of el.children || []) walk(kid, out);
  return out;
};
const byClass = (root, cls) => walk(root).filter(e =>
  String((e.attrs && e.attrs.class) || '').split(/\s+/).includes(cls));

/* Absolute box, with every ancestor translate applied — `treeBox` on the
 * element itself does exactly that for its own subtree, but the stack's
 * offsets live on the PLANE above it, so walk the parents. */
function absBox(el) {
  const b = treeBox(el);
  if (!b) return null;
  let p = el.parentNode;
  while (p) { const [dx, dy] = shift(p);
              b.x0 += dx; b.x1 += dx; b.y0 += dy; b.y1 += dy; p = p.parentNode; }
  return b;
}

/* Coverage of a set of boxes over the viewBox, as a fraction of the canvas.
 * The canvas IS the viewBox once the box has been padded to the stage's
 * aspect — `meet` then scales it to fill, with nothing left over. */
function coverage(boxes, vbx, vby, vbw, vbh, cells = 600) {
  if (!(vbw > 0 && vbh > 0)) return 0;
  const grid = new Uint8Array(cells * cells);
  for (const b of boxes) {
    if (!b) continue;
    const c0 = Math.max(0, Math.floor((b.x0 - vbx) / vbw * cells));
    const c1 = Math.min(cells - 1, Math.ceil((b.x1 - vbx) / vbw * cells) - 1);
    const r0 = Math.max(0, Math.floor((b.y0 - vby) / vbh * cells));
    const r1 = Math.min(cells - 1, Math.ceil((b.y1 - vby) / vbh * cells) - 1);
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) grid[r * cells + c] = 1;
    }
  }
  let on = 0;
  for (let i = 0; i < grid.length; i++) on += grid[i];
  return on / (cells * cells);
}

/* Measured at three real stage shapes, because the defect was never at one
 * viewport: it was 53% at 1600px and 43% at 1280px, at EVERY level. */
/* THE REAL FLOOR, over the real ladder. The three-piece fixture above is
 * shaped for the level rules, not for a measurement: two pieces on one deck
 * measure the padding round an almost-empty floor, which is a number about
 * nothing. These are the six instruments the lab actually has, at the bays
 * they are actually saved on — including OptiMPP 2 and PAC Flash 2, which are
 * both saved on 4.1,0 — spread over the ladder the way a building is. */
const REAL_FLEET = [
  ['b2ce21612b3c', 'OptiMPP 1',   [2.05, 0],    'l1', 'GREEN'],
  ['2a49a1320ca1', 'OptiMPP 2',   [4.1, 0],     'l1', 'RED'],
  ['5fd04c0031f9', 'PAC Flash 1', [0, 0],       'l1', 'GREEN'],
  ['7e8304c31983', 'PAC Flash 2', [4.1, 0],     'l1', 'YELLOW'],
  ['844337a2ba08', 'Multitek NS', [4.1, 2.05],  'l2', 'RED'],
  ['300f71750e3e', 'Multitek S',  [2.05, 2.05], 'l2', 'GREEN'],
].map(([uid, title, pos, level_uid, status]) =>
  Object.assign({}, machine, {machine_uid: uid, title, pos, level_uid, status,
                              level_moved_at: '', level_moved_by: ''}));

const FILL = [];
const FLAT_FILL = [];

/* Measure one payload at three real stage shapes. The defect was never at one
 * viewport — 53% at 1600px and 43% at 1280px, on every level — so an average
 * would hide exactly the thing being looked for. */
async function measureFill(into, payload) {
  const stage = el('#stage');
  const realRect = stage.getBoundingClientRect;
  const svg = el('#floorSimple');
  PAYLOADS['/api/machines'] = payload;
  await sandbox.load();
  await settle();
  for (const [w, h] of [[1600, 1400], [1400, 900], [1100, 960]]) {
    stage.getBoundingClientRect = () => ({left: 0, top: 0, width: w, height: h,
                                          right: w, bottom: h, x: 0, y: 0});
    sandbox.PLAN_SIG = '';
    sandbox.drawSimpleFloor(false);
    const [vx, vy, vw, vh] = String(svg.getAttribute('viewBox') || '')
      .trim().split(/\s+/).map(Number);
    const all = svg.getBBox();
    const kit = byClass(svg, 'simple-machine').map(absBox);
    const plates = byClass(svg, 'unit').map(absBox);

    /* The plane the picker names, and the equipment standing on it. Reported
     * separately because "equipment against the whole canvas" is depressed by
     * how EMPTY the rest of the building is, which is a fact about the lab and
     * not about the drawing — a lab with two bare floors would score a good
     * stack badly. This one is a fit measure and nothing else. */
    const here = byClass(svg, 'lvlplane').filter(
      e => e.attrs['data-current'] === '1');
    const hereBox = here.length ? absBox(here[0]) : null;
    const hereKit = here.length ? byClass(here[0], 'simple-machine').map(absBox) : [];
    into.push({
      w, h,
      stage: w / h,
      viewBox: vw / vh,
      /* Per axis, which is the measurement the defect was stated in: `meet`
       * fits ONE axis and gives the whole of the other away, so a drawing
       * that fills both is a drawing with nothing left to letterbox. The area
       * ratio is the product of the two and reads worse than the fit is —
       * an even 18-unit margin costs 6% of each axis and 12% of the area. */
      fitX: all.width / vw,
      fitY: all.height / vh,
      drawing: (all.width * all.height) / (vw * vh),
      equipment: coverage(kit, vx, vy, vw, vh),
      withPlates: coverage(plates, vx, vy, vw, vh),
      onPlane: hereBox
        ? coverage(hereKit, hereBox.x0, hereBox.y0,
                   hereBox.x1 - hereBox.x0, hereBox.y1 - hereBox.y0) : 0,
      /* THE NUMBER AN OPERATOR FEELS: how much of the screen is taken up by
       * the equipment on the level they are reading. Not against the plane —
       * against the canvas, because that is what decides whether a status bar
       * is readable from across the room. */
      inView: coverage(hereKit, vx, vy, vw, vh),
      units: kit.length,
    });
  }
  stage.getBoundingClientRect = realRect;
}

if (!failed && typeof sandbox.drawSimpleFloor === 'function') {
  const svg = el('#floorSimple');
  const keptFleet = PAYLOADS['/api/machines'];

  /* A LAB WITH NO LEVELS. This is the live production state today and it is
   * also, exactly, the single deck this work replaced — one plane, the whole
   * fleet, no stack and no label. So it is both the "must still draw as it
   * does now" case and the honest BEFORE for every number below. */
  await measureFill(FLAT_FILL, Object.assign({}, FLEET, {
    machines: REAL_FLEET.map(m => Object.assign({}, m, {level_uid: ''})),
    levels: [], default_level: '', ground_level: ''}));

  /* THE SAME SIX PIECES, spread over the three-level ladder — so what is
   * measured here is the GROUND FLOOR's four, drawn on the whole stage. */
  await measureFill(FILL, Object.assign({}, FLEET, {machines: REAL_FLEET}));

  /* THE COLLISION, ON THE REAL FLOOR. OptiMPP 2 and PAC Flash 2 are both saved
   * on 4.1,0. Drawn where they are saved, one is exactly underneath the other
   * and has vanished from the building — which on a stack is worse than it was
   * on a single deck, because the operator has no level to switch to to find
   * it. Read off the drawing: six pieces of equipment, six distinct places. */
  const spots = byClass(svg, 'simple-machine').map(absBox)
    .map(b => `${Math.round(b.x0)},${Math.round(b.y0)}`);
  /* The four on the ground, which is the floor in view — and two of them are
   * OptiMPP 2 and PAC Flash 2, saved on the same bay. Four distinct places or
   * one of them has vanished under the other. */
  claim('two pieces of equipment saved on the SAME bay are both drawn, in '
        + 'different places', spots.length === 4 && new Set(spots).size === 4);

  /* NAME PLATES MAY NOT COVER EACH OTHER. The plate exists so an operator can
   * say WHICH instrument is red from across the room, and a plate with another
   * plate lying over half of it is worse than no plate at all — because it is
   * not obvious which ones are unreadable. The plates are drawn in a layer
   * above every instrument precisely so they are never hidden by a prism; that
   * is no help if they hide each other.
   *
   * Read off the drawn rectangles: a plate is a `rect` inside a `.unit` that
   * is not a `.simple-machine`. Touching corners are fine; overlapping by more
   * than a sliver is not. */
  const plateBoxes = byClass(svg, 'unit')
    .filter(e => !String(e.attrs.class || '').split(/\s+/).includes('simple-machine'))
    .map(absBox).filter(Boolean);
  let worst = 0;
  for (let i = 0; i < plateBoxes.length; i++) {
    for (let j = i + 1; j < plateBoxes.length; j++) {
      const a = plateBoxes[i], b = plateBoxes[j];
      const ox = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
      const oy = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
      if (ox <= 0 || oy <= 0) continue;
      const area = (a.x1 - a.x0) * (a.y1 - a.y0);
      worst = Math.max(worst, (ox * oy) / Math.max(1, area));
    }
  }
  console.log(`  ..   ${plateBoxes.length} name plates, worst overlap `
              + `${(worst * 100).toFixed(1)}%`);
  claim('no name plate is buried under another one', worst < 0.12);

  const pc = v => (v * 100).toFixed(1) + '%';
  const say = (what, rows) => rows.forEach(f => console.log(
    `  ..   ${what} ${f.w}x${f.h}: fit ${pc(f.fitX)}x${pc(f.fitY)} · equipment `
    + `${pc(f.equipment)} · in view ${pc(f.inView)} · of its plane `
    + `${pc(f.onPlane)} · ${f.units} drawn`));
  say('flat  ', FLAT_FILL);
  say('ground', FILL);

  const both = FLAT_FILL.concat(FILL);
  claim('the viewBox is the stage\'s shape at every viewport, with levels and '
        + 'without, so `meet` has nothing left to letterbox',
        both.every(f => Math.abs(f.viewBox / f.stage - 1) < 0.02));
  /* THE DRAWING FILLS THE AXIS IT CAN, AND THE OTHER ONE IS GEOMETRY.
   *
   * An earlier version claimed both axes above 95.5% and got there by SOLVING
   * THE CAMERA TILT per draw so the drawing came out the shape of the stage.
   * On our nearly-square stage that pinned the tilt to its ceiling — 68
   * degrees, sin 0.93 — and the depth axis stopped being foreshortened at all.
   * Ryan: "you have the 3/4ths angle but not the perspective, its top down at
   * an angle instead of isometric." The coverage was real and the drawing was
   * wrong, and every test here passed throughout, because they all measured
   * coverage and none measured the projection.
   *
   * With the tilt fixed at a true 2:1 isometric the drawing's aspect is fixed
   * too, so on a stage of any other shape ONE axis fills and the other cannot.
   * That band is the price of an isometric view and it is not a defect to be
   * tested away. What is still a defect — the original one — is failing to
   * fill the axis that CAN be filled: `meet` used to fit to the width and
   * throw the rest away while the drawing sat small in the middle. */
  claim('the drawing fills its more demanding axis to within its own gutter',
        both.every(f => Math.max(f.fitX, f.fitY) > 0.955));
  claim('…and the axis it cannot fill is the projection, not slack — the '
        + 'drawing is still as large as an isometric can be here',
        both.every(f => Math.min(f.fitX, f.fitY) > 0.5));

  /* THE EQUIPMENT, WHICH IS A DIFFERENT MEASUREMENT FROM THE DECK.
   *
   * Every threshold below is set from what the SINGLE DECK measures on the
   * same six instruments, sitting right above it in this file's output —
   * never from a number that seemed like it ought to be reachable. A stack of
   * three floors cannot draw each of them the size of one floor, and an
   * acceptance test that demanded it would make a correct drawing
   * unreportable. That mistake has its own heading in CLAUDE.md.
   *
   * `onPlane` is the FIT — how much of the plane the picker names is the
   * equipment standing on it. It does not care how empty the rest of the
   * building is, so it is the number that says whether the drawing wastes its
   * own space.
   *
   * `inView` is the same equipment against the whole CANVAS, and it is the
   * one an operator feels. It necessarily falls when three floors are drawn
   * where one was — the same instruments at roughly a third of the linear
   * size. Measured: 35% on a single deck, 5–7% across a three-level stack.
   * That is the price of seeing the whole building at once and it is stated
   * here rather than hidden. What it may NOT do is collapse. */
  claim('the equipment fills the floor rather than sitting in the middle of '
        + 'an empty deck', FILL.every(f => f.onPlane > 0.25));
  claim('…and it holds that density at every viewport, not on average',
        FILL.every(f => f.onPlane > 0.25) && FILL.length === 3);
  /* THE NUMBER THE STACK WAS OVERRULED OVER. Drawn as a stack of three, the
   * equipment on the floor being worked on covered 5.0–6.8% of the canvas
   * here and 15.2% of the stage in a real browser: specks. One floor at a
   * time is what buys it back, and this is the claim that fails the moment
   * canvas starts going to floors nobody is reading. */
  /* Re-set when the camera was fixed. A true isometric on a squarish stage
   * cannot reach what the flattened one did — measured 34.3% in a browser at
   * 68 degrees against 24.0% at 30 — because the drawing no longer stretches
   * to the stage's shape. The threshold is the WORST viewport measured on the
   * shipped projection with a little room under it, not a number that seemed
   * like it ought to be reachable; demanding the flattened figure would make a
   * correct drawing unreportable, which has its own heading in CLAUDE.md. */
  claim('…and the equipment on the floor in view owns a real share of the '
        + 'canvas, not a corner of it', FILL.every(f => f.equipment > 0.15));
  claim('…and the map draws exactly what stands on the level in view — all '
        + 'of it, and nothing from any other floor',
        FILL.every(f => f.units === 4));
  /* The single deck must still draw the way it always did: every piece on it,
   * and the whole canvas spent on the one floor. It is the live production
   * state, and it is also the bar the stack is measured against. */
  claim('a lab with no levels spends the whole canvas on its one floor',
        FLAT_FILL.every(f => f.units === REAL_FLEET.length
                             && f.onPlane > 0.35 && f.inView > 0.20));

  PAYLOADS['/api/machines'] = keptFleet;
  await sandbox.load();
  await settle();
  sandbox.setLevelView('l1');
}

/* ---- ONE FLOOR, AND IT IS THE ONE THE PICKER NAMES ----------------------
 *
 * The floor drew every level at once for a while — each as its own plane,
 * exploded up and to the right. It was legible as a BUILDING and illegible as
 * a MAP: measured in a real browser, the equipment on the floor being worked
 * on covered 15.2% of the stage, because two thirds of the canvas was spent
 * on floors whose instruments were too small to read. Ryan: "the levels take
 * way too much space, whats the point of having them inactive, just go back to
 * 1 floor visible and put a little UI element on the side to show what floor
 * out of what floor they are on."
 *
 * So: one deck, the whole stage, and the building said by the indicator beside
 * it. Every claim below is read off the drawing the page actually built — the
 * recorded element tree, its attributes and its real geometry. None of it is a
 * grep of the template. This repo has been burned three times by tests that
 * passed while the feature was gutted, and the mutation log in the report says
 * which of these catch which gutting. */
const planesOf = () => byClass(el('#floorSimple'), 'lvlplane');
const levelOfPlane = p => p.attrs['data-level'];
/* The equipment standing on the drawn floor, by name. `aria-label` is what the
 * plan announces an instrument as, and it starts with the title. */
const kitOn = p => byClass(p, 'simple-machine')
  .map(g => String(g.attrs['aria-label'] || '').split(',')[0]).sort();
/* Every string a person could read off the drawing. */
const wordsOn = p => walk(p).map(e => String(e.textContent || ''))
  .filter(Boolean).join(' | ');
/* The deck polygon, in the drawing's own coordinates. The deck is the FLOOR —
 * as against the plane's bounding box, which is made of whatever happens to be
 * standing on it. */
const deckPoly = p => {
  const d = byClass(p, 'deck')[0];
  if (!d) return null;
  const [dx, dy] = shift(p);
  return String(d.attrs.points || '').trim().split(/\s+/)
    .map(q => q.split(',').map(Number))
    .map(([x, y]) => [x + dx, y + dy]);
};
const inside = (pt, poly) => {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > pt[1]) !== (yj > pt[1])
        && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
};
/* How many pieces of equipment are drawn OFF the floor they stand on. A bay
 * that lands beside the concrete is an instrument the operator cannot find,
 * and it is what a mis-sized deck looks like from the outside. */
const adriftCount = planes => {
  let adrift = 0;
  for (const p of planes) {
    const poly = deckPoly(p);
    if (!poly) continue;
    for (const g of byClass(p, 'simple-machine')) {
      const b = absBox(g);
      if (!b) continue;
      // The front-centre of the bay: on the deck for anything standing on it,
      // and clear of the prisms that rise off the back of it.
      if (!inside([(b.x0 + b.x1) / 2, b.y1 - 2], poly)) adrift++;
    }
  }
  return adrift;
};
/* What the indicator says, as a person reads it. */
const navHtml = () => String(el('#levelNav').innerHTML || '');
const navRungs = () => navHtml().match(/class="rung[^"]*"/g) || [];

if (!failed && typeof sandbox.drawSimpleFloor === 'function') {
  const svg = el('#floorSimple');
  const stage = el('#stage');
  const realRect = stage.getBoundingClientRect;
  stage.getBoundingClientRect = () => ({left: 0, top: 0, width: 1400,
                                        height: 1100, right: 1400,
                                        bottom: 1100, x: 0, y: 0});
  const keptFleet = PAYLOADS['/api/machines'];
  PAYLOADS['/api/machines'] = Object.assign({}, FLEET, {machines: REAL_FLEET});
  await sandbox.load();
  await settle();
  sandbox.setLevelView('l1');

  let planes = planesOf();

  /* ONE FLOOR. Not three at a third of the size each. */
  claim('the map draws ONE floor, whatever the ladder holds',
        planes.length === 1);
  claim('…and it is the level the picker names',
        levelOfPlane(planes[0]) === 'l1');
  claim('…holding exactly the equipment placed on that level',
        kitOn(planes[0]).join()
          === 'OptiMPP 1,OptiMPP 2,PAC Flash 1,PAC Flash 2');
  /* THE MEZZANINE IS NOT DRAWN AT ALL. Not dimmed, not shrunk, not a pad — a
   * floor nobody is reading spends canvas the floor they ARE reading needs. */
  claim('…and nothing standing on any other floor is drawn anywhere',
        !/Multitek/.test(wordsOn(el('#floorSimple')))
        && byClass(svg, 'simple-machine').length === 4);

  /* DRAWN IN FULL. The whole argument for one floor at a time is that the
     instruments on it are legible: name plate, sub-status pills, status bar,
     and the trunk main that says where the data goes. */
  const plateCount = p =>
    byClass(p, 'unit').length - byClass(p, 'simple-machine').length;
  claim('every piece of equipment on it is named, not merely marked',
        plateCount(planes[0]) === 4);
  claim('…and the QC / PM / CAL pills are drawn on the floor in view',
        /QC/.test(wordsOn(planes[0])) && /PM/.test(wordsOn(planes[0]))
        && /CAL/.test(wordsOn(planes[0])));
  claim('…and the trunk main to LabCore is drawn',
        /LABCORE/.test(wordsOn(planes[0])));
  claim('…and every one of them stands on the deck rather than beside it',
        adriftCount(planes) === 0);

  /* THE PICK TARGET IS THE WHOLE BAY, AND IT IS A REAL TARGET.
   *
   * `.hit` is an invisible polygon over the bay, so a click between two prisms
   * of the same instrument still selects it. Two things can go wrong and both
   * look fine in a screenshot: the polygon can be dropped altogether, and it
   * can be drawn so small nobody can land on it — which is what the stack did
   * to the floors it was not emphasising. Measured against the viewBox, which
   * IS the canvas once it has been padded to the stage's aspect, so this is a
   * size on screen and not a size in the drawing's own units. */
  const vb = () => String(svg.getAttribute('viewBox') || '')
    .trim().split(/\s+/).map(Number);
  const hits = byClass(planes[0], 'hit').map(absBox).filter(Boolean);
  const [hvx, hvy, hvw, hvh] = vb();
  claim('every piece of equipment carries a pick target over its whole bay',
        hits.length === 4);
  claim('…covering the bay it stands in rather than a corner of it',
        hits.every((h, i) => {
          const p = byClass(planes[0], 'plinth').map(absBox)[i];
          return p && (h.x1 - h.x0) >= (p.x1 - p.x0) - 0.5
                   && (h.y1 - h.y0) >= (p.y1 - p.y0) - 0.5;
        }));
  /* Measured against the DECK, not the viewBox.
   *
   * The viewBox is padded out to the stage's shape, and with the camera fixed
   * at a true isometric that padding is a real band — so a viewBox-relative
   * threshold moves when the stage changes shape, which says nothing about
   * whether a finger can land on an instrument. The deck is the floor the
   * target sits on and it is the same drawing whatever the stage does. */
  const deckBox = byClass(planes[0], 'deck').map(absBox).filter(Boolean)[0]
    || {x0: hvx, y0: hvy, x1: hvx + hvw, y1: hvy + hvh};
  claim('…and big enough on screen that a person can land on it',
        coverage(hits, deckBox.x0, deckBox.y0,
                 deckBox.x1 - deckBox.x0, deckBox.y1 - deckBox.y0) > 0.20);

  /* SWITCHING FLOOR IS A NEW DRAWING, AND COSTS NOTHING. Redrawn from the
     payload the floor is already polling; there is no per-level fetch here and
     there must never be one. */
  const before = NET;
  sandbox.setLevelView('l2');
  await settle();
  claim('switching level fires NO request', NET === before);
  planes = planesOf();
  claim('…and the map is now the floor switched to',
        planes.length === 1 && levelOfPlane(planes[0]) === 'l2');
  claim('…drawn in full, with its own equipment named',
        kitOn(planes[0]).join() === 'Multitek NS,Multitek S'
        && plateCount(planes[0]) === 2);
  claim('…and the floor left behind is gone from the drawing entirely',
        !/OptiMPP|PAC Flash/.test(wordsOn(el('#floorSimple'))));

  /* AN EMPTY LEVEL IS A STATE, NOT A RENDERING FAILURE. It keeps a full-sized
     deck and says on the slab itself that nothing stands there, so it can
     never be read as a floor that failed to draw. */
  sandbox.setLevelView('l3');
  await settle();
  planes = planesOf();
  claim('an empty level still gets a deck of its own',
        planes.length === 1 && (deckPoly(planes[0]) || []).length === 4);
  claim('…and says on the floor itself that nothing stands on it',
        /no equipment on this level/.test(wordsOn(planes[0])));
  claim('…and the designed empty panel still comes up over the stage',
        el('#levelEmpty').hidden === false);

  /* ---- THE LITTLE UI ELEMENT ON THE SIDE ------------------------------
   *
   * It is the whole of what the stack used to say, and it says it in a
   * corner instead of in two thirds of the canvas: which floor this is, how
   * many there are, how much stands on each, and a way to any of them. */
  sandbox.setLevelView('l1');
  await settle();
  const rows = sandbox.levelNavRows();
  const pos = sandbox.levelNavPosition();
  claim('the indicator carries one rung per level in the ladder',
        rows.length === 3 && navRungs().length === 3);
  claim('…top of the building first, which is where the top of a building is',
        rows.map(r => r.uid).join() === 'l3,l2,l1');
  claim('…and says which floor of how many this is, counted from the ground',
        pos.at === 1 && pos.of === 3 && /1 of 3/.test(navHtml()));
  claim('…and says how much stands on each floor rather than only naming it',
        rows.find(r => r.uid === 'l1').n === 4
        && rows.find(r => r.uid === 'l2').n === 2
        && rows.find(r => r.uid === 'l3').n === 0);
  /* WHAT STATE IT IS IN, IN WORDS. A dot alone is nothing to a red-green
     operator or to a wall display with the saturation turned down. */
  claim('…and what state the worst of it is in, said rather than only coloured',
        /1 red/.test(rows.find(r => r.uid === 'l1').label)
        && /1 red/.test(rows.find(r => r.uid === 'l2').label));
  claim('…and an empty floor says it holds no equipment',
        /no equipment/.test(rows.find(r => r.uid === 'l3').label));

  /* A FLOOR YOU ARE NOT ON IS A WAY TO GET THERE, and it says where it goes. */
  claim('a floor you are not on is offered as a control that says where it goes',
        rows.filter(r => !r.here).every(r => /^Show /.test(r.label))
        && /Show Mezzanine/.test(navHtml()));
  claim('…rendered as a real button, reachable from the keyboard',
        (navHtml().match(/role="button"/g) || []).length === 2
        && (navHtml().match(/tabindex="0"/g) || []).length === 2);
  claim('the floor you are ON is not offered as a way to get to itself',
        rows.filter(r => r.here).length === 1
        && !/^Show /.test(rows.find(r => r.here).label)
        && /aria-current="true"/.test(navHtml()));
  /* And it is marked by more than colour — `aria-current` and a class the
     stylesheet fills the tread in for, not an amber word on its own. */
  claim('…and is marked as the current one by something other than colour',
        /class="rung on"/.test(navHtml()));

  /* THE CONTROL ACTUALLY WORKS, and costs nothing. `levelNavGo` is the whole
     body of the rung's click handler. */
  const before2 = NET;
  /* `LEVEL_VIEW` is a top-level `let`, so it is in the script's lexical scope
   * and not on the sandbox object. Ask the drawing and the indicator what they
   * settled on instead — which is the better question anyway. */
  const showing = () => (sandbox.levelNavRows().find(r => r.here) || {}).uid;
  sandbox.levelNavGo('l2');
  await settle();
  claim('pressing a rung goes to that floor', showing() === 'l2');
  claim('…and fires NO request doing it', NET === before2);
  claim('…and the map redraws to it',
        levelOfPlane(planesOf()[0]) === 'l2');
  claim('…and the indicator follows, without moving the rungs about',
        sandbox.levelNavPosition().at === 2
        && sandbox.levelNavRows().map(r => r.uid).join() === 'l3,l2,l1');
  /* Pressing the floor you are on is a no-op rather than a redraw. */
  sandbox.levelNavGo('l2');
  claim('pressing the floor you are already on does nothing',
        showing() === 'l2' && levelOfPlane(planesOf()[0]) === 'l2');

  /* IT READS AS ENGLISH. "Equipment" is uncountable, and this page has one
     noun for it. */
  readsWell('the level indicator', navHtml());

  sandbox.setLevelView('l1');
  stage.getBoundingClientRect = realRect;
  PAYLOADS['/api/machines'] = keptFleet;
  await sandbox.load();
  await settle();
  sandbox.setLevelView('l1');
}

/* ---- A LAB WITH NO LEVELS AT ALL --------------------------------------
 *
 * This is the live production state today: nobody has made a level, and the
 * floor has to draw exactly as it always has. One deck, the whole fleet, and
 * NOTHING that says levels are a thing this lab has not used — an indicator
 * reading "1 of 1" over a lab with no floors is an invitation to go looking
 * for the ones that do not exist. */
if (!failed && typeof sandbox.applyLevels === 'function') {
  const keptFleet = PAYLOADS['/api/machines'];
  PAYLOADS['/api/machines'] = Object.assign({}, FLEET, {
    machines: REAL_FLEET.map(m => Object.assign({}, m, {level_uid: ''})),
    levels: [], default_level: '', ground_level: ''});
  await sandbox.load();
  await settle();
  const planes = planesOf();
  claim('a flat lab draws ONE deck', planes.length === 1);
  claim('…holding the whole fleet',
        kitOn(planes[0]).length === REAL_FLEET.length);
  claim('…named after no level, because there is none',
        levelOfPlane(planes[0]) === '');
  const t = shift(planes[0]);
  claim('…and not lifted off the floor as though a level were under it',
        t[0] === 0 && t[1] === 0);
  claim('…and the picker is still hidden on a flat lab',
        el('#btnLevel').hidden === true);
  /* THE INDICATOR IS NOT THERE AT ALL. Not empty, not "1 of 1" — absent. */
  claim('…and the level indicator is not rendered at all',
        el('#levelNav').hidden === true && navHtml() === '');
  claim('…and it offers no rungs to a lab with no ladder',
        sandbox.levelNavRows().length === 0);
  /* And the hint does not offer a gesture there is nothing to use it on. */
  sandbox.paintTools();
  claim('…and the floor hint does not offer a level that cannot exist',
        !/another level|click a level/.test(String(el('#hint').textContent)));

  PAYLOADS['/api/machines'] = keptFleet;
  await sandbox.load();
  await settle();
  sandbox.setLevelView('l1');
}

/* ---- one level, which is neither six nor a flat lab -------------------- */
if (!failed && typeof sandbox.applyLevels === 'function') {
  const keptFleet = PAYLOADS['/api/machines'];
  PAYLOADS['/api/machines'] = Object.assign({}, FLEET, {
    machines: REAL_FLEET.map(m => Object.assign({}, m, {level_uid: 'l1'})),
    levels: [{uid: 'l1', name: 'Ground', rank: 1}],
    default_level: 'l1', ground_level: 'l1'});
  await sandbox.load();
  await settle();
  const planes = planesOf();
  claim('a lab with ONE level draws one deck, and it is that level',
        planes.length === 1 && levelOfPlane(planes[0]) === 'l1');
  claim('…with every piece of equipment on it',
        kitOn(planes[0]).length === REAL_FLEET.length);
  /* The indicator still names it — that is what a wall display with the tool
     row hidden has instead of the picker — but the single rung is not a
     control, because there is nowhere else to go. */
  claim('…and the indicator names it and says it is the only floor',
        sandbox.levelNavPosition().at === 1
        && sandbox.levelNavPosition().of === 1 && /1 of 1/.test(navHtml()));
  claim('…with no button on it, because there is nowhere else to go',
        !/role="button"/.test(navHtml()) && navRungs().length === 1);

  PAYLOADS['/api/machines'] = keptFleet;
  await sandbox.load();
  await settle();
  sandbox.setLevelView('l1');
}

/* ---- six levels: the ladder is data, and three is not a constant -------
 *
 * Level names are user-editable and a lab can have one or six. The sketch
 * happened to show three. A drawing that only fits at three is a drawing that
 * letterboxes the moment somebody adds a floor — which is the defect this work
 * began with, coming back through a different door. */
if (!failed && typeof sandbox.applyLevels === 'function') {
  const keptFleet = PAYLOADS['/api/machines'];
  const TALL = [];
  for (let i = 1; i <= 6; i++) {
    TALL.push({uid: 'f' + i, name: 'Floor ' + i, rank: i});
  }
  PAYLOADS['/api/machines'] = Object.assign({}, FLEET, {
    machines: REAL_FLEET.map((m, i) =>
      Object.assign({}, m, {level_uid: 'f' + (1 + (i % 6))})),
    levels: TALL, default_level: 'f1', ground_level: 'f1'});
  await sandbox.load();
  await settle();
  sandbox.setLevelView('f3');
  const stage = el('#stage');
  const realRect = stage.getBoundingClientRect;
  const svg = el('#floorSimple');
  let worstFit = 1;
  for (const [w, h] of [[1600, 1400], [1400, 900], [1100, 960]]) {
    stage.getBoundingClientRect = () => ({left: 0, top: 0, width: w, height: h,
                                          right: w, bottom: h, x: 0, y: 0});
    sandbox.PLAN_SIG = '';
    sandbox.drawSimpleFloor(false);
    const [, , vw, vh] = String(svg.getAttribute('viewBox') || '')
      .trim().split(/\s+/).map(Number);
    const bb = svg.getBBox();
    worstFit = Math.min(worstFit, bb.width / vw, bb.height / vh);
    /* Same correction as the fit block above: with the camera fixed at a true
     * isometric the drawing has ONE aspect, so it fills the more demanding
     * axis and the other is the projection. What must still hold at six
     * storeys is that the viewBox is the stage's shape — that is what stops
     * `meet` throwing an axis away — and that the drawing is not sitting small
     * inside it. */
    claim(`a six-storey lab fills a ${w}x${h} stage as far as an isometric can`,
          Math.max(bb.width / vw, bb.height / vh) > 0.955
          && Math.min(bb.width / vw, bb.height / vh) > 0.5
          && Math.abs((vw / vh) / (w / h) - 1) < 0.02);
  }
  stage.getBoundingClientRect = realRect;
  const planes = planesOf();
  claim('…and still draws exactly one floor, not six',
        planes.length === 1 && levelOfPlane(planes[0]) === 'f3');
  claim('…holding the one piece of equipment that stands on it',
        kitOn(planes[0]).length === 1);
  claim('…and it stands on the floor rather than beside it',
        adriftCount(planes) === 0);
  /* Six rungs, and the position counted from the ground — the case where a
     hard-coded three would show up. */
  claim('…and the indicator grows to six rungs',
        sandbox.levelNavRows().length === 6 && navRungs().length === 6);
  claim('…and says which of the six this is', /3 of 6/.test(navHtml()));
  claim('…with the other five offered as controls',
        (navHtml().match(/role="button"/g) || []).length === 5);
  claim('…and every piece of equipment is accounted for somewhere on it',
        sandbox.levelNavRows().reduce((a, r) => a + r.n, 0)
          === REAL_FLEET.length);
  console.log(`  ..   six levels: worst axis fill `
              + `${(worstFit * 100).toFixed(1)}%`);

  PAYLOADS['/api/machines'] = keptFleet;
  await sandbox.load();
  await settle();
  sandbox.setLevelView('l1');
}
/* ---- the QC standard states its own staleness window -------------------
 *
 * The window belongs to the MATERIAL — an ampoule opened this morning is not
 * good for a week — so `lem_qc_samples` carries it and the floor's standards
 * dialog is where anybody actually sets it. It used to be missing from that
 * dialog entirely, and because `QcSampleTest.from_dict` reads an absent key as
 * 0.0 (fall-through), editing ANY standard from the floor for ANY reason wiped
 * a window somebody had set, silently. Read off the row the dialog actually
 * builds, not out of the template. */
if (!failed && typeof sandbox.addTestRow === 'function') {
  const rows = () => (el('#sampleTests').children || [])
    .map(r => String(r.innerHTML || ''));

  el('#sampleTests').innerHTML = '';
  sandbox.addTestRow({name: 'Flash Point', value_col: 'Flash Point',
                      expected: 63.7, std_dev: 1.05, k: 2, units: '°C',
                      qc_expire_hours: 8});
  claim('a standard\'s test row offers a staleness window of its own',
        /class="c-win"/.test(rows()[0]));
  claim('…and a window that was set comes back in the box',
        /class="c-win"[^>]*value="8"/.test(rows()[0]));

  /* BLANK IS "USE THE DEFAULT". A literal 0 in a box labelled "Expires (h)"
   * reads as "expires immediately", which is the one thing it never means. */
  el('#sampleTests').innerHTML = '';
  sandbox.addTestRow({name: 'Cloud Point', value_col: 'Cloud Point',
                      expected: -14, std_dev: 2, k: 2, units: '°C',
                      qc_expire_hours: 0});
  claim('…while a standard with no opinion shows an EMPTY box, never a 0',
        /class="c-win"[^>]*value=""/.test(rows()[0])
        && !/class="c-win"[^>]*value="0"/.test(rows()[0]));
  /* And it says what blank will actually get, rather than leaving an
   * unexplained empty box. The number comes off `/api/qc-samples`, so it can
   * never disagree with `resolve_qc_window`. */
  claim('…and says what the effective default is',
        /class="c-win"[^>]*placeholder="24"/.test(rows()[0]));

  el('#sampleTests').innerHTML = '';
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
  /* The note said exactly "Newest first." when nothing was filtered. It now
   * also carries how much of the record is loaded and whether that is the
   * start of it, which is the complaint this work began from: a count with no
   * horizon reads as the whole history. What it must still NOT do when no
   * filter is applied is talk about a subset — "showing 3 of 40" under an
   * unfiltered list is a claim that something is being held back. */
  {
    const note = String(el('#histOrder').textContent).trim();
    claim('clearing the filters stops it talking about a subset',
          /^Newest first/.test(note) && !/showing/i.test(note), note);
    claim('…and it still says where the loaded record ends',
          /start of the record|not been loaded/.test(note), note);
  }
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


/* ==== JOB 1: THE SEARCH BOX =============================================
 *
 * `/api/search` answers with FOUR states and reports every cap it applied.
 * Both of those are load-bearing and both are invisible to a test that greps
 * the template: `state` appears in the source whether or not the four branches
 * are four, and "matched" appears whether or not the number is ever drawn.
 *
 * So every assertion below runs the shipped render function over a real
 * `lab_search.search()` answer and reads what came back.
 *
 * The fixtures are the actual shapes the route sends, taken off the running
 * dev server (`GET /api/search?q=…`) rather than written from the docstring:
 * an invented field on a fixture is how showTip's NaN survived a test suite
 * for a month. */

const HIT_EQUIP = {
  kind: 'equipment', key: 'equipment:pacflash1', id: 'pac-flash-1',
  label: 'PAC Flash 1', score: 8916, match: 'prefix', field: 'title',
  machine_uid: 'pac-flash-1', machine_title: 'PAC Flash 1',
  level_uid: 'l1', ts: '2026-08-26T15:20:00',
  machines: [{machine_uid: 'pac-flash-1', title: 'PAC Flash 1',
              level_uid: 'l1'}],
  machine_count: 1, machines_truncated: false, meta: {status: 'GREEN'},
};
/* A method runs on the whole fleet: eight named, the true count beside them.
 * `machines_truncated` is the cap that reads as "eight benches run this"
 * when it is dropped. */
const HIT_METHOD = {
  kind: 'method', key: 'method:flashpoint', id: 'Flash Point',
  label: 'Flash Point', score: 8216, match: 'prefix', field: 'name',
  machine_uid: 'pac-flash-1', machine_title: 'PAC Flash 1',
  level_uid: 'l1', ts: '',
  machines: [{machine_uid: 'pac-flash-1', title: 'PAC Flash 1', level_uid: 'l1'},
             {machine_uid: 'pac-flash-2', title: 'PAC Flash 2', level_uid: 'l1'},
             {machine_uid: 'pensky-1', title: 'Pensky-Martens 1', level_uid: 'l2'}],
  machine_count: 11, machines_truncated: true, meta: {},
};
const HIT_SAMPLE = {
  kind: 'sample', key: 'sample:l37176', id: 'L-37176', label: 'L-37176',
  score: 8516, match: 'prefix', field: 'name',
  machine_uid: 'gc-2', machine_title: 'GC 2', level_uid: 'l2',
  ts: '2026-08-26T15:22:36.953512',
  machines: [{machine_uid: 'gc-2', title: 'GC 2', level_uid: 'l2'}],
  machine_count: 1, machines_truncated: false,
  meta: {log_kind: 'run', test_name: 'Sulfur', value: '0.8392'},
};
const HIT_STANDARD = {
  kind: 'standard', key: 'standard:std1', id: 'STD-1', label: 'STD-1',
  score: 8316, match: 'prefix', field: 'name',
  machine_uid: 'optimpp-1', machine_title: 'OptiMPP 1', level_uid: 'l1',
  ts: '', machines: [{machine_uid: 'optimpp-1', title: 'OptiMPP 1',
                      level_uid: 'l1'}],
  machine_count: 2, machines_truncated: false, meta: {},
};
const HIT_LEVEL = {
  kind: 'level', key: 'level:l2', id: 'l2', label: 'Mezzanine',
  score: 8116, match: 'prefix', field: 'name',
  machine_uid: 'gc-2', machine_title: 'GC 2', level_uid: 'l2', ts: '',
  machines: [{machine_uid: 'gc-2', title: 'GC 2', level_uid: 'l2'}],
  machine_count: 1, machines_truncated: false, meta: {rank: 2},
};
const HIT_OPERATOR = {
  kind: 'operator', key: 'operator:dana', id: 'dana', label: 'dana',
  score: 8066, match: 'prefix', field: 'name',
  machine_uid: 'gc-1', machine_title: 'GC 1', level_uid: 'l1',
  ts: '2026-08-26T14:00:00',
  machines: [{machine_uid: 'gc-1', title: 'GC 1', level_uid: 'l1'}],
  machine_count: 1, machines_truncated: false, meta: {},
};

const CORPUS_WHOLE = {rows: 385, truncated: false, partial: false,
                      stale: false, refreshed_at: '2026-08-26T16:14:38'};
/* The corpus is capped at its newest SEARCH_CORPUS_ROWS records. At the
 * ceiling "not found" means "not in the last 20 000", which is a different
 * sentence and the one an assessor has to be given. */
const CORPUS_CLIPPED = {rows: 20000, truncated: true, partial: false,
                        stale: false, refreshed_at: '2026-08-26T16:14:38'};
const CORPUS_PARTIAL = {rows: null, truncated: false, partial: true,
                        stale: false, refreshed_at: ''};
const CORPUS_STALE = {rows: 385, truncated: false, partial: false,
                      stale: true, refreshed_at: '2026-08-26T09:00:00'};

const answerBase = over => Object.assign({
  query: '', normalised: '', query_truncated: false, state: 'idle',
  results: [], shown: 0, matched: 0, truncated: false, limit: 25,
  per_kind_limit: 10, min_query_chars: 2, max_query_tokens: 8,
  query_tokens_capped: false, counts: {}, kinds: [], searched: 0,
  indexed: {}, warming: false, age_seconds: 2, corpus: CORPUS_WHOLE,
}, over || {});

const A_IDLE = answerBase({});
const A_SHORT = answerBase({query: 'f', normalised: 'f', state: 'short'});
const A_NO_MATCH = answerBase({query: 'zzqqxx', normalised: 'zzqqxx',
                               state: 'no_match', searched: 115,
                               indexed: {equipment: 13, sample: 85, method: 8,
                                         standard: 2, level: 3, operator: 4}});
const A_OK = answerBase({
  query: 'l-37', normalised: 'l37', state: 'ok',
  results: [HIT_SAMPLE, HIT_EQUIP, HIT_STANDARD, HIT_METHOD, HIT_LEVEL,
            HIT_OPERATOR],
  shown: 6, matched: 84, truncated: true, searched: 115,
  counts: {sample: {matched: 79, shown: 1}, equipment: {matched: 1, shown: 1},
           standard: {matched: 1, shown: 1}, method: {matched: 1, shown: 1},
           level: {matched: 1, shown: 1}, operator: {matched: 1, shown: 1}},
  kinds: ['sample', 'equipment', 'standard', 'method', 'level', 'operator'],
});

if (!failed && typeof sandbox.searchPanelHtml !== 'function') {
  failed = true;
  console.log('FAIL: searchPanelHtml() is missing — the search box draws '
              + 'nothing, and no test below can judge what it says');
}

/* ---- THE FOUR STATES ARE FOUR ------------------------------------------
 *
 * `idle` and `no_match` are the pair that matters. A box that draws "No
 * results" at rest is telling everyone who walks past that the lab is empty,
 * and the payload was shaped with four states specifically to stop it. The
 * assertion is not "the word appears somewhere" — it is that the four
 * renderings are four different things, which is the only form a collapse
 * cannot survive. */
if (!failed) {
  const drawn = {
    idle: String(sandbox.searchPanelHtml(A_IDLE) || ''),
    short: String(sandbox.searchPanelHtml(A_SHORT) || ''),
    no_match: String(sandbox.searchPanelHtml(A_NO_MATCH) || ''),
    ok: String(sandbox.searchPanelHtml(A_OK) || ''),
  };
  const words = html => String(html).replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ').trim().toLowerCase();

  const names = Object.keys(drawn);
  let allDistinct = true;
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      if (words(drawn[names[i]]) === words(drawn[names[j]])) {
        allDistinct = false;
        console.log(`       ${names[i]} and ${names[j]} read identically`);
      }
    }
  }
  claim('the four search states draw four different answers', allDistinct);

  /* NOTHING TYPED IS NOT NOTHING FOUND. */
  claim('idle never says nothing matched',
        !/no results|nothing match|no match|not found|nothing found/
          .test(words(drawn.idle)));
  claim('…and idle still says what can be searched for',
        /lab id|equipment|method|standard|level|person/.test(words(drawn.idle)));
  claim('short asks for more characters rather than reporting a miss',
        /keep typing|more character|at least/.test(words(drawn.short))
        && !/no results|nothing match/.test(words(drawn.short)));
  claim('…and short says how many characters it needs',
        words(drawn.short).includes(String(A_SHORT.min_query_chars)));
  claim('no_match says nothing matched, and names what was typed',
        /nothing|no match|no result/.test(words(drawn.no_match))
        && words(drawn.no_match).includes('zzqqxx'));
  claim('ok draws the hits it was given',
        drawn.ok.includes('L-37176') && drawn.ok.includes('PAC Flash 1')
        && drawn.ok.includes('Flash Point') && drawn.ok.includes('Mezzanine'));
  /* Six kinds, and each one has to be nameable on screen — "L-37176" and
   * "Mezzanine" in one undifferentiated list is not an answer. */
  claim('…and says what kind each hit is',
        ['sample', 'equipment', 'standard', 'method', 'level', 'operator']
          .every(k => words(drawn.ok).includes(k)));
}

/* ---- EVERY CAP IS REPORTED, NEVER SILENT -------------------------------
 *
 * "Showing 25" over 313 matches, drawn without saying so, reads to an
 * assessor as "the lab has 25 of those". Each of these numbers is one the
 * answer carries and the screen has to spend. */
if (!failed) {
  const ok = String(sandbox.searchPanelHtml(A_OK) || '');
  const flat = ok.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

  claim('a truncated answer prints BOTH what is shown and what matched',
        flat.includes('6') && flat.includes('84')
        && /\b6\b[^.]*\b84\b/.test(flat));
  claim('…and a hit capping its instrument list prints the true count',
        /\b11\b/.test(flat) && /3\b/.test(flat));

  /* An uncapped answer must NOT invent a truncation sentence — a caveat that
   * is always on is a caveat nobody reads. */
  const whole = answerBase({query: 'flash', normalised: 'flash', state: 'ok',
                            results: [HIT_EQUIP], shown: 1, matched: 1,
                            truncated: false, kinds: ['equipment'],
                            counts: {equipment: {matched: 1, shown: 1}}});
  const wholeFlat = String(sandbox.searchPanelHtml(whole) || '')
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  claim('an uncapped answer does not claim to be capped',
        !/showing \d+ of \d+/i.test(wholeFlat));

  /* THE QUERY-TOKEN CAP CHANGES THE ANSWER. Past MAX_QUERY_TOKENS the tokens
   * tier is refused rather than sampled, so one more correct word can turn a
   * hit into "no results" — a cap that is reported nowhere else. */
  const capped = answerBase({
    query: 'a b c d e f g h i', normalised: 'a b c d e f g h i',
    state: 'no_match', query_tokens_capped: true, searched: 115});
  const cappedFlat = String(sandbox.searchPanelHtml(capped) || '')
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').toLowerCase();
  claim('a query past the word cap says so, because the cap changed the answer',
        /word/.test(cappedFlat) && cappedFlat.includes('8'));

  const long = answerBase({query: 'x'.repeat(128), normalised: 'x'.repeat(128),
                           state: 'no_match', query_truncated: true});
  claim('a query cut to fit says it was cut',
        /cut|shorten|truncat|first 128|too long/i.test(
          String(sandbox.searchPanelHtml(long) || '')));
}

/* ---- "NOT FOUND" AND "NOT IN WHAT I CAN SEE" ARE DIFFERENT SENTENCES ----
 *
 * `corpus.truncated` / `partial` / `stale` each mean the search looked at
 * less than the lab holds. A flat denial over a clipped corpus is the single
 * most expensive wrong answer this box can give. */
if (!failed) {
  const say = corpus => String(sandbox.searchPanelHtml(
    answerBase({query: 'l-99999', normalised: 'l99999', state: 'no_match',
                searched: 115, corpus})) || '')
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').toLowerCase();

  const whole = say(CORPUS_WHOLE);
  for (const [what, corpus] of [['capped', CORPUS_CLIPPED],
                                ['not fully read yet', CORPUS_PARTIAL],
                                ['stale', CORPUS_STALE]]) {
    const clipped = say(corpus);
    claim(`a ${what} corpus does not read the same as a whole one`,
          clipped !== whole);
    claim(`…and says the miss is about what could be SEEN (${what})`,
          /can see|could see|not in what|searched only|older|not been read|could not be refreshed|last good/
            .test(clipped));
  }
  claim('a whole corpus does not manufacture a caveat',
        !/can see|older records|last good copy/.test(whole));
  /* The cap's own number, because "some records are missing" is not a fact
   * anybody can act on and this one is in the payload. */
  claim('a capped corpus prints how many records it did search',
        say(CORPUS_CLIPPED).includes('20000')
        || say(CORPUS_CLIPPED).includes('20,000'));
}

/* ---- A HIT NAVIGATES ---------------------------------------------------
 *
 * "Clicking equipment opens it" cannot be tested by looking for an onclick in
 * the markup. Drive `openSearchHit` and read what the page did. */
if (!failed && typeof sandbox.openSearchHit === 'function') {
  const realFetch = sandbox.fetch;
  const quiet = () => Promise.resolve({ok: true, status: 200,
    json: () => Promise.resolve({authenticated: true, user: 'ryan',
                                 machines: [], events: [], series: [],
                                 entries: [], actions: [], documents: []})});

  /* Equipment on the level NOT being looked at: opening it has to take the
   * plan there too, or the record names a piece of equipment that is not on
   * screen. */
  /* `selected` is a top-level `let`, so it is NOT a property of the vm
   * context and cannot be read from here. That is the better test anyway:
   * ask what the record rail actually DREW. */
  const openRecord = () => String(el('#railL').innerHTML || '');
  sandbox.fetch = quiet;
  await sandbox.setLevelView('l1');
  await sandbox.openSearchHit(Object.assign({}, HIT_EQUIP, {
    id: 'gc-2', label: 'GC 2', machine_uid: 'gc-2', level_uid: 'l2',
    machines: [{machine_uid: 'gc-2', title: 'GC 2', level_uid: 'l2'}]}));
  await settle();
  claim('clicking an equipment hit opens that equipment record',
        /<h2 class="sign">GC 2<\/h2>/.test(openRecord()));
  claim('…and takes the plan to the level it stands on',
        String(el('#levelName').textContent) === 'Mezzanine');

  /* A sample is a Lab ID that RAN somewhere. "Somewhere useful" is the record
   * of the instrument that ran it, with the history narrowed to that Lab ID —
   * anything less leaves the person to scroll two hundred rows. */
  const asked = [];
  sandbox.fetch = (...args) => { asked.push(String(args[0])); return quiet(); };
  await sandbox.openSearchHit(HIT_SAMPLE);
  await settle();
  claim('a sample hit opens the equipment that ran it',
        /<h2 class="sign">GC 2<\/h2>/.test(openRecord()));
  claim('…on its history, narrowed to that Lab ID',
        String(el('#histQ').value || '').includes('L-37176')
        && asked.some(u => /\/history\?limit=/.test(u)));

  /* A level goes to the level. Nothing is fetched: the ladder rides the
   * payload the floor already polls, and a search result that costs a read is
   * a read behind a gesture people make all day. */
  const before = asked.length;
  await sandbox.openSearchHit(HIT_LEVEL);
  await settle();
  claim('a level hit switches the plan to that level',
        String(el('#levelName').textContent) === 'Mezzanine');
  claim('…and costs no request at all', asked.length === before);

  /* A method and a standard both lead to an instrument that runs it, on the
   * tab where its band is. */
  await sandbox.openSearchHit(Object.assign({}, HIT_METHOD,
    {machine_uid: 'gc-1', machines: [{machine_uid: 'gc-1', title: 'GC 1',
                                      level_uid: 'l1'}]}));
  await settle();
  claim('a method hit opens equipment that runs it',
        /<h2 class="sign">GC 1<\/h2>/.test(openRecord()));
  await sandbox.openSearchHit(Object.assign({}, HIT_STANDARD,
    {machine_uid: 'gc-2', machines: [{machine_uid: 'gc-2', title: 'GC 2',
                                      level_uid: 'l2'}]}));
  await settle();
  claim('a standard hit opens equipment it is assigned to',
        /<h2 class="sign">GC 2<\/h2>/.test(openRecord()));

  /* A hit naming equipment this floor no longer holds must say so rather than
   * silently doing nothing — a retired bench is still in the log. */
  const before5 = openRecord();
  await sandbox.openSearchHit(Object.assign({}, HIT_EQUIP,
    {machine_uid: 'gone-1', id: 'gone-1', label: 'Retired GC',
     machines: [{machine_uid: 'gone-1', title: 'Retired GC', level_uid: ''}]}));
  await settle();
  claim('a hit for equipment that is no longer on the floor says so',
        openRecord() === before5
        && /no longer|not on|retired|could not/i.test(
             String(el('#findPanel').innerHTML || '')));
  sandbox.fetch = realFetch;
}

/* ---- IT DOES NOT FIRE A REQUEST PER KEYSTROKE --------------------------
 *
 * The route costs LabCore nothing, and this is where that gets spent anyway:
 * one fetch per character, per viewer, forever. The harness's own setTimeout
 * is a no-op, so a REAL clock is swapped in for this block — a debounce
 * tested against a timer that never fires proves nothing. */
if (!failed && typeof sandbox.searchTyped === 'function') {
  const realFetch = sandbox.fetch, realSet = sandbox.setTimeout,
        realClear = sandbox.clearTimeout;
  let pending = new Map(), nextId = 1;
  sandbox.setTimeout = (fn, ms) => { const id = nextId++;
                                     pending.set(id, {fn, ms}); return id; };
  sandbox.clearTimeout = id => { pending.delete(id); };
  let hits = 0;
  sandbox.fetch = url => {
    hits++;
    return Promise.resolve({ok: true, status: 200,
      json: () => Promise.resolve(A_OK)});
  };

  const box = el('#findQ');
  for (const typed of ['l', 'l-', 'l-3', 'l-37', 'l-371', 'l-3717']) {
    box.value = typed;
    sandbox.searchTyped();
  }
  claim('typing six characters fires no request on its own', hits === 0);
  claim('…because exactly one timer is left standing, not six',
        pending.size === 1);
  const [only] = [...pending.values()];
  claim('…and it waits long enough to be slower than a typist',
        only && only.ms >= 120);
  await only.fn();
  await settle();
  claim('…then one request goes out, once', hits === 1);

  /* Re-typing the same query is not a new question. */
  hits = 0; pending.clear();
  box.value = 'l-3717';
  sandbox.searchTyped();
  for (const {fn} of [...pending.values()]) await fn();
  await settle();
  claim('asking the identical question again does not ask the server again',
        hits === 0);

  /* An emptied box is `idle` and asks nothing at all. */
  hits = 0; pending.clear();
  box.value = '';
  sandbox.searchTyped();
  for (const {fn} of [...pending.values()]) await fn();
  await settle();
  claim('emptying the box costs no request', hits === 0);
  claim('…and returns the box to idle, not to "no results"',
        !/no results|nothing match/i.test(String(el('#findPanel').innerHTML || '')));

  sandbox.fetch = realFetch;
  sandbox.setTimeout = realSet;
  sandbox.clearTimeout = realClear;
}

/* ---- ARROWS, ENTER, ESCAPE --------------------------------------------- */
if (!failed && typeof sandbox.searchKey === 'function') {
  const key = k => {
    let stopped = false, prevented = false;
    sandbox.searchKey({key: k, preventDefault: () => { prevented = true; },
                       stopPropagation: () => { stopped = true; }});
    return {stopped, prevented};
  };
  sandbox.searchShow(A_OK);
  claim('the results list opens with nothing pre-selected',
        sandbox.searchCursor() === -1);
  key('ArrowDown');
  claim('down arrow walks onto the first hit', sandbox.searchCursor() === 0);
  key('ArrowDown');
  claim('…and on to the second', sandbox.searchCursor() === 1);
  key('ArrowUp'); key('ArrowUp');
  claim('up arrow walks back, and stops at the top rather than wrapping past it',
        sandbox.searchCursor() === -1 || sandbox.searchCursor() === 0);
  /* The active row has to be SAID, not merely styled — the box is a combobox
   * and a screen reader reads aria-activedescendant, not a CSS class. */
  key('ArrowDown');
  claim('the active hit is named for assistive technology',
        String(el('#findQ').getAttribute('aria-activedescendant') || '')
          .length > 0);
  const esc = key('Escape');
  claim('escape closes the list', el('#findPanel').hidden === true);
  claim('…and is swallowed, so it does not also close the open record',
        esc.stopped === true);
}

/* ==== JOB 2: THE EXPANDED EQUIPMENT RECORD ==============================
 *
 * Two payloads, both live on the dev server, neither of them drawn before
 * this. Every number below came off `GET /api/machines/optimpp-1/qc-trend`
 * and `.../status-timeline`. */

const SERIES = {
  test_name: 'Cloud Point', sample_id: 'STD-1',
  points: [
    {ts: '2026-07-30T14:05:36', value: -14.2, in_spec: true},
    {ts: '2026-08-02T14:06:36', value: -14.1, in_spec: true},
    {ts: '2026-08-08T09:12:36', value: -13.6, in_spec: true},
    {ts: '2026-08-14T11:02:36', value: -14.9, in_spec: true},
    {ts: '2026-08-20T16:41:36', value: -13.2, in_spec: true},
    {ts: '2026-08-26T13:34:36', value: -16.8, in_spec: false},
  ],
  runs: 6, failures: 1, unjudged: 0,
  low: -16.0, high: -12.0, expected: -14.0,
  pass_band: {low: -16.0, high: -12.0, expected: -14.0},
  observed: {
    mean: -14.05, s: 0.7078920193865103, n: 28, df: 27, self_fitted: true,
    zones: {'1s': {low: -14.75789201938651, high: -13.342107980613491},
            '2s': {low: -15.465784038773021, high: -12.63421596122698},
            '3s': {low: -16.173676058159533, high: -11.92632394184047}},
  },
  zones_within_band: false,
  self_fitted: true, in_control: false,
  violations: [{rule: '1_3s', indices: [5], side: 'below', provisional: true,
                message: 'Run 28 (-16.8) is beyond the lower 3s control limit '
                  + '(-16.1737). Hold every result since the last good check '
                  + 'and investigate before this instrument reports again. '
                  + 'PROVISIONAL: these limits were computed from the same '
                  + 'results they are judging, so this instrument has no '
                  + 'qualification limits to be out of control against. '
                  + 'Confirm against fixed limits before acting on it.'}],
  firm_violations: 0,
  spread_basis: 'intermediate',
  coverage: {
    basis: 'intermediate',
    caveat: '28 results from 3 analysts over 28 calendar days against 3 '
          + 'calibrations: this spread supports within-laboratory '
          + 'reproducibility, u(Rw).',
    n: 28, operators: ['dana', 'ryan', 'sam'], n_operators: 3,
    n_unknown_operator: 0, n_days: 28, n_undated: 0,
    calibrations: ['2026-06-02', '2026-07-14', '2026-08-11'],
    n_calibrations: 3, n_unknown_calibration: 0,
    supports_repeatability: false, supports_reproducibility: true,
  },
};

/* The same instrument on a thin record: one analyst, one day, one
 * calibration. The module says so in its own sentence, and the chart may not
 * upgrade it. */
const SERIES_THIN = Object.assign({}, SERIES, {
  test_name: 'Pour Point',
  coverage: Object.assign({}, SERIES.coverage, {
    caveat: '6 results from 1 analyst over 1 calendar day against 1 '
          + 'calibration: this spread is repeatability only, u(r) — it does '
          + 'not span analysts, days or calibrations, so it cannot be called '
          + 'within-laboratory reproducibility.',
    n_operators: 1, n_days: 1, n_calibrations: 3 - 2,
    supports_repeatability: true, supports_reproducibility: false,
    basis: 'repeatability'}),
  spread_basis: 'repeatability',
});

if (!failed && typeof sandbox.trendSeriesHtml !== 'function') {
  failed = true;
  console.log('FAIL: trendSeriesHtml() is missing — the control chart cannot '
              + 'be judged on what it draws');
}

/* ---- THE ZONES AND THE PASS BAND ARE DIFFERENT QUANTITIES --------------
 *
 * The band is the STANDARD's control limit — the same band judges every bench
 * running that standard, and it says nothing about this instrument. The zones
 * are +/-1s/2s/3s from THESE results and move as the instrument moves. A wide
 * certificate over a drifting instrument gives narrow zones inside a wide
 * band: in control of nothing, passing everything. Drawing one and labelling
 * it the other says the opposite of what the process is doing. */
if (!failed) {
  const html = String(sandbox.trendSeriesHtml(SERIES) || '');
  const flat = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  const low = flat.toLowerCase();

  claim('the chart draws the standard\'s pass band and names it as the band',
        /pass band|control limit of the standard|the standard/.test(low));
  claim('…and draws the Shewhart zones and names them as zones',
        /zone/.test(low) && /1s/.test(flat) && /2s/.test(flat)
        && /3s/.test(flat));
  /* THE NUMBERS, not only the words. Swapping the two geometries leaves both
   * labels in place and both sentences true, so the only thing that catches it
   * is the value each line is actually drawn at — taken off the payload here
   * rather than typed, so this cannot drift from the fixture.
   *
   * `includes('-16') && includes('-12')` was NOT enough and a mutation proved
   * it: with the band drawn at the 3s zone (-16.17…-11.93) both substrings
   * were still somewhere in the markup, one of them inside a zone label. */
  const bandLabels = [SERIES.pass_band.low, SERIES.pass_band.high,
                      SERIES.pass_band.expected].map(v => v.toFixed(1));
  claim('…and the band is drawn at the band\u2019s own numbers, exactly',
        bandLabels.every(t => html.includes(t)));
  claim('…and the zones at the OBSERVED mean and spread, which are not those',
        /-14\.7[0-9]/.test(html) && /-16\.1[0-9]/.test(html)
        && /-13\.3[0-9]/.test(html));
  claim('…and the two are told apart by more than their labels',
        sandbox.trendZoneStyle && sandbox.trendBandStyle
        && sandbox.trendZoneStyle() !== sandbox.trendBandStyle());
  /* The observed mean is the zones' centre and is NOT the target. Both lines
   * are on this chart, and a reader has to be able to say which is which. */
  claim('the target line off the certificate is drawn and labelled',
        /target/.test(low) && html.includes('-14.0'));
  claim('…and the observed mean is drawn beside it, as a different thing',
        /mean/.test(low) && /-14\.0[0-9]/.test(html));
  claim('the days axis the whiteboard asked for is labelled',
        /day/.test(low));

  /* `zones_within_band: false` is the comparison already made for us. */
  claim('the chart says whether the zones fall inside the band',
        /wider than|outside the band|beyond the band|not inside|exceed/
          .test(low));
}

/* ---- A SELF-FITTED CHART MUST SAY SO -----------------------------------
 *
 * With no qualification limits the analysis judges the points against limits
 * computed from those same points, and every finding comes back
 * `provisional`. A provisional alarm presented as fact is worse than no
 * alarm at all. */
if (!failed) {
  const html = String(sandbox.trendSeriesHtml(SERIES) || '');
  const low = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').toLowerCase();
  /* THE PAGE'S OWN WORDS, with the module's quoted sentence lifted out.
   *
   * `qc_series`' violation message already contains "PROVISIONAL: these limits
   * were computed from the same results they are judging" — so with the whole
   * markup as the haystack, DELETING the chart's own warning left both of
   * these passing. A mutation proved it. The page has to say it itself,
   * because a chart with no violations to quote is exactly the case where the
   * quote is absent and the warning still has to be there. */
  const own = html.split(SERIES.violations[0].message).join(' ')
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').toLowerCase();
  claim('a self-fitted chart says IN ITS OWN WORDS that its limits came from '
        + 'these same results',
        /self-fitted|fitted to|computed from the same|these same results|no qualification/
          .test(own));
  claim('…and marks the finding provisional, in its own words, rather than '
        + 'stating it as fact', /provisional/.test(own));
  /* And with nothing to quote at all: no violations, still self-fitted. */
  const quiet = String(sandbox.trendSeriesHtml(
    Object.assign({}, SERIES, {violations: [], in_control: true})) || '')
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').toLowerCase();
  claim('…and says it even when there is no violation to quote it inside of',
        /provisional/.test(quiet)
        && /computed from the same|these same results|self-fitted|no qualification/
             .test(quiet));
  claim('…and the violation the module found is actually drawn',
        /1_3s|3s control limit|beyond the lower/.test(low));

  /* And when limits ARE fixed, the caveat must go — a warning that is always
   * on is a warning nobody reads, and it would understate a real alarm. */
  const firm = Object.assign({}, SERIES, {
    self_fitted: false, firm_violations: 1,
    observed: Object.assign({}, SERIES.observed, {self_fitted: false}),
    violations: [Object.assign({}, SERIES.violations[0],
                               {provisional: false,
                                message: 'Run 28 (-16.8) is beyond the lower '
                                  + '3s control limit (-16.1737).'})]});
  const firmLow = String(sandbox.trendSeriesHtml(firm) || '')
    .replace(/<[^>]*>/g, ' ').toLowerCase();
  claim('a chart with fixed limits does not call itself provisional',
        !/provisional|self-fitted/.test(firmLow));
}

/* ---- THE COVERAGE CAVEAT IS A SENTENCE THE MODULE WROTE ----------------
 *
 * `coverage.caveat()` states whether the spread supports u(Rw) or only
 * repeatability, and why. It is rendered VERBATIM. A paraphrase is a second
 * copy of an uncertainty claim, drifting from the one the module makes — and
 * the whole reason `qc_series` writes the sentence rather than the flags. */
if (!failed) {
  const html = String(sandbox.trendSeriesHtml(SERIES) || '');
  claim('the coverage caveat is rendered word for word',
        html.includes(SERIES.coverage.caveat));
  const thin = String(sandbox.trendSeriesHtml(SERIES_THIN) || '');
  claim('…and so is the thin one, which claims the opposite',
        thin.includes(SERIES_THIN.coverage.caveat));
  /* The dangerous direction: claiming reproducibility the payload denies. */
  const thinFlat = thin.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  const claimsRw = thinFlat.replace(SERIES_THIN.coverage.caveat, ' ');
  claim('a repeatability-only spread is never presented as u(Rw)',
        !/u\(Rw\)|within-laboratory reproducibility/i.test(claimsRw));
  claim('…and it says which basis the spread actually is',
        /repeatability/i.test(thinFlat));
}

/* ---- THE GUTTER ---------------------------------------------------------
 *
 * Ryan's whiteboard: the events list with a colour band down its side, and
 * the QC readings drawn as the transitions BETWEEN the states — GREEN above,
 * an arrow into `QC AO25 · 7.8 C`, YELLOW below. */

const TIMELINE = {
  machine_uid: 'optimpp-1',
  qc_expire_hours: 24.0, qc_expire_source: 'default', qc_expire_from: '',
  source: 'snapshot', snapshot_age_seconds: 4.5,
  complete: false, covers_from: '2026-08-25T19:09:36',
  events: [
    {machine_uid: 'optimpp-1', ts: '2026-08-26T15:05:36', kind: 'run',
     lab_id: 'L-37508', test_name: 'Cloud Point', value: '0.8326', qc: false,
     status: 'RED', reason: 'QC Out of Spec',
     status_since: '2026-08-26T12:31:36'},
    {machine_uid: 'optimpp-1', ts: '2026-08-26T14:34:36', kind: 'run',
     lab_id: 'L-37328', test_name: 'Cloud Point', value: '0.8564', qc: false,
     status: 'RED', reason: 'QC Out of Spec',
     status_since: '2026-08-26T12:31:36'},
    {machine_uid: 'optimpp-1', ts: '2026-08-26T13:34:36', kind: 'qc',
     lab_id: 'STD-1', test_name: 'Cloud Point', value: '-16.8', qc: true,
     status: 'RED', reason: 'QC Out of Spec',
     status_since: '2026-08-26T12:31:36',
     transition: {from: 'GREEN', to: 'RED', in_spec: false, value: -16.8,
                  band: {low: -16.0, high: -12.0, expected: -14.0}}},
    {machine_uid: 'optimpp-1', ts: '2026-08-26T11:02:36', kind: 'run',
     lab_id: 'L-37101', test_name: 'Cloud Point', value: '0.8401', qc: false,
     status: 'GREEN', reason: 'QC Fresh',
     status_since: '2026-08-26T10:15:36'},
    {machine_uid: 'optimpp-1', ts: '2026-08-26T10:15:36', kind: 'qc',
     lab_id: 'STD-1', test_name: 'Cloud Point', value: '-14.1', qc: true,
     status: 'GREEN', reason: 'QC Fresh',
     status_since: '2026-08-26T10:15:36',
     transition: {from: 'UNKNOWN', to: 'GREEN', in_spec: true, value: -14.1,
                  band: {low: -16.0, high: -12.0, expected: -14.0}}},
    /* Before the first QC reading there is no verdict to stand on. That is
     * UNKNOWN — a real state with a reason — and NOT a colourless gap. */
    {machine_uid: 'optimpp-1', ts: '2026-08-25T21:43:36', kind: 'run',
     lab_id: 'L-37974', test_name: 'Cloud Point', value: '0.8396', qc: false,
     status: 'UNKNOWN', reason: 'No valid QC data found.', status_since: null},
    {machine_uid: 'optimpp-1', ts: '2026-08-25T19:09:36', kind: 'run',
     lab_id: 'L-37731', test_name: 'Cloud Point', value: '0.8248', qc: false,
     status: 'UNKNOWN', reason: 'No valid QC data found.', status_since: null},
  ],
};

if (!failed && typeof sandbox.gutterHtml !== 'function') {
  failed = true;
  console.log('FAIL: gutterHtml() is missing — the status gutter cannot be '
              + 'judged on what it draws');
}

if (!failed) {
  const html = String(sandbox.gutterHtml(TIMELINE) || '');
  const flat = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  const low = flat.toLowerCase();

  /* THE GUTTER IS THE POINT. Three states are in this window and the BAND has
   * to carry all three.
   *
   * Counting the distinct colours anywhere in the markup is NOT enough, and a
   * mutation proved it: painting every band from one constant still left all
   * three colours in the file, because a QC transition names the state it came
   * from and the state it went to. So each band is paired with the state it is
   * banding, and both have to be that state's colour. */
  const PALETTE = {GREEN: '#21c071', YELLOW: '#f5c542', RED: '#f85b5b',
                   SERVICE: '#a855f7', 'DEAD-LINE': '#e2483d',
                   UNKNOWN: '#6b7280'};
  const bands = [...html.matchAll(
    /<b style="color:(#[0-9a-f]{6})">([A-Z][A-Z-]*)<\/b>[\s\S]*?gutband[^>]*background:(#[0-9a-f]{6})/gi)]
    .map(m => ({head: m[1].toLowerCase(), state: m[2],
                band: m[3].toLowerCase()}));
  claim('every run of events is banded, and the band is the run\u2019s own state',
        bands.length >= 2
        && bands.every(b => PALETTE[b.state]
                            && b.band === PALETTE[b.state]
                            && b.head === PALETTE[b.state]));
  claim('the gutter is painted in the colour of each state it passes through',
        new Set(bands.map(b => b.band)).size >= 2
        && bands.some(b => b.state === 'RED')
        && bands.some(b => b.state === 'UNKNOWN'));
  claim('…and it is a band down the side, not a dot per row',
        /class="[^"]*gut/.test(html));

  /* UNKNOWN IS A STATE, NOT AN ABSENCE. */
  claim('the stretch before the first QC reading reads as UNKNOWN',
        /unknown/.test(low));
  claim('…and says why, in the reason the server derived',
        /no valid qc data/.test(low));

  /* THE QC EVENTS ARE THE TRANSITIONS. */
  claim('a QC event is drawn as the transition it caused',
        /green\s*[^a-z]{0,4}\s*red/i.test(flat)
        || /green.{0,24}(→|->|to)\s*red/i.test(flat));
  claim('…and the first one, out of UNKNOWN, too',
        /unknown.{0,24}(→|->|to)\s*green/i.test(flat));
  claim('…carrying the reading that decided it',
        flat.includes('-16.8') && flat.includes('-14.1'));
  claim('…and the band it was judged against, off that row',
        flat.includes('-16') && flat.includes('-12'));
  claim('…and the standard it was run on', flat.includes('STD-1'));
  /* A QC row must be distinguishable from a run row, or the transitions are
   * lost in the list they punctuate. */
  claim('a QC event is marked out from the runs around it',
        /class="[^"]*(qcev|transition|tr)\b/.test(html)
        || (html.match(/data-qc/g) || []).length > 0);

  /* A CLIPPED WINDOW IS NOT A COMPLETE RECORD. `complete: false` means the
   * snapshot's EVENT_LIMIT rows for the WHOLE lab ran out before this
   * instrument's history did — a quiet bench clipped by a busy neighbour. */
  claim('a clipped window says it is clipped',
        /clipped|not everything|older|only the newest|cut short|more than this/
          .test(low));
  claim('…and does not imply that is all that ever happened',
        !/nothing else happened|that is everything/.test(low));
  claim('…and says how far back it does reach',
        flat.includes('2026-08-25'));

  const whole = Object.assign({}, TIMELINE, {complete: true});
  const wholeLow = String(sandbox.gutterHtml(whole) || '')
    .replace(/<[^>]*>/g, ' ').toLowerCase();
  claim('a complete window does not manufacture a clipping warning',
        !/clipped|only the newest|cut short/.test(wholeLow));
}

/* ---- WHERE THE QC WINDOW CAME FROM ------------------------------------
 *
 * `qc_expire_source` is `standard` | `request` | `default`. The default is
 * the number nobody chose, and presenting it as configuration sends somebody
 * looking for the setting that produced it. */
if (!failed) {
  const say = over => String(sandbox.gutterHtml(
    Object.assign({}, TIMELINE, over)) || '')
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').toLowerCase();

  const byDefault = say({});
  claim('a defaulted QC window is named as the default',
        /default/.test(byDefault) && byDefault.includes('24'));
  claim('…and is not presented as something somebody set',
        !/configured|set to|as configured|specified/.test(byDefault));

  const fromStd = say({qc_expire_source: 'standard', qc_expire_hours: 12.0,
                       qc_expire_from: 'Diesel - AO25'});
  claim('a window off the standard names the standard it came from',
        fromStd.includes('diesel - ao25') && fromStd.includes('12')
        && !/default/.test(fromStd));
  const fromReq = say({qc_expire_source: 'request', qc_expire_hours: 8.0});
  claim('a window asked for in the request says that is where it came from',
        /request|asked/.test(fromReq) && fromReq.includes('8'));
}

/* ---- AND THE TWO PANELS ARE ACTUALLY WIRED TO THE TWO ROUTES ----------- */
if (!failed) {
  const realFetch = sandbox.fetch;
  const asked = [];
  sandbox.fetch = (...args) => {
    asked.push(String(args[0]));
    const url = String(args[0]);
    const body = url.includes('qc-trend') ? {series: [SERIES]}
               : url.includes('status-timeline') ? TIMELINE
               : {authenticated: true, user: 'ryan', events: [], machines: []};
    return Promise.resolve({ok: true, status: 200,
                            json: () => Promise.resolve(body)});
  };
  if (typeof sandbox.drawTrend === 'function') {
    await sandbox.drawTrend(FLEET.machines[0]);
    await settle();
    claim('the QC tab reads the trend route',
          asked.some(u => /\/qc-trend$/.test(u)));
    claim('…and draws the coverage caveat into the panel',
          String(el('#trend').innerHTML || '').includes(SERIES.coverage.caveat));
  }
  asked.length = 0;
  if (typeof sandbox.loadTimeline === 'function') {
    await sandbox.loadTimeline(FLEET.machines[0]);
    await settle();
    claim('the events list reads the status-timeline route',
          asked.some(u => /\/status-timeline$/.test(u)));
    const drawn = String(el('#railR').innerHTML || '');
    claim('…and the gutter it draws carries more than one state',
          /#f85b5b/i.test(drawn) && /#21c071/i.test(drawn));
  }
  sandbox.fetch = realFetch;

  /* A route that could not be read is never an empty record: "nothing has
   * ever happened on this bench" is a statement about the record. */
  sandbox.fetch = () => Promise.resolve({ok: false, status: 503,
    json: () => Promise.resolve({error: 'That could not be read.'})});
  if (typeof sandbox.loadTimeline === 'function') {
    await sandbox.loadTimeline(FLEET.machines[0]);
    await settle();
    const drawn = String(el('#railR').innerHTML || '').toLowerCase();
    claim('a failed timeline read reads as a failure, not as an empty bench',
          /could not/.test(drawn) && !/nothing logged yet/.test(drawn));
  }
  sandbox.fetch = realFetch;
}

/* ---- and everything new reads in the one noun -------------------------- */
if (!failed) {
  readsWell('the search results panel', sandbox.searchPanelHtml(A_OK));
  readsWell('an idle search box', sandbox.searchPanelHtml(A_IDLE));
  readsWell('a search that found nothing', sandbox.searchPanelHtml(A_NO_MATCH));
  /* The violation MESSAGE is `qc_series`' own sentence, quoted verbatim
   * because it carries the PROVISIONAL clause and the instruction that goes
   * with it. It is data this page relays, not prose this page wrote — the
   * same exemption `prose()` already makes for wire identifiers — so it is
   * lifted out before the rename check, and nothing else is. */
  readsWell('the control chart',
            String(sandbox.trendSeriesHtml(SERIES) || '')
              .split(SERIES.violations[0].message).join(' '));
  readsWell('the status gutter', sandbox.gutterHtml(TIMELINE));
}


/* ---- THE PROJECTION IS A FLAT PLAN, AND STAYS FLAT ----------------------
 *
 * Ryan, 27 Aug: "Make the map not isometric but a simple top down 2d view for
 * faster loading." So this guard is INVERTED, not deleted.
 *
 * Deleting it is how the drawing drifts back. The history is the argument: a
 * tilt solver once bent the camera per draw to fill the stage, every coverage
 * claim in this file passed throughout, and a square rotated 45 degrees
 * shipped as an isometric because not one test measured what the drawing
 * looked like. The invariant has changed; the fact that SOME invariant is
 * enforced has not.
 *
 * Flat and overhead means two things, and both are checked below:
 *   AXIS-ALIGNED — a bay is a rectangle, not a diamond. Grid x moves the
 *     projection horizontally only, grid y vertically only.
 *   NO HEIGHT — the h argument cannot move a point. In a top-down view there
 *     is no elevation to see, and a wall that still contributes height is an
 *     isometric hiding inside a plan.
 *
 * The old text is kept below for what it explains about how this was got wrong
 * the first time.
 *
 * ---- WHAT THIS REPLACED --------------------------------------------------
 *
 * This is the test whose absence let a rotated plan ship as an isometric.
 *
 * `planFitTilt` used to SOLVE the camera angle per draw so the drawing came out
 * the shape of the stage, and on a nearly-square stage it pinned the tilt to
 * its own ceiling — 68 degrees, sin 0.93, a depth axis with almost no
 * foreshortening. Every fit claim in this file passed the whole time, because
 * they all measured how much CANVAS was covered and not one of them measured
 * what the drawing looked like. Coverage was 97% and the floor was a square
 * rotated 45 degrees.
 *
 * At yaw 45 the projected deck is a diamond whose width:height is exactly
 * 1/sin(tilt), independent of how the bays are laid out — so the deck's own
 * box IS the tilt, and asserting on it needs no access to the camera. A true
 * 2:1 isometric is 2.0. The flattened one measured 1.08.
 *
 * Asserted at every viewport the fit tests use, because "fits the stage" is
 * precisely the pressure that bent it last time.
 */
async function checkIsometric() {
  const svg = el('#floorSimple');
  const stage = el('#stage');
  const realRect = stage.getBoundingClientRect;
  const ratios = [];
  for (const [w, h] of [[1600, 1400], [1400, 900], [1100, 960], [2400, 700]]) {
    stage.getBoundingClientRect = () => ({left: 0, top: 0, width: w, height: h,
                                          right: w, bottom: h, x: 0, y: 0});
    sandbox.PLAN_SIG = '';
    sandbox.drawSimpleFloor(false);
    const planes = planesOf();
    const deck = planes.length
      ? byClass(planes[0], 'deck').map(absBox).filter(Boolean)[0] : null;
    if (deck) {
      ratios.push([`${w}x${h}`,
                   (deck.x1 - deck.x0) / Math.max(1e-6, deck.y1 - deck.y0)]);
    }
  }
  stage.getBoundingClientRect = realRect;
  sandbox.PLAN_SIG = '';
  sandbox.drawSimpleFloor(false);

  ratios.forEach(([at, r]) => console.log(
    `  ..   deck at ${at}: ${r.toFixed(2)}:1  (the isometric pinned this at 2.00)`));
  /* THE DECK IS UNDISTORTED, which under a flat plan is a different claim
   * from "the deck is square".
   *
   * The isometric pinned the deck at 2:1 whatever the bay layout — that ratio
   * was the CAMERA, which is why asserting on it caught a bent one. Flat, the
   * deck's aspect is the bay grid's own aspect (this floor measures 1.37,
   * about 11x8), and pinning a number here would only assert what the fixture
   * happens to be laid out as.
   *
   * So: divide the drawn deck by one projected bay and recover the grid it was
   * drawn on. Whole numbers mean the deck is on the same projection as
   * everything else, and the recovered ratio has to be the measured ratio. A
   * camera that went back to a tilt fails this — at 2:1 the recovered rows
   * come out at half the bays and land between whole numbers. */
  const proj = sandbox.planIso;
  if (ratios.length === 4 && typeof proj === 'function') {
    const o = proj(0, 0, 0);
    const bw = Math.abs(proj(1, 0, 0)[0] - o[0]);
    const bh = Math.abs(proj(0, 1, 0)[1] - o[1]);
    stage.getBoundingClientRect = () => ({left: 0, top: 0, width: 1400,
                                          height: 900, right: 1400,
                                          bottom: 900, x: 0, y: 0});
    sandbox.PLAN_SIG = '';
    sandbox.drawSimpleFloor(false);
    const deck = byClass(planesOf()[0], 'deck').map(absBox).filter(Boolean)[0];
    stage.getBoundingClientRect = realRect;
    sandbox.PLAN_SIG = '';
    sandbox.drawSimpleFloor(false);
    // The deck is measured in screen pixels and the bay in drawing units, so
    // only the RATIO of the two is meaningful — scale cancels.
    const cols = (deck.x1 - deck.x0) / bw, rows = (deck.y1 - deck.y0) / bh;
    console.log(`  ..   deck spans ${cols.toFixed(2)} x ${rows.toFixed(2)}`
                + ' bay-widths');
    claim('the deck aspect is the bay grid\'s own, not the camera\'s',
          Math.abs((cols / rows) - ratios[1][1]) < 0.02,
          `${(cols / rows).toFixed(3)} vs measured ${ratios[1][1].toFixed(3)}`);
    claim('and it is not the 2:1 the isometric camera pinned it at',
          Math.abs(ratios[1][1] - 2) > 0.1, ratios[1][1].toFixed(3));
  }
  claim('…and it is the SAME projection at every stage shape, so no viewport '
        + 'can bend the camera to fill itself',
        ratios.length === 4
        && Math.max(...ratios.map(([, r]) => r))
           - Math.min(...ratios.map(([, r]) => r)) < 0.02);

  /* The projection itself, not the drawing it produced. `planIso` is a
   * function declaration, so it is on the sandbox context. */
  const P = sandbox.planIso;
  claim('planIso() is still the one projection everything goes through',
        typeof P === 'function');
  if (typeof P === 'function') {
    const o = P(0, 0, 0), gx = P(1, 0, 0), gy = P(0, 1, 0);
    claim('grid x moves the drawing horizontally only',
          Math.abs(gx[1] - o[1]) < 1e-9 && Math.abs(gx[0] - o[0]) > 1e-9,
          `${JSON.stringify(o)} -> ${JSON.stringify(gx)}`);
    claim('grid y moves the drawing vertically only',
          Math.abs(gy[0] - o[0]) < 1e-9 && Math.abs(gy[1] - o[1]) > 1e-9,
          `${JSON.stringify(o)} -> ${JSON.stringify(gy)}`);
    claim('one bay is square — x and y carry the same scale',
          Math.abs(Math.abs(gx[0] - o[0]) - Math.abs(gy[1] - o[1])) < 1e-9);
    claim('height cannot move a point — there is no elevation in a plan',
          Math.abs(P(2, 3, 9)[0] - P(2, 3, 0)[0]) < 1e-9
          && Math.abs(P(2, 3, 9)[1] - P(2, 3, 0)[1]) < 1e-9);
  }
  /* And the mechanism is gone, not merely unused.
   *
   * `PLAN_TILT` and `PLAN_CAM` are top-level `const`s, which in this sandbox
   * are script-scoped and never become properties of the context — so they
   * cannot be read from here, and an assertion that appeared to check them
   * would be checking `undefined === undefined`. `planFitTilt` was a function
   * DECLARATION and would be on the context if it still existed, so its
   * absence is real. The rest is asserted against the source: exactly one
   * assignment to the camera's tilt, and it takes the constant rather than
   * anything derived from the stage. */
  const tiltWrites = [...src.matchAll(/PLAN_CAM\s*\.\s*tilt\s*=/g)].length;
  claim('the tilt solver is gone, not just unused',
        typeof sandbox.planFitTilt === 'undefined');
  claim('…and exactly one line sets the camera, from the constant',
        tiltWrites === 1 && /PLAN_CAM\s*\.\s*tilt\s*=\s*PLAN_TILT\b/.test(src));
}

/* ---- THE BAY IS A PLAN, NOT A LITTLE BUILDING ---------------------------
 *
 * Ryan, 27 Aug, having asked for a flat top-down map: put the equipment names
 * "in the top left of the sample boxes", "remove the building sillhoutes in
 * the boxes", and remove the readout panel — the dark rounded rect with a
 * coloured bar across it, drawn on the operator side of every bay.
 *
 * All three were the isometric's furniture. `planPrism` extruded one of four
 * archetypes per instrument (a fractionating column, an analyser cabinet, a
 * bench unit with a chimney, a twin-vessel bath) and those are exactly the
 * silhouettes; the readout panel was a face drawn on a box that no longer has
 * a face; the name plate hung BELOW the bay because the pill row ran down and
 * to the right in a projection that no longer runs anywhere.
 *
 * Asserted structurally, because "looks flat" is not testable: no prisms are
 * drawn, the function that drew them is gone rather than merely unused (the
 * same rule the tilt solver is held to), no bay carries a <rect>, and the name
 * plate's box sits at the bay's top-left corner.
 */
function checkFlatBays() {
  const svg = el('#floorSimple');
  const units = byClass(svg, 'simple-machine');
  claim('there are instruments on the plan to look at', units.length > 0);

  claim('the prism builder is gone, not just uncalled',
        typeof sandbox.planPrism === 'undefined');
  const prismCalls = [...src.matchAll(/\bplanPrism\s*\(/g)].length;
  claim('…and nothing in the page still calls it', prismCalls === 0,
        `${prismCalls} call(s) left in floor.html`);

  /* The readout panel was the ONLY <rect> inside a bay — everything else on
   * the plan is a polygon, because everything else was projected. So counting
   * rects is an exact test for it and needs no class of its own. */
  const isTag = (e, t) => String(e.tagName || '').toLowerCase() === t;
  const rects = units.flatMap(u => walk(u).filter(e => isTag(e, 'rect')));
  claim('no bay carries a readout panel', rects.length === 0,
        `${rects.length} <rect> still drawn inside instrument groups`);

  /* The name plate. It lives in its own layer above the machines, so it is
   * found by class `unit` WITHOUT `simple-machine` — the distinction the
   * plate's own comment explains. Its box must sit at the top-left of the bay
   * it names: left of the bay's centre, and above the bay's top edge. */
  const plinths = units.map(u => byClass(u, 'plinth')[0]).filter(Boolean)
                       .map(absBox).filter(Boolean);
  const plates = byClass(svg, 'unit')
    .filter(u => !(u.getAttribute('class') || '').includes('simple-machine'));
  claim('every instrument still has a name plate', plates.length === units.length,
        `${plates.length} plates for ${units.length} instruments`);

  const boxes = plates.map(pl => walk(pl).find(e => isTag(e, 'rect')))
                      .filter(Boolean).map(absBox).filter(Boolean);
  claim('…and each plate has a box to place', boxes.length === plates.length);

  if (boxes.length && plinths.length === boxes.length) {
    // Pair each plate with its nearest bay by x, which is enough on a grid.
    const pairs = boxes.map(b => {
      const bay = plinths.reduce((best, p) =>
        Math.hypot(p.x0 - b.x0, p.y0 - b.y0)
          < Math.hypot(best.x0 - b.x0, best.y0 - b.y0) ? p : best);
      return {b, bay};
    });
    claim('the name sits at the LEFT of its bay, not centred under it',
          pairs.every(({b, bay}) =>
            b.x0 < bay.x0 + (bay.x1 - bay.x0) * 0.35),
          pairs.map(({b, bay}) =>
            ((b.x0 - bay.x0) / (bay.x1 - bay.x0)).toFixed(2)).join(' '));
    claim('…and at the TOP of it, not below the bay',
          pairs.every(({b, bay}) => b.y1 <= bay.y0 + (bay.y1 - bay.y0) * 0.5),
          pairs.map(({b, bay}) =>
            ((b.y0 - bay.y0) / (bay.y1 - bay.y0)).toFixed(2)).join(' '));

    /* INSIDE the bay, not floating above it.
     *
     * The first version put the plate above the bay's top edge, which is
     * "top-left" of nothing on a grid where bays touch: the bottom row's names
     * landed on top of the row above, covering its status bar and its PM and
     * CAL pills. A label that hides the state of a DIFFERENT instrument is
     * worse than one in an awkward place. Ryan said "in the top left of the
     * sample boxes" — in. */
    claim('the plate sits INSIDE its bay, not over the one behind it',
          pairs.every(({b, bay}) =>
            b.y0 >= bay.y0 - 1 && b.x0 >= bay.x0 - 1 && b.x1 <= bay.x1 + 1),
          pairs.map(({b, bay}) =>
            `${(b.y0 - bay.y0).toFixed(0)}px above bay top`).join(' '));
  }
}

/* ---- THE LEVEL PICKER IS ON THE RIGHT ----------------------------------
 *
 * Ryan: "on the right side of the map if there was a UI element to show the
 * levels that are visible, highlight the current level, and have you able to
 * click and select a level."
 *
 * Two of the three already worked — the rungs highlight the level in view and
 * `levelNavGo` switches without a fetch. It was on the left, over the corner
 * of the drawing. This asserts all three so the working two cannot be lost
 * while the third is moved.
 */
function checkLevelPicker() {
  const nav = el('#levelNav');
  claim('the level picker exists', !!nav);
  if (!nav) return;

  /* `src` is the page's largest <script>, not the page — the stylesheet is not
   * in it, and slicing on a missing '</style>' silently searched the script
   * body instead. `html` is the whole file. */
  const css = html.slice(0, html.indexOf('</style>'));
  const rule = /\.lvlnav\s*\{[^}]*\}/.exec(css);
  claim('it is pinned to the RIGHT of the stage',
        !!rule && /(^|[;{])\s*right\s*:/.test(rule[0])
        && !/(^|[;{])\s*left\s*:/.test(rule[0]),
        rule ? rule[0].replace(/\s+/g, ' ') : 'no .lvlnav rule');

  /* The other two thirds of the ask — the level in view is highlighted, and
   * every other level is a button that switches to it without a fetch — were
   * already true, and are already held about 2500 lines up where the harness
   * has a three-level lab loaded: `aria-current="true"`, `class="rung on"`,
   * "pressing a rung goes to that floor", "…and fires NO request doing it",
   * and the single-level case where the one rung is deliberately NOT a button.
   *
   * This function runs at the tail, against whatever fixture the last check
   * left behind — a one-level lab — so re-asserting them here would test the
   * wrong state and say nothing the existing block does not already say
   * better. Only the position is new, and only the position is checked. */
}

if (!failed) await checkIsometric();
if (!failed) checkFlatBays();
if (!failed) checkLevelPicker();

console.log(failed ? '\nthe floor does not boot' : '\nthe floor boots');
process.exit(failed ? 1 : 0);
