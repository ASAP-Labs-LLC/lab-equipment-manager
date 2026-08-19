// Two pure functions pulled straight out of floor.html, so these cannot drift
// from what ships.
//
//   panelSignature(m) — what the open instrument panel is showing. load() now
//     re-renders only when this changes. It used to re-render on EVERY poll,
//     which at a 30s timer was a blink and at 2s is a flicker: `select()`
//     replaces the whole rail's innerHTML, so the tab snaps back to QC, the
//     scroll position is lost, and the trend flashes "Loading…".
//
//   lastQcAt(m) — when this instrument last ran a QC check, for the hover card.
import fs from 'fs';

const html = fs.readFileSync(new URL('../../templates/floor.html', import.meta.url), 'utf8');

function extract(name) {
  const at = html.indexOf(`function ${name}(`);
  if (at === -1) { console.log(`FAIL: ${name}() not found in floor.html`); process.exit(1); }
  const body = html.slice(at, html.indexOf('\n}', at) + 2);
  return new Function(`${body}; return ${name};`)();
}

const panelSignature = extract('panelSignature');
const lastQcAt = extract('lastQcAt');
const feedSignature = extract('feedSignature');

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

const machine = (over = {}) => Object.assign({
  machine_uid: 'pac-flash-2',
  title: 'PAC Flash 2',
  status: 'GREEN',
  reason: '',
  state: 'running',
  live: true,
  watching: 'single_csv C:/data/flash.csv',
  last_activity: '2026-08-05T14:02:10',
  updated_at: '2026-08-05T14:02:11',
  last_poll: '2026-08-05T14:02:11',
  sub_statuses: { qc: 'GREEN', pm: 'GREEN', calibration: 'GREEN' },
  effective_specs: [
    { test_name: 'Flash Point', low: 60.5, high: 64.5,
      last_qc_at: '2026-08-05T13:40:00', last_qc_value: 62.4, last_qc_in_spec: true },
  ],
  qc_specs: [],
  qc_targets: [],
}, over);

console.log('panelSignature — what makes the panel redraw');

// The flicker: a bench pushing an unchanged status every poll must not repaint.
check('an unchanged machine keeps its signature',
  panelSignature(machine()) === panelSignature(machine()), true);

check('a fresh push with nothing new does not redraw',
  panelSignature(machine()) === panelSignature(machine({
    updated_at: '2026-08-05T14:02:41', last_poll: '2026-08-05T14:02:41' })), true);

// Real changes must still reach the panel.
check('a status change redraws',
  panelSignature(machine()) !== panelSignature(machine({ status: 'RED' })), true);

check('a new reason redraws',
  panelSignature(machine()) !== panelSignature(machine({ reason: 'Flash Point out of spec' })), true);

check('a new parse redraws',
  panelSignature(machine()) !== panelSignature(machine({ last_activity: '2026-08-05T14:05:00' })), true);

check('a module that stopped redraws',
  panelSignature(machine()) !== panelSignature(machine({ state: 'stopped', live: false })), true);

check('a new QC result redraws',
  panelSignature(machine()) !== panelSignature(machine({
    effective_specs: [{ test_name: 'Flash Point', low: 60.5, high: 64.5,
      last_qc_at: '2026-08-05T14:10:00', last_qc_value: 62.6, last_qc_in_spec: true }] })), true);

check('a changed QC band redraws',
  panelSignature(machine()) !== panelSignature(machine({
    effective_specs: [{ test_name: 'Flash Point', low: 61.0, high: 64.0,
      last_qc_at: '2026-08-05T13:40:00', last_qc_value: 62.4, last_qc_in_spec: true }] })), true);

check('a PM going yellow redraws',
  panelSignature(machine()) !== panelSignature(machine({
    sub_statuses: { qc: 'GREEN', pm: 'YELLOW', calibration: 'GREEN' } })), true);

check('a different instrument is a different panel',
  panelSignature(machine()) !== panelSignature(machine({ machine_uid: 'm-other' })), true);

console.log('lastQcAt — when this instrument last ran a control');

check('the resolved spec supplies it',
  lastQcAt(machine()), '2026-08-05T13:40:00');

check('the newest of several checks wins',
  lastQcAt(machine({ effective_specs: [
    { test_name: 'Flash Point', last_qc_at: '2026-08-05T13:40:00' },
    { test_name: 'Density', last_qc_at: '2026-08-05T14:15:00' },
    { test_name: 'Sulfur', last_qc_at: '2026-08-04T09:00:00' },
  ] })), '2026-08-05T14:15:00');

check('a check that has never run is not a time',
  lastQcAt(machine({ effective_specs: [{ test_name: 'Flash Point', last_qc_at: '' }] })), '');

check('an instrument with no QC assigned has no time',
  lastQcAt(machine({ effective_specs: [] })), '');

check('a run alongside one that never ran still counts',
  lastQcAt(machine({ effective_specs: [
    { test_name: 'Flash Point', last_qc_at: '' },
    { test_name: 'Density', last_qc_at: '2026-08-05T14:15:00' },
  ] })), '2026-08-05T14:15:00');

check('nothing at all is not a crash',
  lastQcAt({}), '');

console.log('feedSignature — what makes the activity rail redraw');

const EVENTS = [
  { machine_uid: 'm1', ts: '2026-08-05T14:02:10', kind: 'run', lab_id: 'L-1' },
  { machine_uid: 'm2', ts: '2026-08-05T14:01:00', kind: 'qc', lab_id: 'QC1' },
];

check('the same feed does not redraw',
  feedSignature(EVENTS) === feedSignature(EVENTS.map(e => ({...e}))), true);

check('a new run redraws',
  feedSignature(EVENTS) !== feedSignature(
    [{ machine_uid: 'm3', ts: '2026-08-05T14:03:00', kind: 'run', lab_id: 'L-9' },
     ...EVENTS]), true);

check('an empty feed is stable too',
  feedSignature([]) === feedSignature([]), true);

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log('all ok');
