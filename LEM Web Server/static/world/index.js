/* index.js — the lab as a place.
 *
 * The floor map used to be an SVG drawing of boxes on a deck. It is now a
 * rendered world: every instrument is a station building standing in forest,
 * every parse sends a train out of that station and down the line to the
 * LabCore terminal, and the sky, the weather and the light are real-time.
 *
 * What did NOT change is the contract with the page around it. `floor.html`
 * still polls `/api/machines` and `/api/events`, still opens the same rails,
 * dialogs and menus, and still writes positions back to `/api/machines/<uid>/
 * position`. This file is the whole seam between that page and the world:
 *
 *     const world = new LEMWorld(canvas, {onSelect, onContext, onHover, onMove});
 *     world.setMachines(list);      // every poll, whole payload
 *     world.setSelected(uid);       // the rail opened this instrument
 *     world.parse(uid, labId);      // a print was parsed → send a train
 *
 * Subsystems are loaded dynamically and independently. A subsystem that fails
 * to load is logged and skipped rather than taking the map down with it — the
 * floor is a status display before it is a rendering, and a broken weather
 * module must never be the reason nobody can see that an instrument is RED.
 */
import * as THREE from 'three';
import {Engine} from 'world/engine.js';
import {CameraRig} from 'world/camera.js';
import * as Tex from 'world/textures.js';

/* One bay on the old floor grid is this many metres of map, ACROSS A RANK.
 *
 * A bay is no longer square, and this constant kept the name because it kept
 * the meaning it was written with: "a rank of instruments 2.05 apart becomes
 * stations ~90m apart". A rank runs across, in x. The pitch between ROWS is
 * `METRES_PER_BAY_Z` and it is still 44 — see the note there, and note that
 * `HUB_SETBACK` is derived from THAT one and has not moved.
 *
 * WHY IT IS NO LONGER 44. A bay across a rank is the stand pitch on the loading
 * road: rail.js plants one stand per bench at `sd.nearest(station.x, dockZ).s`,
 * so the distance between two stabled trains is the fleet's own x-spacing and
 * nothing else. rail.js measured what that has to be and asked for it
 * (REQUESTS.md, "the passing loop is 25 m short"): a loop transition needs
 * 49.3 m, splitting the loading apron for a mid-rank turnout costs
 * 3 × PAVE_TAPER = 27 m, and at the old 91.5 m pitch that leaves 15.2 m for a
 * consist trains.js builds 64.5–84 m long. The loop fitted and the train had
 * nowhere to stand. The ask was 115 m; 57 delivers a measured 116.5–117.0.
 *
 * The last dimension rail asked this file for (236 m at `hub.z`) was REFUSED by
 * a sweep, so this one was swept too, and it is granted because the sweep says
 * it is free. `harness/ix-spacing.mjs`, one page load, the fleet re-planned and
 * terrain and rail rebuilt at every step, across all six of `soak.mjs`'s own
 * layouts. Stand gap is the minimum over every loading road:
 *
 *   metres per bay, x     44    50.6   57.2
 *   stand gap, layout 0   89.9  103.4  116.9
 *   branches L0/L1/L2      2/1/7 2/1/7  2/1/7
 *   branches L3/L4/L5      5/6/1 5/6/1  5/6/1
 *   stations routed        7/7   7/7    7/7   (on every layout)
 *   rail exceptions       main 90/44.9 + one per branch, IDENTICAL at every step
 *   islandR, layout 0     479   499    519
 *
 * Nothing falls off a cliff, which is the opposite of what `HUB_SETBACK` does
 * and is worth stating plainly: the setback cliff is driven by rail's `legIn`
 * clearance closing on its own throat radius, and that is a Z dimension. Site
 * WIDTH does not enter it — which the setback sweep already implied, since a
 * rank of seven (541 m wide) and the real floor (271 m wide) failed at the same
 * setback as each other.
 *
 * The price is paid in island radius: +8.4% on the real floor, +10.7% on the
 * sparse layout, because the island is sized from the railway's keep-out hull
 * and the hull got wider. That is a real cost and it is the reason this is 57
 * and not 70. Cold-load gates, ablated in one session (44 → 57): ework spans
 * 72 → 66 and 3973 → 4360 m; alignment holds 2.5% on every route; tq-relief
 * mean slope 15.79 → 15.41 deg but radius sigma 62.7 → 80.7 and outline
 * roughness 16.3 → 23.8; tq-budget free radius 5.3% → 5.3%. */
export const METRES_PER_BAY = 57;

/* The pitch between ROWS, which is a different engineering problem from the
 * pitch across a rank and keeps the original number.
 *
 * Rows are separated by what the railway needs to get a branch off the ring and
 * into each one, and by nothing the loading road cares about. It is left at 44
 * for two reasons that are both measured elsewhere in this file: `HUB_SETBACK`
 * is derived from it and must not move (the sweep in that note), and the 'grid'
 * arrangement's own note measures a deeper site plan costing two rows in three
 * their railway. */
export const METRES_PER_BAY_Z = 44;

/* The ruling gradient of the railway, as a fraction. QUOTED from rail.js's
 * `GRADE_RULING`, which is where it is decided; it is here because the bench
 * schedule below is a set of ELEVATIONS and a level a train cannot climb to is
 * not a site plan, it is a picture. If rail.js ever moves its ruling gradient,
 * this moves with it — there is no way to read it across from here. */
export const RULING_GRADE = 0.025;

