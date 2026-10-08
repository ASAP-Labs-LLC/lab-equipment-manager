// The floor plan's pure logic (static/js/plan.js), shared by the map view
// (/?view=map) and the wall (/floor, piece 13).
//
// Why these are tested here and not only in a browser:
//
// * The plan is SIZED TO THE PLACED BAYS (ia-final §3.2). Judge J1 found the
//   old map drew a fixed grid of empty dashed cells, half the width of the
//   page, with dead space below. The bounding box is arithmetic, so it is
//   held here: no empty first or last row or column, ever.
// * A bay is round(saved / 2.05). Production saves 4.1, 6.15, -2.05 …; the
//   map must read them back as neighbours, and a move must write the same
//   kind of number back, or the old floor and the wall disagree about where
//   anything is.
// * Two instruments saved on one bay is a real production bug (OptiMPP 2 and
//   PAC Flash 2, both 4.1,0). Neither may vanish under the other: the second
//   goes to the NEAREST free bay, deterministically by title then uid, so a
//   repaint never shuffles the floor.
// * Nothing moves until Arrange is entered (P15): canDrag is false for every
//   state that is not "arranging and signed in".
// * The address bar is the view: ?view=map&level=&cause=&focus=&arrange=1.
//   A junk value is ignored, never obeyed.
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/plan.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const P = root.LEMPlan;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};
if (!P) { console.log('  FAIL plan.js does not define window.LEMPlan'); process.exit(1); }

const row = (uid, pos, extra) => Object.assign({
  uid, title: uid.toUpperCase(), href: '/instruments/' + uid, level_uid: 'L1',
  readiness: { state: 'ok', word: 'Ready', glyph: 'final', reason: '', detail: '2 checks in spec' },
  bench: { state: 'in', word: 'Checking in' }, where: { level: 'Ground Floor', placed: !!pos, pos: pos || null },
  problems: [],
}, extra || {});
const at = (lay) => Object.fromEntries(lay.bays.map(b => [b.uid, [b.x, b.y]]));

// ── a bay is the saved number over the pitch ───────────────────────────────
check('pitch is the one production saves at', P.PITCH, 2.05);
check('bay index: production numbers', [4.1, 6.15, -2.05, 0, 8.2].map(P.bayIndex), [2, 3, -1, 0, 4]);
check('bay index: not a number is not a bay', [null, undefined, NaN, 'x', Infinity].map(P.bayIndex), [null, null, null, null, null]);
check('saved coordinate: the same kind of number comes back', [2, 3, -1, 0, 4].map(P.coord), [4.1, 6.15, -2.05, 0, 8.2]);

// ── sized to the placed bays ───────────────────────────────────────────────
// production, 2026-10-01: 16 placed on one level, one not (Agilent GC 2)
const prod = [
  ['Agilent GC 1', [6.15, 4.1]], ['Agilent GC 2', null], ['Aquamax 1', [-2.05, 0]],
  ['Aquamax 2', [2.05, 0]], ['Aquamax 3', [-2.05, 4.1]], ['Auto Grabner', [0, 6.15]],
  ['Eraspec', [8.2, 0]], ['Eraspec NIR', [2.05, 4.1]], ['Eravap', [2.05, -2.05]],
  ['Mini Grabner', [0, 4.1]], ['Multitek NS', [6.15, 2.05]], ['Multitek S', [6.15, 0]],
  ['OptiMPP 1', [4.1, 2.05]], ['OptiMPP 2', [4.1, 0]], ['PAC Flash 1', [0, 0]],
  ['PAC Flash 2', [0, 2.05]], ['Viscocity', [-4.1, 0]],
].map(([t, p]) => row(t.toLowerCase().replace(/\s+/g, '-'), p, { title: t }));
const lay = P.layout(prod);
check('production: the box is 7 bays by 5', [lay.w, lay.h], [7, 5]);
check('production: the origin is the top-left placed bay', lay.origin, [-2, -1]);
check('production: 16 drawn, 1 named as not on the map', [lay.bays.length, lay.unplaced.map(r => r.title)], [16, ['Agilent GC 2']]);
check('production: Viscocity is the left edge, Eravap the top', [at(lay)['viscocity'], at(lay)['eravap']], [[0, 1], [3, 0]]);
const usedX = new Set(lay.bays.map(b => b.x)), usedY = new Set(lay.bays.map(b => b.y));
check('no empty first or last column or row', [usedX.has(0), usedX.has(lay.w - 1), usedY.has(0), usedY.has(lay.h - 1)], [true, true, true, true]);
check('nothing spilled on a floor with no collisions', lay.bays.filter(b => b.spilled).length, 0);

