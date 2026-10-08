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
// Round 4: the rule used to fire at "more than 90 s since the last good
// answer". The last good answer lands at or just before the feed stops, and
// the wall redraws once a second, so a strict 90 s threshold always fired
// at 90 s or LATER after the stop: the critic measured 90.6 s on /floor and
// 91.2 s on /qc, with /qc still saying "Live" past the promise. The promise
// is "never Live after 90 s"; the trigger sits under it by the visible poll
// period plus the redraw tick plus a second of timer slack.
check('the promise is 90 s', W.STALE_MS, 90000);
check('the trigger sits under the promise by poll + tick + slack',
      W.STALE_AFTER_MS, W.STALE_MS - (W.POLL_MS + W.TICK_MS + 1000));
check('a healthy feed\'s gaps never read as stale (one poll timed out and backed off)',
      live({ nowMs: T0 + 15000 + 12000 + 3000 }).kind, 'live');
// the margin is only honest while it matches what the page really does:
// live.js's visible poll period and wall.js's redraw interval
{
  const liveSrc = fs.readFileSync(new URL('../../static/js/live.js', import.meta.url), 'utf8');
  const wallSrc = fs.readFileSync(new URL('../../static/js/wall.js', import.meta.url), 'utf8');
  check('POLL_MS is live.js\'s visible poll', +(/const VISIBLE_MS = (\d+);/.exec(liveSrc) || [])[1], W.POLL_MS);
  check('the wall redraws every TICK_MS', /\}, L\.TICK_MS\);/.test(wallSrc), true);
}
check('80 s is still live', live({ nowMs: T0 + 80000 }).kind, 'live');
check('just past the trigger is not', live({ nowMs: T0 + W.STALE_AFTER_MS + 1 }).kind, 'lost');
check('89 s is already not live', live({ nowMs: T0 + 89000 }).kind, 'lost');
// The worst case, simulated the way the page runs it: the feed stops at
// `stop`; its last good answer came 0..one poll before; the page redraws on
// a 1 s tick of any phase, and a tick can run up to 250 ms late. The first
// redraw that says "Not live" must come no later than 90 s after the stop.
{
  let worst = 0;
  const stop = T0 + 500000;
  for (let lastOkAgo = 0; lastOkAgo <= W.POLL_MS; lastOkAgo += 250) {
    for (let phase = 0; phase < W.TICK_MS; phase += 50) {
      let t = stop + phase;
      while (W.liveState({ loadedMs: T0, lastOkMs: stop - lastOkAgo, nowMs: t, tz: TZ }).kind === 'live') t += W.TICK_MS;
      worst = Math.max(worst, t + 250 - stop);
    }
  }
  check('worst case, measured from the stop: Not live within 90 s (' + worst + ' ms)', worst <= 90000, true);
}
check('the headline says when', live({ nowMs: T0 + 95000 }).headline, 'Not live · last update 13:15');
// round 3: the shooter read "Not live · last update 12:32" in the headline
// and "Stale · last update 12:32:37 PDT" in the footer, two different words
// for one state. The footer now says what the headline says, with seconds
// and the zone, and still calls it stale (§3.8).
check('the footer says what the headline says, and stale', live({ nowMs: T0 + 95000 }).footer,
      'Not live · last update 13:15:04 PDT · stale');
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

// ── kiosk parameters ──────────────────────────────────────────────────────
check('defaults', W.parseKiosk(''), { theme: '', level: '', rotate: true, show: ['floor', 'qc'], every: 60, dwell: 20, whole: false });
check('a pinned TV', W.parseKiosk('?theme=dark&level=ee1ce78a6d79&rotate=0'),
      { theme: 'dark', level: 'ee1ce78a6d79', rotate: false, show: ['floor', 'qc'], every: 60, dwell: 20, whole: false });
check('junk is ignored', W.parseKiosk('?theme=neon&level=<script>&rotate=maybe&show=x,y&every=abc&dwell=soon&whole=yes'),
      { theme: '', level: '', rotate: true, show: ['floor', 'qc'], every: 60, dwell: 20, whole: false });
check('show one wall', W.parseKiosk('?show=qc').show, ['qc']);
check('every is held to 15..600 s', [W.parseKiosk('?every=2').every, W.parseKiosk('?every=9999').every], [15, 600]);