/* How far north of the nearest row the LabCore terminal stands, in metres.
 *
 * 185, and it STAYS 185 — this is a measured refusal of rail.js's ask, not a
 * taste constant anybody forgot to revisit. Written down because the next
 * person to read `Rail.exceptions` will want to move it, and moving it kills
 * the railway.
 *
 * rail.js records a formal exception on this floor —
 *
 *     main, minimum radius: want 90, got 44.9 — "the platform road is 124m
 *     from the nearest row's running line. A 90m corner, a 1:6 lead and a 90m
 *     branch throat need 201m, and the hub's position is set by index.js from
 *     that row."
 *
 * — and asks in scratchpad/REQUESTS.md for 236m of that gap. The gap is this
 * setback minus 60.4m (`DOCK_OFFSET` 26 south of the hub, `DOCK_OFFSET +
 * LOOP_OFFSET` 34.4 north of the row), so the ask is a setback of 297.
 *
 * It was tried. At 297 the ring corner comes out at 106m and every exception
 * clears — and every BRANCH is refused, `main` and `terminal.loop` go dead,
 * and no station has a route to the terminal. Swept in one page load, the
 * demo fleet, terrain and rail re-planned at each step:
 *
 *     setback  185  190  195  200  205  210  215  225  ... 297
 *     branches   2    2    2    2    2    2    0    0        0
 *     ring R    45  47.7 50.5 53.2 55.9 58.6 61.4 66.8      106
 *     islandR  479  480  481  482  483  484  485  487       506
 *
 * The cliff is rail's own arithmetic closing on itself, and the two constants
 * that disagree are both quoted in rail.js. `_rebuild` stands each leg off the
 * site by `legIn = 124 + 0.86·R_MIN_YARD + 24` — a clearance sized for a
 * throat curve at the YARD MINIMUM, 55m. `_branch` then sizes the throat from
 * the z it has actually been given, `(swing − LEAD_Z − 4)/TURN_Z`, and a
 * bigger setback is precisely what makes `swing` bigger. The branch's corner
 * vertex stands `0.86·R` inboard of the leg, so past about 60m of throat the
 * curve eats the 124m of clear line the loading road's outermost turnout
 * needs, and `_branch` refuses the row. More room for the ring buys a wider
 * throat, and it is the wider throat that will not fit.
 *
 * That reading is measured, not deduced from the source alone. A single COLUMN
 * of benches is the one layout where `legIn` is beaten by rail's other floor,
 * `hub.x ± 210` — the legs stand 210m out instead of 195.3, 14.7m more on each
 * side — and it is the one layout with no cliff: seven benches in a file keep
 * all seven branches at every setback up to 225. A rank of seven (site 541m
 * wide) and the real floor (271m wide) fall over at the same setback as each
 * other, which is what a clearance measured off the END BENCH does and what an
 * absolute site size would not.
 *
 * So the ask cannot be granted from here. It can be granted in rail.js: size
 * `legIn` from the throat radius `_branch` will actually choose, or cap that
 * radius at what `legIn` reserved. Either makes 297 safe and takes `main` from
 * 44.9m to 106m. Until then this is 185 and the exception stands, because a
 * recorded exception is a better outcome than a dead railway — and
 * `harness/soak.mjs` is the gate that says so. */
export const HUB_SETBACK = METRES_PER_BAY_Z * 4.2;

/* The subsystems, in build order. Terrain has to exist before anything can ask
 * for a ground height; the hub and the rail have to exist before a train can
 * be dispatched onto it; vegetation goes last because it needs to know where
 * everything else stands so it can avoid it. */
const SUBSYSTEMS = [
  {id: 'sky',        module: 'world/sky.js',        export: 'Sky'},
  {id: 'gi',         module: 'world/gi.js',         export: 'GlobalIllumination'},
  {id: 'terrain',    module: 'world/terrain.js',    export: 'Terrain'},
  {id: 'buildings',  module: 'world/buildings.js',  export: 'Buildings'},
  {id: 'rail',       module: 'world/rail.js',       export: 'Rail'},
  {id: 'trains',     module: 'world/trains.js',     export: 'Trains'},
  {id: 'vegetation', module: 'world/vegetation.js', export: 'Vegetation'},
  /* Props go after vegetation for the same reason vegetation goes last:
   * they need to know where everything else stands so they can avoid it. */
  {id: 'props',      module: 'world/props.js',      export: 'Props'},
  {id: 'weather',    module: 'world/weather.js',    export: 'Weather'},
  {id: 'labels',     module: 'world/labels.js',     export: 'Labels'},
];

/** A stable per-instrument random source: the same machine always generates the
 *  same building, so operators learn the site by shape the way they learned the
 *  old floor by silhouette. */
export function seededRandom(key) {
  let h = 2166136261;
  for (const ch of String(key)) {
    h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h ^= h >>> 13;
    return ((h >>> 0) % 100000) / 100000;
  };
}

/** Which bay each instrument stands in.
 *
 *  Kept a pure function, and exported, for two reasons. It is the one piece of
 *  the world with a rule that can be wrong in a way nobody sees — and it has
 *  been: two instruments are saved on the SAME bay (OptiMPP 2 and PAC Flash 2,
 *  both at 4.1,0), and when placement depended on payload order, whichever
 *  reported last claimed the square and the other vanished underneath it,
 *  flipping on every refresh. Ryan reported it as "everytime this thing
 *  refreshes it changes layout".
 *
 *  So every decision here is made in a fixed order derived from the INSTRUMENT:
 *  title first so the result reads sensibly, uid as the tiebreak because titles
 *  are not unique. A collider spills to the next free bay, where it can be seen
 *  and dragged where it belongs, rather than hiding under its neighbour.
 *
 *  `tests/js/layout.mjs` pulls this function out of the shipped file and
 *  shuffles the real floor's payload at it. Keep it pure.
 */
export function claimBays(machines, BAY = 2.05) {
  const seq = [...machines].sort((a, b) =>
    String(a.title || '').toLowerCase().localeCompare(
      String(b.title || '').toLowerCase()) ||
    String(a.machine_uid).localeCompare(String(b.machine_uid)));

  const taken = new Set();
  const placed = [];
  const spill = [];
  const key = (x, y) => `${Math.round(x / BAY)},${Math.round(y / BAY)}`;

  for (const m of seq) {
    if (!Array.isArray(m.pos)) { spill.push(m); continue; }
    const k = key(m.pos[0], m.pos[1]);
    if (taken.has(k)) { spill.push(m); continue; }
    taken.add(k);
    placed.push({m, gx: m.pos[0], gy: m.pos[1]});
  }
  const cols = Math.max(2, Math.ceil(Math.sqrt(seq.length * 1.25)));
  let slot = 0;
  for (const m of spill) {
    let gx, gy, k;
    do {
      gx = (slot % cols) * BAY; gy = Math.floor(slot / cols) * BAY; slot++;
      k = key(gx, gy);
    } while (taken.has(k));
    taken.add(k);
    placed.push({m, gx, gy, spilled: Array.isArray(m.pos)});
  }
  return placed;
}

/** Whole-floor arrangements, in bay units.
 *
 *  An operator rearranging a lab does not want to drag sixteen instruments one
 *  at a time into a straight line. These are the three shapes worth having, and
 *  each is deterministic from the instrument — the same fleet always arranges
 *  the same way, so "Grid" is a place you can go back to rather than a shuffle.
 *
 *  Ordered by title, uid as the tiebreak, for the same reason `claimBays` is:
 *  anything keyed off payload order churns every time an instrument reports.
 */
