// The wall kiosks' pure logic (static/js/wall_logic.js): /floor, /qc, /wall.
//
// What a browser alone cannot be trusted to show, held here:
//
// * THE STALE RULE (ia-final §3.8). A wall that froze at green must not look
//   like a wall that is green. After 90 s with no answer from /api/ui/live
//   the headline becomes "Not live · last update 13:15" and the plan dims;
//   at 89 s it is still live. The clock starts at the page's own first
//   paint, so a TV that never got one answer still goes stale on time. A
//   snapshot the server itself says is stale (LabCore not answering) is not
//   current either, and says when the record is from.
// * THE LAB'S CLOCK, WITH ITS ZONE. The wall hangs in the lab; the TV's own
//   zone is whatever the TV was set to. Times are formatted in lab_tz from
//   /api/ui/live, never by slicing an ISO string (the old wall's
//   .slice(0, 16) showed UTC-less server strings as if they were local).
// * KIOSK PARAMETERS are a TV's configuration, typed once into a bookmark:
//   ?theme=dark|light, ?level=<uid>, ?rotate=0, /wall?show=floor,qc&every=60.
//   A junk value is ignored, never obeyed.
// * ROTATION pauses while a pointer or a finger is on the wall (somebody is
//   reading a card) and resumes a full period after it leaves; it never
//   runs when motion is reduced.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/wall_logic.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const W = root.LEMWallLogic;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
if (!W) { console.log('  FAIL wall_logic.js does not define window.LEMWallLogic'); process.exit(1); }

const TZ = 'America/Los_Angeles';
// 2026-10-01 13:15:04 PDT == 20:15:04Z
const T0 = Date.parse('2026-10-01T20:15:04Z');

// ── the lab's clock ───────────────────────────────────────────────────────
check('clock is local with the zone', W.clock(T0, TZ, true), '13:15:04 PDT');
check('short clock is hh:mm', W.clock(T0, TZ, false), '13:15');
check('another lab zone, named as Intl names it', W.clock(T0, 'Europe/London', true), '21:15:04 GMT+1');
check('a junk zone falls back to the browser, and says no zone it did not know',
      typeof W.clock(T0, 'Not/AZone', true), 'string');
check('no time is said as such', W.clock(null, TZ, true), '—');
check('day and time for a card', W.when('2026-09-30T15:04:24-07:00', TZ, T0), '30 Sep 15:04');
check('today is just the time', W.when('2026-10-01T09:12:00-07:00', TZ, T0), '09:12');
check('a naive server stamp is the lab\'s local time, not UTC',
      W.when('2026-10-01T09:12:00', TZ, T0, '2026-10-01T13:15:04-07:00'), '09:12');

// ── the stale rule ────────────────────────────────────────────────────────
const live = (o) => W.liveState(Object.assign({ loadedMs: T0, lastOkMs: T0, nowMs: T0, tz: TZ }, o));
check('fresh is live', live({ nowMs: T0 + 5000 }).kind, 'live');
check('89 s is still live', live({ nowMs: T0 + 89000 }).kind, 'live');
check('90 s and one is not', live({ nowMs: T0 + 90001 }).kind, 'lost');
check('the headline says when', live({ nowMs: T0 + 95000 }).headline, 'Not live · last update 13:15');
check('the footer says stale', live({ nowMs: T0 + 95000 }).footer, 'Stale · last update 13:15:04 PDT');
check('lost dims', live({ nowMs: T0 + 95000 }).dim, true);
check('never answered: the clock runs from the first paint',
      live({ lastOkMs: 0, nowMs: T0 + 91000 }).kind, 'lost');
check('never answered within 90 s is still the first paint\'s answer',
      live({ lastOkMs: 0, nowMs: T0 + 30000 }).kind, 'live');
check('live footer has the time and zone', live({ nowMs: T0 + 2000 }).footer, 'Live · updated 13:15:04 PDT');
check('live does not dim', live({ nowMs: T0 + 2000 }).dim, false);
check('a stale record is not current',
      live({ nowMs: T0 + 2000, snapshotStale: true, builtAt: '2026-10-01T13:02:11-07:00' }).headline,
      'Not current · record from 13:02');
check('…and dims', live({ nowMs: T0 + 2000, snapshotStale: true, builtAt: '2026-10-01T13:02:11-07:00' }).dim, true);
check('…and its footer says so',
      live({ nowMs: T0 + 2000, snapshotStale: true, builtAt: '2026-10-01T13:02:11-07:00' }).footer,
      'Record from 13:02:11 PDT · LabCore not answering');
check('losing LEM outranks an old record',
      live({ nowMs: T0 + 95000, snapshotStale: true, builtAt: '2026-10-01T13:02:11-07:00' }).kind, 'lost');
check('the limit is 90 s', W.STALE_MS, 90000);

// ── kiosk parameters ──────────────────────────────────────────────────────
check('defaults', W.parseKiosk(''), { theme: '', level: '', rotate: true, show: ['floor', 'qc'], every: 60 });
check('a pinned TV', W.parseKiosk('?theme=dark&level=ee1ce78a6d79&rotate=0'),
      { theme: 'dark', level: 'ee1ce78a6d79', rotate: false, show: ['floor', 'qc'], every: 60 });
check('junk is ignored', W.parseKiosk('?theme=neon&level=<script>&rotate=maybe&show=x,y&every=abc'),
      { theme: '', level: '', rotate: true, show: ['floor', 'qc'], every: 60 });
check('show one wall', W.parseKiosk('?show=qc').show, ['qc']);
check('every is held to 15..600 s', [W.parseKiosk('?every=2').every, W.parseKiosk('?every=9999').every], [15, 600]);

// ── rotation ──────────────────────────────────────────────────────────────
{
  const r = W.rotator({ count: 3, every: 15000, now: 0 });
  check('starts on the first', r.index, 0);
  check('not before the period', r.tick(14999).index, 0);
  check('then the next', r.tick(15000).index, 1);
  r.pause(16000);
  check('paused on pointer: does not move', r.tick(60000).index, 1);
  r.resume(61000);
  check('resumes a full period after the pointer leaves', r.tick(75999).index, 1);
  check('…and then moves', r.tick(76000).index, 2);
  check('wraps', r.tick(91000).index, 0);
  check('remaining seconds', Math.round(r.remaining(95000) / 1000), 11);
  r.go(-1, 95000);
  check('Previous by hand', r.index, 2);
  const one = W.rotator({ count: 1, every: 15000, now: 0 });
  check('one page never rotates', one.tick(100000).index, 0);
  const off = W.rotator({ count: 3, every: 15000, now: 0, enabled: false });
  check('rotate=0 or reduced motion never rotates', off.tick(100000).index, 0);
  r.setCount(2, 96000);
  check('a page that went away is not shown', r.index < 2, true);
}

// ── /qc capacity: how many cards fit ──────────────────────────────────────
check('1440x900 holds 3 by 3', W.qcGrid(1376, 690), { cols: 3, rows: 3, per: 9 });
check('1920x1080 holds 4 by 3', W.qcGrid(1856, 870), { cols: 4, rows: 3, per: 12 });
check('pages', W.pages(13, 9), 2);

// ── /wall alternation ────────────────────────────────────────────────────
check('alternates in the order given', W.wallSequence(['floor', 'qc']), ['floor', 'qc']);
check('one wall stays', W.wallSequence(['qc']), ['qc']);

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall wall_logic checks passed');