check('one instrument is a one-bay plan', (() => { const l = P.layout([row('a', [8.2, 8.2])]); return [l.w, l.h, at(l).a]; })(), [1, 1, [0, 0]]);
check('nothing placed is an empty plan, not a 0-sized error', (() => { const l = P.layout([row('a', null)]); return [l.w, l.h, l.bays.length, l.unplaced.length]; })(), [0, 0, 0, 1]);
check('no rows at all', (() => { const l = P.layout([]); return [l.w, l.h, l.bays.length]; })(), [0, 0, 0]);

// ── two on one bay: neither vanishes, the order is the instrument's ────────
const clash = [row('pac-flash-2', [4.1, 0], { title: 'PAC Flash 2' }), row('optimpp-2', [4.1, 0], { title: 'OptiMPP 2' }),
               row('gc', [0, 0], { title: 'GC' })];
const c1 = P.layout(clash), c2 = P.layout([...clash].reverse());
check('a clash: the first by title keeps its bay', at(c1)['optimpp-2'], [2, 0]);
check('a clash: the other goes to the nearest free bay and says so',
      [at(c1)['pac-flash-2'], c1.bays.find(b => b.uid === 'pac-flash-2').spilled], [[1, 0], true]);
check('a clash: payload order does not decide it', at(c2), at(c1));

// ── Arrange: one ring of empty bays around the box, to grow into ───────────
const ring = P.layout(prod, { ring: 1 });
check('arranging: one bay of room on every side', [ring.w, ring.h, ring.origin], [9, 7, [-3, -2]]);
check('arranging: the instruments keep their places', at(ring)['viscocity'], [1, 2]);
check('arranging: a grid cell maps back to the saved numbers', P.toSaved(1, 2, ring.origin), { x: -4.1, y: 0 });
check('arranging: the free cells are the ones nobody stands on', P.isFree(ring, 0, 0) && !P.isFree(ring, 1, 2), true);
check('who stands on a cell', [P.occupant(ring, 1, 2), P.occupant(ring, 0, 0)], ['viscocity', null]);

// ── nothing moves until Arrange is entered (P15) ───────────────────────────
check('canDrag: viewing, signed in', P.canDrag({ arranging: false, user: 'Cody' }), false);
check('canDrag: arranging, signed out', P.canDrag({ arranging: true, user: '' }), false);
check('canDrag: nothing known', P.canDrag(null), false);
check('canDrag: arranging, signed in', P.canDrag({ arranging: true, user: 'Cody' }), true);

// ── the address bar is the view ────────────────────────────────────────────
check('parse: nothing', P.parseMapView(''), { level: '', cause: '', focus: '', arrange: false });
check('parse: all of it', P.parseMapView('?view=map&level=L2&cause=ok_but-qc&focus=b2ce21612b3c&arrange=1'),
      { level: 'L2', cause: 'ok_but-qc', focus: 'b2ce21612b3c', arrange: true });
check('parse: junk is ignored, not obeyed', P.parseMapView('?view=map&focus=%3Cscript%3E&level=a%20b&arrange=yes'),
      { level: '', cause: '', focus: '', arrange: false });