export function arrangement(machines, kind = 'grid', BAY = 2.05) {
  const seq = [...machines].sort((a, b) =>
    String(a.title || '').toLowerCase().localeCompare(
      String(b.title || '').toLowerCase()) ||
    String(a.machine_uid).localeCompare(String(b.machine_uid)));
  const out = {};
  if (kind === 'row') {
    seq.forEach((m, i) => { out[m.machine_uid] = [i * BAY, 0]; });
  } else if (kind === 'compact') {
    /* As square as the fleet allows, which is what "tidy" means on a floor
     * plan — and it keeps the rail runs short. */
    const cols = Math.max(1, Math.round(Math.sqrt(seq.length)));
    seq.forEach((m, i) => {
      out[m.machine_uid] = [(i % cols) * BAY, Math.floor(i / cols) * BAY];
    });
  } else {                                  // 'grid' — roomy, one aisle between
    /* The aisle is ACROSS a row and not between rows, and that asymmetry is
     * measured rather than tidy. Grid used to be two bays in both directions,
     * and on the seven-instrument floor that is the single most expensive shape
     * an operator can ask for: the island is sized from the railway's keep-out
     * hull, the hull is sized from the block, and a block twice as deep pushes
     * the ring's corners out on the diagonal. Same fleet, one page load, the
     * arrangement applied through `setMachines` and terrain and rail re-planned
     * at each step; the numbers are `terrain.siteRadial`, `terrain.islandR` and
     * `rail.branches` read off the live subsystems:
     *
     *     grid, 2 bays × 2 bays   siteRadial 504   islandR 594   branches 1 of 3
     *     grid, 2 bays × 1 bay    siteRadial 454   islandR 541   branches 3 of 3
     *
     * Nine per cent of island radius, and — the part that is not about the
     * picture — every row gets a railway instead of one row in three. Rows two
     * bays apart are far enough that `_branch` sizes its throat curve off the z
     * it has rather than off the ring's corner, the radius comes out past 60m,
     * and rail.js then refuses the branch because the curve's vertex eats the
     * 124m of clear line its loading road needs. Same cliff as `HUB_SETBACK`,
     * same one-line fix in rail.js, and until that lands a deeper site plan is
     * a site plan with less railway on it.
     *
     * A bay across is still 90m, which is what the loading road needs anyway:
     * the docks are one bay apart and trains.js builds consists 64.5–84m long. */
    const cols = Math.max(2, Math.ceil(Math.sqrt(seq.length * 1.25)));
    seq.forEach((m, i) => {
      out[m.machine_uid] = [(i % cols) * BAY * 2, Math.floor(i / cols) * BAY];
    });
  }
  return out;
}

/* ---- the bench schedule ---------------------------------------------------
 *
 * WHY THIS EXISTS. terrain.js grades the whole block of instruments to ONE
 * tilted plane (`_fitDesignPlane`), clamped to a rail-legal 1.8% on each axis.
 * On the real floor that plane is AT the clamp on both axes, and the result is
 * measured (`harness/ix-bench.mjs`, this file's own instrument):
 *
 *   natural ground under the eight fitted points spans   34.9 m
 *   the design plane's range over the seven stations      4.9 m
 *   over the station block, 10 m grid: 581 fill cells against 71 cut,
 *     max fill 39.8 m, max cut 5.0 m, mean |earth moved| 9.74 m
 *   the LabCore terminal stands on                       31.1 m of fill
 *
 * One plane is burying thirty metres of relief and the terminal is on a
 * thirty-metre embankment. That is the "podium", and the round-29 verdict — no
 * terminator, a gully with no lit wall and no shaded one — is downstream of it:
 * a 1.8% wash is 1.03 degrees and photographs dead flat.
 *
 * WHAT A BENCH SCHEDULE CHANGES, AND WHAT IT CANNOT. It cannot express much
 * more TOTAL relief. Every bench has to be reachable by a railway held to a
 * 2.5% ruling gradient, so across the real floor, whose furthest two benches
 * are 333 m apart, the whole level set is pinned inside 0.025 × 333 = 8.3 m
 * however it is arranged. The plane already spends 4.87 m of that. Benching is
 * worth about 1.7 times the plane in TOTAL fall and no more, and saying
 * otherwise would be the fourteenth confident wrong answer on this project.
 *
 * What it changes is WHERE the fall is, and that is the whole point. A plane
 * spreads 4.87 m as a 1.8% wash over four hundred metres — 1.03 degrees, which
 * photographs dead flat and is exactly the round-29 verdict. A bench schedule
 * spends the same metres as level platforms with SHORT BATTERS between them.
 * What the schedule actually publishes on the real floor, at the 1:2 below
 * (`harness/ix-verify.mjs`):
 *
 *   row:0 → row:11   1.93 m over an 8.0 m batter   13.6 degrees
 *   row:0 → hub      4.62 m over a  9.2 m batter   26.6 degrees
 *   row:11 → hub     2.69 m over an 8.0 m batter   18.6 degrees
 *
 * A 26.6-degree face running the width of the site is an opposed pair of faces,
 * which is the thing a terminator needs and the thing this site has never had.
 * THE WHOLE VALUE OF THIS CONTRACT IS IN THE BATTER BEING SHORT — smoothed over
 * a hundred metres it buys nothing at all, and it will measure exactly as flat
 * as before while looking as though it was consumed.
 *
 * It also halves the earthwork. Balanced cut and fill takes the worst move on
 * the real floor from 39.8 m of fill to 14.55 m either way, and the mean
 * |earth moved| over the bench probe cells from 16.83 m to 13.57 m.
 */

/* Terrain's `pad` feature is 27 m half-extent and its `hub` is 64 × 48. The
 * probe windows match, because the level of a bench should be the level of the
 * ground the bench's own platform covers and not of the field beyond it. */
const PROBE_PAD_HALF = 27, PROBE_HUB_HALF_X = 64, PROBE_HUB_HALF_Z = 48;
const PROBE_STEP = 13.5;

/* How the step between two benches is meant to be built. A batter is a cut or
 * fill FACE: 1:2 is the flattest a worked slope is normally laid back to, and
 * it is steep enough to read against a low sun, which is the entire point. The
 * minimum run keeps a small step from becoming a kerb; the maximum stops a big
 * one from being smoothed into another wash. */
const BENCH_BATTER = {grade: 0.5, minRunM: 8, maxRunM: 30};

/** The rows of benches, as a grouping — no elevations, no terrain required.
 *
 *  Pure and exported for the same reason `claimBays` is: it is a rule that can
 *  be wrong in a way nobody sees, and three other files already compute their
 *  own copy of it.
 */