// ── levels are pages (2026-10-07) ─────────────────────────────────────────
// Ryan: "levels is for different screens … like android app pages, where you
// slide left to right on them", and "we dont want everything on the screen
// all the time". The wall shows one level per page; how long each page stays
// up is ?dwell= (seconds), and the old everything-at-once floor is now only
// ?whole=1, asked for by name.
check('a page stays up 20 s unless told', W.parseKiosk('').dwell, 20);
check('dwell is a TV setting', W.parseKiosk('?dwell=45').dwell, 45);
check('dwell is held to 5..600 s', [W.parseKiosk('?dwell=1').dwell, W.parseKiosk('?dwell=9999').dwell], [5, 600]);
check('the whole floor at once only when asked', [W.parseKiosk('').whole, W.parseKiosk('?whole=1').whole], [false, true]);

// A monitor showing the Mezzanine must still say the Upper Lab has an
// instrument that cannot run: each level's tab carries the worst state on it.
check('the worst state on a level wins', W.worstState(['ok', 'ok_but', 'not_ok', 'ok']), 'not_ok');
check('never checked in is worse than taken off line on purpose', W.worstState(['off_line', 'cant_tell', 'ok']), 'cant_tell');
check('OK, but beats both', W.worstState(['cant_tell', 'ok_but']), 'ok_but');
check('all OK is OK', W.worstState(['ok', 'ok']), 'ok');
check('an empty level has no state, not an OK one', W.worstState([]), null);
check('an unknown state is not read as OK', W.worstState(['ok', 'martian']), 'cant_tell');
check('glyphs match the counts', ['not_ok', 'ok_but', 'cant_tell', 'off_line', 'ok', null].map(W.stateGlyph),
      ['error', 'half', 'dashed', 'off', 'final', '']);

// Swipe like a phone's home screens: finger right-to-left is the next page.
// A short drag, or a mostly vertical one, is a tap or a scroll, not a turn.
check('swipe left is the next page', W.swipeStep(-90, 10), 1);
check('swipe right is the previous page', W.swipeStep(90, -5), -1);
check('a short drag is not a swipe', W.swipeStep(-30, 0), 0);
check('a vertical drag is not a swipe', W.swipeStep(-80, 140), 0);

// Turning a page by hand (tab, arrow key, swipe, ‹ ›) holds it for a minute,
// then the wall goes back to cycling on its own: a monitor nobody touches
// again must not stay on the page somebody last looked at.
{
  const r = W.rotator({ count: 3, every: 20000, now: 0 });
  r.go(1, 1000).hold(1000, 60000);
  check('held after a hand turn', [r.tick(40000).index, r.running()], [1, false]);
  check('still held just before the minute', r.tick(60999).index, 1);
  r.tick(61000);
  check('cycling again after the minute', r.running(), true);
  check('…a full page later, not at once', [r.tick(80999).index, r.tick(81000).index], [1, 2]);
  const p = W.rotator({ count: 3, every: 20000, now: 0, enabled: false });
  p.hold(0, 60000); p.tick(61000);
  check('a hold never starts a wall that was not rotating', p.running(), false);
}

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

// ── the whole floor at once (round 2) ────────────────────────────────────
// A wall exists so the room sees the whole floor at a glance. Round 1 drew
// one level at a time, so a three-level demo floor showed 5 of 13 benches
// in huge, mostly empty bays, and the blind judge picked a mock that showed
// all of them. floorPack lays every level out together whenever each bay
// can still hold its words at the bar's sizes (minW x minH). Only when no
// arrangement can does the wall rotate levels.
{
  const demo = [{ uid: 'g', w: 3, h: 2 }, { uid: 'm', w: 3, h: 2 }, { uid: 'u', w: 3, h: 2 }];
  const o1440 = { gap: 12, panelGap: 20, head: 32, minW: 140, minH: 112 };
  const p = W.floorPack(demo, 1019, 640, o1440);
  check('1440: the three demo levels fit together, two to a row', p && p.rows, [[0, 1], [2]]);
  check('1440: every bay is at least the readable size', p && p.cellW >= 140 && p.cellH >= 112, true);
  const q = W.floorPack(demo, 1400, 830, { gap: 12, panelGap: 20, head: 36, minW: 170, minH: 136 });
  check('1920: two to a row as well (three in a row makes slivers)', q && q.rows, [[0, 1], [2]]);
  check('1920: bays never taller than wide by more than 5 %', q && q.cellH <= Math.floor(q.cellW * 1.05), true);
  const big = [{ uid: 'a', w: 7, h: 5 }, { uid: 'b', w: 7, h: 5 }, { uid: 'c', w: 7, h: 5 }];
  check('a floor too big to read at once rotates (null)', W.floorPack(big, 1019, 640, o1440), null);
  check('one level is one panel', W.floorPack([{ uid: 'x', w: 7, h: 5 }], 1019, 640, { gap: 12, panelGap: 20, head: 0, minW: 100, minH: 90 }).rows, [[0]]);
  check('no levels is nothing to pack', W.floorPack([], 1000, 600, o1440), null);
  check('a box not measured yet packs nothing', W.floorPack(demo, 0, 0, o1440), null);
}