check('query: the map view always says it is the map', P.mapQuery({}), '?view=map');
check('query: round trip', P.mapQuery(P.parseMapView('?view=map&level=L2&cause=ok_but-qc')), '?view=map&level=L2&cause=ok_but-qc');
check('query: arrange and focus', P.mapQuery({ focus: 'a1', arrange: true }), '?view=map&focus=a1&arrange=1');

// ── which level the plan draws ─────────────────────────────────────────────
const lv = { levels: [{ uid: 'L1', name: 'Ground Floor' }, { uid: 'L2', name: 'Upper Lab' }], default_level: 'L2',
             instruments: [row('a', [0, 0]), row('b', [0, 0], { level_uid: 'L2' }), row('c', [0, 0], { level_uid: '' })] };
check('level: the one asked for', P.currentLevel(lv, { level: 'L1' }), 'L1');
check('level: a level that is not populated is not obeyed', P.currentLevel(lv, { level: 'L9' }), 'L2');
check('level: the lab default when none is asked for', P.currentLevel(lv, {}), 'L2');
check('level: a focused instrument brings its level', P.currentLevel(lv, { focus: 'a' }), 'L1');
check('level: an asked-for level beats the focus', P.currentLevel(lv, { focus: 'a', level: 'L2' }), 'L2');
check('level: a chosen cause brings the level of the first instrument with it',
      P.currentLevel(Object.assign({}, lv, { instruments: [row('z', [0, 0], { level_uid: 'L1', problems: [{ key: 'not_ok-qc' }] }), ...lv.instruments] }),
                     { cause: 'not_ok-qc' }), 'L1');
check('level: an asked-for level beats the cause', P.currentLevel(lv, { cause: 'not_ok-qc', level: 'L2' }), 'L2');
check('level: one populated level means no level at all', P.currentLevel({ levels: [], instruments: lv.instruments }, {}), '');
check('on a level: an instrument with no level stands on the default',
      P.onLevel(lv.instruments, 'L2', lv).map(r => r.uid), ['b', 'c']);
check('on no level: everyone', P.onLevel(lv.instruments, '', lv).map(r => r.uid), ['a', 'b', 'c']);

// ── a bay's words ──────────────────────────────────────────────────────────
const bad = row('o', [0, 0], { title: 'OptiMPP 1',
  readiness: { state: 'not_ok', word: 'Stop', glyph: 'error',
               detail: 'Cloud Point and Pour Point out of spec · calibration overdue since 9 Aug too' } });
const w = P.bayWords(bad);
check('bay: name, glyph and word, one detail line', [w.name, w.glyph, w.word, w.detail],
      ['OptiMPP 1', 'error', 'Stop', 'Cloud Point and Pour Point out of spec']);
check('bay: the full story is in the title, never lost to the ellipsis', w.title,
      'OptiMPP 1 · Stop: Cloud Point and Pour Point out of spec · calibration overdue since 9 Aug too');
check('bay: a stop is marked (ink border, bold word), not filled red', [w.stop, w.state], [true, 'not_ok']);
const quiet = P.bayWords(row('q', [0, 0], { bench: { state: 'stopped', word: 'Stopped' },
  readiness: { state: 'cant_tell', word: "No data", glyph: 'dashed', detail: 'Its bench stopped checking in' } }));
check('bay: a stopped bench gets the dashed outline', [quiet.stopped, quiet.stop], [true, false]);
check('bay: never checked in is stopped too', P.bayWords(row('n', [0, 0], { bench: { state: 'never' } })).stopped, true);
check('bay: an OK bay with no detail says nothing extra', P.bayWords(row('k', [0, 0], { readiness: { state: 'ok', word: 'Ready', glyph: 'final', detail: '' } })).title, 'K · Ready');