export function benchGroups(stations, hub) {
  const by = new Map();
  for (const s of stations) {
    const k = s.row ?? Math.round(s.z / 8);
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(s);
  }
  const mk = (id, key, kind, list, cx, cz, probe) => ({
    id, key, kind, n: list.length,
    uids: list.map(s => s.uid).sort(),
    cx, cz,
    minX: list.length ? Math.min(...list.map(s => s.x)) : cx,
    maxX: list.length ? Math.max(...list.map(s => s.x)) : cx,
    minZ: list.length ? Math.min(...list.map(s => s.z)) : cz,
    maxZ: list.length ? Math.max(...list.map(s => s.z)) : cz,
    probe,
    level: null, levelAbsolute: null, naturalM: null, moveM: null,
  });
  const out = [...by.entries()].sort((a, b) => a[0] - b[0]).map(([key, list]) => {
    const xs = list.map(s => s.x), zs = list.map(s => s.z);
    return mk(`row:${key}`, key, 'row', list,
              (Math.min(...xs) + Math.max(...xs)) / 2,
              (Math.min(...zs) + Math.max(...zs)) / 2,
              {centres: list.map(s => [s.x, s.z]),
               halfX: PROBE_PAD_HALF, halfZ: PROBE_PAD_HALF,
               stepM: PROBE_STEP, reduce: 'median'});
  });
  if (hub) {
    out.push(mk('hub', null, 'hub', [], hub.x, hub.z,
                {centres: [[hub.x, hub.z]],
                 halfX: PROBE_HUB_HALF_X, halfZ: PROBE_HUB_HALF_Z,
                 stepM: PROBE_STEP, reduce: 'median'}));
  }
  return out;
}

/** Every point a bench's level is measured over. */
export function benchProbePoints(probe) {
  const pts = [];
  if (!probe || !Array.isArray(probe.centres)) return pts;
  for (const [cx, cz] of probe.centres) {
    for (let dx = -probe.halfX; dx <= probe.halfX + 1e-9; dx += probe.stepM) {
      for (let dz = -probe.halfZ; dz <= probe.halfZ + 1e-9; dz += probe.stepM) {
        pts.push([cx + dx, cz + dz]);
      }
    }
  }
  return pts;
}

/** Levels for a set of benches, from the natural ground under each.
 *
 *  `naturalM[i]` is the natural (ungraded) elevation under bench `i`, in
 *  whatever datum the sampler works in — the answer does not depend on it, see
 *  below. Pure, so `tests/js` can shuffle it.
 *
 *  THE RULE, in full, because a consumer has to be able to re-derive it:
 *
 *  1. `run(a,b) = |cx_a − cx_b| + |cz_a − cz_b|`. Manhattan, not straight line,
 *     because rail.js's railway is axis-aligned — a ring with north–south legs
 *     and an east–west platform road — so a path between two benches turns one
 *     corner and Manhattan is its length. It is a LOWER BOUND on the real rail
 *     run, which detours out to the leg and back — measured, the chord is a
 *     1.9x to 3.9x underestimate (`harness/ix-verify.mjs` reports both), and
 *     REQUESTS.md asks rail for a bench-to-bench run so it can be relaxed.
 *  2. One uniform scale `k = min(1, min_pairs RULING_GRADE·run / |ΔN|)`, NOT a
 *     per-bench clamp. This is measured, not taste: on the real floor a
 *     per-bench clamp INVERTS the two rows, because the far row has the longer
 *     run and therefore the larger allowance while sitting on the lower ground.
 *     A uniform scale keeps the shape of the natural ground exactly and
 *     compresses it, and it reduces to one number that can be reported: "this
 *     plan expresses k of its own relief".
 *  3. The datum is chosen to MINIMISE THE WORST EARTHWORK — `d` such that
 *     `max_b |d + k·N_b − N_b|` is least, i.e. the midrange of `(1−k)·N`. That
 *     is what balances cut against fill; the plane does not do this and that is
 *     why it is 8:1 fill on the real floor.
 *  4. `level` is quoted relative to the mean of the finished bench elevations,
 *     so it is INVARIANT to the sampler's datum: add a constant to every
 *     `naturalM` and every `level` is unchanged (`k` is a ratio of differences,
 *     `d` shifts by `(1−k)·c`, the mean shifts by `c`). That matters because
 *     terrain's `_smoothBase` is unshifted while `heightAt` carries `yShift`,
 *     and `yShift` is itself derived from the fit — so an absolute elevation
 *     here would be a circular reference and a relative one is not.
 */
export function benchSchedule(benches, naturalM, opts = {}) {
  const grade = opts.grade ?? RULING_GRADE;
  const n = benches.length;
  const N = naturalM.map(Number);
  const out = {scale: 1, binding: null, datumAbsolute: 0,
               naturalSpanM: 0, expressedM: 0, maxCutM: 0, maxFillM: 0,
               level: [], levelAbsolute: [], steps: []};
  if (!n || N.some(v => !isFinite(v))) return out;

  const run = (a, b) => Math.abs(benches[a].cx - benches[b].cx)
                      + Math.abs(benches[a].cz - benches[b].cz);
  let k = 1, binding = null;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const dN = Math.abs(N[i] - N[j]);
    if (dN < 1e-6) continue;
    const cap = grade * run(i, j) / dN;
    if (cap < k) { k = cap; binding = [benches[i].id, benches[j].id]; }
  }
  k = Math.max(0, Math.min(1, k));
  const d = (1 - k) * (Math.max(...N) + Math.min(...N)) / 2;
  const abs = N.map(v => d + k * v);
  const mean = abs.reduce((s, v) => s + v, 0) / n;

  out.scale = k;
  out.binding = binding;
  out.datumAbsolute = mean;
  out.levelAbsolute = abs;
  out.level = abs.map(v => v - mean);
  out.naturalSpanM = Math.max(...N) - Math.min(...N);
  out.expressedM = Math.max(...abs) - Math.min(...abs);
  out.maxCutM = Math.max(0, ...N.map((v, i) => v - abs[i]));
  out.maxFillM = Math.max(0, ...N.map((v, i) => abs[i] - v));
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const rise = abs[j] - abs[i], r = run(i, j);
    out.steps.push({from: benches[i].id, to: benches[j].id,
                    riseM: rise, runM: r,
                    gradePct: r > 0 ? (rise / r) * 100 : 0,
                    legal: r > 0 ? Math.abs(rise / r) <= grade + 1e-9 : true});
  }
  return out;
}