// ── round 3: no dead area, and whole words on one line ──────────────────
// The round-2 wall packed the demo's three 3x2 levels two to a row beside a
// fixed Needs-attention column. At 1440x900 that left a 520x360 hole under
// Mezzanine (about 30 % of the plan: J1's "dead area") and bays 150px wide,
// so 9 of 13 state words broke in two ("Not OK to / run"). wallLayout
// weighs that against a second arrangement: the levels take the whole
// width and Needs attention fills the hole the levels leave. It picks
// whichever gives the bigger bay, and a hole only counts when Needs
// attention fits in it.
{
  const demo = [{ uid: 'g', w: 3, h: 2 }, { uid: 'm', w: 3, h: 2 }, { uid: 'u', w: 3, h: 2 }];
  const o = { gap: 12, panelGap: 20, head: 32, minW: 120, minH: 112, attnW: 331, attnGap: 26, holeMinW: 560, holeMinH: 280 };
  const a = W.wallLayout(demo, 1376, 690, o);
  check('1440: Needs attention fills the hole the levels leave', a && a.mode, 'hole');
  check('1440: the hole is top right, where the eye goes after the headline', a && a.rows, [[0], [1, 2]]);
  check('1440: the hole is in row 0', a && a.hole.row, 0);
  check('1440: bays are wide enough for "Attention" on one line (>= 190px)', a && a.cellW >= 190, true);
  check('1440: bays are tall enough for name, word and detail', a && a.cellH >= 112, true);
  check('1440: the hole holds Needs attention', a && a.hole.w >= 560 && a.hole.h >= 280, true);
  const col = W.floorPack(demo, 1376 - 331 - 26, 690, o);
  check('1440: and the bay is wider than beside a fixed column', a.cellW > col.cellW, true);
  // the levels plus Needs attention cover the room: no dead block
  const used = a.rows.reduce((s, r) => s + r.reduce((t, i) => t + (demo[i].w * a.cellW + 12 * (demo[i].w + 1)) *
      (o.head + demo[i].h * a.cellH + 12 * (demo[i].h + 1)), 0), 0) + a.hole.w * a.hole.h;
  check('1440: levels and Needs attention cover >= 85 % of the room', used / (1376 * 690) >= 0.85, true);

  const q = W.wallLayout(demo, 1856, 870, Object.assign({}, o, { head: 36, attnW: 460, attnGap: 34 }));
  check('1920: the hole layout too', q && q.mode, 'hole');
  // one wide level leaves no hole that holds the list: the column stays
  const one = [{ uid: 'x', w: 7, h: 5 }];
  check('one level keeps the column', W.wallLayout(one, 1376, 690, o).mode, 'column');
  // levels that fill every row leave no hole: the column stays
  const four = [{ uid: 'a', w: 3, h: 2 }, { uid: 'b', w: 3, h: 2 }, { uid: 'c', w: 3, h: 2 }, { uid: 'd', w: 3, h: 2 }];
  check('four even levels keep the column', W.wallLayout(four, 1376, 690, o).mode, 'column');
  check('nothing readable is null (rotate)', W.wallLayout([{ uid: 'a', w: 7, h: 5 }, { uid: 'b', w: 7, h: 5 }, { uid: 'c', w: 7, h: 5 }], 1376, 690, o), null);
}

// ── round 3: a request that never answers is a failed read ──────────────
// The critic held /api/ui/wall/floor open forever while /api/ui/live kept
// answering: the wall said "Live · updated <now>" for 200 s on frozen data,
// because a fetch with no deadline never fails. overdue() is the deadline
// the walls' ticks hold every data request to.
check('a request inside its deadline is not overdue', W.overdue(T0, T0 + W.FETCH_TIMEOUT_MS), false);
check('past it, it is', W.overdue(T0, T0 + W.FETCH_TIMEOUT_MS + 1), true);
check('no request in flight is never overdue', W.overdue(null, T0 + 999999), false);
check('the deadline leaves the stale rule time to say so', W.FETCH_TIMEOUT_MS <= 30000, true);

// ── /wall alternation ────────────────────────────────────────────────────
check('alternates in the order given', W.wallSequence(['floor', 'qc']), ['floor', 'qc']);
check('one wall stays', W.wallSequence(['qc']), ['qc']);

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall wall_logic checks passed');