// a short last word stays with the one before it, so a two-line name never
// strands its number ("PAC Flash / 1" on the wall at 1440): it breaks as
// "PAC / Flash 1" instead
check('glue: a number stays with its name', P.glue('PAC Flash 1'), 'PAC Flash\u00a01');
check('glue: GC 1', P.glue('Agilent GC 1'), 'Agilent GC\u00a01');
check('glue: a long last word is left to wrap', P.glue('Mini Grabner'), 'Mini Grabner');
check('glue: one word', P.glue('Eravap'), 'Eravap');
check('glue: a short word that is not a number still wraps ("Eraspec / NIR")', P.glue('Eraspec NIR'), 'Eraspec NIR');
check('glue: a one-letter model', P.glue('Multitek S'), 'Multitek\u00a0S');

// ── the cell size: uniform, filling the height it is given, within reason ──
check('cell height: fills the room', P.cellHeight(600, 4, 12), 141);
check('cell height: two rows stop short of a poster', P.cellHeight(700, 2, 12), 240);
check('cell height: never taller than the bay is wide', P.cellHeight(700, 2, 12, 200), 200);
check('cell height: a narrow bay still gets a readable height', P.cellHeight(700, 2, 12, 60), 96);
check('cell height: five rows in 640px', P.cellHeight(640, 5, 12), 118);
check('cell height: never below a readable bay (two-line name, word, detail)', P.cellHeight(200, 6, 12), 96);
check('cell height: never a poster', P.cellHeight(2000, 1, 12), 240);
check('cell height: no room reported', P.cellHeight(NaN, 3, 12), 112);
// a 4K desk (2026-10-08, "on my 4k screen it takes up 25%"): a wide bay may
// grow past 240, to three quarters of its width, so the floor fills the screen
check('cell height: a wide bay on a big screen grows past 240', P.cellHeight(2000, 5, 12, 450), 337);
check('cell height: a wide bay still stops at the room it has', P.cellHeight(800, 5, 12, 450), 150);

// ── a short word, for a bay too narrow for the whole one ───────────────────
// the glyph's SHAPE still tells the states apart; the title has the words
check('short words', ['not_ok', 'ok_but', 'ok', 'off_line', 'cant_tell', 'no_qc', 'zzz'].map(P.shortWord),
      ['Stop', 'Attn', 'Ready', 'Off line', 'No data', 'No data', '']);
check('a cut at a word, never mid-word when a word boundary is near', P.cutAt('Calibration overdue since 25 Jul', 24), 'Calibration overdue…');
check('a cut with no space to use cuts the word', P.cutAt('Pensky-Martens', 8), 'Pensky-M…');
check('a cut never ends on a separator', P.cutAt('PM overdue · calibration', 13), 'PM overdue…');

// ── density: a wide floor packs tighter instead of cutting every word ─────
// production's 7 bays across a 744px plan are 92px at a 12px gutter; at 8px
// they are 97px, and the bay's padding shrinks with them
check('density: room enough', P.density(744, 3, false), { compact: false, gap: 12, minH: 96 });
check('density: production at the desk packs tighter', P.density(744, 7, false), { compact: true, gap: 8, minH: 96 });
check('density: Arrange is compact, and its bays are shorter (names only)', P.density(1000, 3, true), { compact: true, gap: 8, minH: 72 });
check('density: nothing measured yet', P.density(0, 0, false), { compact: false, gap: 12, minH: 96 });
check('cell height: Arrange may go shorter', P.cellHeight(200, 6, 8, undefined, 72), 72);

// ── the needs column: a cause brightens its instruments ────────────────────
check('dimmed: no cause, nothing dimmed', P.dimmed(bad, ''), false);
check('dimmed: a cause it does not have', P.dimmed(row('x', [0, 0], { problems: [{ key: 'ok_but-qc' }] }), 'not_ok-qc'), true);
check('dimmed: a cause it has', P.dimmed(row('x', [0, 0], { problems: [{ key: 'not_ok-qc' }] }), 'not_ok-qc'), false);