export class LEMWorld {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.opts = opts;
    this.machines = [];
    this.selected = null;
    this.locked = true;
    this.subsystems = new Map();
    this.failed = [];
    this._listeners = new Map();

    this.engine = new Engine(canvas, {autoQuality: opts.autoQuality !== false});
    this.scene = this.engine.scene;
    this.camera = this.engine.camera;
    this.rig = new CameraRig(this.camera, canvas);
    this.engine.add({update: dt => this.rig.update(dt)});

    /* Weather and time of day are world state, owned here so every subsystem
     * reads one copy: the sky tints the light, the terrain darkens when wet,
     * the trains throw spray, and the buildings' windows come on at dusk. */
    this.weather = {
      preset: 'clear', rain: 0, snow: 0, wetness: 0, fog: 0.12,
      wind: 0.35, windAngle: 0.6, cloud: 0.25, temperature: 14,
    };
    const now = new Date();
    this.timeOfDay = now.getHours() + now.getMinutes() / 60;

    /* The season, as a first-class world property.
     *
     * It existed before this, by accident: vegetation read
     * `weather.temperature` to decide how much autumn colour to mix, so a cold
     * snap turned the forest orange in what every other cue said was July. Two
     * rounds of blind critics saw an October wood in a summer frame and nobody
     * could guess the cause was a thermometer. Weather is what today is doing;
     * season is what time of year it is. They are different facts and they now
     * live apart.
     *
     * A continuous 0..1 around the year rather than four names, because the
     * interesting part is the turn — a forest half-way into autumn is a real
     * thing and a four-state enum cannot say it. Named points are provided for
     * anyone who wants to be blunt about it.
     */
    this.season = LEMWorld.seasonNow(now);

    this.ctx = {
      THREE, Tex,
      scene: this.scene, camera: this.camera, renderer: this.engine.renderer,
      engine: this.engine, rig: this.rig, world: this,
      weather: this.weather,
      season: this.season,
      seededRandom,
      METRES_PER_BAY, METRES_PER_BAY_Z,
      /** Ground height — terrain answers if it is loaded, sea level if not. */
      ground: (x, z) => this.subsystems.get('terrain')?.heightAt?.(x, z) ?? 0,
      station: uid => this.plan?.byUid.get(uid) || null,
      /** The bench a station stands on, and its level. See `siteBenches`. */
      bench: uid => {
        const id = this.plan?.byUid.get(uid)?.bench;
        return (this.benches?.benches || []).find(b => b.id === id) || null;
      },
      on: (name, fn) => this.on(name, fn),
      emit: (name, payload) => this.emit(name, payload),
    };
    /* `quality` is a LIVE READ, not a snapshot.
     *
     * It used to be `quality: this.engine.tier`, evaluated once while the ctx
     * was being built — which is before the adaptive ladder has climbed. Since
     * the ladder now probes upward from `floor` (changed on Ryan's instruction,
     * because probing downward from high was nearly crashing bench PCs), every
     * subsystem that read `ctx.quality` read `floor`, for ever, at every
     * setting. Traced live: engine.tier went floor -> low -> medium -> high ->
     * ultra over seven seconds while ctx.quality stayed `floor` the whole time.
     *
     * props.js found it because its umbrella count came out as two at all five
     * tiers. Six other modules read this field. The live channel — engine's
     * `onQuality(tier)` broadcast — was always correct, so a module that only
     * acts in `onQuality` was fine; a module that decided anything from
     * `ctx.quality` was deciding it at floor.
     *
     * A getter cannot go stale. */
    Object.defineProperty(this.ctx, 'quality', {
      get: () => this.engine.tier,
      enumerable: true,
    });

    this.pickables = [];
    this._raycaster = new THREE.Raycaster();
    this._pointer = new THREE.Vector2();
    this._bindPointer();
  }

  /* ---- events ----------------------------------------------------------- */

  on(name, fn) {
    if (!this._listeners.has(name)) this._listeners.set(name, []);
    this._listeners.get(name).push(fn);
    return () => {
      const list = this._listeners.get(name) || [];
      const i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    };
  }

  emit(name, payload) {
    for (const fn of this._listeners.get(name) || []) {
      try { fn(payload); } catch (err) { console.error(`[world:${name}]`, err); }
    }
  }

  /* ---- boot -------------------------------------------------------------- */

  async ready() {
    if (this._ready) return this._ready;
    this._ready = (async () => {
      this.engine.resize();
      /* Texture resolution is a build-time decision: every map is generated
       * once, so the scale has to be set before any subsystem builds — and
       * therefore it CANNOT be what the adaptive ladder adjusts. The ladder now
       * starts at the floor tier and climbs, so taking its texture scale here
       * would bake every map at a quarter resolution and leave it there no
       * matter how capable the machine turned out to be.
       *
       * So: a pinned tier's scale is honoured, because that is a decision the
       * operator made and a reload will regenerate at it. Otherwise a middling
       * value is used. Generating textures is canvas work — it costs load time,
       * not frame time, and frame time is what was crashing machines. */
      const pinnedTier = this.engine.qualityMode !== 'auto'
        ? this.engine.tier.textureScale : 0.75;
      Tex.setTextureScale?.(pinnedTier ?? 1);
      /* `only` exists for the solo harness in world/dev — one subsystem at a
       * time, so a builder can look at their own work without waiting for
       * everyone else's. Production passes nothing and gets all of them. */
      const wanted = this.opts.only
        ? SUBSYSTEMS.filter(s => this.opts.only.includes(s.id))
        : SUBSYSTEMS;
      for (const spec of wanted) {
        try {
          const mod = await import(/* @vite-ignore */ spec.module);
          const Ctor = mod[spec.export] || mod.default;
          if (!Ctor) throw new Error(`${spec.module} exports no ${spec.export}`);
          const instance = new Ctor(this.ctx);
          await instance.build?.(this.plan);
          this.subsystems.set(spec.id, instance);
          this.engine.add(instance);
        } catch (err) {
          /* A subsystem is an enhancement. The status of the lab is not. */
          this.failed.push(spec.id);
          console.error(`[world] subsystem "${spec.id}" did not load —`,
                        'the map continues without it.', err);
        }
      }
      this.engine.shadowNeedsUpdate = true;
      this.engine.start();
      if (this.machines.length) this._replan();
      /* And again with terrain in the room. The bench schedule needs a natural
       * ground sampler and terrain is the only thing that has one, so the first
       * publish (from a `setMachines` before boot) carries the GROUPING with no
       * levels on it, and this one carries the levels. Exactly the shape
       * rail.js publishes its earthworks in, and for the same reason: the
       * subsystem that needs the number is not the one that has it, and the two
       * do not build in that order. */
      this._publishBenches();
      this.emit('ready', {failed: this.failed});
      return this;
    })();
    return this._ready;
  }

  /* ---- the fleet --------------------------------------------------------- */

  /** The whole `/api/machines` payload, every poll. Cheap when nothing that
   *  matters to the world has changed — which is almost every call, since the
   *  floor polls every 2 seconds and instruments move about once a year. */
  setMachines(list) {
    const before = this._layoutSignature(this.machines);
    this.machines = Array.isArray(list) ? list : [];
    const after = this._layoutSignature(this.machines);
    if (before !== after) this._replan();
    else this._restate();
  }

  _layoutSignature(list) {
    return list.map(m => `${m.machine_uid}@${(m.pos || []).join(',')}`)
               .sort().join('|');
  }

  /** Where everything stands. Derived from the instrument, never from the
   *  order the payload arrived in. */
  _plan() {
    const stations = claimBays(this.machines);
    const byUid = new Map();
    const out = stations.map(({m, gx, gy}, i) => {
      const z = gy * METRES_PER_BAY_Z;
      const entry = {
        uid: m.machine_uid, title: m.title || m.machine_uid, machine: m,
        index: i, gx, gy,
        x: gx * METRES_PER_BAY, z,
        /* Which row of benches this one stands in. Published rather than left
         * to be re-derived: terrain.js (`_makeSite`, yard links and maintenance
         * roads) and rail.js (`_rows`, one branch and one loading road per row)
         * each compute `Math.round(z / 8)` independently off the same z, and a
         * third and fourth copy of a grouping rule is how two files end up
         * disagreeing about what a row is. Same expression, one owner. */
        row: Math.round(z / 8),
        rng: seededRandom(m.machine_uid),
      };
      byUid.set(entry.uid, entry);
      return entry;
    });

    /* The LabCore terminal: where every line ends, downhill and to the north of
     * the site, far enough out that a train is on the road for a few seconds
     * rather than arriving the moment it leaves. It is NOT only a picture
     * decision: it is the plan dimension rail.js's ring corner comes out of,
     * and it is short. `HUB_SETBACK` carries the arithmetic, and the sweep
     * that says why it cannot be lengthened from this file. */
    const xs = out.map(s => s.x), zs = out.map(s => s.z);
    const nearestRowZ = Math.min(...zs, 0);
    const hub = {
      uid: '__labcore__', title: 'LABCORE',
      x: (Math.min(...xs, 0) + Math.max(...xs, 0)) / 2,
      z: nearestRowZ - HUB_SETBACK,
    };
    const plan = {stations: out, byUid, hub,
            bounds: {minX: Math.min(...xs, hub.x), maxX: Math.max(...xs, hub.x),
                     minZ: Math.min(...zs, hub.z), maxZ: Math.max(...zs, hub.z)}};
    plan.benches = benchGroups(out, hub);
    for (const b of plan.benches) {
      for (const uid of b.uids) {
        const s = byUid.get(uid);
        if (s) s.bench = b.id;
      }
    }
    return plan;
  }

  _replan() {
    this.plan = this._plan();
    this.ctx.plan = this.plan;
    /* TWICE, and the second one is not belt and braces.
     *
     * Before `onPlan`, so a subsystem that grades the ground finds the grouping
     * already on the plan it is handed rather than having to wait for an event.
     *
     * After `onPlan`, because the levels come from terrain's natural ground and
     * terrain's natural ground DEPENDS ON THE PLAN — `_baseHeight` reads the
     * island centre, the island radius and the site features, all of which
     * `_makeSite` sets from the plan it was just given. Sampling only before
     * `onPlan` would level this layout's benches against the previous layout's
     * island, which is a stale answer that nothing would ever report.
     *
     * So the event fires twice per re-plan and the second one carries the
     * authoritative levels. A consumer must be idempotent, exactly as it must
     * be for `rail:earthworks`, which republishes on every rail rebuild. */
    this._publishBenches();
    for (const [, sub] of this.subsystems) {
      try { sub.onPlan?.(this.plan); } catch (err) { console.error(err); }
    }
    this._publishBenches();
    this._restate();
    this.engine.shadowNeedsUpdate = true;
    if (!this._framed && this.plan.stations.length) {
      this._framed = true;
      const box = new THREE.Box3(
        new THREE.Vector3(this.plan.bounds.minX - 120, 0, this.plan.bounds.minZ - 120),
        new THREE.Vector3(this.plan.bounds.maxX + 120, 40, this.plan.bounds.maxZ + 120));
      this.rig.frame(box);
    }
  }

  /** Natural ground — the surface BEFORE anything was graded.
   *
   *  `ctx.ground` is no use here: it answers `heightAt`, which is the finished
   *  surface, so at a station it returns the design plane and a level derived
   *  from it would be a level derived from itself.
   *
   *  `naturalAt` is what terrain.js is asked for in REQUESTS.md. Until it
   *  exists, `_smoothBase` is used, which is not a guess — it is the EXACT
   *  surface `_fitDesignPlane` is fitted to, so a bench level and a plane
   *  intercept are answers to the same question off the same data. It is
   *  unshifted where `heightAt` is not, and that is why `level` is published
   *  relative to a datum rather than as an elevation. */
  _naturalSampler() {
    const t = this.subsystems.get('terrain');
    if (!t) return null;
    if (typeof t.naturalAt === 'function') {
      return {name: 'terrain.naturalAt', at: (x, z) => t.naturalAt(x, z)};
    }
    if (typeof t._smoothBase === 'function') {
      return {name: 'terrain._smoothBase', at: (x, z) => t._smoothBase(x, z)};
    }
    return null;
  }

  /** THE BENCH SCHEDULE, published.
   *
   *  Two channels, the same two rail.js uses for its earthworks declaration and
   *  for the same reason: `ctx.siteBenches` is the record, `site:benches` is the
   *  live channel, and `plan.benches` carries the grouping to anything that only
   *  ever sees a plan. A consumer may use whichever suits it; they are the same
   *  object.
   *
   *  The full shape, and how to consume it, is written up in scratchpad/
   *  REQUESTS.md — "index.js → terrain.js: the bench schedule". */
  _publishBenches() {
    if (!this.plan) return null;
    const benches = this.plan.benches || [];
    let sampler = null, sched = null;
    try { sampler = this._naturalSampler(); } catch { sampler = null; }
    if (sampler && benches.length) {
      try {
        const nat = benches.map(b => {
          const pts = benchProbePoints(b.probe);
          const vals = pts.map(([x, z]) => sampler.at(x, z))
                          .filter(v => isFinite(v)).sort((p, q) => p - q);
          return vals.length ? vals[vals.length >> 1] : NaN;
        });
        sched = benchSchedule(benches, nat);
        /* `benchSchedule` returns empty arrays rather than throwing when a
         * sample is not finite — a bench whose probe fell entirely off the
         * heightfield, say. Publishing `undefined` as a level would be worse
         * than publishing none, so this is the check that turns that into the
         * grouping-only payload below. */
        if (sched.level.length !== benches.length) {
          throw new Error('bench schedule returned no levels');
        }
        benches.forEach((b, i) => {
          b.naturalM = nat[i];
          b.levelAbsolute = sched.levelAbsolute[i];
          b.level = sched.level[i];
          b.moveM = sched.levelAbsolute[i] - nat[i];
        });
      } catch (err) {
        /* A site plan without levels is still a site plan with rows in it, and
         * the map is a status display before it is a rendering. */
        console.warn('[world] bench levels unavailable —', err);
        sched = null;
      }
    }
    const payload = {
      version: 1,
      grouping: 'row',
      rowKeyExpr: 'Math.round(z / 8)',
      rulingGradePct: RULING_GRADE * 100,
      sampler: sampler ? sampler.name : null,
      batter: BENCH_BATTER,
      benches,
      scale: sched ? sched.scale : null,
      binding: sched ? sched.binding : null,
      datumAbsolute: sched ? sched.datumAbsolute : null,
      naturalSpanM: sched ? sched.naturalSpanM : null,
      expressedM: sched ? sched.expressedM : null,
      maxCutM: sched ? sched.maxCutM : null,
      maxFillM: sched ? sched.maxFillM : null,
      steps: sched ? sched.steps : [],
    };
    this.benches = payload;
    this.ctx.siteBenches = payload;
    this.emit('site:benches', payload);
    return payload;
  }

  /** Status only — colours, pills, beacons, smoke. No geometry is rebuilt. */
  _restate() {
    if (!this.plan) return;
    for (const m of this.machines) {
      const station = this.plan.byUid.get(m.machine_uid);
      if (station) station.machine = m;
    }
    for (const [, sub] of this.subsystems) {
      try { sub.onMachines?.(this.machines, this.plan); }
      catch (err) { console.error(err); }
    }
  }

  setSelected(uid) {
    this.selected = uid || null;
    for (const [, sub] of this.subsystems) sub.onSelected?.(this.selected);
    const station = uid && this.plan?.byUid.get(uid);
    if (station) {
      this.rig.flyTo(new THREE.Vector3(station.x, this.ctx.ground(station.x, station.z) + 6,
                                       station.z),
                     Math.min(this.rig.goalDistance, 150));
    }
  }

  setLocked(locked) { this.locked = !!locked; }

  /** Arrange mode: the operator is moving instruments around the site.
   *
   *  Dragging worked before this existed, but only if you already knew that the
   *  map had a lock, that the lock was shared with everyone else looking at the
   *  floor, and that an instrument could be dragged at all — none of which the
   *  screen said. This turns it into a mode you can enter on purpose, and draws
   *  the bays so you can see where a building will land instead of discovering
   *  it on release. */
  setArranging(on) {
    this.arranging = !!on;
    if (this.arranging) this._showBays(); else this._hideBays();
    for (const [, sub] of this.subsystems) sub.onArranging?.(this.arranging);
  }

  _showBays() {
    if (!this.plan) return;
    this._hideBays();
    /* A bay is a rectangle on the ground, not a square — see
     * `METRES_PER_BAY_Z`. It has to be DRAWN as one, or Arrange mode shows the
     * operator a square and drops the building somewhere else. */
    const BAY = 2.05, stepX = BAY * METRES_PER_BAY, stepZ = BAY * METRES_PER_BAY_Z;
    const b = this.plan.bounds;
    const padX = stepX * 1.5, padZ = stepZ * 1.5;
    const x0 = Math.floor((b.minX - padX) / stepX) * stepX;
    const x1 = Math.ceil((b.maxX + padX) / stepX) * stepX;
    const z0 = Math.floor((b.minZ - padZ) / stepZ) * stepZ;
    const z1 = Math.ceil((b.maxZ + padZ) / stepZ) * stepZ;
    const pts = [];
    /* Drawn as one LineSegments rather than a grid helper so it can follow the
     * ground: a flat grid floating over a graded site is worse than none. */
    const lift = 0.45;
    for (let x = x0; x <= x1 + 1e-6; x += stepX) {
      for (let z = z0; z < z1 - 1e-6; z += stepZ / 4) {
        pts.push(x, this.ctx.ground(x, z) + lift, z,
                 x, this.ctx.ground(x, z + stepZ / 4) + lift, z + stepZ / 4);
      }
    }
    for (let z = z0; z <= z1 + 1e-6; z += stepZ) {
      for (let x = x0; x < x1 - 1e-6; x += stepX / 4) {
        pts.push(x, this.ctx.ground(x, z) + lift, z,
                 x + stepX / 4, this.ctx.ground(x + stepX / 4, z) + lift, z);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    const mat = new THREE.LineBasicMaterial({color: 0xff8a3d, transparent: true,
                                             opacity: 0.42, depthWrite: false});
    this._bays = new THREE.LineSegments(geo, mat);
    this._bays.name = 'arrange-bays';
    this._bays.renderOrder = 3;
    this.scene.add(this._bays);
  }

  _hideBays() {
    if (!this._bays) return;
    this.scene.remove(this._bays);
    this._bays.geometry.dispose();
    this._bays.material.dispose();
    this._bays = null;
  }

  /** Positions for a whole-floor rearrangement, in bay units.
   *
   *  Exported as a pure function (see `arrangement` below) so it can be tested
   *  without a browser — the same reason `claimBays` is pure. */
  arrange(kind) {
    return arrangement(this.machines, kind);
  }

  /** A print was parsed. One train, loaded, out of that station's yard and
   *  down the line to LabCore — the 3D form of the old pipe blip. */
  parse(uid, labId) {
    this.emit('parse', {uid, labId, at: Date.now()});
  }

  setWeather(patch) {
    Object.assign(this.weather, patch || {});
    for (const [, sub] of this.subsystems) sub.onWeather?.(this.weather);
    this.engine.shadowNeedsUpdate = true;
  }

  /** 0 = midwinter, 0.25 = spring, 0.5 = midsummer, 0.75 = autumn. Northern
   *  hemisphere; the lab is in Alberta. */
  static seasonNow(date = new Date()) {
    const start = Date.UTC(date.getUTCFullYear(), 0, 1);
    const day = (Date.UTC(date.getUTCFullYear(), date.getUTCMonth(),
                          date.getUTCDate()) - start) / 86400000;
    return ((day / 365.25) + 0.0) % 1;
  }

  static SEASONS = {winter: 0.0, spring: 0.25, summer: 0.5, autumn: 0.75};

  /** Set the season. Accepts a name or a 0..1 position in the year. */
  setSeason(season) {
    const v = typeof season === 'string'
      ? LEMWorld.SEASONS[season.toLowerCase()]
      : Number(season);
    if (!isFinite(v)) return false;
    this.season = ((v % 1) + 1) % 1;
    this.ctx.season = this.season;
    for (const [, sub] of this.subsystems) sub.onSeason?.(this.season);
    this.engine.shadowNeedsUpdate = true;
    return true;
  }

  /** How far into autumn colour the world should be, 0..1 — the single number
   *  most subsystems actually want, so each does not re-derive it differently.
   *  Peaks in mid-autumn and is zero through spring and summer. */
  get autumnality() {
    const s = this.season;
    /* 0.62 (early autumn) → 0.86 (leaf fall), peaking around 0.75. */
    if (s < 0.60 || s > 0.92) return 0;
    return Math.sin(((s - 0.60) / 0.32) * Math.PI);
  }

  /** Snow cover the season alone implies, 0..1, before weather has its say. */
  get winterliness() {
    const s = this.season;
    const d = Math.min(Math.abs(s), Math.abs(s - 1));   // distance to midwinter
    return Math.max(0, 1 - d / 0.22);
  }

  setTimeOfDay(hours) {
    this.timeOfDay = hours;
    this.ctx.timeOfDay = hours;
    for (const [, sub] of this.subsystems) sub.onTime?.(hours);
    this.engine.shadowNeedsUpdate = true;
  }

  stats() {
    return {fps: Math.round(this.engine.fps || 0),
            drawCalls: this.engine.drawCalls || 0,
            triangles: this.engine.triangles || 0,
            tier: this.engine.tier.name,
            failed: this.failed};
  }

  /* ---- pointer ----------------------------------------------------------- */

  _bindPointer() {
    const canvas = this.canvas;
    const pick = e => {
      const r = canvas.getBoundingClientRect();
      this._pointer.set(((e.clientX - r.left) / r.width) * 2 - 1,
                        -((e.clientY - r.top) / r.height) * 2 + 1);
      this._raycaster.setFromCamera(this._pointer, this.camera);
      const hits = this._raycaster.intersectObjects(this.pickables, true);
      for (const hit of hits) {
        let o = hit.object;
        while (o && !o.userData.machineUid) o = o.parent;
        if (o) return {uid: o.userData.machineUid, point: hit.point, object: o};
      }
      return null;
    };
    this._pick = pick;

    let downAt = null, dragging = null, moved = false;

    canvas.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      const hit = pick(e);
      downAt = {x: e.clientX, y: e.clientY, uid: hit?.uid || null};
      moved = false;
      if (hit && !this.locked && this.opts.canDrag?.()) {
        dragging = {uid: hit.uid, offset: new THREE.Vector3()};
        this.rig.suspended = true;
      }
    });

    canvas.addEventListener('pointermove', e => {
      if (dragging) {
        moved = true;
        const p = this._groundPoint(e);
        if (p) this.emit('dragging', {uid: dragging.uid, point: p});
        return;
      }
      if (downAt && (Math.abs(e.clientX - downAt.x) > 4 ||
                     Math.abs(e.clientY - downAt.y) > 4)) moved = true;
      const hit = pick(e);
      const uid = hit?.uid || null;
      if (uid !== this._hovered) {
        this._hovered = uid;
        canvas.style.cursor = uid ? 'pointer' : 'grab';
        this.opts.onHover?.(uid, uid ? {x: e.clientX, y: e.clientY} : null);
        for (const [, sub] of this.subsystems) sub.onHover?.(uid);
      } else if (uid) {
        this.opts.onHover?.(uid, {x: e.clientX, y: e.clientY});
      }
    });

    window.addEventListener('pointerup', e => {
      if (dragging) {
        const p = this._groundPoint(e);
        this.rig.suspended = false;
        if (p && moved) {
          const BAY = 2.05;
          /* The inverse of `_plan`'s mapping, which is anisotropic. Rounding a
           * drop with the wrong scale on one axis is a building that lands a
           * bay away from where the operator let go of it. */
          const gx = Math.round(p.x / METRES_PER_BAY / BAY) * BAY;
          const gy = Math.round(p.z / METRES_PER_BAY_Z / BAY) * BAY;
          this.opts.onMove?.(dragging.uid, gx, gy);
        }
        dragging = null; downAt = null;
        return;
      }
      if (downAt && !moved && downAt.uid) this.opts.onSelect?.(downAt.uid);
      else if (downAt && !moved && !downAt.uid) this.opts.onSelect?.(null);
      downAt = null;
    });

    canvas.addEventListener('contextmenu', e => {
      const hit = pick(e);
      if (!hit) return;
      e.preventDefault();
      this.opts.onContext?.(hit.uid, e);
    });
  }

  /** Where the pointer meets the ground plane — for dropping a dragged
   *  instrument on a bay. */
  _groundPoint(e) {
    const r = this.canvas.getBoundingClientRect();
    this._pointer.set(((e.clientX - r.left) / r.width) * 2 - 1,
                      -((e.clientY - r.top) / r.height) * 2 + 1);
    this._raycaster.setFromCamera(this._pointer, this.camera);
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const out = new THREE.Vector3();
    return this._raycaster.ray.intersectPlane(plane, out) ? out : null;
  }

  /** Screen position of a station, for anchoring the HTML tooltip. */
  screenPoint(uid, lift = 26) {
    const s = this.plan?.byUid.get(uid);
    if (!s) return null;
    const v = new THREE.Vector3(s.x, this.ctx.ground(s.x, s.z) + lift, s.z);
    v.project(this.camera);
    const r = this.canvas.getBoundingClientRect();
    return {x: r.left + (v.x * 0.5 + 0.5) * r.width,
            y: r.top + (-v.y * 0.5 + 0.5) * r.height,
            behind: v.z > 1};
  }

  dispose() {
    this.rig.dispose();
    this.engine.dispose();
    Tex.disposeAll();
  }
}

export default LEMWorld;