// ── the map's levels are pages too (2026-10-07) ────────────────────────────
// Ryan, after v4.1.0 turned the wall's levels into swipeable pages: "the map
// didn't get updated either". The map's level tabs now carry the worst state
// on each level and how many there need you, so a problem upstairs is seen
// from the ground-floor tab; a swipe or an arrow key turns the level. The
// rank and the swipe are the wall's (wall_logic.js), and the two pages must
// never disagree about which state is worse, so the wall's copy is loaded
// here too and held to the same answers.
{
  const lv = { levels: [{ uid: 'L1', name: 'Ground' }, { uid: 'L2', name: 'Upstairs' }, { uid: 'L3', name: 'Empty' }],
               default_level: 'L1', instruments: [
    row('a', [0, 0], { level_uid: 'L1', needs_you: false }),
    row('b', [0, 2.05], { level_uid: 'L1', needs_you: true, readiness: { state: 'ok_but', word: 'Attention', glyph: 'half' } }),
    row('c', [0, 0], { level_uid: 'L2', needs_you: true, readiness: { state: 'not_ok', word: 'Stop', glyph: 'error' } }),
    row('d', [2.05, 0], { level_uid: 'L2', needs_you: true, readiness: { state: 'off_line', word: 'Off line', glyph: 'off' } }),
    row('e', [4.1, 0], { level_uid: 'nowhere', needs_you: false }),   // an unknown level counts on the default
  ] };
  check('each level tab: its worst state and how many there need you', P.levelMarks(lv),
        [{ uid: 'L1', name: 'Ground', state: 'ok_but', glyph: 'half', need: 1, total: 3 },
         { uid: 'L2', name: 'Upstairs', state: 'not_ok', glyph: 'error', need: 2, total: 2 },
         { uid: 'L3', name: 'Empty', state: null, glyph: '', need: 0, total: 0 }]);
  check('no levels, no tabs', P.levelMarks({ levels: [], instruments: [row('a', [0, 0])] }), []);
  check('a level turned by hand wraps around, both ways',
        [P.stepLevel(lv, 'L1', 1), P.stepLevel(lv, 'L3', 1), P.stepLevel(lv, 'L1', -1), P.stepLevel(lv, '', 1)],
        ['L2', 'L1', 'L3', 'L2']);

  const wsrc = fs.readFileSync(new URL('../../static/js/wall_logic.js', import.meta.url), 'utf8');
  const wroot = {};
  new Function('window', 'module', wsrc)(wroot, undefined);
  const W = wroot.LEMWallLogic;
  const sets = [['ok', 'not_ok'], ['off_line', 'cant_tell'], ['ok_but', 'cant_tell'], ['ok'], [], ['ok', 'martian']];
  check('the map and the wall rank states alike', sets.map(P.worstState), sets.map(W.worstState));
  const drags = [[-90, 10], [90, -5], [-30, 0], [-80, 140], [61, 0]];
  check('the map and the wall read a swipe alike', drags.map(([x, y]) => P.swipeStep(x, y)), drags.map(([x, y]) => W.swipeStep(x, y)));
  check('glyphs alike', ['not_ok', 'ok_but', 'cant_tell', 'off_line', 'ok', null].map(P.stateGlyph),
        ['not_ok', 'ok_but', 'cant_tell', 'off_line', 'ok', null].map(W.stateGlyph));
}

// ── a bay's detail is shortened by meaning, never cut mid-word ────────────
// The map cut "Calibration overdue since 30 Jul" to "Calibration…" on a
// phone and "Cloud Point and Pour Point out of spec" to "Cloud Point and
// Pour Point…" on a desktop: the cut kept the half that says least. The
// short form drops the date tail and anything after the first " · ", which
// is what the record (one tap away) spells out.
check('the date tail goes first', P.shortDetail('Calibration overdue since 30 Jul'), 'Calibration overdue');
check('then everything after the first ·', P.shortDetail('Flash Point out of spec · calibration overdue since 26 Jun too'), 'Flash Point out of spec');
check('a short detail is left alone', P.shortDetail('2 checks in spec'), '2 checks in spec');
check('nothing in, nothing out', P.shortDetail(''), '');

if (fails) { console.log(`\n${fails} failed`); process.exit(1); }
console.log('\nall plan checks passed');
