/* rail.js — the railway.
 *
 * Every parse sends a train from an instrument to the LabCore terminal, so the
 * line between them is the one piece of this world that is never scenery: it is
 * the drawing of the lab's data path. It is built the way a railway is built,
 * not the way a pipe is drawn.
 *
 * Four decisions carry the whole file.
 *
 *   1. The network is *splines first*. `Track` turns a polyline of control
 *      points into a real alignment — straights joined by clothoid-easement
 *      corners into a constant-radius arc — and everything else (ballast,
 *      sleepers, rails, turnouts, signals, the routes trains sample) is read
 *      off that one sampled frame array. Nothing is positioned by hand twice,
 *      so nothing can drift out of register with anything else.
 *
 *   2. Detail is texture, repetition is instancing. One extruded rail profile
 *      per track, one instanced sleeper for the whole railway, ballast as a
 *      seven-vertex ribbon whose stone lives entirely in a normal map. The
 *      close-up test — cam=street, nose on the ballast — is won by the maps,
 *      because a sculpted stone is 40 triangles that one texel already had.
 *
 *   3. Junctions are drawn as junctions. A turnout that is two lines crossing
 *      is the single thing that gives a toy railway away, so `_turnout` builds
 *      the parts a real one has: stock rails, planed switch blades that taper
 *      to nothing at the tip, a frog casting where the routes cross, check
 *      rails opposite it, long bearers under the whole assembly and a point
 *      machine on the outside. Its diverging geometry is not a formula — it is
 *      sampled off the siding that actually leaves there, so the blades point
 *      where the rails genuinely go.
 *
 *   4. The vertical profile is a string pulled taut inside the ground, not a
 *      curve drawn above it. `Track.build` grades every alignment between a
 *      floor it may never go below (the ground it crosses, because nothing here
 *      can excavate) and a ceiling of 1.5m (the tallest bank a works railway
 *      builds), and inside that tube it relaxes to the flattest line it can
 *      find. Get this wrong upward, as this file did until 2026-08-07, and the
 *      railway leaves the landscape entirely: a 2m median float, an unbroken
 *      pale causeway from one edge of the site to the other, and the first
 *      thing Ryan said when he saw it.
 *
 * The topology comes from the plan and from what the other subsystems have
 * already committed to. `buildings.js` promises a dock 26m north of every
 * station running east–west, and `terrain.js` grades one tilted plane over the
 * whole block of instruments plus a corridor out to the hub. So:
 *
 *   - one RING, laid as a single alignment with three legs: north up the west
 *     side of the site, east along the terminal's platform road under the
 *     loading gantry, and south down a return alignment on the east side. Arc
 *     length increases the whole way round;
 *   - one BRANCH per row of instruments, leaving the ring's east leg at a
 *     facing turnout, running west past the docks, and rejoining the ring's
 *     west leg at a trailing one;
 *   - one LOADING ROAD per row, off its own branch, with a stand on it for
 *     every bench in that row — the road under the gantry that is already
 *     standing there;
 *   - a second platform road at the terminal so the throat can hold two
 *     arrivals, and a reception road where a cut of tanks can stand clear.
 *
 * ---- why it is a ring, which is the whole of the current design -------------
 *
 * It was a spur. The trunk ran west and north to the terminal, a balloon loop
 * turned the train there, and it came home down the same rails. Dumping one
 * working's circuit said it plainly: `main 316→553` outbound, `main 763→897`
 * returning — one working, one piece of track, two directions. That is what
 * made a head-on collision possible at all, and it is why single-line token
 * working had to be invented on top of the block reservation to prevent it.
 *
 * On a one-way circuit two workings physically cannot meet. It is not a rule
 * the interlocking enforces; it is a shape the network has. Real petroleum
 * unit-train terminals are built as loops for exactly this reason, and it is
 * why Factorio's right-hand-drive networks are safe by construction rather than
 * by signalling. The block reservation stays — a train can still run into the
 * back of a slower one, and a junction still needs the chain rule — but the
 * topology is now doing most of the work, and `oneWayReport()` is the check.
 *
 * The number of rows does not change the shape of any of that, and that is the
 * point: a rank, a file, a scatter and the lab's real floor are one network
 * with a different number of branches on it. `_branch` says why a branch has
 * two ends; `_loadingLoop` says why the loading road serves a row rather than
 * a bench.
 *
 * ---- what trains.js buys from this file -----------------------------------
 *
 *   rail.route(uid)   → a THREE.Curve subclass, so `getPointAt(u)` works, plus
 *                       `length`, `pointAt(t)` (t is 0..1), `pointAtDistance(m)`,
 *                       `dockPoint`, `arrivalPoint`, `points`. Positions are on
 *                       the **top of the rail**, on the track centreline — a
 *                       vehicle origin sits exactly there, so `railTop` is 0
 *                       and deliberately not exported.
 *   rail.cycle(uid)   → the whole working as one closed ONE-WAY circuit, plus
 *                       `terminal`, `loopExit`, `turned`, `line` and
 *                       `segments` (below).
 *   rail.oneWayReport() → whether any working runs over any track twice, or in
 *                       both directions, and whether every circuit closes.
 *   rail.yardRoute()  → the same shape, along a spare yard road, for shunting.
 *   rail.occupy(id,t) / rail.release(id) → signalling responds to real trains.
 *
 * The route runs station → hub. trains.js re-orients from the geometry anyway,
 * but it runs that way.
 *
 * ---- and what a signaller, or a test, buys ---------------------------------
 *
 * Every working now runs over the same ring, so "two trains in one place" is a
 * question the network has to be able to answer — and arc length along a
 * train's own route cannot answer it: two workings out of different benches
 * stand on the same forty metres of platform road at completely different arc
 * lengths. So a route also carries what it is laid ON.
 *
 *   cycle.segments        [{track, from, to, s0, s1}] — which physical track
 *                         each stretch of the route runs over, `from`/`to`
 *                         being indices into `route.points`.
 *   cycle.docks           [{uid, s}] — every stand on this road, ascending, in
 *                         the circuit's own arc length. One circuit serves the
 *                         whole row, so these are directly comparable.
 *   rail.blocksFor(cycle, headS, tailS) → the block ids a train covers.
 *   rail.blockSpans(cycle) → [{id, a, b, junction, run}] — where each block
 *                         begins and ends along that circuit, which is what a
 *                         train needs before it may move rather than after.
 *   rail.runFor(id)       → the whole single-line token a block belongs to, or
 *                         null on rail only ever worked one way.
 *   rail.clear(id, blocks) / rail.reserve(id, blocks) / rail.unreserve(id) /
 *   rail.heldBy(block)    → path reservation. `reserve` refuses, atomically, if
 *                         anything in the set is held by anybody else.
 *
 * That is OpenTTD's path signalling, and it is the only sort of anti-collision
 * worth having: a working that cannot get a reservation does not move, so two
 * trains in one block stops being something to detect afterwards. There is ONE
 * ledger, `_held`, and the only thing that ever writes to it is a consist
 * declaring where it actually is — see trains.js `_signal`.
 */
import * as THREE from 'three';

/* ---- prototype dimensions -----------------------------------------------
 * Everything below is the real thing in metres. The world is compressed —
 * stations are 90m apart where a real works would be a kilometre — but the
 * track itself is full size, because track is the object in this scene whose
 * proportions a viewer actually knows by heart. */

const GAUGE = 1.435;
const HALF_GAUGE = GAUGE / 2;
const RAIL_H = 0.172;          // UIC60: 172mm tall
const SLEEPER_LEN = 2.60;
const SLEEPER_W = 0.300;       // along the track
const SLEEPER_H = 0.155;
const SLEEPER_PITCH = 0.61;    // 1640 to the kilometre, near enough
const PAD_H = 0.020;           // tie plate / baseplate under the rail foot

/* All heights are quoted from the top of the rail, which is y=0 on a frame. */
const SLEEPER_TOP = -RAIL_H - PAD_H;
const SLEEPER_BOT = SLEEPER_TOP - SLEEPER_H;
/* Ballast well down in the cribs. Level with the sleeper tops is the giveaway
 * of a track drawn rather than laid: what reads at three metres is the shadow
 * in the crib and the sleeper's own side catching light above the stone.
 *
 * 105mm rather than 72. At 72 only 46% of a 155mm sleeper stood clear, and the
 * face that was showing was two texels tall — which is most of why the ties
 * read as "zero-thickness quads" no matter what the map on them said. A tie
 * that has just been tamped stands with two thirds of its depth out of the
 * crib, and that is also what gives the sun something to cast between them. */
const BALLAST_CRIB = SLEEPER_TOP - 0.105;
const BALLAST_TOE = SLEEPER_BOT - 0.28;     // 280mm of ballast under the tie
const SHOULDER_X = SLEEPER_LEN / 2 + 0.62;  // 620mm shoulder past the tie end
const TOE_X = SHOULDER_X + (BALLAST_CRIB - BALLAST_TOE) * 1.6;   // 1:1.6 batter
const VERGE_X = TOE_X + 1.7;                // cess, out to natural ground

/** Top of rail above natural ground on the flat. The formation is graded from
 *  this, so track never follows every bump the terrain has.
 *
 *  This number is the whole permanent way stacked up and nothing else: 280mm of
 *  ballast under the tie, a 155mm sleeper, the 20mm baseplate, 172mm of rail,
 *  and 60mm of formation standing proud so the toe of the stone is never
 *  z-fighting the grass. 687mm, and a railhead two thirds of a metre above the
 *  field beside it is what every photograph of ballasted track looks like.
 *
 *  It was never the problem. Ryan's "the amount that the train rails float above
 *  the terrain is insane" measured at a median of 2.07m, and the missing 1.4m
 *  was not in this constant — it was fill piled on by the grading in `build`,
 *  which was upward-only in four separate places and had no ceiling worth the
 *  name. See the comments there. */
const FORMATION = -BALLAST_TOE + 0.06;

/** How far the stone toe must clear the highest ground it covers. Small on
 *  purpose: this is the floor the whole profile is pressed down on to, so every
 *  centimetre here is a centimetre of railway standing off the ground for the
 *  entire length of the site. */
const TOE_CLEAR = 0.03;

/** The tallest bank this railway is allowed to build.
 *
 *  It used to be 3.6m, and on this terrain — which has 25m of relief across the
 *  site and local slopes of 1 in 3 — the ruling-grade fill wanted more than that
 *  almost everywhere, so the ceiling bound along the whole trunk and the line
 *  came out riding a continuous 2-3m causeway from one end of the map to the
 *  other. That is not an embankment; an embankment is a local thing you can see
 *  the ends of.
 *
 *  1.5m is a bank a works railway actually builds, it reads as one in the
 *  three-quarter shots, and — because the fill needed is a hollow-by-hollow
 *  thing rather than a constant — capping here leaves the great majority of the
 *  line sitting on the ground at FORMATION. Where the ground genuinely dives
 *  faster than a bank can follow, the profile follows it down; that is a
 *  gradient, which is honest, rather than a viaduct with no piers, which is not.
 *
 *  The real fix is a cut corridor in the terrain — see scratchpad/REQUESTS.md,
 *  2026-08-07 — after which the ruling grade can be held without any of this. */
const FILL_CAP = 1.50;

/* ---- the geometry rules --------------------------------------------------
 *
 * Ryan, 2026-08-07, on the alignment as it stood: "the erosion wont allow the
 * track to go without earth beneath it, it even cuts through terrain right now
 * to keep it flat ... it clearly doesnt care about curves too, you can see
 * right now it just has super sharp angles. And I want some elevation change,
 * and a bridge over some water too. but we need some rules before we let it go
 * ham."
 *
 * These are the rules. They are deliberately NOT full-size practice — a 90m
 * running-line curve is a light railway's, not a main line's, and 2.5% is
 * steeper than anything a bulk railway would work unassisted — because the
 * whole site is 900m across and a 400m curve would not fit on it twice. What
 * matters is that they are applied CONSISTENTLY rather than approximated away,
 * because the failure they replace was not "the numbers were a bit small": it
 * was that there were no numbers at all. Measured on three routes at 400
 * samples each, the alignment before this round ran to a 29m minimum radius and
 * a 35% maximum gradient, and declared not one metre of cutting anywhere — it
 * was a polyline draped over the terrain, passing through the land wherever the
 * land came up.
 *
 * A radius is per CLASS of line, and the class is a real distinction rather
 * than a way of excusing the tight ones: the ring is the running line, worked at
 * speed and by every train on the railway, and the branches, loading roads,
 * terminal roads and yard spurs are yard connections worked at a walk. Track
 * with a 15 km/h limit on it is laid to different radii in every yard in the
 * world.
 */
const R_MIN_RUN = 90;          // minimum radius, running lines
const R_MIN_YARD = 55;         // ...and in yards and sidings
const GRADE_RULING = 0.025;    // ruling gradient on a running line
const GRADE_YARD = 0.040;      // absolute maximum, yard roads only
/* A spiral into and out of every curve, no exceptions. Quoted as a fraction of
 * the radius so a tight yard corner gets a short easement and a running-line
 * one a long one; the floor is what it takes for the cant to be run off at all.
 * Anything whose total deflection is under `CURVE_MIN_TURN` is not a curve —
 * it is a survey wobble worth 5mm of offset — and is laid as straight. */
const EASE_K = 0.30;
const EASE_MIN = 10;
const CURVE_MIN_TURN = 0.012;  // radians: below this a corner is not a curve

/* Where earthworks stop being earthworks and become structures. Both numbers
 * are the point at which the prototype stops digging or tipping and builds
 * something, scaled to this site the same way the radii are. */
const VIADUCT_FILL = 6.0;      // bank deeper than this is a viaduct
const TUNNEL_CUT = 9.0;        // cutting deeper than this is a tunnel
const FILL_BATTER = 1.5;       // 1:1.5 on fill
const CUT_BATTER = 1.0;        // 1:1 in cut
/* Ground within this of the water plane is not ground a railway stands on: it
 * is foreshore, and terrain.js paints it as mud. A line crosses it on a bridge,
 * always — never on a causeway, which is the one water crossing that reads as a
 * mistake from every angle. */
const WET_FREEBOARD = 2.5;
/* Nothing shorter than this is worth building as a structure, and any gap
 * shorter than this between two of them is absorbed into the pair: four
 * separate eight-metre viaducts with six metres of bank between them is one
 * viaduct that has been sampled badly. */
const FILL_BIAS = 0.72;
const STRUCT_MIN = 26;
const STRUCT_GAP = 22;

const GEOM_STEP = 1.5;         // frame spacing for extruded geometry
const ROUTE_STEP = 2.2;        // frame spacing for the arrays trains sample
const MAX_CANT = 0.040;        // 2.3°: reads on the ballast, invisible to a bogie
const CANT_GAIN = 5.6;         // radians of roll per 1/m of curvature

/* Sizes the rest of the site already committed to. buildings.js puts the dock
 * 26m north of a station and stands its gantry columns 4.6m north of that
 * centreline, so the running line has to clear 5.2m before its own ballast
 * shoulder is counted. 8.4m leaves the toe of the stone a clear metre short of
 * the nearest footing. */
const DOCK_OFFSET = 26;
const LOOP_OFFSET = 8.4;
const LINE_SPACING = 12.3;
/* Standage between two junctions on the ring. trains.js builds consists that
 * measure 64.5–84.0m, and a plan whose junctions are 67.5m apart — which is
 * what this railway had — has nowhere to hold a working clear of the one
 * behind it. 104 leaves ten metres at each end of the longest rake. See
 * `_branch.place`, which also says why that is a target and not a guarantee. */
const MIN_STANDAGE = 104;
/* How much railway is LAID past the outermost junction on the ring.
 *
 * The trunk's alignment has to run to the site's south edge — both legs are
 * chosen by walking the ground between the platform road and that line, before
 * a single branch exists, so the corridor has to be surveyed further than the
 * railway will use. What is laid past the last set of points is a separate
 * question and it was never asked: measured on the lab's own floor the
 * outermost junction sits at z = -17.3 and the alignment ran on to z = +123.8,
 * so 141m of track at EACH corner ran south over a hill, past nothing, to a
 * buffer stop. That is Ryan's "extensions of the railroad that lead to
 * nowhere", and `_auditTracks` is constitutionally unable to see it: it asks
 * whether any working reaches this TRACK, and `main` is the busiest track on
 * the railway. The eye asks about the METRE.
 *
 * 48m is what a headshunt is for — a shunting neck an engine can draw forward
 * into clear of the points and set back out of. It is also comfortably past
 * `junctionBlock`'s 32m, so the buffer stop does not stand inside the last
 * turnout's own overlap. */
const HEADSHUNT = 48;
/* And the minimum z a branch's diagonal needs to swing from the ring's own
 * direction round to the row's. `room > 46` further down is the real check;
 * this is the cheap pre-filter and the cap on how far a junction may be pushed
 * for standage.
 *
 * 72 rather than the 54 the arithmetic first suggested, and the difference is
 * the LEAD. A 1:6 turnout's exit port is 22m past its tip, so the diagonal a
 * corner actually has to fit in is the throat minus the whole lead: 72 − 22 is
 * 50, which is a 46m corner and two metres to spare. Sizing this from the
 * corner alone refused a third of the branches on the lab's own floor. */
const THROAT_MIN = 76;
/* How much of a leg a 1:6 turnout's lead eats before the road that leaves it
 * has begun to turn: R = 2·G·N² = 103.3m, the frog is √(2RG) = 17.2m out, and
 * there is 5.4m of closure rail past it. Quoted along the parent because that
 * is the axis every clearance on this railway is measured in. */
const LEAD_Z = 23;
const BALLAST_TILE = 1.15;     // metres per repeat of the ballast map

/* ---- passing loops on a loading road -------------------------------------
 *
 * The operator: "There's no way for a train to get out (without clipping
 * through) if the station in front of it doesn't move." On a one-way single
 * line with N stands in series, no dispatch rule gets the second train out
 * before the first — trains.js measured that and said so plainly. Only metal
 * does, and the metal is a connection from the loading road back on to the
 * row's own branch, sited between two stands. The branch already runs the whole
 * length of the rank eight metres away and carries nothing there, so it is
 * already the bypass; what was missing is a way on to it other than the one
 * turnout at the far end.
 *
 * THE NUMBER THIS STANDS OR FALLS ON is not the length of the lead. It is the
 * FOULING POINT: how far past the switch tip the diverging road has opened far
 * enough from the through road for a train on one to clear a train standing on
 * the other. A train leaving stand B runs alongside the train standing at stand
 * A while it is still closing, and the run available is
 *
 *     stand pitch  −  the rake standing at A  −  where the tip has to be
 *
 * At the pitch index.js granted (115.5m on the lab's own floor, 117m on a rank
 * of seven) against trains.js's longest rake (84.0m) that is 28.5m, for a tip
 * three metres in front of the stand it releases. `leadClearRun` below turns a
 * frog number into the run it needs, and `leadClearAt` into the clearance a
 * given run buys. Measured minimum body-to-body distance, sliding a real body
 * down a lead built on the real road (harness/pl-foul.mjs), worst gap, longest
 * rake — soak.mjs calls anything under 5.00m a collision:
 *
 *     frog     R       run to 5.75m    stand pitch it     clearance at
 *                      of clearance    would need         115.5m   117m
 *     1:8    183.7m       51.6m           138.6m           2.46     2.71
 *     1:6    103.3m       37.8m           124.8m           4.01     4.33
 *     1:5.5   86.8m       34.4m           121.4m           4.62     4.97
 *     1:5     71.8m       31.1m           118.1m           5.09     5.36
 *     1:4.5   58.1m       27.8m           114.8m           6.00     6.49
 *
 * So this railway's own 1:6 FOULS by a metre and there is no 1:6 turnout here.
 * 1:4.5 — R = 58.1m, three metres above this file's own yard floor — is the
 * only lead that clears with a margin worth having, and it clears by 1.0m.
 * A 1:6 connection needs a 125m stand pitch and a 1:5 one needs 118m; both are
 * in REQUESTS for index.js, because the pitch is not this file's to set.
 *
 * It is a sharp turnout and that is the honest cost of the site being this
 * tight. It is taken at a walk by a train that started from a stand forty
 * metres back, which is exactly what a 1:4.5 is for. */
const LINK_FROG = 4.5;         // both ends of the crossover
const LINK_MIN_STANDS = 4;     // trains.js's rule: one for a rank of four or more
const LINK_TWO_STANDS = 7;     // ...and two, at the thirds, for seven or more
const LINK_EPS = 3.0;          // tip this far in front of the stand it releases
const LINK_RAKE = 84.0;        // the longest consist trains.js builds
const LINK_CLEAR = 5.75;       // demanded clearance: soak's 5.0 and 15% on top
/* And the block on the through road may not reach into where the NEXT train
 * stands, or that train's tail is inside a junction span for ever and
 * `trains.js:_onRoad` never reads it as home. `junctionBlock`'s 32m is longer
 * than the 28.5m available here, so this one is derived from the room. */
const LINK_BLOCK_GAP = 1.5;    // clear of the next rake's tail by this much

/* ---- 2D vector helpers ---------------------------------------------------
 * The alignment is solved flat, in x/z, and lifted onto the terrain afterwards.
 * Plain objects rather than Vector2 because this runs tens of thousands of
 * times at build and none of it should touch the allocator. */

const sub2 = (a, b) => ({x: a.x - b.x, z: a.z - b.z});
const len2 = a => Math.hypot(a.x, a.z);
const dot2 = (a, b) => a.x * b.x + a.z * b.z;
/** Positive when b turns to the right of a, with y up and z into the screen. */
const cross2 = (a, b) => a.x * b.z - a.z * b.x;
const rightOf = a => ({x: -a.z, z: a.x});
function norm2(a) { const l = len2(a) || 1; return {x: a.x / l, z: a.z / l}; }

/** Where the ray from `p` along `d` crosses the line through `q` along `e`.
 *
 *  Used to place the control point that follows a turnout. A road leaving a 1:8
 *  turnout is already 7° off the through road and 1.6m over by the time the
 *  lead ends, and a waypoint that ignores that is a corner the fillet has to
 *  eat — so the waypoint is put where the lead's own tangent meets the leg the
 *  road actually wants, and the alignment leaves the turnout straight. */
function rayHit(p, d, q, e) {
  const den = d.x * e.z - d.z * e.x;
  if (Math.abs(den) < 1e-9) return null;
  const t = ((q.x - p.x) * e.z - (q.z - p.z) * e.x) / den;
  if (!(t > 1)) return null;
  return {x: p.x + d.x * t, z: p.z + d.z * t, t};
}

/* ---- the alignment -------------------------------------------------------
 *
 * A corner is not a corner. Real track leaves a straight on a clothoid whose
 * curvature ramps linearly from zero to 1/R, holds the arc, and ramps back —
 * which is why a train on a curve looks like it is being *steered* rather than
 * hinged. The fillet is integrated numerically from that curvature schedule
 * rather than assembled from closed forms, because the same loop then gives us
 * per-sample curvature for free, and curvature is what cants the track, throws
 * the check rails and decides where a train may run fast.
 */

/** Integrate one corner in a local frame: start at the origin heading +u,
 *  finish heading `turn` radians toward +v. Returns the sample list and the
 *  tangent distance T back to the intersection of the two straights. */
function fillet(turn, radius, easement) {
  const R = Math.max(8, radius);
  /* The easement eats angle at both ends (Ls/2R each), so it has to be short
   * enough to leave an arc behind. A corner too sharp for any arc becomes two
   * spirals meeting — still smooth, still a curve, just no constant section. */
  let Ls = Math.min(easement, 0.48 * R * turn);
  if (!(Ls > 0.05)) Ls = 0;
  const arcLen = Math.max(0, turn * R - Ls);
  const total = arcLen + 2 * Ls;
  if (!(total > 0.01)) return null;

  const step = Math.min(0.5, total / 24);
  const n = Math.max(6, Math.ceil(total / step));
  const ds = total / n;
  const pts = new Array(n + 1);
  let u = 0, v = 0, psi = 0;
  pts[0] = {u: 0, v: 0, k: Ls > 0 ? 0 : 1 / R};
  for (let i = 1; i <= n; i++) {
    const s0 = (i - 1) * ds, s1 = i * ds, sm = (s0 + s1) * 0.5;
    /* curvature schedule: ramp in, hold, ramp out */
    let k;
    if (Ls > 0 && sm < Ls) k = (sm / Ls) / R;
    else if (Ls > 0 && sm > Ls + arcLen) k = ((total - sm) / Ls) / R;
    else k = 1 / R;
    /* midpoint integration — a straight Euler step drifts visibly over a
     * 150m arc, and the drift shows up as a kink where the arc meets the
     * straight after it. */
    const pm = psi + k * ds * 0.5;
    u += Math.cos(pm) * ds;
    v += Math.sin(pm) * ds;
    psi += k * ds;
    pts[i] = {u, v, k};
  }
  const tanT = Math.tan(turn);
  const T = Math.abs(tanT) < 1e-6 ? u : u - v / tanT;
  return {pts, T: Math.max(0, T), length: total, Ls, R};
}

/** A polyline of control points, filleted at every corner and resampled at a
 *  uniform arc-length step. Uniform spacing is the point: every lookup after
 *  this is an array index, not a search. */
class Track {
  constructor(name, control, opts = {}) {
    this.name = name;
    this.radius = opts.radius ?? 150;
    /* One radius for the whole alignment is right for a line and wrong for a
     * turning loop, where the cap has to be tight enough to fit the site and
     * the two connections back on to the running line have to be gentle enough
     * to be turnouts a train can take at speed. `radii[i]` is the radius at
     * control point i+1; anything not named falls back to `radius`. */
    this.radii = opts.radii || null;
    /* A shrinking corner is safe geometry and bad railway: the fit pass below
     * will happily take a running-line corner down to a tenth of its nominal
     * radius to make two fillets share a leg. Below this the alignment is no
     * longer something track could be laid on, so the corner stops shrinking
     * and the caller finds out (`this.tight`) rather than shipping a hairpin. */
    /* The class of line decides the two numbers a track plan is actually judged
     * on. It is passed rather than guessed because only the caller knows what a
     * road is FOR: `main` is the running line every train works over, and a
     * loading road under a gantry is a yard connection taken at a walk. */
    this.klass = opts.klass === 'yard' ? 'yard' : 'running';
    this.classRadius = this.klass === 'yard' ? R_MIN_YARD : R_MIN_RUN;
    /* `minRadius` is the floor the fit pass may not shrink past before it gives
     * up and sets `tight`. It is normally the class minimum — but a site can be
     * genuinely too small for the rules, and on a site that is, refusing to lay
     * any track at all is not the more honest answer. A caller that has measured
     * the room and found it short passes `hardFloor`, takes what it can get, and
     * records the shortfall in `Rail.exceptions`, which is reported. Silently
     * shrinking a corner is what this file used to do; declaring that the site
     * cannot hold the rule is a different thing entirely. */
    this.minRadius = Number.isFinite(opts.hardFloor)
      ? Math.max(30, opts.hardFloor)
      : Math.max(opts.minRadius ?? this.classRadius, this.classRadius);
    /* Ruling grade. A line is graded to a gradient a train can work, not to
     * whatever the hillside does — see `build`. */
    this.maxGrade = Math.min(opts.maxGrade ?? GRADE_RULING,
                             this.klass === 'yard' ? GRADE_YARD : GRADE_RULING);
    /* The length of the vertical curve between two grades. Shorter in a yard
     * for the same reason the radius is: a road worked at a walk can change
     * grade in a shorter distance than one worked at speed. */
    this.vCurve = opts.vCurve ?? (this.klass === 'yard' ? 10 : 20);
    /* How the profile splits the difference between the all-fill railway and
     * the all-cut one. A half is the balanced answer and is what an earthworks
     * estimate would use.
     *
     * It is not a half here, and the reason is scheduling rather than
     * engineering. Fill is a thing THIS file can draw — the ballast batter walks
     * its toe out at 1:1.5 and above VIADUCT_FILL `_span` builds a deck on piers
     * — and cut is not: terrain.js owns the ground, builds before rail, and does
     * not yet apply the declaration (`earthworks()`, published this round, is
     * what would let it). So an honest balanced profile puts half the railway
     * underground until that lands, and a railway you cannot see is worse than
     * one that is expensive. 0.72 keeps the declared cuts shallow enough for the
     * ballast drape to absorb and turns the deep places into structures.
     *
     * When terrain applies the cut this becomes 0.5 and the earthwork halves.
     * It is one number and this comment is the whole of the reason it is not. */
    this.fillBias = opts.fillBias ?? FILL_BIAS;
    /* Where the water is, so a crossing can be declared as a bridge rather than
     * discovered as a causeway. −Infinity means "there is no water here", which
     * is the honest answer when terrain has not built. */
    this.waterY = Number.isFinite(opts.waterY) ? opts.waterY : -Infinity;
    this.easement = opts.easement ?? 0;
    this.step = opts.step ?? GEOM_STEP;
    this.control = control.map(p => ({x: p[0] ?? p.x, z: p[1] ?? p.z}));
    this.renderFrom = opts.renderFrom ?? 0;
    this.renderTo = opts.renderTo ?? Infinity;
    this.sleepers = opts.sleepers !== false;
    /* A yard road laid straight onto a concrete slab has no cess to grade to,
     * and pinning its outer edge to the natural ground would drop a skirt of
     * geometry clean through the terminal's apron. */
    this.verge = opts.verge !== false;
    /* "Is there a formation here" and "does the ground need moving" are two
     * different questions and one flag used to answer both. A crossover between
     * two roads that are already graded needs no earthwork of its own — but
     * there is very much a railway there, and vegetation.js keeps out of
     * `formationCorridors()`, so answering the second question no must not
     * answer the first one no as well. That is a tree in the four-foot. */
    this.corridor = opts.corridor ?? this.verge;
    this.speed = opts.speed ?? 1;       // scenery hint: yard roads are slow
    this.blocks = [];                   // arc ranges a sleeper may not stand in
    /* And the arc ranges a turnout's OVERLAP covers, which is a different and
     * longer thing. `blocks` is a drawing fact — bearers here, so no timbers —
     * and it stops at the heel of the lead because that is where the assembly
     * stops. The overlap is a SIGNALLING fact and it stops at the fouling
     * point, which for a 1:6 lead is fifteen metres further on. They were one
     * list, sized by the drawing, and `_sectionBlocks` was cutting the block
     * table at the wrong place because of it. Merging them again would put a
     * fifteen-metre gap in the sleepers on plain line at every junction — the
     * measurement is in `junctionOverlap`. */
    this.overlaps = [];
    /* The turnout leads this road leaves and arrives on, spliced into the
     * alignment rather than drawn beside it — see `_splice` and `makeLead`. */
    this.prefix = opts.prefix || null;
    this.suffix = opts.suffix || null;
    this._solve();
  }

  /** Straights and fillets, in flat x/z, as a dense list of {x, z, k}. */
  _solve() {
    const P = this.control;
    const n = P.length;
    if (n < 2) { this.raw = []; return; }

    const dir = [], leg = [];
    for (let i = 0; i < n - 1; i++) {
      const d = sub2(P[i + 1], P[i]);
      leg.push(len2(d));
      dir.push(norm2(d));
    }

    /* Per-corner radius, then shrunk until consecutive fillets fit in the leg
     * between them. Two passes is enough for anything this file builds, and
     * shrinking is always safe — a tighter corner is ugly, an overlapping one
     * folds the alignment back on itself. */
    const corner = [];
    for (let i = 1; i < n - 1; i++) {
      const a = dir[i - 1], b = dir[i];
      const c = cross2(a, b);
      const turn = Math.acos(Math.max(-1, Math.min(1, dot2(a, b))));
      /* A deflection under about two thirds of a degree is not a curve. On a
       * 90m radius it is four centimetres of offset — less than the tolerance
       * the track is laid to — and treating it as one would demand a spiral and
       * a cant run-off for a corner no wheel could feel. Laid straight, and the
       * curve statistics stay about curves. */
      if (!(turn > CURVE_MIN_TURN)) { corner.push(null); continue; }
      corner.push({i, a, b, side: c >= 0 ? 1 : -1, turn,
                   R: (this.radii && this.radii[i - 1]) || this.radius, f: null});
    }
    for (let pass = 0; pass < 4; pass++) {
      for (const c of corner) {
        /* The easement is a fraction of the RADIUS, recomputed every pass,
         * because the fit pass below shrinks radii to make two fillets share a
         * leg — and a spiral sized once from a radius that then halved is a
         * transition longer than the arc it transitions into. `fillet` caps it
         * again at 0.48·R·θ so there is always some constant-radius arc left in
         * the middle; a corner too small to hold one becomes two spirals meeting
         * nose to nose, which is still a transitioned curve. */
        if (c) c.f = fillet(c.turn, c.R, Math.max(EASE_MIN, EASE_K * c.R));
      }
      let ok = true;
      for (let i = 0; i < n - 1; i++) {
        const before = corner[i - 1], after = corner[i];
        const need = (before?.f?.T || 0) + (after?.f?.T || 0);
        const room = leg[i] - 0.5;
        if (need > room && need > 0) {
          ok = false;
          const k = Math.max(0.12, room / need) * 0.97;
          if (before) before.R = Math.max(this.minRadius, before.R * k);
          if (after) after.R = Math.max(this.minRadius, after.R * k);
        }
      }
      if (ok) break;
    }
    /* A corner that could not be made to fit above its floor is reported, not
     * silently laid: the caller (`_branch`, `_terminalLoop`) would rather skip
     * a connection than build one no train could take. */
    this.minRadiusUsed = Infinity;
    this.tight = false;
    this.curves = 0;
    this.spiralMin = Infinity;
    for (const c of corner) {
      if (!c) continue;
      this.curves++;
      this.minRadiusUsed = Math.min(this.minRadiusUsed, c.R);
      if (c.f) this.spiralMin = Math.min(this.spiralMin, c.f.Ls);
      /* Below the class floor the fit pass has run out of room, and the caller
       * is told rather than handed a hairpin: `_branch`, `_loadingLoop` and
       * `_terminalLoop` all refuse a connection they cannot lay legally. */
      if (c.R <= this.minRadius + 1e-6) this.tight = true;
    }
    if (!this.curves) { this.minRadiusUsed = Infinity; this.spiralMin = Infinity; }

    const raw = [];
    const push = (x, z, k) => {
      const last = raw[raw.length - 1];
      if (last && Math.abs(last.x - x) < 1e-4 && Math.abs(last.z - z) < 1e-4) {
        last.k = k;
        return;
      }
      raw.push({x, z, k});
    };
    push(P[0].x, P[0].z, 0);
    for (let i = 1; i < n - 1; i++) {
      const c = corner[i - 1];
      if (!c || !c.f) { push(P[i].x, P[i].z, 0); continue; }
      const a = c.a, r = rightOf(a), s = c.side;
      const sx = P[i].x - a.x * c.f.T, sz = P[i].z - a.z * c.f.T;
      push(sx, sz, 0);
      for (const q of c.f.pts) {
        push(sx + a.x * q.u + r.x * s * q.v,
             sz + a.z * q.u + r.z * s * q.v, s * q.k);
      }
    }
    push(P[n - 1].x, P[n - 1].z, 0);
    this.raw = raw;
    this._splice();
  }

  /** Splice the turnout leads on to the ends of the alignment.
   *
   *  This is where the file keeps Factorio's invariant. A road that leaves
   *  another road does not START near it: it starts ON it, on the through
   *  road's own centreline, and its first ~28m ARE the turnout. `makeLead`
   *  generates that curve from the parent's own frame, so its first point and
   *  its first tangent are the parent's to the last float; splicing it in here
   *  means the child's arc length zero *is* the switch tip and everything read
   *  off the alignment afterwards — ballast, rails, sleepers, blocks, the
   *  routes trains run — comes off one continuous curve rather than two that
   *  were drawn to nearly meet.
   *
   *  The caller has already made the lead's exit port the terminal control
   *  point, so the join here is a duplicate point, not a corner. */
  _splice() {
    const copy = q => ({x: q.x, z: q.z, k: q.k});
    if (this.prefix?.length > 1) {
      this.raw = [...this.prefix.map(copy), ...this.raw.slice(1)];
    }
    if (this.suffix?.length > 1) {
      this.raw = [...this.raw.slice(0, -1), ...this.suffix.map(copy)];
    }
  }

  /** Resample the flat alignment at a uniform step, then lift, grade and cant
   *  it. `ground` is ctx.ground; it may answer 0 for the whole map, which is
   *  the flat case and must look right too. */
  build(ground) {
    const raw = this.raw;
    if (!raw || raw.length < 2) { this.frames = null; this.length = 0; return this; }
    const acc = [0];
    for (let i = 1; i < raw.length; i++) {
      acc.push(acc[i - 1] + Math.hypot(raw[i].x - raw[i - 1].x,
                                       raw[i].z - raw[i - 1].z));
    }
    const total = acc[acc.length - 1];
    if (!(total > 1)) { this.frames = null; this.length = 0; return this; }
    const count = Math.max(2, Math.round(total / this.step) + 1);
    const step = total / (count - 1);
    this.step = step;
    this.length = total;

    const X = new Float32Array(count), Z = new Float32Array(count);
    const K = new Float32Array(count);
    let seg = 0;
    for (let i = 0; i < count; i++) {
      const s = Math.min(total, i * step);
      while (seg < acc.length - 2 && acc[seg + 1] < s) seg++;
      const span = acc[seg + 1] - acc[seg] || 1;
      const t = (s - acc[seg]) / span;
      X[i] = raw[seg].x + (raw[seg + 1].x - raw[seg].x) * t;
      Z[i] = raw[seg].z + (raw[seg + 1].z - raw[seg].z) * t;
      K[i] = raw[seg].k + (raw[seg + 1].k - raw[seg].k) * t;
    }

    /* ---- the vertical alignment ---------------------------------------
     *
     * A railway's profile is DESIGNED and then the ground is made to fit it.
     * That sentence is the whole of this round's change, and everything that
     * was wrong before follows from its opposite having been true.
     *
     * What this used to do was drape. The profile was pulled taut inside a tube
     * whose floor was the ground itself — `lo = G + TOE_CLEAR`, because nothing
     * in this file can excavate — so wherever the hillside rose faster than a
     * bank could be built the line simply climbed it. Measured across three
     * routes at 400 samples each the result was a 35% maximum gradient, 132 to
     * 184 samples out of 400 laid steeper than the ruling grade, and — the
     * clause that gives it away — not one metre of cutting declared anywhere,
     * ever, on any layout. A line that only ever fills is not economising on
     * earthworks; it is a polyline that has been draped over a heightfield and
     * is passing through the land wherever the land comes up.
     *
     * So the order of operations is inverted. terrain.js builds the natural
     * landform knowing nothing about rail; this file plans an alignment inside
     * the geometry rules and then DECLARES, chainage by chainage, what it needs
     * done to the ground — cut, fill, viaduct, tunnel or bridge. `earthworks()`
     * publishes it. Nothing is silently intersected: where the formation is
     * below the ground it says so and asks for a cutting, and where it stands
     * more than VIADUCT_FILL above it, it stops asking for a bank and builds a
     * structure instead.
     *
     * The profile itself is the minimum-earthwork g-Lipschitz function, which
     * is a mouthful for a simple and rather pretty construction:
     *
     *   · `dilate(G)` is the smallest profile at or above the ground that never
     *     exceeds the ruling gradient — the all-fill railway, a cone hull;
     *   · `erode(G)` is the largest one at or below it — the all-cut railway;
     *   · their mean is g-Lipschitz too (the average of two g-Lipschitz
     *     functions always is), and it balances what it digs against what it
     *     tips, which is what a real earthworks estimate is trying to do.
     *
     * Then it is pulled back toward the ground a few times, re-projecting after
     * each pull so it can never leave the gradient rule, and finally rounded
     * over so that every change of grade is a vertical curve rather than a
     * corner. Both sweeps of each operator are O(n) with a running maximum, so
     * the whole railway's profile costs a few hundred microseconds.
     *
     * The one thing this deliberately does NOT do is compromise. It does not
     * clip to a fill ceiling and it does not floor itself on the ground, and
     * both of those were in the previous version. An envelope clipped to a
     * ceiling is a causeway at the ceiling height (which is exactly what Ryan
     * saw in the round before this one), and a profile floored on the ground is
     * a line that cannot cut — which is this round's.
     */

    /* The ground under the formation is the *highest* ground the formation
     * covers, not the ground on the centreline: terrain.js answers heightAt
     * from a grid, and a station pad's graded edge crossing the line at an
     * angle can be most of a metre above what the centreline reports a couple
     * of metres away.
     *
     * ±1.30m, the sleeper end. Outside it the ballast batter is a drape that
     * rises to meet the ground, so sampling out to the shoulder pays for the
     * same protection twice — in railway height, along the whole site. It is
     * still a maximum rather than a mean because this array is now also what a
     * declared CUTTING is quoted against, and a cutting has to clear the
     * highest ground it passes through, not the average of it. */
    const G = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      if (!ground) { G[i] = 0; continue; }
      const a = Math.max(0, i - 1), b = Math.min(count - 1, i + 1);
      let tx = X[b] - X[a], tz = Z[b] - Z[a];
      const tl = Math.hypot(tx, tz) || 1;
      const nx = -tz / tl, nz = tx / tl;
      let g = ground(X[i], Z[i]) || 0;
      let lo = g;
      for (const d of [-1.30, 1.30]) {
        const h = ground(X[i] + nx * d, Z[i] + nz * d) || 0;
        g = Math.max(g, h);
        lo = Math.min(lo, h);
      }
      G[i] = g;
    }
    this.groundY = G;

    const smooth = this._grade(G, count, step);
    /* Kept because `pinEnd` re-derives from it. Pinning a junction is a change
     * to the DESIGN, not a patch on top of the last one: applying two pins
     * cumulatively to an already-pinned profile is how a road that meets its
     * parent at one end ends up leaving the ground at the other. */
    this.designY = Float32Array.from(smooth);
    this._anchors = {};

    const pos = new Float32Array(count * 3);
    const tan = new Float32Array(count * 3);
    const upA = new Float32Array(count * 3);
    const rgt = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      pos[i * 3] = X[i];
      pos[i * 3 + 1] = smooth[i] + FORMATION;
      pos[i * 3 + 2] = Z[i];
    }
    this.frames = {count, pos, tan, up: upA, right: rgt, k: K, step};
    this._works = null;
    this._reframe();
    this.measure();
    return this;
  }

  /** The minimum-earthwork profile that never exceeds the ruling gradient.
   *
   *  `dilate` and `erode` are the two extreme answers — the all-fill railway
   *  and the all-cut one — and both are computed in two linear sweeps because a
   *  cone hull is separable: propagate the steepest admissible line left to
   *  right, then right to left, and what is left is `max_j (G[j] − g·|i−j|)`
   *  exactly. Their mean is g-Lipschitz for free and balances the two.
   *
   *  `fit` is then a legality operator that can be applied to anything: it maps
   *  an arbitrary profile to a g-Lipschitz one near it, and it is the identity
   *  on a profile that is already legal. That is what makes the pull toward the
   *  ground safe to iterate — every step ends inside the rule, so no amount of
   *  chasing the terrain can produce an illegal gradient. */
  _ops(n, step) {
    const d = Math.max(1e-4, this.maxGrade * step);
    const A = new Float64Array(n), B = new Float64Array(n);
    const dilate = (src, dst) => {
      dst[0] = src[0];
      for (let i = 1; i < n; i++) dst[i] = Math.max(src[i], dst[i - 1] - d);
      for (let i = n - 2; i >= 0; i--) dst[i] = Math.max(dst[i], dst[i + 1] - d);
    };
    const erode = (src, dst) => {
      dst[0] = src[0];
      for (let i = 1; i < n; i++) dst[i] = Math.min(src[i], dst[i - 1] + d);
      for (let i = n - 2; i >= 0; i--) dst[i] = Math.min(dst[i], dst[i + 1] + d);
    };
    /* The legality operator. It maps ANY profile to a g-Lipschitz one near it
     * and is the identity on one that is already legal, which is what makes it
     * safe to apply after every other operation in this file — the pull toward
     * the ground, the junction blends, the vertical curves. */
    const bias = this.fillBias;
    const fit = (src, dst) => {
      dilate(src, A); erode(src, B);
      for (let i = 0; i < n; i++) dst[i] = A[i] * bias + B[i] * (1 - bias);
    };
    /* A box mean, which applied twice is the parabolic vertical curve a railway
     * is set out with. A mean can only reduce a slope, so it never breaks the
     * gradient rule it is applied after. */
    const box = (src, dst, w) => {
      const m = 2 * w + 1;
      let sum = 0;
      for (let j = -w; j <= w; j++) sum += src[Math.min(n - 1, Math.max(0, j))];
      for (let i = 0; i < n; i++) {
        dst[i] = sum / m;
        sum += src[Math.min(n - 1, Math.max(0, i + w + 1))]
             - src[Math.min(n - 1, Math.max(0, i - w))];
      }
    };
    return {d, fit, box, w: Math.max(1, Math.round(this.vCurve / step))};
  }

  _grade(G, n, step) {
    const {fit, box, w} = this._ops(n, step);
    const y = new Float64Array(n);
    const t = new Float64Array(n);
    fit(G, y);
    /* Six pulls at 0.62 of the remaining distance. The first few take out most
     * of the earthwork the balanced cone hull leaves over long shallow reaches;
     * past six the profile stops moving because what is left is genuinely
     * forced by the gradient rule, and the numbers say so — on this site the
     * mean depth of cut falls 0.31m over the first three rounds and 0.02m over
     * the last three. */
    for (let r = 0; r < 6; r++) {
      for (let i = 0; i < n; i++) t[i] = y[i] + 0.62 * (G[i] - y[i]);
      fit(t, y);
    }
    /* ---- vertical curves ------------------------------------------------
     *
     * What comes out of the cone hull is a profile made of straight grades that
     * meet at corners, because that is what a max of cones IS. A corner in the
     * profile is a vertical discontinuity a bogie cannot follow and a coupler
     * would ride up over, and it is also the thing that made this railway's
     * worst vertical radius come out at eight metres in the round before this
     * one. Every change of grade gets a curve, which here is two passes of a
     * box mean over `vCurve` metres: a box mean applied twice is a piecewise
     * quadratic, which is precisely the parabolic vertical curve a railway is
     * actually set out with, and its length is the window — 20m on a running
     * line, 10m in a yard.
     *
     * A mean can only ever reduce a slope, so this cannot break the gradient
     * rule; the `fit` afterwards is belt and braces and costs nothing, being
     * the identity on anything already legal. */
    box(y, t, w); box(t, y, w);
    fit(y, y);
    return y;
  }

  /** What this alignment actually came out at, measured on the finished frames
   *  rather than on the profile that produced them.
   *
   *  It matters that it is the frames: `pinEnd` runs afterwards and moves an
   *  end of the profile on to the road it leaves from, and a statistic taken
   *  before that is a statistic about a railway that was not built. */
  measure() {
    const f = this.frames;
    if (!f) return this;
    const {count, pos, step} = f;
    let worst = 0, worstS = 0, steep = 0, vk = 0, vkS = 0;
    for (let i = 1; i < count; i++) {
      const g = Math.abs(pos[i * 3 + 1] - pos[(i - 1) * 3 + 1]) / step;
      if (g > worst) { worst = g; worstS = i * step; }
      if (g > this.maxGrade * 1.005) steep++;
      if (i < count - 1) {
        const k = Math.abs(pos[(i + 1) * 3 + 1] - 2 * pos[i * 3 + 1]
                           + pos[(i - 1) * 3 + 1]) / (step * step);
        if (k > vk) { vk = k; vkS = i * step; }
      }
    }
    this.ruling = worst;
    this.rulingAt = worstS;
    this.overGrade = steep / Math.max(1, count - 1);
    this.vRadius = vk > 1e-9 ? 1 / vk : Infinity;
    this.vRadiusAt = vkS;
    const G = this.groundY;
    if (G && G.length === count) {
      let fill = 0, cut = 0, nf = 0, nc = 0, mf = 0, mc = 0;
      for (let i = 0; i < count; i++) {
        const dd = (pos[i * 3 + 1] - FORMATION) - G[i];
        if (dd >= 0) { fill += dd; nf++; mf = Math.max(mf, dd); }
        else { cut -= dd; nc++; mc = Math.max(mc, -dd); }
      }
      this.meanFill = fill / count;
      this.meanCut = cut / count;
      this.maxFill = mf;
      this.maxCut = mc;
      this.cutFraction = nc / count;
      this.bankFraction = nf / count;
    }
    return this;
  }

  /** The earthworks declaration: what the ground has to become, chainage by
   *  chainage, for this alignment to be buildable.
   *
   *  This is the piece the whole round exists to produce. terrain.js owns the
   *  land and builds first; it cannot know where the railway will go, and this
   *  file cannot excavate. The only way out of that is for the railway to say
   *  what it needs in a form the land can be built to — so every metre of every
   *  alignment is classified, the classification is run-length encoded into
   *  spans a machine or a person could actually work to, and `Rail.earthworks()`
   *  publishes the lot.
   *
   *  The five kinds are the five things a railway does when the ground is not
   *  where it wants it, and the thresholds between them are the rules at the
   *  top of this file:
   *
   *    cut      the ground comes down to the formation, battered back at 1:1
   *    fill     it is built up to the formation, battered out at 1:1.5
   *    tunnel   cut deeper than TUNNEL_CUT: bored, not dug
   *    viaduct  fill taller than VIADUCT_FILL: built, not tipped
   *    bridge   the ground is at or under the water plane. Always a bridge.
   *             A causeway is cheaper and is the one water crossing that reads
   *             as a mistake from every angle, so this rule has no threshold
   *             and no exception — any wet chainage at all is a span.
   *
   *  Short structures are demoted and short gaps between structures absorbed,
   *  because a 6m viaduct with 9m of bank at each end is one viaduct that has
   *  been sampled badly, and it is the sampling that would be visible. */
  earthworks() {
    if (this._works) return this._works;
    const f = this.frames, G = this.groundY;
    if (!f || !G || G.length !== f.count) return (this._works = []);
    const n = f.count, step = f.step;
    const kind = new Array(n);
    const depth = new Float64Array(n);
    const wet = Number.isFinite(this.waterY) ? this.waterY + WET_FREEBOARD
                                            : -Infinity;
    for (let i = 0; i < n; i++) {
      const sub = f.pos[i * 3 + 1] - FORMATION;
      const d = sub - G[i];
      depth[i] = d;
      if (G[i] <= wet) kind[i] = 'bridge';
      else if (d > VIADUCT_FILL) kind[i] = 'viaduct';
      else if (-d > TUNNEL_CUT) kind[i] = 'tunnel';
      else if (d > 0.20) kind[i] = 'fill';
      else if (d < -0.20) kind[i] = 'cut';
      else kind[i] = 'grade';
    }
    /* ---- nothing is declared on alignment nobody lays ----------------------
     *
     * Every road on this railway begins and ends on its PARENT's centreline: a
     * turnout lead is the parent's own track for its whole length, and the
     * child's alignment is carried across it only so that arc length is
     * continuous through the junction. `renderFrom`/`renderTo` are where the
     * child is genuinely its own railway. Classifying the chainage outside them
     * declares the same metre of ground twice — once by the parent, correctly,
     * and once by a child whose formation there is an extrapolation.
     *
     * It is not a cosmetic error. Measured before this change
     * (`harness/rr-look.mjs --list`): `load:0` declared a 22.5m `tunnel` at
     * chainage 452.6-475.1 against a drawn extent that ended at 452.5, and
     * `load:90` the same at 364.3-385.3 against 362.7. `_buildStructures`
     * duly built FOUR masonry portals — four of the eight in the world — with
     * no rails going into them, standing in open grass, two of them within two
     * metres of a real bore's mouth so that two headwalls interpenetrated. It
     * is also where terrain's complaint about a 23.3m open cut on `load:90`
     * came from: the deepest earthwork on the railway was on a piece of
     * alignment that is not a railway.
     *
     * The window is applied before the gap and demotion passes, and every one
     * of those passes is clamped to it, so a structure can be shortened by the
     * window but can never be widened back out through it. */
    const iLo = Math.max(0, Math.ceil((this.renderFrom || 0) / step - 1e-6));
    const iHi = Math.min(n - 1, Math.floor(
      Math.min(this.renderTo, this.length) / step + 1e-6));
    for (let i = 0; i < n; i++) {
      if (i < iLo || i > iHi) { kind[i] = 'grade'; depth[i] = 0; }
    }
    const isStruct = k => k === 'bridge' || k === 'viaduct' || k === 'tunnel';
    /* Absorb a short gap between two structures of the same kind: the gap is
     * spanned rather than interrupted. */
    const gapN = Math.max(1, Math.round(STRUCT_GAP / step));
    for (let i = 0; i < n; i++) {
      if (!isStruct(kind[i])) continue;
      const k = kind[i];
      let j = i;
      while (j < n && kind[j] === k) j++;
      for (let g = j; g < Math.min(n, j + gapN); g++) {
        if (kind[g] === k) { for (let q = j; q < g; q++) kind[q] = k; break; }
      }
      i = j - 1;
    }
    /* And demote anything too short to be worth building. A tunnel is demoted
     * to cut and a viaduct to fill, which is the honest fallback: the ground
     * still has to be moved, it just is not a structure. A short WET span is
     * not demoted — it is widened, because there is no such thing as twelve
     * metres of causeway.
     *
     * The same is true of a deep one, and it was not handled. Demotion ignored
     * depth, so a tunnel that happened to be short became an open cut of
     * whatever depth it had: terrain applied a declared 23.3 m `cut` — 14 m
     * past this file's own TUNNEL_CUT — and its 1:1 batter took the cover over
     * a neighbouring bore from 36.1 m down to 25.4 m. The threshold existed and
     * the demotion walked straight through it.
     *
     * There is no such thing as a 23 m open cut on a railway that calls 9 m a
     * tunnel. So a short structure is only demoted when what it demotes TO is
     * legal: past the threshold it is widened to the minimum length instead,
     * exactly as the wet span already was. Widening buys ground; demoting sells
     * a rule. */
    const minN = Math.max(2, Math.round(STRUCT_MIN / step));
    const worstOver = (a, b) => {
      let w = 0;
      for (let q = a; q < b; q++) w = Math.max(w, Math.abs(depth[q]));
      return w;
    };
    for (let i = 0; i < n; i++) {
      const k = kind[i];
      if (!isStruct(k)) continue;
      let j = i;
      while (j < n && kind[j] === k) j++;
      if (j - i < minN) {
        const deep = k === 'tunnel' ? worstOver(i, j) > TUNNEL_CUT
                   : k === 'viaduct' ? worstOver(i, j) > VIADUCT_FILL
                   : false;
        if (k === 'bridge' || deep) {
          const pad = Math.ceil((minN - (j - i)) / 2);
          /* Widening buys ground, but never ground outside the laid window:
           * padding a short deep bore back out into a turnout lead is how the
           * phantom portals would come straight back. */
          for (let q = Math.max(iLo, i - pad); q <= Math.min(iHi, j + pad - 1); q++) {
            kind[q] = k;
          }
        } else {
          const to = k === 'tunnel' ? 'cut' : 'fill';
          for (let q = i; q < j; q++) kind[q] = to;
        }
      }
      i = j - 1;
    }
    /* ---- the approach reserve ----------------------------------------------
     *
     * A deck span is excluded from grading, so the ground under it is whatever
     * terrain leaves. That is only true of the ground under the MIDDLE of it.
     * An embankment is graded with a 1:1.5 batter, and a batter is a
     * three-dimensional thing: it reaches past the end of the span that asked
     * for it by about one and a half times its own height, in every direction
     * including along the line. So the fill either side of a viaduct pours into
     * the last several metres of the hole the viaduct is there to cross.
     *
     * This is not a guess. `harness/rr-abut.mjs`, before this change: at the
     * `from` end of branch0's viaduct rail sampled the ground at −11.21 m and
     * declared 6.73 m of fill; the ground terrain finally built there is −4.52,
     * i.e. six and three quarter metres of earth arrived under the deck. All
     * four deck ends in the world had their soffit below the finished ground,
     * worst −1.11 m. On branch1's 24 m span the fill had come so far in that
     * the deepest clearance left anywhere along it was 1.3 m: a bridge with no
     * hole under it, which is why it read as a length of track that stops.
     *
     * The fix is to claim the ground the batter would otherwise take. Each deck
     * span is extended outward by the reach of the fill at its own end —
     * FILL_BATTER times the depth there, capped at 14 m — through chainage that
     * is already bank, and never through a cutting, a bore, or out of the laid
     * window. Terrain's fill then starts far enough away that its toe lands
     * outside the span, and the deck carries the track over the reserve, so
     * nothing is left standing on ground that was never built.
     *
     * It costs deck. It buys the only thing that makes a bridge read as a
     * bridge, which is being able to see what it stands on. */
    {
      const runs = [];
      for (let i = 0; i < n; i++) {
        if (kind[i] !== 'viaduct' && kind[i] !== 'bridge') continue;
        const k = kind[i];
        let j = i;
        while (j < n && kind[j] === k) j++;
        runs.push([i, j, k]);
        i = j - 1;
      }
      const spill = d => Math.min(Math.round(FILL_BATTER * Math.max(0, d) / step),
                                  Math.round(14 / step));
      for (const [i, j, k] of runs) {
        let back = spill(depth[i]);
        for (let q = i - 1; q >= iLo && back > 0; q--, back--) {
          if (kind[q] !== 'fill' && kind[q] !== 'grade') break;
          if (!(depth[q] > 0.15)) break;         // no bank here to spill
          kind[q] = k;
        }
        let fwd = spill(depth[j - 1]);
        for (let q = j; q <= iHi && fwd > 0; q++, fwd--) {
          if (kind[q] !== 'fill' && kind[q] !== 'grade') break;
          if (!(depth[q] > 0.15)) break;
          kind[q] = k;
        }
      }
    }
    const out = [];
    for (let i = 0; i < n;) {
      const k = kind[i];
      let j = i;
      let worst = 0;
      while (j < n && kind[j] === k) {
        worst = Math.max(worst, Math.abs(depth[j]));
        j++;
      }
      const a = i * step, b = Math.min(this.length, (j - 1) * step);
      if (b > a + 0.5 || isStruct(k)) {
        out.push({track: this.name, kind: k, from: a, to: b,
                  length: b - a, maxDepth: worst,
                  half: VERGE_X, batter: k === 'cut' || k === 'tunnel'
                                          ? CUT_BATTER : FILL_BATTER,
                  i0: i, i1: j - 1});
      }
      i = j;
    }
    this._works = out;
    /* Two views the mesh builders want by arc length rather than by span: the
     * decks it must not drape ballast off the side of, and the bores it must
     * not draw any permanent way inside at all. */
    this.decks = out.filter(w => w.kind === 'bridge' || w.kind === 'viaduct')
                    .map(w => [w.from, w.to]);
    this.bores = out.filter(w => w.kind === 'tunnel').map(w => [w.from, w.to]);
    return out;
  }

  /** Whether arc length `s` is inside one of the listed ranges. */
  inRanges(list, s) {
    if (!list) return false;
    for (const r of list) if (s >= r[0] && s <= r[1]) return true;
    return false;
  }

  /** Tangents, cant and the right vector, read off `pos` and the curvature
   *  array. Extracted from `build` so a profile that has been pinned to a
   *  neighbour's rail level (`pinEnd`) can be re-squared without laying the
   *  alignment again. */
  _reframe() {
    const f = this.frames;
    if (!f) return;
    const {count, pos, tan, up: upA, right: rgt, k: K} = f;
    for (let i = 0; i < count; i++) {
      const a = Math.max(0, i - 1), b = Math.min(count - 1, i + 1);
      let tx = pos[b * 3] - pos[a * 3];
      let ty = pos[b * 3 + 1] - pos[a * 3 + 1];
      let tz = pos[b * 3 + 2] - pos[a * 3 + 2];
      const l = Math.hypot(tx, ty, tz) || 1;
      tx /= l; ty /= l; tz /= l;
      /* right = tangent × worldUp, horizontal by construction */
      let rx = -tz, rz = tx;
      const rl = Math.hypot(rx, rz) || 1;
      rx /= rl; rz /= rl;
      const roll = Math.max(-MAX_CANT, Math.min(MAX_CANT, K[i] * CANT_GAIN));
      const c = Math.cos(roll), s = Math.sin(roll);
      /* Roll the deck toward the outside of the curve: leaning the normal to
       * the right raises the left-hand rail, which is the outer one in a
       * right-hand curve (K > 0). */
      let ux = rx * s, uy = c, uz = rz * s;
      const ul = Math.hypot(ux, uy, uz) || 1;
      ux /= ul; uy /= ul; uz /= ul;
      /* re-square right against the canted up */
      const qx = ty * uz - tz * uy, qy = tz * ux - tx * uz, qz = tx * uy - ty * ux;
      const ql = Math.hypot(qx, qy, qz) || 1;
      tan[i * 3] = tx; tan[i * 3 + 1] = ty; tan[i * 3 + 2] = tz;
      upA[i * 3] = ux; upA[i * 3 + 1] = uy; upA[i * 3 + 2] = uz;
      rgt[i * 3] = qx / ql; rgt[i * 3 + 1] = qy / ql; rgt[i * 3 + 2] = qz / ql;
    }
  }

  /** Pin one end of the profile to the road it leaves from — exactly.
   *
   *  Plan and tangent already close to the last float: the lead was generated
   *  from the parent's own frame. Height does not, and nothing before this
   *  noticed. `build` grades every alignment on its own — smoothing, filling,
   *  ruling gradient — so two roads that meet at one point in plan are graded
   *  to two different heights there, and a turnout whose two halves are forty
   *  centimetres apart vertically is a join that does not close however right
   *  it looks from above.
   *
   *  So the end is moved on to the neighbour's rail level AND its gradient, and
   *  the correction is a cubic that dies to nothing over `blend` metres. That
   *  is a vertical transition curve, which is exactly what a railway would put
   *  there to do the same job — and because it is C1 at both ends it cannot
   *  introduce the kink it exists to remove.
   *
   *  `grade` is dy/ds measured travelling AWAY from the junction, which is the
   *  same sign at either end of the road. */
  pinEnd(which, y, grade, blend = 80) {
    const f = this.frames;
    if (!f || !this.designY) return 0;
    const before = f.pos[(which === 'start' ? 0 : f.count - 1) * 3 + 1];
    (this._anchors = this._anchors || {})[which] = {y, grade, blend};
    this._applyAnchors();
    this._reframe();
    /* The declaration and the statistics both describe the frames, and the
     * frames have just moved. Recomputing here is what stops `earthworks()`
     * publishing a cut that was cancelled by a junction pin. */
    this._works = null;
    this.measure();
    return y - before;
  }

  /** Re-derive the profile from the design, with every junction it has been
   *  pinned at honoured — in level, in gradient, and without breaking the
   *  ruling grade to do it.
   *
   *  That last clause is the whole of this method's reason to exist, and it was
   *  the largest single hole in the round that introduced it. The blend used to
   *  be a fixed 90m: a road whose own grading put its end four metres away from
   *  its parent's railhead had those four metres taken out over ninety, which is
   *  1 in 12 — so the branches came out at a measured 6.6% and 10.8% worst
   *  gradient while `Track._grade` was, correctly, guaranteeing 2.5%. The steep
   *  chainages on this railway were ALL junction blends. A blend is a piece of
   *  vertical alignment like any other and it has to obey the same rule.
   *
   *  So two things happen. The blend is made as long as the discrepancy needs
   *  rather than as long as it has always been; and afterwards the whole profile
   *  is clamped into the cones that radiate from each anchor at exactly the
   *  ruling gradient. A cone is g-Lipschitz and so is a min or a max of cones,
   *  so clamping a legal profile between them leaves it legal — and at the
   *  anchor itself the two cones pinch to a point, which is what makes the pin
   *  exact rather than approximate. */
  _applyAnchors() {
    const f = this.frames;
    const {count, pos, step} = f;
    const A = this._anchors || {};
    const y = new Float64Array(count);
    for (let i = 0; i < count; i++) y[i] = this.designY[i] + FORMATION;
    for (const which of ['start', 'end']) {
      const a = A[which];
      if (!a) continue;
      const idx = i => (which === 'start' ? i : count - 1 - i);
      const j0 = idx(0), j1 = idx(1);
      const dY = a.y - y[j0];
      const g0 = (y[j1] - y[j0]) / step;
      /* The parent's gradient, but never steeper than this road is allowed to
       * be. A loading road pinned to a branch at 1 in 40 would otherwise be
       * asked to leave its junction at 1 in 40 and then be clamped back to its
       * own rule two sleepers later, which is a kink at the switch. */
      const pg = Math.max(-this.maxGrade, Math.min(this.maxGrade, a.grade));
      const dG = pg - g0;
      /* Long enough that the correction itself is inside the ruling grade. The
       * quintic's steepest point carries 1.875·dY/L of the level shift and about
       * dG of the gradient shift, and half the budget is left for the profile
       * that is already there. */
      const room = Math.max(0.25 * this.maxGrade,
                            this.maxGrade * 0.55 - Math.abs(dG));
      const want = Math.max(a.blend || 60, 1.875 * Math.abs(dY) / room);
      const m = Math.min(count - 1,
                         Math.max(3, Math.round(Math.min(want, this.length * 0.48)
                                                / step)));
      const L = m * step;
      for (let i = 0; i <= m; i++) {
        const t = i / m, t2 = t * t, t3 = t2 * t, t4 = t3 * t, t5 = t4 * t;
        const H0 = 1 - 10 * t3 + 15 * t4 - 6 * t5;
        const H1 = t - 6 * t3 + 8 * t4 - 3 * t5;
        y[idx(i)] += dY * H0 + dG * L * H1;
      }
    }
    /* Now make the whole thing legal again, and the pins exact again, and keep
     * doing both until they stop arguing.
     *
     * `fit` maps the blended profile back inside the ruling gradient but moves
     * the ends off their junctions; the cone clamp restores the junctions
     * exactly and leaves the profile legal, because a cone is g-Lipschitz and
     * clamping a g-Lipschitz function between two of them keeps it one. Neither
     * alone is enough — alternating them is a projection onto the intersection
     * of two convex sets and it converges in a handful of rounds — and the
     * vertical-curve pass rides along inside the loop so the corners the blend
     * and the clamp leave get rounded off rather than shipped.
     *
     * The fixed 90m blend this replaces did neither: it held the pin and broke
     * the gradient, and the steepest chainage on every branch of this railway
     * was the eighty metres either side of a junction. */
    const {fit, box, w} = this._ops(count, step);
    const tmp = new Float64Array(count);
    const d = this.maxGrade * step;
    let lo = null, hi = null;
    for (const which of ['start', 'end']) {
      const a = A[which];
      if (!a) continue;
      const at = which === 'start' ? 0 : count - 1;
      lo = lo || new Float64Array(count).fill(-Infinity);
      hi = hi || new Float64Array(count).fill(Infinity);
      for (let i = 0; i < count; i++) {
        const r = Math.abs(i - at) * d;
        lo[i] = Math.max(lo[i], a.y - r);
        hi[i] = Math.min(hi[i], a.y + r);
      }
    }
    const clamp = () => {
      if (!lo) return;
      for (let i = 0; i < count; i++) {
        /* `hi` first, then `lo`: where two anchors are further apart in level
         * than the ruling grade can join — which a plan can produce and this
         * file cannot refuse — the cones cross, and holding the pin at each end
         * is worth more than a rule that cannot be satisfied between them. */
        y[i] = Math.max(lo[i], Math.min(hi[i], y[i]));
      }
    };
    for (let r = 0; r < 4; r++) {
      fit(y, y);
      box(y, tmp, w); box(tmp, y, w);
      clamp();
    }
    for (let i = 0; i < count; i++) pos[i * 3 + 1] = y[i];
  }

  /** Frame at arc length `s`, interpolated. Uniform spacing makes this an
   *  index and a lerp — it is called for every sleeper, every signal and every
   *  turnout on the railway. */
  at(s, out) {
    const f = this.frames;
    const o = out || {position: new THREE.Vector3(), tangent: new THREE.Vector3(),
                      up: new THREE.Vector3(), right: new THREE.Vector3(), k: 0};
    if (!f) return o;
    const q = Math.max(0, Math.min(f.count - 1.0001, s / f.step));
    const i = q | 0, t = q - i, j = Math.min(f.count - 1, i + 1);
    const lerp3 = (arr, v) => v.set(
      arr[i * 3] + (arr[j * 3] - arr[i * 3]) * t,
      arr[i * 3 + 1] + (arr[j * 3 + 1] - arr[i * 3 + 1]) * t,
      arr[i * 3 + 2] + (arr[j * 3 + 2] - arr[i * 3 + 2]) * t);
    lerp3(f.pos, o.position);
    lerp3(f.tan, o.tangent);
    lerp3(f.up, o.up);
    lerp3(f.right, o.right);
    o.k = f.k[i] + (f.k[j] - f.k[i]) * t;
    return o;
  }

  /** Arc length of the point on this track nearest (x, z) — how every link
   *  finds where it attaches to its parent, so no junction position is ever
   *  written down twice. */
  nearest(x, z) {
    const f = this.frames;
    if (!f) return {s: 0, distance: Infinity};
    let best = 0, bestD = Infinity;
    for (let i = 0; i < f.count; i++) {
      const dx = f.pos[i * 3] - x, dz = f.pos[i * 3 + 2] - z;
      const d = dx * dx + dz * dz;
      if (d < bestD) { bestD = d; best = i; }
    }
    return {s: best * f.step, distance: Math.sqrt(bestD)};
  }

  blocked(s) {
    for (const b of this.blocks) if (s > b[0] && s < b[1]) return true;
    return false;
  }
}

/* ---- a small mesh builder ------------------------------------------------
 *
 * three's geometry merge lives in `three/addons`, which is not vendored, so
 * the primitives this file needs are appended into plain arrays and handed to
 * one BufferGeometry at the end. Everything is non-indexed: the shapes here
 * are hard-edged (rail foot, tie plate, cabinet, buffer beam) and sharing a
 * vertex between two faces at 90° would smear the shading that sells them.
 */
class Mesher {
  constructor(useColor = false) {
    this.p = []; this.n = []; this.u = [];
    this.c = useColor ? [] : null;
    this.color = [1, 1, 1];
  }

  tint(r, g, b) { this.color = [r, g, b]; return this; }

  _vert(q, nx, ny, nz, uu, vv) {
    this.p.push(q[0], q[1], q[2]);
    this.n.push(nx, ny, nz);
    this.u.push(uu, vv);
    if (this.c) this.c.push(this.color[0], this.color[1], this.color[2]);
  }

  /** One triangle with an explicit normal — the cap on a tube, where the quad
   *  form would hand two coincident corners to the cross product and come back
   *  with a zero-length normal and a black face. */
  tri(a, b, c, uv = [[0, 0], [1, 0], [0, 1]]) {
    const ex = b[0] - a[0], ey = b[1] - a[1], ez = b[2] - a[2];
    const fx = c[0] - a[0], fy = c[1] - a[1], fz = c[2] - a[2];
    let nx = ey * fz - ez * fy, ny = ez * fx - ex * fz, nz = ex * fy - ey * fx;
    const l = Math.hypot(nx, ny, nz);
    if (!(l > 1e-9)) return this;
    nx /= l; ny /= l; nz /= l;
    this._vert(a, nx, ny, nz, uv[0][0], uv[0][1]);
    this._vert(b, nx, ny, nz, uv[1][0], uv[1][1]);
    this._vert(c, nx, ny, nz, uv[2][0], uv[2][1]);
    return this;
  }

  /** One quad, wound a→b→c→d, with a flat normal and an explicit UV rect. */
  quad(a, b, c, d, u0 = 0, v0 = 0, u1 = 1, v1 = 1) {
    const ex = b[0] - a[0], ey = b[1] - a[1], ez = b[2] - a[2];
    const fx = d[0] - a[0], fy = d[1] - a[1], fz = d[2] - a[2];
    let nx = ey * fz - ez * fy, ny = ez * fx - ex * fz, nz = ex * fy - ey * fx;
    const l = Math.hypot(nx, ny, nz) || 1;
    nx /= l; ny /= l; nz /= l;
    this._vert(a, nx, ny, nz, u0, v0);
    this._vert(b, nx, ny, nz, u1, v0);
    this._vert(c, nx, ny, nz, u1, v1);
    this._vert(a, nx, ny, nz, u0, v0);
    this._vert(c, nx, ny, nz, u1, v1);
    this._vert(d, nx, ny, nz, u0, v1);
    return this;
  }

  /** An axis-aligned box of size (w,h,d) centred at (cx,cy,cz), optionally put
   *  through a Matrix4. UVs are per-face and divided by `uvScale` metres so a
   *  box built at any size keeps the texture at world scale. `skip` drops named
   *  faces — the underside of a sleeper is 2 triangles nobody will ever see,
   *  and there are four thousand sleepers. */
  box(w, h, d, cx, cy, cz, m = null, uvScale = 1, uvOff = [0, 0], skip = 0) {
    const hx = w / 2, hy = h / 2, hz = d / 2;
    const V = [];
    for (let i = 0; i < 8; i++) {
      const x = cx + ((i & 1) ? hx : -hx);
      const y = cy + ((i & 2) ? hy : -hy);
      const z = cz + ((i & 4) ? hz : -hz);
      if (m) {
        const e = m.elements;
        V.push([e[0] * x + e[4] * y + e[8] * z + e[12],
                e[1] * x + e[5] * y + e[9] * z + e[13],
                e[2] * x + e[6] * y + e[10] * z + e[14]]);
      } else V.push([x, y, z]);
    }
    const U = uvScale, O = uvOff;
    const f = (bit, a, b, c, d2, su, sv) => {
      if (skip & bit) return;
      this.quad(V[a], V[b], V[c], V[d2], O[0], O[1], O[0] + su / U, O[1] + sv / U);
    };
    f(1, 4, 5, 7, 6, w, h);   // +z
    f(2, 1, 0, 2, 3, w, h);   // -z
    f(4, 5, 1, 3, 7, d, h);   // +x
    f(8, 0, 4, 6, 2, d, h);   // -x
    f(16, 2, 6, 7, 3, w, d);  // +y
    f(32, 0, 1, 5, 4, w, d);  // -y
    return this;
  }

  /** A cylinder along +y, `sides` around, with a capped top. Used for masts,
   *  posts and pipes — 6 or 8 sides is plenty at the distance any of them are
   *  ever read. */
  tube(r0, r1, h, cx, cy, cz, sides = 8, m = null, cap = true) {
    const put = (x, y, z) => {
      if (!m) return [x, y, z];
      const e = m.elements;
      return [e[0] * x + e[4] * y + e[8] * z + e[12],
              e[1] * x + e[5] * y + e[9] * z + e[13],
              e[2] * x + e[6] * y + e[10] * z + e[14]];
    };
    const top = put(cx, cy + h, cz);
    for (let i = 0; i < sides; i++) {
      const a0 = (i / sides) * Math.PI * 2, a1 = ((i + 1) / sides) * Math.PI * 2;
      const c0 = Math.cos(a0), s0 = Math.sin(a0);
      const c1 = Math.cos(a1), s1 = Math.sin(a1);
      const p00 = put(cx + c0 * r0, cy, cz + s0 * r0);
      const p10 = put(cx + c1 * r0, cy, cz + s1 * r0);
      const p11 = put(cx + c1 * r1, cy + h, cz + s1 * r1);
      const p01 = put(cx + c0 * r1, cy + h, cz + s0 * r1);
      this.quad(p00, p10, p11, p01, i / sides, 0, (i + 1) / sides, h);
      if (cap && r1 > 0.001) this.tri(top, p01, p11);
    }
    return this;
  }

  get empty() { return this.p.length === 0; }

  geometry() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.p, 3));
    g.setAttribute('normal', new THREE.Float32BufferAttribute(this.n, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(this.u, 2));
    if (this.c) g.setAttribute('color', new THREE.Float32BufferAttribute(this.c, 3));
    g.computeBoundingSphere();
    return g;
  }
}

/** One quad, wound so that its normal points the way you SAY it does.
 *
 *  Every structure below is a closed solid, and a closed solid whose faces are
 *  wound by hand always gets one of them backwards eventually. A backwards face
 *  is lit from inside, so it renders at ambient only — near black. That is not
 *  a subtle defect: the viaduct decks and the tunnel headwalls were both built
 *  with the section traversed the wrong way round, and the result was a black
 *  slab across a valley and a black card with an arch in it, which is exactly
 *  what "bridges do not read as bridges" and "the portals look awful" describe.
 *  Stating which way OUT is, once per face, cannot be got wrong by
 *  transcription the way a winding order can. */
function facing(m, a, b, c, d, out, u0 = 0, v0 = 0, u1 = 1, v1 = 1) {
  const ex = b[0] - a[0], ey = b[1] - a[1], ez = b[2] - a[2];
  const fx = d[0] - a[0], fy = d[1] - a[1], fz = d[2] - a[2];
  const nx = ey * fz - ez * fy, ny = ez * fx - ex * fz, nz = ex * fy - ey * fx;
  if (nx * out[0] + ny * out[1] + nz * out[2] < 0) m.quad(a, d, c, b, u0, v0, u1, v1);
  else m.quad(a, b, c, d, u0, v0, u1, v1);
  return m;
}

/** A closed section swept from ring A to ring B, every side face turned away
 *  from the axis joining the two ring centres. `flip` turns it inside out,
 *  which is what a tunnel barrel is: the same tube read from within. */
function ringSweep(m, A, B, ca, cb, v0 = 0, v1 = 1, flip = false) {
  const n = A.length;
  const cx = (ca[0] + cb[0]) / 2, cy = (ca[1] + cb[1]) / 2, cz = (ca[2] + cb[2]) / 2;
  const s = flip ? -1 : 1;
  for (let j = 0; j < n; j++) {
    const k = (j + 1) % n;
    const mx = (A[j][0] + A[k][0] + B[j][0] + B[k][0]) / 4 - cx;
    const my = (A[j][1] + A[k][1] + B[j][1] + B[k][1]) / 4 - cy;
    const mz = (A[j][2] + A[k][2] + B[j][2] + B[k][2]) / 4 - cz;
    facing(m, A[j], A[k], B[k], B[j], [mx * s, my * s, mz * s],
           j / n, v0, (j + 1) / n, v1);
  }
}

/** A box built in an arbitrary basis: `o` is one corner, `du`/`dv`/`dw` the
 *  three edge vectors from it. Every face is turned outward from the middle, so
 *  a stiffener glued on the outside of a girder web is a solid rather than six
 *  cards that happen to touch. */
function slab(m, o, du, dv, dw, uv = 1) {
  const V = [];
  for (let i = 0; i < 8; i++) {
    const a = (i & 1) ? 1 : 0, b = (i & 2) ? 1 : 0, c = (i & 4) ? 1 : 0;
    V.push([o[0] + du[0] * a + dv[0] * b + dw[0] * c,
            o[1] + du[1] * a + dv[1] * b + dw[1] * c,
            o[2] + du[2] * a + dv[2] * b + dw[2] * c]);
  }
  const mid = [o[0] + (du[0] + dv[0] + dw[0]) / 2,
               o[1] + (du[1] + dv[1] + dw[1]) / 2,
               o[2] + (du[2] + dv[2] + dw[2]) / 2];
  const F = [[0, 1, 3, 2], [4, 5, 7, 6], [0, 1, 5, 4],
             [2, 3, 7, 6], [0, 2, 6, 4], [1, 3, 7, 5]];
  const L = [Math.hypot(...du), Math.hypot(...dv), Math.hypot(...dw)];
  const SU = [L[0], L[0], L[0], L[0], L[1], L[1]];
  const SV = [L[1], L[1], L[2], L[2], L[2], L[2]];
  for (let i = 0; i < 6; i++) {
    const q = F[i];
    const cx = (V[q[0]][0] + V[q[1]][0] + V[q[2]][0] + V[q[3]][0]) / 4 - mid[0];
    const cy = (V[q[0]][1] + V[q[1]][1] + V[q[2]][1] + V[q[3]][1]) / 4 - mid[1];
    const cz = (V[q[0]][2] + V[q[1]][2] + V[q[2]][2] + V[q[3]][2]) / 4 - mid[2];
    facing(m, V[q[0]], V[q[1]], V[q[2]], V[q[3]], [cx, cy, cz],
           0, 0, SU[i] / uv, SV[i] / uv);
  }
  return m;
}

/** Which frames a swept profile actually needs. Track is resampled every 1.5m
 *  so a curve is smooth, but a rail on a 300m straight does not need 200 rings
 *  of 16 quads to say it is straight — and on this railway most of the line is
 *  straight. Curvature decides: anything bending gets every frame, anything
 *  flat gets one ring every `maxStraight` metres. It is a five-fold saving on
 *  the single largest swept mesh in the file. */
function geomIndices(track, maxStraight = 8) {
  const f = track.frames;
  if (!f) return [];
  const out = [0];
  let acc = 0;
  for (let i = 1; i < f.count; i++) {
    acc += f.step;
    if (Math.abs(f.k[i]) > 1.2e-3 || acc >= maxStraight) { out.push(i); acc = 0; }
  }
  if (out[out.length - 1] !== f.count - 1) out.push(f.count - 1);
  return out;
}

/** Sweep a closed 2D profile along an explicit list of frame indices.
 *  `profile` is [[lateral, vertical], …] wound clockwise in that plane, which
 *  is what puts the face normals outward. */
function sweep(frames, idx, profile, lateral, mesher, uvRow = 0) {
  const np = profile.length;
  if (idx.length < 2) return 0;
  const P = frames.pos, R = frames.right, U = frames.up;
  const ring = new Float32Array(np * 3);
  let prev = null, run = 0;
  /* the u coordinate walks the profile's own perimeter so the texture never
   * stretches on the web and squashes on the head */
  const per = [0];
  for (let i = 1; i <= np; i++) {
    const a = profile[i - 1], b = profile[i % np];
    per.push(per[i - 1] + Math.hypot(b[0] - a[0], b[1] - a[1]));
  }
  const perTotal = per[np] || 1;
  for (const i of idx) {
    const px = P[i * 3], py = P[i * 3 + 1], pz = P[i * 3 + 2];
    const rx = R[i * 3], ry = R[i * 3 + 1], rz = R[i * 3 + 2];
    const ux = U[i * 3], uy = U[i * 3 + 1], uz = U[i * 3 + 2];
    for (let j = 0; j < np; j++) {
      const lat = lateral + profile[j][0];
      const ver = profile[j][1];
      ring[j * 3] = px + rx * lat + ux * ver;
      ring[j * 3 + 1] = py + ry * lat + uy * ver;
      ring[j * 3 + 2] = pz + rz * lat + uz * ver;
    }
    if (prev) {
      const dz = Math.hypot(ring[0] - prev[0], ring[1] - prev[1], ring[2] - prev[2]);
      run += dz;
      for (let j = 0; j < np; j++) {
        const k = (j + 1) % np;
        mesher.quad([prev[j * 3], prev[j * 3 + 1], prev[j * 3 + 2]],
                    [prev[k * 3], prev[k * 3 + 1], prev[k * 3 + 2]],
                    [ring[k * 3], ring[k * 3 + 1], ring[k * 3 + 2]],
                    [ring[j * 3], ring[j * 3 + 1], ring[j * 3 + 2]],
                    per[j] / perTotal, (run - dz) + uvRow,
                    per[j + 1] / perTotal, run + uvRow);
      }
      prev.set(ring);
    } else {
      prev = Float32Array.from(ring);
    }
  }
  return run;
}

/* ---- the rail section ----------------------------------------------------
 *
 * Half a UIC60 in millimetres, mirrored, and deliberately coarse: sixteen
 * facets round the whole section rather than the twenty-six the real profile
 * has. The section is read from four metres away at most and the head, the web
 * and the foot are the only three things a viewer can name — the extra facets
 * only cost triangles on two thousand metres of railway.
 *
 * It starts at the crown of the head so the swept u coordinate puts 0 (and 1)
 * on the running surface, which is what lets one strip texture put polish on
 * the head and rust on the web without a second material or a second draw call.
 */
function railProfile() {
  const mm = 0.001;
  const half = [
    [0, 0], [36, -5], [37, -22], [24, -42],
    [10, -60], [9, -116], [20, -138],
    [40, -150], [75, -159], [75, -172], [0, -172],
  ];
  const out = [];
  for (const [x, y] of half) out.push([x * mm, y * mm]);
  for (let i = half.length - 2; i >= 1; i--) {
    out.push([-half[i][0] * mm, half[i][1] * mm]);
  }
  return out;
}
const RAIL_PROFILE = railProfile();

/* ---- textures ------------------------------------------------------------
 *
 * Every map here is generated once and shared by the whole railway. Ballast is
 * the only one that gets the full treatment — it is the surface a camera at
 * cam=street spends its time on, and it is where the "conservative geometry,
 * detail from textures" trade is actually cashed. */

function texKit(Tex) {
  const T = Tex.Tex || Tex;
  return {
    make: Tex.makeTexture || T.makeTexture,
    paint: Tex.paint || T.paint,
    packORM: Tex.packORM || T.packORM,
    normalFromHeight: Tex.normalFromHeight || T.normalFromHeight,
    fbm: Tex.fbm || T.fbm,
    cells: Tex.cells || T.cells,
  };
}

/* Worley with the cell's IDENTITY, which `Tex.cells` does not hand back and
 * which is the whole difference between "noise that looks stony" and stones.
 *
 * A stone is one colour all over. Key the colour to a distance field instead —
 * which is all `f1`/`f2` are — and every chip comes out as a ring or a cone
 * shaded from its own centre outwards, and a field of them reads as high-
 * frequency dirt rather than as aggregate. That is precisely what a blind
 * critic saw: "uniform high-frequency sandpaper noise — no individual stones".
 * Returning which cell won lets the paint below give each stone a flat tone of
 * its own, and lets a *sparse* subset of cells be promoted to big chips. */
function stoneHash(x, y, seed) {
  let h = x * 374761393 + y * 668265263 + seed * 2246822519;
  h = (h ^ (h >>> 13)) * 1274126177;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function stoneField(x, y, period, seed) {
  const xi = Math.floor(x), yi = Math.floor(y);
  let best = 1e9, second = 1e9, id = 0;
  for (let oy = -1; oy <= 1; oy++) {
    for (let ox = -1; ox <= 1; ox++) {
      const cx = xi + ox, cy = yi + oy;
      const wx = ((cx % period) + period) % period;
      const wy = ((cy % period) + period) % period;
      const px = cx + stoneHash(wx, wy, seed);
      const py = cy + stoneHash(wx, wy, seed + 977);
      const d = (px - x) * (px - x) + (py - y) * (py - y);
      if (d < best) {
        second = best; best = d;
        id = stoneHash(wx, wy, seed + 4211);
      } else if (d < second) second = d;
    }
  }
  return {f1: Math.sqrt(best), f2: Math.sqrt(second), id};
}

/* Ballast: THREE cell layers, and the point of three is size variation.
 *
 * The reference's trackbed is not one grade of chip. It is 50mm ballast with a
 * scatter of much larger stones sitting proud of it and fines packed into the
 * joints, and the eye reads that spread — not the average — as crushed rock.
 * One layer, however well tuned, is a screen of identical dots, which is the
 * failure the critics named twice.
 *
 * So: a sparse coarse layer (only the third of cells whose id says so, ~130mm),
 * the working layer (~52mm), and fines that fill only where nothing above them
 * reaches. Whichever layer OWNS a texel gives it its tone, flat across that
 * stone; the joints between them go dark, which is where the reading of
 * "individual stones" actually comes from — a ballast shoulder in sunlight is
 * mostly pale tops separated by black shadow.
 *
 * The tile is 1.15m at 512, so 2.2mm a texel: a 50mm chip is 22 texels across
 * and survives four mip levels, which is what the far end of the yard needs. */
function ballastMaterial(Tex) {
  return Tex.material('rail.ballast', () => {
    const K = texKit(Tex);
    const S = 512;
    const P = 22;                  // working ballast, 1.15/22 ≈ 52mm
    const PB = 9;                  // scattered big chips, ≈ 128mm
    const PF = 54;                 // fines, ≈ 21mm
    const height = new Float32Array(S * S);
    /* smoothstep on the wall distance: a stone with a rounded-off top and a
     * crisp joint to the next, rather than a cone. */
    const dome = (c, k) => {
      const t = Math.min(1, (c.f2 - c.f1) * k);
      return t * t * (3 - 2 * t);
    };
    const map = K.paint(S, (x, y, u, v) => {
      /* Ballast is *crushed* stone: angular, every chip a different size, and
       * with no lattice to it at all. A plain Worley field has a lattice — the
       * cells tile into visible hexagons — so the domain is warped first. That
       * one line is the difference between gravel and cobblestones. */
      const wx = K.fbm(u * 9 + 1.3, v * 9 - 2.1, {octaves: 2, period: 9, seed: 91});
      const wz = K.fbm(u * 9 - 4.7, v * 9 + 0.6, {octaves: 2, period: 9, seed: 92});
      const uu = u + (wx - 0.5) * 0.055, vv = v + (wz - 0.5) * 0.055;

      const big = stoneField(uu * PB, vv * PB, PB, 811);
      const med = stoneField(uu * P, vv * P, P, 7);
      const fin = stoneField(uu * PF, vv * PF, PF, 23);
      /* Only some coarse cells are actually a big stone; the rest are ordinary
       * ballast, which is what makes the large ones read as a scatter rather
       * than as a second regular screen over the first. */
      const hasBig = big.id > 0.63 ? 1 : 0;
      const hB = hasBig * dome(big, 3.4) * (0.72 + big.id * 0.5);
      const hM = dome(med, 3.0);
      const hF = dome(fin, 3.4);
      /* Ownership decides the tone. A texel belongs to whichever layer is
       * physically on top of it there; the fines only own the ground the two
       * coarser layers have left. */
      const cB = hB * 1.00, cM = hM * 0.74, cF = hF * 0.34;
      let id, own, cell, tight;
      if (cB >= cM && cB >= cF && hB > 0.02) { id = big.id; own = cB; cell = big; tight = 4.0; }
      else if (cM >= cF) { id = med.id; own = cM; cell = med; tight = 5.0; }
      else { id = fin.id; own = cF; cell = fin; tight = 7.5; }
      const grit = K.fbm(u * 30, v * 30, {octaves: 2, period: 30, seed: 3});
      const h = Math.max(cB, cM, cF) * 0.92 + grit * 0.055;
      height[y * S + x] = h;

      /* Granite: grey, and *grey* is the operative word — but PALE grey. The
       * previous mix sat around 0.35 with the contrast carried by noise, which
       * is a wet-tarmac value, and against it the fines read as sand. Fresh
       * crushed granite in sun is a good deal lighter than that and the
       * variation is stone-to-stone rather than texel-to-texel.
       *
       * The scatter runs off the owning stone's id, so it is FLAT across each
       * chip. That is the whole fix. */
      const warm = K.fbm(u * 5.5, v * 5.5, {octaves: 3, period: 6, seed: 41});
      /* Fresh stone and old stone in patches: a spread of tamped, dusty and
       * newly-packed areas, which is what stops a tiling gravel map reading as
       * one grey carpet the length of the railway. */
      const clump = K.fbm(u * 3.0, v * 3.0, {octaves: 3, period: 3, seed: 205});
      /* The joint. `f2−f1` going to zero is the wall between two cells, and the
       * shadow that collects there is most of what says "loose stone" at four
       * metres — the normal map cannot do it, because at a grazing sun the
       * relief it carries is exactly what the terminator eats. */
      const wall = Math.min(1, (cell.f2 - cell.f1) * tight);
      const joint = (1 - wall) * (1 - wall);
      const tone = (0.300 + id * 0.300 + own * 0.130) * (0.88 + clump * 0.26);
      const shade = tone * (1 - joint * 0.52);
      let r = shade * 1.010, g = shade * 1.020, b = shade * 1.045;
      r += (warm - 0.5) * 0.040; b -= (warm - 0.5) * 0.018;
      if (id < 0.20) { r *= 0.64; g *= 0.66; b *= 0.70; }        // dark basalt
      else if (id > 0.88) { r *= 1.16; g *= 1.01; b *= 0.86; }   // iron-stained
      /* Fines and brake dust wash down into the low ground between stones. */
      const dust = Math.max(0, 0.30 - h) * 0.30 * (0.45 + grit);
      return [r + dust, g + dust * 0.95, b + dust * 0.84];
    });
    const nrm = K.normalFromHeight(height, S, 2.4);
    const orm = K.packORM(256, (x, y, u, v) => {
      const h = K.fbm(u * 12, v * 12, {octaves: 3, period: 12, seed: 7});
      return {ao: 0.72 + h * 0.28, roughness: 0.92 - h * 0.08, metalness: 0};
    });
    return new THREE.MeshStandardMaterial({
      /* Anisotropy 16, not the default 8. A trackbed is the one surface in this
       * world that is *always* seen at a grazing angle running away from the
       * camera, which is the exact case an isotropic mip chain cannot serve: it
       * picks the mip for the short axis and the long axis aliases. A critic
       * put the failure at a distance — "from ~x=840 the track becomes a
       * shimmering white speckle band with rails no longer separable" — and
       * that is what the shimmer is. */
      map: K.make(map, {srgb: true, aniso: 16}),
      normalMap: K.make(nrm, {aniso: 16}),
      roughnessMap: K.make(orm, {aniso: 16}),
      /* Eased back from 0.72. A strong normal map on a surface at an extreme
       * grazing angle tips the shading normal past the terminator and the
       * surface goes to pure black diffuse — which is what the batter of a
       * distant embankment is, all day, and it is half of why the far side of
       * the formation reads as a hole rather than as a bank. The near-field
       * stone loses very little: the value contrast painted into the albedo is
       * what says "gravel" at four metres, not the relief. */
      normalScale: new THREE.Vector2(0.58, 0.58),
      roughness: 1, metalness: 0, vertexColors: true, dithering: true,
    });
  });
}

/* The two-tone rail. `u` runs around the section from the railhead crown, `v`
 * runs along it, so a narrow band at both ends of u is the running surface and
 * everything between is web and foot. It is the single cheapest detail in the
 * file and the reference calls it the one that most says "real track". */
function railMaterial(Tex) {
  return Tex.material('rail.steel', () => {
    const K = texKit(Tex);
    const S = 128;
    /* Where the running surface actually is. The swept `u` walks the section's
     * own perimeter from the crown of the head, and the head is 53mm of a 700mm
     * perimeter — so the polished band is the first 7.5% of u and the last
     * 7.5%, not the first fifteen. The old 6.4 spread the polish a third of the
     * way down the web, which is the same as having none: what reads as a rail
     * from four metres is a bright LINE with a dark section under it, and a
     * gradient has no line in it. */
    const headAt = u => Math.max(0, 1 - Math.min(u, 1 - u) * 15.5);
    const map = K.paint(S, (x, y, u, v) => {
      const head = headAt(u);
      const wear = K.fbm(v * 14, u * 3, {octaves: 3, period: 14, seed: 12});
      const rust = K.fbm(u * 7, v * 11, {octaves: 4, period: 11, seed: 30});
      /* THE WEB IS NOT ORANGE.
       *
       * It used to reach 0.43 red against 0.12 blue — a ratio of 3.6, which is
       * traffic-cone rust, not rail. Mixed with a blown white head it gave the
       * one reading both blind critics arrived at independently: "the rails
       * read pink/violet, not steel, at every distance". A rail out of a works
       * is black mill scale; a rail in traffic is dark brown oxide with the
       * scale still on it in patches, and its VALUE is close to the shadow side
       * of the sleeper next to it. Everything that makes it read as steel is
       * the thin polished line on top, which needs something dark under it to
       * be a line at all.
       *
       * So: half the value it had, and a red-to-blue ratio of 1.7 rather than
       * 3.6. The foot, sitting in wet ballast, keeps a little more of the red. */
      const low = Math.abs(u - 0.5) < 0.16 ? 1 : 0;
      let r = 0.082 + rust * 0.104 + low * 0.030;
      let g = 0.058 + rust * 0.062;
      let b = 0.048 + rust * 0.040;
      /* The polished band. 0.62 rather than 1.02: an albedo over 1 is not a
       * surface, it is a light, and pushed through a 0.78-metal specular it
       * clipped to paper white at every distance and took the section's shape
       * with it. Real burnished steel is a mid grey that happens to be very
       * shiny — the brightness in a photograph of a railhead is the SUN in it,
       * which the specular lobe is there to provide. */
      const bright = 0.44 + wear * 0.20;
      const k = Math.pow(head, 1.5);
      /* A hairline of dark rolled edge where the head turns down into the
       * fishing: without it the polish bleeds into the web and the rail is a
       * gradient again, and a gradient has no line in it. */
      const edge = Math.max(0, 1 - Math.abs(head - 0.13) * 14) * 0.55;
      /* Faintly WARM, not faintly blue. Steel's own reflectance falls a little
       * from red to blue; making the head's albedo blue instead put a cold cast
       * on top of an already-blue sky specular, and cold specular over a red web
       * is the arithmetic behind "the rails read pink/violet". */
      r = (r * (1 - k) + bright * k) * (1 - edge);
      g = (g * (1 - k) + bright * 0.984 * k) * (1 - edge);
      b = (b * (1 - k) + bright * 0.952 * k) * (1 - edge);
      return [r, g, b];
    });
    const orm = K.packORM(S, (x, y, u, v) => {
      const head = headAt(u);
      const wear = K.fbm(v * 14, u * 3, {octaves: 3, period: 14, seed: 12});
      const k = Math.pow(head, 1.5);
      /* Not fully metallic on the head. A metalness of 1 has no diffuse term at
       * all, so a polished railhead under this engine's indirect light comes
       * back as whatever the sky reflection happens to be — which on a clear
       * day is BLUE, and blue specular over a red web is exactly how a rail
       * ends up reading violet. 0.70, and a rougher polish than a mirror:
       * a railhead is burnished by wheels, not lapped. */
      return {ao: 1,
              roughness: (0.92 - wear * 0.08) * (1 - k) + (0.185 + wear * 0.06) * k,
              metalness: 0.10 * (1 - k) + 0.70 * k};
    });
    return new THREE.MeshStandardMaterial({
      map: K.make(map, {srgb: true, aniso: 16}),
      roughnessMap: K.make(orm, {aniso: 16}),
      metalnessMap: K.make(orm, {aniso: 16}),
      roughness: 1, metalness: 1, envMapIntensity: 0.85,
    });
  });
}

/* Timber sleepers, in FOUR bands, because a sleeper is a solid and the three
 * faces of it a camera can see are three different surfaces.
 *
 * The map used to be two bands: timber and tie plate, with the timber shared by
 * the top, the sides and the ends. That is what a blind critic was describing
 * as "flat, zero-thickness quads of near-identical tone" — a sleeper whose end
 * is painted with the same stretched long-grain as its top has no end, and a
 * side with no dirt line where it enters the ballast has no bottom. The
 * reference's ties read as baulks of timber because each shows a sawn top, a
 * darker side and a pale checked END, and the eye assembles a box out of the
 * three.
 *
 *   v 0.00–0.46  top     long grain, weathered, adze marks, spike holes
 *   v 0.46–0.72  side    sawn face, dirtying downward into the crib
 *   v 0.72–0.86  end     END GRAIN: rings and radial checks, paler and warmer
 *   v 0.86–1.00  plate   the rusted tie plate
 *
 * The variation between one sleeper and the next is not in here at all — it is
 * an instance colour, which is free. */
const SL_TOP0 = 0.00, SL_TOP1 = 0.46;
const SL_SIDE0 = 0.46, SL_SIDE1 = 0.72;
const SL_END0 = 0.72, SL_END1 = 0.86;
const SL_PLATE0 = 0.86;

function sleeperMaterial(Tex) {
  return Tex.material('rail.sleeper', () => {
    const K = texKit(Tex);
    const S = 512;
    const height = new Float32Array(S * S);
    /* Creosoted oak, weathering to driftwood. Two rounds ago this was mixed 1.7
     * red-to-blue and the whole permanent way came out orange; the correction
     * took it to 0.15 base, which is charcoal, and a rank of charcoal dashes is
     * what "near-identical tone" looks like. The reference's timber sits about
     * here: a mid brown, low saturation, with a wide spread between one tie and
     * the next carried by the instance colour. */
    const timber = (t, grey, split) => {
      let r = 0.292 + t * 0.190, g = 0.252 + t * 0.164, b = 0.222 + t * 0.144;
      const w = Math.max(0, grey - 0.46) * 1.8;
      r += w * 0.120; g += w * 0.128; b += w * 0.132;
      r -= split * 0.085; g -= split * 0.070; b -= split * 0.052;
      return [r, g, b];
    };
    const map = K.paint(S, (x, y, u, v) => {
      if (v < SL_TOP1) {
        /* The top. Grain runs along u — which on the top face is now genuinely
         * the sleeper's LENGTH; it used to run across it, because `Mesher.box`
         * hands its +y face a UV rect transposed against the quad it winds, and
         * a two-metre baulk with the grain running across its 300mm width reads
         * as a scrubbing brush. `sleeperPrototype` builds the top by hand now. */
        const w = (v - SL_TOP0) / (SL_TOP1 - SL_TOP0);
        const grain = K.fbm(u * 40, w * 5, {octaves: 4, period: 40, seed: 5});
        const tight = K.fbm(u * 130, w * 9, {octaves: 2, period: 130, seed: 17});
        const split = Math.max(0, K.fbm(u * 60, w * 3, {octaves: 2, period: 60, seed: 71}) - 0.70) * 3.6;
        const grey = K.fbm(u * 6, w * 7, {octaves: 4, period: 6, seed: 66});
        height[y * S + x] = grain * 0.30 + tight * 0.16 - split * 0.5;
        return timber(grain * 0.55 + tight * 0.45, grey, split);
      }
      if (v < SL_SIDE1) {
        /* The side, with v running UP the face — `Mesher.box` puts v0 on the
         * −y corners — so the bottom of this band is the buried edge. Ballast
         * dust and creosote bleed collect there, and that dark line at the
         * stone is most of what says the timber has a thickness at all. */
        const w = (v - SL_SIDE0) / (SL_SIDE1 - SL_SIDE0);
        const grain = K.fbm(u * 34, w * 4, {octaves: 4, period: 34, seed: 25});
        const tight = K.fbm(u * 110, w * 7, {octaves: 2, period: 110, seed: 37});
        const split = Math.max(0, K.fbm(u * 50, w * 3, {octaves: 2, period: 50, seed: 73}) - 0.70) * 3.4;
        const grey = K.fbm(u * 5, w * 6, {octaves: 4, period: 5, seed: 68});
        height[y * S + x] = grain * 0.26 + tight * 0.14 - split * 0.45;
        const c = timber(grain * 0.55 + tight * 0.45, grey * 0.7, split);
        /* dirty into the crib, and a shade darker overall: a vertical face
         * never sees the sky the way the top does */
        const soil = Math.pow(1 - w, 2.2) * 0.55 + 0.12;
        return [c[0] * (1 - soil * 0.62), c[1] * (1 - soil * 0.66),
                c[2] * (1 - soil * 0.70)];
      }
      if (v < SL_END1) {
        /* END GRAIN, and it is the single most recognisable 200mm on a piece of
         * railway: concentric rings, radial checks opened by the weather, and a
         * colour a stop paler and warmer than the sawn faces because the cut is
         * across the fibre and the creosote never soaked in as far. */
        const w = (v - SL_END0) / (SL_END1 - SL_END0);
        const cx = u * 2 % 1, cy = w;              // two ends across the band
        const dx = (cx - 0.5) * 1.9, dy = (cy - 0.42) * 1.0;
        const rad = Math.hypot(dx, dy);
        const ang = Math.atan2(dy, dx);
        const wob = K.fbm(cx * 7, cy * 7, {octaves: 3, period: 7, seed: 88});
        const rings = 0.5 + 0.5 * Math.cos((rad * 26 + wob * 3.2) * Math.PI);
        /* Radial checks: a handful of splits running out from the heart, which
         * is what every weathered end has and what no noise field produces. */
        const spokes = Math.max(0, Math.cos(ang * 5 + wob * 6) - 0.86) * 7 *
                       Math.min(1, rad * 3.2);
        const grey = K.fbm(cx * 4, cy * 4, {octaves: 3, period: 4, seed: 91});
        height[y * S + x] = rings * 0.18 - spokes * 0.9;
        const c = timber(0.36 + rings * 0.42, grey, spokes * 0.8);
        return [c[0] * 1.16, c[1] * 1.08, c[2] * 1.00];
      }
      /* The tie plate. It is a seventh of this map now and it used to be a
       * quarter: two plates 420 x 320 are a third of the visible area of a
       * sleeper from above, so at anything past a few metres the plate's colour
       * IS the sleeper's colour — and mixed 2.5 red to blue it turned every tie
       * on the site orange. That is where the "printed hatch marks" reading came
       * from; it was never the geometry. Rusted steel under a rail is dark, and
       * most of what a plate shows is the shadow the rail foot casts on it. */
      const w = (v - SL_PLATE0) / (1 - SL_PLATE0);
      const rust = K.fbm(u * 20, w * 40, {octaves: 4, period: 20, seed: 21});
      const flake = K.cells(u * 30, w * 60, 30, 9).f1;
      height[y * S + x] = 0.25 + rust * 0.4 + flake * 0.2;
      return [0.176 + rust * 0.140, 0.132 + rust * 0.092, 0.110 + rust * 0.064];
    });
    const nrm = K.normalFromHeight(height, S, 2.4);
    const orm = K.packORM(256, (x, y, u, v) => ({
      ao: 1, roughness: v < SL_PLATE0 ? 0.92 : 0.70,
      metalness: v < SL_PLATE0 ? 0 : 0.65,
    }));
    return new THREE.MeshStandardMaterial({
      map: K.make(map, {srgb: true, aniso: 16}),
      normalMap: K.make(nrm, {aniso: 16}),
      roughnessMap: K.make(orm, {aniso: 16}),
      metalnessMap: K.make(orm, {aniso: 16}),
      normalScale: new THREE.Vector2(1.15, 1.15),
      roughness: 1, metalness: 1, vertexColors: true,
    });
  });
}

/** The works apron the loading road is EMBEDDED in.
 *
 *  A blind critic, on the old version of this: "the paved track is a printed
 *  decal — a pale ribbon with regular dark dashes, no rails, no flangeway
 *  groove, no thickness", and separately, of a junction in the same area, "the
 *  divergence is drawn only as converging dash rows on asphalt". Both were
 *  looking at ordinary ballasted track at fifty metres, where the stone had
 *  gone pale and the rails had mipped away — but the fair reading of that is
 *  that a rail-served loading rack inside a works has no business being
 *  ballasted at all. Road tankers, drum trucks and men with hoses cross it all
 *  day. The prototype pours a slab and beds the rail in it.
 *
 *  So this is a real concrete apron: 3m panels with poured joints, aggregate
 *  showing through where it has worn, oil down the four-foot, and the tyre
 *  polish of vehicles crossing. */
function paveMaterial(Tex) {
  return Tex.material('rail.pave', () => {
    const K = texKit(Tex);
    const S = 512;
    const height = new Float32Array(S * S);
    const map = K.paint(S, (x, y, u, v) => {
      /* The panel joint. It runs at the tile edge in both directions, so the
       * 3m repeat below lays a 3m grid of joints over the apron — which is what
       * a slab poured in bays looks like from above and is the single cue that
       * separates concrete from a grey stain. */
      const eu = Math.min(u, 1 - u), ev = Math.min(v, 1 - v);
      const joint = Math.max(Math.max(0, 1 - eu * 90), Math.max(0, 1 - ev * 90));
      const grain = K.fbm(u * 16, v * 16, {octaves: 4, period: 16, seed: 9});
      /* Exposed aggregate: the stone in the mix, showing where the laitance has
       * worn off. Sparse, or it reads as gravel rather than as concrete. */
      const agg = K.cells(u * 46, v * 46, 46, 61);
      const stone = Math.max(0, 1 - (agg.f2 - agg.f1) * 6.5) *
                    Math.max(0, K.fbm(u * 7, v * 7, {octaves: 2, period: 7, seed: 33}) - 0.42) * 2.2;
      /* Cracks: the low tail of a ridged band, which is how a crack gets a
       * length rather than being a dot. */
      /* Cracks, and only a few. At *13 the whole slab crazed and read as crazy
       * paving, which is a different (and much worse) surface than concrete —
       * a crack is a rare event on a bay that has been down a decade, and what
       * carries the age is the joint and the wear, not a net. */
      const crk = Math.max(0, 0.5 - Math.abs(K.fbm(u * 5, v * 5, {octaves: 4, period: 5, seed: 51}) - 0.5) * 19);
      const oil = K.fbm(u * 3.5, v * 3.5, {octaves: 4, period: 4, seed: 77});
      /* The aggregate is a COLOUR, not a relief. At 0.5 into the height field
       * the Sobel turned every chip into a bump and the slab came back as
       * reptile skin — a poured bay is flat, and what shows through the
       * laitance shows as tone. */
      height[y * S + x] = grain * 0.22 + stone * 0.20 - joint * 0.9 - crk * 0.5;
      let c = 0.478 + grain * 0.105 + stone * 0.070;
      c *= (1 - joint * 0.42) * (1 - crk * 0.26);
      /* Diesel and gear oil, which is the whole colour story of a fuel rack:
       * near-black in the middle, iridescent brown at the edges. Kept to a
       * small fraction of the slab — a rack that is uniformly oiled is a wet
       * road, and the point of the concrete is that it reads as concrete. */
      const spill = Math.max(0, oil - 0.68) * 2.2;
      const r = c * (1 - spill * 0.52), g = c * (1 - spill * 0.58),
            b = c * (1 - spill * 0.56);
      return [r * 1.005, g * 0.995, b * 0.965];
    });
    const nrm = K.normalFromHeight(height, S, 1.9);
    const orm = K.packORM(256, (x, y, u, v) => {
      const polish = K.fbm(u * 3.5, v * 3.5, {octaves: 3, period: 4, seed: 77});
      return {ao: 1, roughness: 0.90 - Math.max(0, polish - 0.55) * 0.9,
              metalness: 0};
    });
    return new THREE.MeshStandardMaterial({
      map: K.make(map, {srgb: true, aniso: 16}),
      normalMap: K.make(nrm, {aniso: 16}),
      roughnessMap: K.make(orm, {aniso: 16}),
      normalScale: new THREE.Vector2(0.60, 0.60),
      roughness: 1, metalness: 0, vertexColors: true, dithering: true,
    });
  });
}

/** Painted steel — signal masts, cabinets, buffer stops, troughing lids. One
 *  material for all of it, tinted per-vertex, because a second draw call is a
 *  worse deal than a shared roughness. */
function kitMaterial(Tex) {
  return Tex.material('rail.kit', () => {
    const K = texKit(Tex);
    const S = 256;
    const height = new Float32Array(S * S);
    const map = K.paint(S, (x, y, u, v) => {
      const grime = K.fbm(u * 9, v * 9, {octaves: 4, period: 9, seed: 15});
      const streak = K.fbm(u * 3, v * 20, {octaves: 3, period: 20, seed: 44});
      const rust = Math.max(0, K.fbm(u * 14, v * 14, {octaves: 3, period: 14, seed: 71}) - 0.72) * 1.7;
      height[y * S + x] = grime * 0.5 + rust * 0.5;
      const c = 0.66 + grime * 0.26 - streak * 0.17;
      return [c * (1 + rust * 0.35), c * (1 - rust * 0.20), c * (1 - rust * 0.45)];
    });
    const nrm = K.normalFromHeight(height, S, 1.1);
    const orm = K.packORM(128, (x, y, u, v) => ({
      ao: 1, roughness: 0.52 + K.fbm(u * 8, v * 8, {octaves: 2, period: 8, seed: 5}) * 0.34,
      metalness: 0.35,
    }));
    return new THREE.MeshStandardMaterial({
      map: K.make(map, {srgb: true}), normalMap: K.make(nrm),
      roughnessMap: K.make(orm), metalnessMap: K.make(orm),
      roughness: 1, metalness: 1, vertexColors: true,
    });
  });
}

/** Concrete: trough runs, signal bases, crossing decks, buffer blocks. */
function concreteMaterial(Tex) {
  return Tex.material('rail.concrete', () => {
    const K = texKit(Tex);
    const S = 256;
    const height = new Float32Array(S * S);
    const map = K.paint(S, (x, y, u, v) => {
      const g = K.fbm(u * 11, v * 11, {octaves: 4, period: 11, seed: 9});
      const pit = K.cells(u * 26, v * 26, 26, 33).f1;
      height[y * S + x] = g * 0.4 + (1 - pit) * 0.22;
      const c = 0.36 + g * 0.15 - (1 - pit) * 0.12;
      return [c, c * 0.995, c * 0.965];
    });
    const nrm = K.normalFromHeight(height, S, 1.4);
    return new THREE.MeshStandardMaterial({
      map: K.make(map, {srgb: true}), normalMap: K.make(nrm),
      roughness: 0.94, metalness: 0, vertexColors: true,
    });
  });
}

/** The signal lamp. Unlit and driven past 1.0 on purpose: the engine's bloom
 *  thresholds at 1.05 luminance, so an aspect only reads as a *lamp* rather
 *  than a coloured disc if it is allowed over the line. */
function lensMaterial() {
  return new THREE.MeshBasicMaterial({vertexColors: true, toneMapped: false,
                                      fog: false});
}

/* ---- the permanent way ---------------------------------------------------
 *
 * Ballast is a ribbon of seven vertices per ring: crown, both shoulder
 * breaks, both toes, and an outer edge pinned to the real terrain so the
 * formation always meets the ground however the ground moves. The stone is
 * entirely in the normal map; the only thing the geometry has to get right is
 * the section, and the section is the thing everybody recognises.
 */
const SHOULDER_TOP = SLEEPER_TOP - 0.010;   // stone heaped to the tie top
const TIE_END = SLEEPER_LEN / 2 - 0.02;
/* The crest is a round-off, not a corner.
 *
 * Two rings on the outside of each shoulder rather than one, and it is the
 * cheapest thing in this file that a critic named: "a hard polygonal shoulder
 * facet". A ballast shoulder is loose stone at its angle of repose — it cannot
 * hold a knife edge, and the light on the crest is a soft roll from the flat
 * top into the batter. One vertex per side, ~0.30m out and only 0.14 down
 * against the 0.19 a straight batter would be, makes the crest convex and gives
 * the sun two facets to read instead of one. It costs two quads a ring on a
 * ribbon that is already sampled every two metres. */
const CREST_X = SHOULDER_X + 0.30;
const CREST_Y = SHOULDER_TOP - 0.14;
const BALLAST_SECTION = [
  [-VERGE_X, null], [-TOE_X, BALLAST_TOE], [-CREST_X, CREST_Y],
  [-SHOULDER_X, SHOULDER_TOP],
  [-TIE_END, BALLAST_CRIB], [0, BALLAST_CRIB], [TIE_END, BALLAST_CRIB],
  [SHOULDER_X, SHOULDER_TOP],
  [CREST_X, CREST_Y], [TOE_X, BALLAST_TOE], [VERGE_X, null],
];

function ballastRibbon(track, mesher, ground, step2 = 2.0, from = null, to = null) {
  const f = track.frames;
  if (!f) return;
  const n = BALLAST_SECTION.length;
  const ring = new Float32Array(n * 3);
  let prev = null, prevCol = null, run = 0;
  const col = new Float32Array(n * 3);
  const stride = Math.max(1, Math.round(step2 / f.step));
  /* The across-section texture coordinate is accumulated from the lateral
   * offsets this ring actually used, not from the nominal section. The batter
   * walks out with the depth of fill (below), so on a four-metre bank the outer
   * panel is six metres of ground wearing 1.7m of texture — which is how a
   * stone map ends up reading as brown corduroy down the side of an
   * embankment. */
  const cross = new Float32Array(n);
  /* `from`/`to` let a road be ballasted in two pieces with a paved apron
   * between them; without them the stone would be laid under the slab and show
   * through it wherever the two disagreed by a millimetre. */
  const lo = Math.max(track.renderFrom, from ?? -Infinity);
  const hi = Math.min(Math.min(track.renderTo, track.length), to ?? Infinity);
  if (!(hi > lo + 1)) return;
  const i0 = Math.max(0, Math.floor(lo / f.step));
  const i1 = Math.min(f.count - 1, Math.ceil(hi / f.step));
  for (let i = i0; i <= i1; i += stride) {
    const last = i + stride > i1;
    const idx = last ? i1 : i;
    const px = f.pos[idx * 3], py = f.pos[idx * 3 + 1], pz = f.pos[idx * 3 + 2];
    const rx = f.right[idx * 3], ry = f.right[idx * 3 + 1], rz = f.right[idx * 3 + 2];
    const ux = f.up[idx * 3], uy = f.up[idx * 3 + 1], uz = f.up[idx * 3 + 2];
    let lastLat = 0, lastVer = 0;
    for (let j = 0; j < n; j++) {
      let lat = BALLAST_SECTION[j][0];
      let ver = BALLAST_SECTION[j][1];
      let edge = 0;
      /* On a deck the stone is contained by the parapet and there is no ground
       * under it to reach for. Draping here was the first version's mistake and
       * it is spectacular: the ribbon's pinned outer edge hunts for the valley
       * floor and hangs a twenty-metre curtain of ballast texture off each side
       * of the bridge. Inside a deck span the section is drawn exactly as
       * designed — which is what a ballasted deck looks like. */
      const onDeck = track.decks && track.inRanges(track.decks, idx * f.step);
      if (ver === null) {
        /* the pinned outer edge: wherever the terrain actually is, a shade
         * below it, so ballast and ground never show daylight between them */
        if (track.verge && ground && !onDeck) {
          /* A bank is a SLOPE, not a wall.
           *
           * The section's width was fixed — 3.3m either side of the centre —
           * while the fill it has to reach down is allowed to be four metres
           * deep, which is a batter of better than 1 in 1, i.e. a cliff. On the
           * near shots the embankment came out as a sand-coloured wall standing
           * in a field, and it read as a retaining structure nobody had built.
           * Real fill sits at about 1 in 1.5, so the toe walks OUT as the
           * ground falls away: two passes, because how far out it goes decides
           * where the ground is sampled and therefore how far down it has to
           * go. Two is enough — the second pass moves it by centimetres. */
          const sgn = lat < 0 ? -1 : 1;
          let l = lat;
          for (let pass = 0; pass < 2; pass++) {
            const gx = px + rx * l, gz = pz + rz * l;
            ver = Math.min(BALLAST_TOE - 0.02, (ground(gx, gz) || 0) - py - 0.06);
            /* ...but only as far as a batter goes. Pinning it to the ground
             * with no floor means one step in the terrain beside the line hangs
             * the whole ribbon off the edge of it. Four metres is a tall bank;
             * below that the ground is terrain.js's problem and this stops
             * being a curtain. */
            /* ...but only as far as a batter goes, and the floor is now the
             * threshold at which a bank stops being a bank. Above VIADUCT_FILL
             * the declaration has already called for a viaduct and `_span` has
             * built one, so a drape that kept going past it would be a curtain
             * hanging beside a bridge. */
            ver = Math.max(ver, BALLAST_TOE - VIADUCT_FILL);
            const want = VERGE_X + Math.max(0, BALLAST_TOE - ver) * 1.5;
            if (Math.abs(want - Math.abs(l)) < 0.06) break;
            l = sgn * want;
          }
          lat = l;
        } else ver = BALLAST_TOE - 0.10;
        edge = 1;
      } else if (ground && track.verge && !onDeck &&
                 Math.abs(lat) > SLEEPER_LEN / 2) {
        /* The formation profile is a straight section, and the ground it is
         * laid on is not. Grading the centreline is not enough — four metres
         * out, at the toe, terrain.js's own pads and roads climb, and where
         * they climb past the batter the hillside comes up through the stone
         * in patches. So the batter is a drape: it may rise to meet the ground
         * but never sink below it. The crown never moves, so no sleeper ever
         * follows the ground it is supposed to be levelling. */
        const gx = px + rx * lat, gz = pz + rz * lat;
        const gy = (ground(gx, gz) || 0) - py + 0.055;
        if (gy > ver) ver = Math.min(BALLAST_CRIB - 0.02, gy);
        /* And the shoulder wanders here too. It used not to: the drape and the
         * wander were the two arms of one `else`, so every point outside the
         * sleeper end — which is the whole shoulder and the whole batter — took
         * the drape and came out geometrically perfect. That is exactly the
         * band a raking sun reads, and a perfect one is what "a hard polygonal
         * shoulder facet" means. */
        const h2 = Math.sin(idx * 12.9898 + j * 78.233) * 43758.5453;
        ver += (h2 - Math.floor(h2) - 0.5) * 0.052;
      } else {
        /* A ribbon whose crown is a perfect plane goes flat the moment the sun
         * rakes across it. Two centimetres of hash-driven wander is enough for
         * the shoulder to break up without unseating a single sleeper — the
         * sleepers ride the frames, not this. */
        const hsh = Math.sin(idx * 12.9898 + j * 78.233) * 43758.5453;
        ver += (hsh - Math.floor(hsh) - 0.5) * 0.038;
      }
      ring[j * 3] = px + rx * lat + ux * ver;
      ring[j * 3 + 1] = py + ry * lat + uy * ver;
      ring[j * 3 + 2] = pz + rz * lat + uz * ver;
      cross[j] = j === 0 ? 0
        : cross[j - 1] + Math.hypot(lat - lastLat, ver - lastVer);
      lastLat = lat; lastVer = ver;
      /* the cess is spoil and weed-killed dirt, not clean stone: tint it
       * brown and darken it, which costs nothing and stops the ballast
       * reading as a grey carpet laid over the landscape */
      const dirt = edge ? 1 : (Math.abs(lat) >= TOE_X - 0.001 ? 0.80
                            : Math.abs(lat) > SHOULDER_X ? 0.34 : 0);
      /* Cess and batter, and no darker than that.
       *
       * These used to take 44/56/70 per cent out, which over the old sandy
       * ballast made a believable brown verge. Over the grey ballast it is
       * near-black, and the cess plus the batter are three or four metres wide
       * down BOTH sides of every road on the site — so where the line curves
       * away from the camera the two of them stack up in perspective into an
       * unbroken black band across the middle distance. That band was mistaken
       * for a shadow bug twice. A cess is ash, spoil and weedkiller: mid-brown,
       * a stop or so under the stone, never a hole. */
      col[j * 3] = 1 - dirt * 0.20;
      col[j * 3 + 1] = 1 - dirt * 0.30;
      col[j * 3 + 2] = 1 - dirt * 0.44;
    }
    if (prev) {
      const mid = ((n >> 1) * 3);
      const dz = Math.hypot(ring[mid] - prev[mid], ring[mid + 1] - prev[mid + 1],
                            ring[mid + 2] - prev[mid + 2]);
      run += dz;
      for (let j = 0; j < n - 1; j++) {
        const k = j + 1;
        mesher.tint(prevCol[j * 3], prevCol[j * 3 + 1], prevCol[j * 3 + 2]);
        mesher.quad([prev[j * 3], prev[j * 3 + 1], prev[j * 3 + 2]],
                    [prev[k * 3], prev[k * 3 + 1], prev[k * 3 + 2]],
                    [ring[k * 3], ring[k * 3 + 1], ring[k * 3 + 2]],
                    [ring[j * 3], ring[j * 3 + 1], ring[j * 3 + 2]],
                    cross[j] / BALLAST_TILE, (run - dz) / BALLAST_TILE,
                    cross[k] / BALLAST_TILE, run / BALLAST_TILE);
      }
      prev.set(ring); prevCol.set(col);
    } else {
      prev = Float32Array.from(ring);
      prevCol = Float32Array.from(col);
    }
    if (last) break;
  }
  mesher.tint(1, 1, 1);
}

/* ---- embedded track ------------------------------------------------------
 *
 * A loading rack is not ballasted. Road tankers, drum trucks and men with hoses
 * cross it all day, so the prototype pours a slab and beds the rail in it — and
 * the one thing that makes embedded track read as track rather than as two
 * lines painted on concrete is the FLANGEWAY: a 48mm slot, 70mm deep, on the
 * gauge side of each rail, which is the only place a wheel flange has to go.
 * It is also the only part of this that is not optional. Without it the slab
 * meets the rail head on both sides and the whole thing is a decal, which is
 * exactly the word the critic used.
 *
 * The section is generated per ring from a blend `t`, so the apron can be
 * ramped into the ballasted road at each end rather than ending in a 285mm
 * step in the middle of the four-foot: at t=0 it is a flat plane at ballast
 * crown level and SHOULDER_X wide, i.e. indistinguishable from the crib it is
 * taking over from, and at t=1 it is the full slab.
 */
const FLANGE_W = 0.048;        // the slot a wheel flange runs in
const FLANGE_D = 0.070;
const HEAD_HW = 0.036;         // half the width of the rail head
const PAVE_X = 3.15;           // half-width of the apron
const PAVE_DECK = -0.012;      // slab surface, just under the running surface
const PAVE_KERB = 0.13;        // the slab's own edge thickness — it is a solid
const PAVE_TILE = 3.0;         // metres per repeat: one poured bay
const PAVE_TAPER = 9.0;        // ramp from crib level to slab level

/** One ring of the apron, as [lateral, vertical] pairs, mirrored about the
 *  centre. `null` vertical means "pin it to the ground", exactly as the ballast
 *  section does. */
function paveSection(t) {
  const deck = BALLAST_CRIB + (PAVE_DECK - BALLAST_CRIB) * t;
  const fd = FLANGE_D * t;
  const px = SHOULDER_X + (PAVE_X - SHOULDER_X) * t;
  const gi = HALF_GAUGE - HEAD_HW;              // gauge face of the rail
  const gf = HALF_GAUGE + HEAD_HW;              // field face
  const fw = gi - FLANGE_W;
  const half = [
    [px + 0.16 + PAVE_KERB * 1.4, null],        // pinned out to natural ground
    [px, deck - PAVE_KERB],                     // the kerb face: it has a THICKNESS
    [px, deck],
    [gf, deck],                                 // slab up to the field side
    [gi, deck],                                 // and across under the head
    [gi, deck - fd],                            // flangeway wall
    [fw, deck - fd],                            // flangeway floor
    [fw, deck],                                 // flangeway wall
  ];
  /* `half` runs OUTWARD-IN, so the mirrored side is pushed in the same order
   * with the sign flipped and the near side after the centre. Pushing the
   * mirror reversed folds the ribbon back on itself: the outer edge lands next
   * to the four-foot and every quad between them is a sheet through the middle
   * of the slab. */
  const out = [];
  for (const p of half) out.push([-p[0], p[1]]);
  out.push([0, deck]);
  for (let i = half.length - 1; i >= 0; i--) out.push([half[i][0], half[i][1]]);
  return out;
}

/* Which parts of that section are what, for the vertex tint. `k` counts in
 * from the outermost point on either side; the section is symmetric. Each quad
 * takes the tint of its inner-numbered edge, so the flangeway's two walls and
 * its floor all have to be named, not just its floor. */
function paveTint(j, n) {
  const k = Math.min(j, n - 1 - j);
  if (k === 0) return [0.80, 0.70, 0.56];       // spoil at the edge of the slab
  if (k === 1) return [0.88, 0.87, 0.85];       // the kerb face, in its own shade
  if (k >= 4 && k <= 7) return [0.54, 0.52, 0.50];   // the flangeway slot
  if (k === 8) return [0.80, 0.78, 0.76];       // the four-foot, walked on and oiled
  return [1, 1, 1];
}

/** The apron, swept between two arc lengths of a road. */
function pavedDeck(track, mesher, ground, from, to, step2 = 2.0) {
  const f = track.frames;
  if (!f || !(to > from + 2 * PAVE_TAPER)) return;
  const n = paveSection(1).length;
  const ring = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  const cross = new Float32Array(n);
  let prev = null, prevCol = null, run = 0;
  const stride = Math.max(1, Math.round(step2 / f.step));
  const i0 = Math.max(0, Math.floor(from / f.step));
  const i1 = Math.min(f.count - 1, Math.ceil(to / f.step));
  for (let i = i0; i <= i1; i += stride) {
    const last = i + stride > i1;
    const idx = last ? i1 : i;
    const s = idx * f.step;
    /* Smoothstep in, smoothstep out — a linear ramp puts a visible crease at
     * both ends of it and the crease is 30m long. */
    const e = Math.min(1, Math.min(s - from, to - s) / PAVE_TAPER);
    const t = e <= 0 ? 0 : e * e * (3 - 2 * e);
    const sec = paveSection(t);
    const px = f.pos[idx * 3], py = f.pos[idx * 3 + 1], pz = f.pos[idx * 3 + 2];
    const rx = f.right[idx * 3], ry = f.right[idx * 3 + 1], rz = f.right[idx * 3 + 2];
    const ux = f.up[idx * 3], uy = f.up[idx * 3 + 1], uz = f.up[idx * 3 + 2];
    let lastLat = 0, lastVer = 0;
    for (let j = 0; j < n; j++) {
      let lat = sec[j][0];
      let ver = sec[j][1];
      if (ver === null) {
        /* The same batter walk-out the ballast uses. A slab standing a metre
         * out of the ground with a 130mm edge is a diving board; what is under
         * it is fill, and fill sits at about 1 in 1.5. */
        const sgn = lat < 0 ? -1 : 1;
        let l = lat;
        if (ground) {
          for (let pass = 0; pass < 2; pass++) {
            const gx = px + rx * l, gz = pz + rz * l;
            ver = Math.min(BALLAST_TOE - 0.02, (ground(gx, gz) || 0) - py - 0.06);
            ver = Math.max(ver, BALLAST_TOE - 4.0);
            const want = Math.abs(sec[1][0]) + 0.16 +
                         Math.max(0, BALLAST_TOE - ver) * 1.5;
            if (Math.abs(want - Math.abs(l)) < 0.06) break;
            l = sgn * want;
          }
          lat = l;
        } else ver = BALLAST_TOE - 0.10;
      }
      ring[j * 3] = px + rx * lat + ux * ver;
      ring[j * 3 + 1] = py + ry * lat + uy * ver;
      ring[j * 3 + 2] = pz + rz * lat + uz * ver;
      cross[j] = j === 0 ? 0
        : cross[j - 1] + Math.hypot(lat - lastLat, ver - lastVer);
      lastLat = lat; lastVer = ver;
      const c = paveTint(j, n);
      col[j * 3] = c[0]; col[j * 3 + 1] = c[1]; col[j * 3 + 2] = c[2];
    }
    if (prev) {
      const mid = ((n >> 1) * 3);
      const dz = Math.hypot(ring[mid] - prev[mid], ring[mid + 1] - prev[mid + 1],
                            ring[mid + 2] - prev[mid + 2]);
      run += dz;
      for (let j = 0; j < n - 1; j++) {
        const k = j + 1;
        mesher.tint(prevCol[j * 3], prevCol[j * 3 + 1], prevCol[j * 3 + 2]);
        mesher.quad([prev[j * 3], prev[j * 3 + 1], prev[j * 3 + 2]],
                    [prev[k * 3], prev[k * 3 + 1], prev[k * 3 + 2]],
                    [ring[k * 3], ring[k * 3 + 1], ring[k * 3 + 2]],
                    [ring[j * 3], ring[j * 3 + 1], ring[j * 3 + 2]],
                    cross[j] / PAVE_TILE, (run - dz) / PAVE_TILE,
                    cross[k] / PAVE_TILE, run / PAVE_TILE);
      }
      prev.set(ring); prevCol.set(col);
    } else {
      prev = Float32Array.from(ring);
      prevCol = Float32Array.from(col);
    }
    if (last) break;
  }
  mesher.tint(1, 1, 1);
}

/** Exactly which frames a road's own rails are laid between.
 *
 *  Snapped to whole frames and recorded on the track, because it is the arc
 *  length a turnout has to end at. The closure rails of a junction are drawn by
 *  sampling the diverging road itself, and if they stopped one frame short of
 *  where `railPair` starts there would be a gap in the rail; if they ran one
 *  frame long there would be two coincident sweeps fighting for the same
 *  depth. Both halves quote `railFrom`/`railTo`, so neither can happen. */
function railSpan(track) {
  const f = track.frames;
  if (!f) return null;
  const i0 = Math.max(0, Math.ceil((track.renderFrom || 0) / f.step - 1e-6));
  const i1 = Math.min(f.count - 1,
    Math.floor(Math.min(track.renderTo, track.length) / f.step + 1e-6));
  if (!(i1 > i0)) return null;
  track.railFrom = i0 * f.step;
  track.railTo = i1 * f.step;
  return {i0, i1};
}

/** The stretches of a road that are actually built: what is laid, less the
 *  tunnel bores, which are inside a hill and drawn by nobody. */
function visibleSpans(track) {
  const lo = track.renderFrom || 0;
  const hi = Math.min(track.renderTo, track.length);
  if (!(hi > lo)) return [];
  let bores = [];
  try { track.earthworks(); bores = track.bores || []; } catch { bores = []; }
  if (!bores.length) return [[lo, hi]];
  const out = [];
  let at = lo;
  for (const [a, b] of [...bores].sort((p, q) => p[0] - q[0])) {
    if (a > at + 1) out.push([at, Math.min(a, hi)]);
    at = Math.max(at, b);
  }
  if (hi > at + 1) out.push([at, hi]);
  return out.filter(r => r[1] > r[0] + 1);
}

/** The two running rails of one track. */
function railPair(track, mesher, thin = 1, from = null, to = null) {
  const f = track.frames;
  if (!f) return;
  /* `railSpan` is always taken over the WHOLE laid road, because `railFrom` and
   * `railTo` are what a turnout's closure rails stop against — a road drawn in
   * pieces because a tunnel bore is missing out of the middle must still tell
   * its junctions where its rails begin. Only the drawing is clipped. */
  const span = railSpan(track);
  if (!span) return;
  if (from !== null) {
    span.i0 = Math.max(span.i0, Math.ceil(from / f.step - 1e-6));
    span.i1 = Math.min(span.i1, Math.floor(to / f.step + 1e-6));
    if (!(span.i1 > span.i0)) return;
  }
  /* The sparse index list is a straight-line saving, not a licence to move the
   * ends: the first and last rings are pinned to the exact boundary frames so a
   * turnout's rails meet these ones and not a metre of nothing. */
  const idx = geomIndices(track, 8 * thin)
    .filter(i => i > span.i0 && i < span.i1);
  idx.unshift(span.i0);
  idx.push(span.i1);
  if (idx.length < 2) return;
  sweep(f, idx, RAIL_PROFILE, -HALF_GAUGE, mesher, 0);
  sweep(f, idx, RAIL_PROFILE, HALF_GAUGE, mesher, 0.37);
}

/** A quad with per-corner UVs. `Mesher.quad` only takes a UV *rect*, which ties
 *  the u axis to the a→b edge — and on the top of a sleeper that is the 300mm
 *  width, so the grain came out running across the baulk. Two tris with
 *  explicit coordinates cost exactly the same and can be oriented. */
function faceUV(m, p0, p1, p2, p3, t0, t1, t2, t3) {
  m.tri(p0, p1, p2, [t0, t1, t2]);
  m.tri(p0, p2, p3, [t0, t2, t3]);
}

/** The sleeper, once, as an instanced prototype: a chamfered timber baulk and
 *  its two tie plates.
 *
 *  ---- why this is no longer a box ----------------------------------------
 *
 *  It was `Mesher.box`, five faces, one UV rect for all of them. Two blind
 *  critics described the result as "flat, zero-thickness quads of near-
 *  identical tone" against a reference whose ties are "real solids, visible top
 *  and end faces". They were reading three things, all of them fixable without
 *  a single extra triangle on two of them:
 *
 *    - the top's grain ran the wrong way (see `faceUV`);
 *    - every face sampled the same band of the map, so a sleeper's end was
 *      painted with stretched long grain and read as a cut-off nothing;
 *    - the top edge was a perfect arris, and a perfect arris under a raking sun
 *      is one abrupt value step, which reads as a printed edge rather than as
 *      timber. A real sleeper's top edges are worn round.
 *
 *  The chamfer is the only part that costs anything — four triangles, and it
 *  buys the narrow lit band along the top of every tie that says the thing has
 *  a thickness. It is dropped on the big layouts, where the whole permanent way
 *  is already thinned and no camera is within forty metres of a sleeper.
 *
 *  `variant` slides the grain window along u so three instanced meshes give
 *  three different timbers; the difference between one tie and the next is
 *  otherwise only its instance colour, and a rank of identically-grained ties
 *  is exactly the "printed" reading. The underside is skipped; it is buried in
 *  three thousand copies. */
function sleeperPrototype(variant = 0, chamfer = true) {
  const m = new Mesher(true);
  m.tint(1, 1, 1);
  const hx = SLEEPER_LEN / 2, hz = SLEEPER_W / 2;
  const top = SLEEPER_TOP, bot = SLEEPER_TOP - SLEEPER_H;
  const ch = chamfer ? 0.022 : 0;
  const hzc = hz - ch, topc = top - ch;
  /* The grain window. 0.37 of the map's width per sleeper, so the three
   * variants overlap only slightly and none of them tiles within one baulk. */
  const u0 = variant * 0.31, u1 = u0 + 0.37;
  const vTop0 = SL_TOP0 + 0.02, vTop1 = SL_TOP1 - 0.02;
  const vSide0 = SL_SIDE0 + 0.01, vSide1 = SL_SIDE1 - 0.02;

  /* top */
  faceUV(m, [-hx, top, hzc], [hx, top, hzc], [hx, top, -hzc], [-hx, top, -hzc],
         [u0, vTop1], [u1, vTop1], [u1, vTop0], [u0, vTop0]);
  for (const s of [1, -1]) {
    if (ch > 0) {
      /* the worn arris, sampling the very top of the side band */
      const a = [-hx, topc, s * hz], b = [hx, topc, s * hz];
      const c = [hx, top, s * hzc], d = [-hx, top, s * hzc];
      if (s > 0) faceUV(m, a, b, c, d, [u0, vSide1], [u1, vSide1],
                        [u1, vSide1 + 0.012], [u0, vSide1 + 0.012]);
      else faceUV(m, b, a, d, c, [u0, vSide1], [u1, vSide1],
                  [u1, vSide1 + 0.012], [u0, vSide1 + 0.012]);
    }
    /* side: v runs up the face, so the dirty end of the band is at the ballast */
    const a = [-hx, bot, s * hz], b = [hx, bot, s * hz];
    const c = [hx, topc, s * hz], d = [-hx, topc, s * hz];
    if (s > 0) faceUV(m, a, b, c, d, [u0, vSide0], [u1, vSide0],
                      [u1, vSide1], [u0, vSide1]);
    else faceUV(m, b, a, d, c, [u0, vSide0], [u1, vSide0],
                [u1, vSide1], [u0, vSide1]);
  }
  /* the two ends, as the six-sided section they actually are, on the end-grain
   * band — the two halves of which are the two ends */
  for (const s of [1, -1]) {
    const base = s > 0 ? 0.02 : 0.52;
    const uv = (z, y) => [base + ((z / hz) * 0.5 + 0.5) * 0.46,
                          SL_END0 + ((y - bot) / (top - bot)) *
                          (SL_END1 - SL_END0)];
    const ring = s > 0
      ? [[hz, bot], [-hz, bot], [-hz, topc], [-hzc, top], [hzc, top], [hz, topc]]
      : [[-hz, bot], [hz, bot], [hz, topc], [hzc, top], [-hzc, top], [-hz, topc]];
    const P = ring.map(([z, y]) => [s * hx, y, z]);
    const T = ring.map(([z, y]) => uv(z, y));
    for (let i = 1; i < ring.length - 1; i++) {
      m.tri(P[0], P[i], P[i + 1], [T[0], T[i], T[i + 1]]);
    }
  }
  /* A real tie plate is about 360 x 200 under a rail this size. The 420 x 320
   * this used to be covered a third of the tie and, being the brightest thing
   * in the map, decided what colour the whole permanent way looked. */
  m.tint(0.76, 0.75, 0.74);
  for (const s of [-1, 1]) {
    /* uvScale 2.4 so the plate's 225mm depth lands inside the 0.14 the plate
     * band is wide; at the old 0.5 it ran off the end of the map and wrapped
     * back into the timber, which put a strip of grain on every baseplate. */
    m.box(0.36, PAD_H, 0.225, s * HALF_GAUGE, SLEEPER_TOP + PAD_H / 2, 0,
          null, 2.4, [0.02, SL_PLATE0 + 0.02], 32);
  }
  return m.geometry();
}

/** The rail clips, as their own instanced mesh so the quality ladder can drop
 *  them without touching the sleepers.
 *
 *  Only the two field-side clips are modelled, and that is a budget decision
 *  taken with the numbers in front of me: four clips on every sleeper on this
 *  railway is 184,000 triangles — forty per cent of the whole subsystem — spent
 *  on 60mm castings that are two pixels past eight metres. The two that survive
 *  are the ones on the outside of each rail, which are the pair a camera beside
 *  the track can actually see. The gauge-side clips are in the tie-plate's
 *  texture, where they cost nothing. */
function fastenerPrototype() {
  const m = new Mesher(true);
  m.tint(0.62, 0.60, 0.57);
  for (const s of [-1, 1]) {
    m.box(0.055, 0.046, 0.112, s * (HALF_GAUGE + 0.098),
          SLEEPER_TOP + PAD_H + 0.020, 0, null, 0.3, [0.2, 0.81], 32);
  }
  return m.geometry();
}

/** Long timbers under a turnout — same prototype, scaled along its length. */
function bearerPrototype() {
  const m = new Mesher(true);
  m.tint(1, 1, 1);
  m.box(1, SLEEPER_H, SLEEPER_W * 1.15, 0, SLEEPER_TOP - SLEEPER_H / 2, 0,
        null, 1.4, [0, 0], 32);
  return m.geometry();
}
/* ---- turnout leads -------------------------------------------------------
 *
 * Factorio's rail planner has exactly one non-negotiable invariant: a piece is
 * emitted only when it connects exactly — same point, same tangent, same level
 * — to the piece before it, and the planner refuses rather than emit a join
 * that does not close. This file did not have that, and an audit against that
 * planner is what found it.
 *
 * Every road that left another road used to be drawn from its own control
 * polyline, graded on its own, and then TRIMMED back to wherever it first stood
 * 1.9m clear of its parent; the turnout artwork was pasted at the nearest point
 * on the parent and asked to bridge the difference. So the diverging road's
 * railhead began a metre and a half sideways of the through road's, at a height
 * nobody had reconciled, and the switch blades — which are supposed to taper to
 * nothing against the stock rail — tapered to nothing 1.18m out in the
 * four-foot. Measured on the lab's own seven-instrument floor before this
 * change, the worst of those joins missed by 1.92m.
 *
 * The lead fixes it by inverting the order of construction. The turnout is
 * generated FIRST, off the parent's own frame, and the road that leaves there
 * is planned from the turnout's exit port rather than the other way round:
 *
 *   - a constant-radius curve leaving the stock rail TANGENTIALLY (there is no
 *     lateral step at a switch tip on any real railway; the blade lies against
 *     the stock rail and the divergence is a²/2R);
 *   - radius fixed by the frog number, 2·G·N², which is the textbook
 *     construction: on one constant radius from the tip, the gauge faces cross
 *     exactly where the offset reaches a full gauge, and that IS the 1:N
 *     crossing. 1:8 comes out at 184m and 1:6 at 103m, both radii a real yard
 *     would recognise — the check that the construction is right rather than
 *     merely self-consistent;
 *   - the exit port, position and tangent, becomes the road's first control
 *     point, and the lead itself is spliced into the road's alignment
 *     (`Track._splice`), so the road's arc length zero IS the switch tip.
 *
 * Nothing is left to bridge. `Rail.jointReport()` measures every one of them.
 */
function makeLead(parent, tipXZ, awayXZ, N = 8) {
  if (!parent?.frames) return null;
  const near = parent.nearest(tipXZ.x, tipXZ.z);
  if (!(near.distance < 9)) return null;
  const f = parent.at(near.s);
  let tx = f.tangent.x, tz = f.tangent.z;
  const tl = Math.hypot(tx, tz);
  if (!(tl > 1e-6)) return null;
  tx /= tl; tz /= tl;
  /* Which way a train faces through the switch, and which hand it turns —
   * both read off where the road is actually going rather than declared, so a
   * caller cannot get them inconsistent with the geometry it then draws. */
  const ax = awayXZ.x - f.position.x, az = awayXZ.z - f.position.z;
  const pdir = (ax * tx + az * tz) >= 0 ? 1 : -1;
  const ux = tx * pdir, uz = tz * pdir;
  const rx = -uz, rz = ux;
  const hand = (ax * rx + az * rz) >= 0 ? 1 : -1;

  const R = 2 * GAUGE * N * N;
  const aFrog = Math.sqrt(2 * R * GAUGE);
  const len = aFrog + 5.4;              // a little closure rail past the frog
  /* The tip has to have railway behind it as well as in front: a turnout laid
   * against the end of its own through road has no stock rail to plane a blade
   * against. */
  if (near.s < len * 0.5 || near.s > parent.length - len * 0.5) return null;
  const n = Math.max(12, Math.ceil(len / 1.1));
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const s = (len * i) / n, phi = s / R;
    const u = R * Math.sin(phi), v = R * (1 - Math.cos(phi));
    pts.push({x: f.position.x + ux * u + rx * hand * v,
              z: f.position.z + uz * u + rz * hand * v,
              k: hand / R});
  }
  const phi = len / R;
  const tan = {x: ux * Math.cos(phi) + rx * hand * Math.sin(phi),
               z: uz * Math.cos(phi) + rz * hand * Math.sin(phi)};
  /* The gradient is quoted per metre of FLAT arc, not per metre of rail. Every
   * alignment in this file is parametrised by its plan length — `Track.build`
   * measures arc in x/z and adds height afterwards — so handing a profile the
   * 3D tangent's y component asks it for a gentler grade than the parent has
   * by a factor of cos(θ). At 1 in 40 that is nothing; on the sparse layouts,
   * where the ground under the trunk really does run at 1 in 2, it is 13% and
   * it showed as a degree and a half of tangent mismatch at the join. */
  const horiz = Math.hypot(f.tangent.x, f.tangent.z) || 1;
  return {parent, pts, exit: pts[n], tan, R, aFrog, len, N, hand, pdir,
          tipS: near.s, frame: f,
          y: f.position.y, grade: (pdir * f.tangent.y) / horiz};
}

/** How far past a switch tip a 1:N lead has opened `clear` metres from the road
 *  it leaves — the FOULING POINT, quoted along the through road.
 *
 *  It is the whole of the passing-loop arithmetic and it is derived from
 *  `makeLead`'s own curve so the two can never disagree: a circular arc of
 *  R = 2·G·N² for √(2RG) + 5.4 metres, then the frog angle carried on straight.
 *  Past the heel the offset grows as v = v₀ + m·(u − u₀) with m = tan φ, and the
 *  distance from a point out there to a body standing on the through road is
 *  hypot(u₀ + something, v) — minimised, not read off the perpendicular, which
 *  is where a first pass at this was optimistic by half a metre. The minimum of
 *  √(h² + (V − mh)²) over h is V/√(1+m²), so the run wanted is the u at which
 *  v reaches clear·√(1+m²).
 *
 *  Checked against `harness/pl-foul.mjs`, which slides a real body down a lead
 *  built on the real road and measures the real distance: predicted 5.97m at
 *  1:4.5 on the lab's worst gap, measured 6.00m. */
function leadClearRun(N, clear) {
  const R = 2 * GAUGE * N * N;
  const len = Math.sqrt(2 * R * GAUGE) + 5.4;
  const phi = len / R;
  const v0 = R * (1 - Math.cos(phi)), u0 = R * Math.sin(phi), m = Math.tan(phi);
  return u0 + (clear * Math.hypot(1, m) - v0) / m;
}

/** ...and the inverse: what clearance a run of `g` metres actually buys. */
function leadClearAt(N, g) {
  const R = 2 * GAUGE * N * N;
  const len = Math.sqrt(2 * R * GAUGE) + 5.4;
  const phi = len / R;
  const v0 = R * (1 - Math.cos(phi)), u0 = R * Math.sin(phi), m = Math.tan(phi);
  return (v0 + m * (g - u0)) / Math.hypot(1, m);
}

/** How much rail a turnout's OVERLAP has to cover, measured from the tip.
 *
 *  This was the number `32`, written out by hand at one call site and copied to
 *  five others as `lead.len + 6` and `lead.len + 26`, and it was wrong for the
 *  reason REQUESTS.md's "five inert rules" section describes: an absolute
 *  constant standing in for a quantity the file already knows how to derive.
 *  `leadClearAt(6, 32)` is **4.49m** — the block on a 1:6 junction stopped half
 *  a metre inside soak.mjs's own 5.00m fouling threshold and 1.26m inside this
 *  file's own declared `FOUL_CLEAR`. So a train standing at the end of the
 *  plain block behind a junction was 4.56m from the tail of a train that had
 *  just come off the branch (measured, `harness/rz-pair.mjs`, layout 0:
 *  `main#2 / branch0#8*  4.56`), with both correctly signalled and no block in
 *  common. That is the collision, and this is the number that caused it.
 *
 *  An overlap has to reach to where the diverging road is genuinely clear,
 *  which is `leadClearRun`, not a fixed distance:
 *
 *      frog    lead.len   leadClearAt(N, 32)   leadClearRun(N, 5.75)
 *      1:6      22.6m          4.49m                  37.8m
 *      1:4.5    18.3m          6.86m                  27.8m
 *
 *  Note 1:4.5 goes the OTHER way: at a sharp frog 32m was longer than it had to
 *  be, which is why `_midRankLinks` had to cap its crossover block by hand
 *  (`LINK_BLOCK_GAP`) to keep it off the next rake's tail. 27.8m is what that
 *  geometry actually wanted and the cap goes from 4.99m of trimming to 0.8m.
 *
 *  THIS IS NOT THE SLEEPER RANGE. `Track.blocks` means "bearers here, no
 *  timbers" and it stops at the heel of the assembly; the overlap runs fifteen
 *  metres past that on a 1:6, and drawing it as a gap in the sleepers would put
 *  fifteen bare metres of plain line beyond every junction on the railway. The
 *  two live in `blocks` and `overlaps` and only `_sectionBlocks` reads both. */
const FOUL_CLEAR = 5.75;       // soak.mjs's 5.00m fouling threshold, and 15%
function foulRun(N, len) {
  return Math.max((len || 0) + 5, leadClearRun(N, FOUL_CLEAR));
}

/** The stretch of the PARENT a turnout stands on, as an arc range.
 *
 *  It is where a sleeper may not be planted (the bearers are there instead).
 *  The lead runs from the tip in the +pdir direction, so the range is one-sided
 *  and the side depends on the hand of the junction — which two call sites used
 *  to write out by hand, one of them with the sign inverted, so the block sat on
 *  the clear rail beyond a turnout while the turnout itself was drawn over plain
 *  sleepers. Derived from the lead means it cannot disagree with the geometry it
 *  protects. */
function junctionBlock(lead) {
  return [lead.tipS - 2 * lead.pdir, lead.tipS + 32 * lead.pdir]
           .sort((a, b) => a - b);
}

/** ...and the same turnout's OVERLAP, which is what the block table cuts at.
 *
 *  The 2m on the far side of the tip is deliberately NOT the fouling distance.
 *  Nothing diverging exists there — the child's alignment ends ON the parent's
 *  centreline at the tip — so a train there is nose-to-tail with one on the
 *  child, on the same rails, and that is trains.js's lookahead (`CLEAR`, 6.5m),
 *  not this table's job. Measured either way that pair sits at 2.00m in
 *  `rz-pair.mjs` and never closer than 8.5m on the metal. */
function junctionOverlap(lead) {
  return [lead.tipS - 2 * lead.pdir,
          lead.tipS + foulRun(lead.N, lead.len) * lead.pdir]
           .sort((a, b) => a - b);
}

/** ...and the same overlap on the CHILD, quoted from the end that joins.
 *
 *  `which` is `_joint`'s: 'start' means arc zero is the tip. The child's own
 *  arc runs along the lead, so the fouling point is at very nearly the same
 *  distance measured either way — the two differ by the versine of a curve
 *  whose offset is under six metres over forty, i.e. millimetres. */
function childOverlap(child, lead, which, minRun) {
  const run = Math.max(minRun || 0, foulRun(lead.N, lead.len));
  return which === 'start' ? [-2, run]
                           : [child.length - run, child.length + 2];
}

/* ---- turnouts ------------------------------------------------------------
 *
 * The one assembly that decides whether this reads as track or as two lines
 * that happen to touch. Built partly in the frame of the *through* route at the
 * switch tip and partly by sampling the diverging road itself, which is the
 * whole point:
 *
 *   · stock rails are already there — they are the through track's own rails;
 *   · the switch blades are rails swept with a vertical scale that goes to
 *     nothing at the tip, lying against the stock rail and opening to the heel,
 *     one closed and one open, exactly as a set of points sits. The tip is ON
 *     the through centreline, because the lead starts there;
 *   · the closure rails from the heel to the end of the lead are not drawn from
 *     a formula at all — they are swept along the diverging road's own frames
 *     at ±half gauge, ending on the exact frame `railPair` starts that road
 *     from. That is the join, and it closes to the float;
 *   · the frog is a solid casting straddling the crossing, which is where the
 *     offset reaches a full gauge — the same place the frog number put it;
 *   · a check rail opposite the frog on EACH road, which is where the prototype
 *     puts them: the wheel is held off the nose from the other rail of its own
 *     road, not from the road it is leaving;
 *   · the bearers run long and get longer toward the heel;
 *   · a point machine and its rodding sit outside the four-foot.
 */
function buildTurnout(rail, kit, ballast, timberSlots, t) {
  const {track, s, child, which, hand, pdir, closed = true} = t;
  const f = track.frames;
  if (!f || !child?.frames) return;
  const fr = track.at(s);
  const pos = fr.position, U = fr.up;
  /* Reading the turnout backwards down the line flips two axes, not one — one
   * flip would mirror every box in it and turn its normals inside out. */
  const R = fr.right.clone().multiplyScalar(pdir);
  const T = fr.tangent.clone().multiplyScalar(pdir);

  const p = (a, l, v) => [
    pos.x + T.x * a + R.x * l + U.x * v,
    pos.y + T.y * a + R.y * l + U.y * v,
    pos.z + T.z * a + R.z * l + U.z * v,
  ];

  /* The diverging road, in its own frames, `a` metres past the tip. `which`
   * says which end of that road the tip is, and arc length on both roads is
   * measured from the same point, so `a` means the same thing to each. */
  const c0 = which === 'start' ? 0 : child.length;
  const cdir = which === 'start' ? 1 : -1;
  const CF = {position: new THREE.Vector3(), tangent: new THREE.Vector3(),
              up: new THREE.Vector3(), right: new THREE.Vector3(), k: 0};
  const C = a => child.at(c0 + cdir * a, CF);
  /* How far the diverging road stands off the through road — sampled off the
   * road that actually leaves here, never a formula, which is what makes the
   * blades point where the rails genuinely go. It is 0 at a = 0 now, and that
   * is the whole of the fix. */
  const div = a => {
    const q = C(a).position;
    return (q.x - pos.x) * R.x + (q.z - pos.z) * R.z;
  };

  /* Where this road's OWN rails begin: the turnout's closure rails run to
   * exactly there and stop. */
  const endA = which === 'start'
    ? (child.railFrom ?? 0)
    : (child.length - (child.railTo ?? child.length));
  if (!(endA > 8)) return;
  const H = hand;
  const aFrog = Math.min(endA - 3.2, t.aFrog || Math.sqrt(2 * t.R * GAUGE));
  const BLADE = Math.min(10.5, aFrog - 4.5);
  if (!(BLADE > 3)) return;

  /* -- switch blades ---------------------------------------------------- */
  for (const side of [-1, 1]) {
    const openSide = side === H;     // the blade on the diverging side
    const n = 12;
    const np = RAIL_PROFILE.length;
    let prev = null;
    for (let i = 0; i <= n; i++) {
      const a = (i / n) * BLADE;
      const grow = Math.min(1, 0.10 + (i / n) * 1.35);
      /* A closed blade hugs its stock rail; the open one stands off by the
       * throw (≈115mm) and closes to zero at the heel. */
      const stand = openSide === closed ? (1 - i / n) * 0.115 : 0;
      const lat = side * (HALF_GAUGE - 0.028) + (openSide ? div(a) : 0)
                + side * stand;
      const drop = -RAIL_H * (1 - grow);
      const pts = [];
      for (let j = 0; j < np; j++) {
        pts.push(p(a, lat + RAIL_PROFILE[j][0] * (0.55 + grow * 0.45),
                   RAIL_PROFILE[j][1] * grow + drop));
      }
      if (prev) {
        for (let j = 0; j < np; j++) {
          const k = (j + 1) % np;
          rail.quad(prev[j], prev[k], pts[k], pts[j],
                    j / np, a, (j + 1) / np, a + 0.8);
        }
      }
      prev = pts;
    }
  }

  /* -- closure rails: the diverging road's own rails, swept along it ------
   *
   * Not `p(a, div(a))`. The through road's frame is straight and the diverging
   * road is not, so reconstructing it from a lateral offset is short by
   * a³/6R² — eleven centimetres at the end of a 1:8 lead, which is exactly the
   * kind of nearly-right that leaves a visible step where these rails hand over
   * to the road's own. Sampling the road's frames instead makes the last ring
   * here and the first ring `railPair` lays the same ring. */
  {
    const np = RAIL_PROFILE.length;
    const n = Math.max(8, Math.ceil((endA - BLADE) / 1.4));
    for (const side of [-1, 1]) {
      let prev = null;
      for (let i = 0; i <= n; i++) {
        const a = BLADE + ((endA - BLADE) * i) / n;
        const c = C(a);
        const pts = [];
        for (let j = 0; j < np; j++) {
          const lat = side * HALF_GAUGE + RAIL_PROFILE[j][0];
          const ver = RAIL_PROFILE[j][1];
          pts.push([c.position.x + c.right.x * lat + c.up.x * ver,
                    c.position.y + c.right.y * lat + c.up.y * ver,
                    c.position.z + c.right.z * lat + c.up.z * ver]);
        }
        if (prev) {
          for (let j = 0; j < np; j++) {
            const k = (j + 1) % np;
            rail.quad(prev[j], prev[k], pts[k], pts[j],
                      j / np, a, (j + 1) / np, a + 0.9);
          }
        }
        prev = pts;
      }
    }
  }

  /* -- the frog: a solid casting where the two gauge faces cross ----------
   * It straddles the crossing, which sits half a gauge off the diverging
   * road's centreline on the through-road side — the same point the frog
   * number put it. Drawn on the diverging road's own frames so it lands on the
   * rails rather than near them. */
  kit.tint(0.40, 0.39, 0.37);
  const y0 = SLEEPER_TOP + PAD_H;
  {
    const nose = -H * HALF_GAUGE;
    for (let i = 0; i < 9; i++) {
      const a0 = aFrog - 2.2 + i * 0.62, a1 = a0 + 0.62;
      const w0 = 0.075 + i * 0.050, w1 = 0.075 + (i + 1) * 0.050;
      const A = C(a0), pa = A.position.clone(), ra = A.right.clone(),
            ua = A.up.clone();
      const B = C(a1), pb = B.position.clone(), rb = B.right.clone(),
            ub = B.up.clone();
      const q = (P, Rv, Uv, l, v) => [P.x + Rv.x * l + Uv.x * v,
                                      P.y + Rv.y * l + Uv.y * v,
                                      P.z + Rv.z * l + Uv.z * v];
      kit.quad(q(pa, ra, ua, nose - w0, y0 + 0.058),
               q(pb, rb, ub, nose - w1, y0 + 0.058),
               q(pb, rb, ub, nose + w1, y0 + 0.058),
               q(pa, ra, ua, nose + w0, y0 + 0.058),
               0, i * 0.3, 1, (i + 1) * 0.3);
      /* the casting's own sides, so it catches light as a block rather than a
       * decal painted between the rails */
      for (const s2 of [-1, 1]) {
        kit.quad(q(pa, ra, ua, nose + s2 * w0, y0 + 0.058),
                 q(pb, rb, ub, nose + s2 * w1, y0 + 0.058),
                 q(pb, rb, ub, nose + s2 * w1, y0 - 0.02),
                 q(pa, ra, ua, nose + s2 * w0, y0 - 0.02),
                 0, i * 0.3, 0.3, (i + 1) * 0.3);
      }
    }
  }

  /* -- check rails, one opposite the frog on each road, flared both ends --
   * The through road's is on the rail away from the crossing; the diverging
   * road carries its own. Both are drawn on the frames of the road they belong
   * to, which is what keeps them parallel to the rail they check. */
  {
    const np = RAIL_PROFILE.length;
    const nSeg = 8, len = 4.6;
    const sweepCheck = (frameAt, side) => {
      let prev = null;
      for (let i = 0; i <= nSeg; i++) {
        const a = aFrog - len / 2 + (i / nSeg) * len;
        const flare = Math.abs(i / nSeg - 0.5) * 2;
        const off = side * (HALF_GAUGE - 0.041 - 0.055 * (1 - flare * flare));
        const c = frameAt(a);
        const pts = [];
        for (let j = 0; j < np; j++) {
          const lat = off + RAIL_PROFILE[j][0] * 0.9;
          const ver = RAIL_PROFILE[j][1] * 0.86 - RAIL_H * 0.14;
          pts.push([c.position.x + c.right.x * lat + c.up.x * ver,
                    c.position.y + c.right.y * lat + c.up.y * ver,
                    c.position.z + c.right.z * lat + c.up.z * ver]);
        }
        if (prev) {
          for (let j = 0; j < np; j++) {
            const k = (j + 1) % np;
            rail.quad(prev[j], prev[k], pts[k], pts[j],
                      j / np, a, (j + 1) / np, a + 0.5);
          }
        }
        prev = pts;
      }
    };
    /* the through road, in the tip's own frame — it is straight here */
    const straight = a => ({position: {x: pos.x + T.x * a, y: pos.y + T.y * a,
                                       z: pos.z + T.z * a},
                            right: R, up: U});
    sweepCheck(straight, -H);
    sweepCheck(a => C(a), H);
  }

  /* -- the ballast widens with the junction ------------------------------
   * A turnout's bearers are up to five metres long and they reach out over
   * where the diverging road is going. Laid on the through track's own bed
   * they hang over its shoulder and stand on grass, which is the single most
   * obvious thing wrong with a junction built as two tracks. Real ballast
   * spreads to carry them, so the apron is swept off the same `div` the
   * blades are. */
  {
    /* A trapezium per step: the inner edge is the through track's shoulder,
     * the outer one walks out with the diverging road. Wound so the pair of
     * lateral offsets increases, because that is what puts the face normal up
     * whichever hand the junction turns. */
    const strip = (a0, a1, lA0, lA1, lB0, lB1, vA, vB, dirt) => {
      ballast.tint(1 - dirt * 0.20, 1 - dirt * 0.30, 1 - dirt * 0.44);
      const flip = lB0 < lA0;
      const q0 = flip ? [lB0, lB1, vB] : [lA0, lA1, vA];
      const q1 = flip ? [lA0, lA1, vA] : [lB0, lB1, vB];
      ballast.quad(p(a0, q0[0], q0[2]), p(a0, q1[0], q1[2]),
                   p(a1, q1[1], q1[2]), p(a1, q0[1], q0[2]),
                   q0[0] / BALLAST_TILE, a0 / BALLAST_TILE,
                   q1[0] / BALLAST_TILE, a1 / BALLAST_TILE);
    };
    const step = 2.2;
    const batter = TOE_X - SHOULDER_X;
    const inner = H * (SHOULDER_X + 0.02);
    for (let a = -3.0; a < endA + 5; a += step) {
      const a1 = Math.min(endA + 5, a + step);
      const wide = l => (H > 0 ? Math.max(l, inner) : Math.min(l, inner));
      const o0 = wide(div(a) + H * SHOULDER_X), o1 = wide(div(a1) + H * SHOULDER_X);
      if (Math.abs(o0 - inner) < 0.02 && Math.abs(o1 - inner) < 0.02) continue;
      strip(a, a1, inner, inner, o0, o1, SHOULDER_TOP, SHOULDER_TOP, 0);
      strip(a, a1, o0, o1, o0 + H * batter, o1 + H * batter,
            SHOULDER_TOP, BALLAST_TOE, 0.5);
    }
    ballast.tint(1, 1, 1);
  }

  /* -- bearers: long timbers, lengthening toward the heel ---------------- */
  const M = new THREE.Matrix4();
  const basis = new THREE.Matrix4().makeBasis(R, U, T);
  for (let a = -1.4; a < endA + 4.0; a += SLEEPER_PITCH) {
    const grow = Math.max(0, Math.min(1, (a + 1.4) / (endA + 5.4)));
    const len = SLEEPER_LEN + grow * 2.5 + Math.abs(div(a)) * 1.05;
    const shift = H * (len - SLEEPER_LEN) * 0.42;
    const q = p(a, shift, 0);
    M.copy(basis).setPosition(q[0], q[1], q[2]);
    timberSlots.push({m: M.clone(), len});
  }

  /* -- point machine, stretcher bars and the rodding run ----------------- */
  const mx = -H * (SLEEPER_LEN / 2 + 0.85);
  kit.tint(0.30, 0.33, 0.36);
  const boxM = new THREE.Matrix4().copy(basis);
  const bp = p(BLADE * 0.34, mx, SLEEPER_TOP + 0.30);
  boxM.setPosition(bp[0], bp[1], bp[2]);
  kit.box(0.62, 0.58, 1.55, 0, 0, 0, boxM, 0.7);
  kit.tint(0.50, 0.48, 0.43);
  for (let i = 0; i < 2; i++) {
    const a = 1.1 + i * (BLADE - 2.4);
    kit.box(0.075, 0.075, Math.abs(mx) * 1.15, 0, 0, 0,
            new THREE.Matrix4().copy(basis).setPosition(
              ...p(a, mx * 0.45, SLEEPER_TOP + 0.09)), 0.4);
  }
  kit.tint(1, 1, 1);
}

/* ---- trackside -----------------------------------------------------------
 *
 * TF2 and Train Simulator both look real for the same unglamorous reason: the
 * line is *equipped*. Signals, relay cabinets, mileposts, a cable route, a
 * fence, a crossing. None of it is interesting on its own and all of it is
 * missing from every toy railway, so the whole set is built here — instanced,
 * because there are dozens of each and they are all the same object.
 */

/** A three-aspect colour-light signal on a tubular mast: base, mast, ladder
 *  stile, backboard, hood shells. The lenses are a separate instanced mesh so
 *  they can change colour without touching this one. */
function signalPrototype() {
  const m = new Mesher(true);
  m.tint(0.34, 0.35, 0.34);
  m.box(0.62, 0.22, 0.62, 0, 0.11, 0, null, 0.6);         // concrete base
  m.tint(0.30, 0.32, 0.31);
  m.tube(0.075, 0.070, 4.15, 0, 0.20, 0, 8);              // mast
  /* backboard and head: a plain black sheet with three hooded lamps, which is
   * what reads at 40m — the hoods are the silhouette, not the lenses */
  m.tint(0.055, 0.058, 0.062);
  m.box(0.50, 1.42, 0.075, 0, 4.05, -0.07, null, 0.8);
  m.box(0.36, 1.30, 0.30, 0, 4.05, 0.12, null, 0.8);
  for (let i = 0; i < 3; i++) {
    const y = 4.05 + (1 - i) * 0.415;
    m.tint(0.075, 0.078, 0.082);
    m.tube(0.145, 0.155, 0.20, 0, 0, 0, 8,
           new THREE.Matrix4().makeRotationX(-Math.PI / 2)
             .setPosition(0, y, 0.20));
  }
  m.tint(0.28, 0.30, 0.29);
  m.box(0.30, 0.34, 0.16, 0, 3.05, 0.06, null, 0.5);      // route indicator box
  m.tint(0.55, 0.54, 0.20);
  m.box(0.20, 0.30, 0.03, 0, 2.55, 0.10, null, 0.4);      // ID plate
  /* the ladder stile — two stringers and rungs, the thing that breaks the
   * mast's silhouette and says the lamp gets changed by a person */
  m.tint(0.32, 0.34, 0.33);
  for (const s of [-1, 1]) {
    m.box(0.035, 3.3, 0.035, s * 0.16, 1.85, -0.24, null, 0.3);
  }
  for (let i = 0; i < 9; i++) {
    m.box(0.32, 0.022, 0.022, 0, 0.45 + i * 0.36, -0.24, null, 0.3);
  }
  return m.geometry();
}

/** The lens: a disc facing +z, drawn unlit so it reads as a lamp and blooms.
 *  It carries a flat white vertex colour it does not need, because the aspect
 *  arrives as an instance colour and three only multiplies that into the
 *  fragment when the material is declared vertex-coloured. */
function lensGeometry() {
  const g = new THREE.CircleGeometry(0.118, 10);
  const n = g.attributes.position.count;
  g.setAttribute('color', new THREE.Float32BufferAttribute(
    new Float32Array(n * 3).fill(1), 3));
  return g;
}

function cabinetPrototype() {
  const m = new Mesher(true);
  m.tint(0.42, 0.44, 0.43);
  m.box(1.35, 0.16, 0.95, 0, 0.08, 0, null, 1.0);
  m.tint(0.52, 0.55, 0.53);
  m.box(1.20, 1.28, 0.80, 0, 0.80, 0, null, 1.0);
  m.tint(0.40, 0.42, 0.41);
  m.box(1.34, 0.07, 0.94, 0, 1.47, 0, null, 1.0);         // lid overhang
  m.tint(0.30, 0.31, 0.31);
  m.box(0.06, 0.90, 0.03, 0, 0.80, 0.41, null, 0.4);      // door seam
  return m.geometry();
}

function milepostPrototype() {
  const m = new Mesher(true);
  m.tint(0.36, 0.37, 0.36);
  m.box(0.10, 0.95, 0.10, 0, 0.475, 0, null, 0.4);
  m.tint(0.82, 0.82, 0.78);
  m.box(0.34, 0.26, 0.03, 0, 0.98, 0.02, null, 0.4);
  return m.geometry();
}

/** Concrete cable troughing: a U-section run with its lids on, the thing that
 *  actually draws the line's edge in every lineside photograph ever taken. */
function troughPrototype() {
  const m = new Mesher(true);
  m.tint(0.88, 0.87, 0.84);
  m.box(0.44, 0.30, 1.12, 0, 0.15, 0, null, 0.8, [0, 0], 32);
  m.tint(0.72, 0.71, 0.69);
  m.box(0.47, 0.045, 1.06, 0, 0.322, 0, null, 0.8);
  return m.geometry();
}

function fencePostPrototype() {
  const m = new Mesher(true);
  m.tint(0.40, 0.38, 0.34);
  m.box(0.085, 1.30, 0.085, 0, 0.65, 0, null, 0.4, [0, 0], 32);
  return m.geometry();
}

/** A buffer stop: two rails bent up on to a sleeper-built block with a red
 *  target plate. Every stub on this railway ends in one, because a track that
 *  simply stops is the other half of the toy-railway tell. */
function bufferPrototype() {
  const m = new Mesher(true);
  m.tint(0.46, 0.44, 0.40);
  for (const s of [-1, 1]) {
    m.box(0.22, 0.22, 3.2, s * HALF_GAUGE, SLEEPER_TOP + 0.10, -1.2, null, 1.0);
    /* the ramp: three stacked blocks approximating a rail bent up on to it */
    for (let i = 0; i < 3; i++) {
      m.box(0.20, 0.24, 1.0, s * HALF_GAUGE,
            SLEEPER_TOP + 0.30 + i * 0.24, 0.35 + i * 0.42, null, 1.0);
    }
  }
  m.tint(0.34, 0.35, 0.34);
  m.box(2.05, 0.34, 0.34, 0, SLEEPER_TOP + 1.02, 1.72, null, 0.8);
  m.tint(1.95, 0.26, 0.20);                                // above 1: it blooms
  m.box(1.30, 0.62, 0.05, 0, SLEEPER_TOP + 1.42, 1.80, null, 0.6);
  m.tint(1, 1, 1);
  return m.geometry();
}

/* ---- routes --------------------------------------------------------------
 *
 * What trains.js is handed. It is a THREE.Curve so `getPointAt` works, but it
 * is parametrised by arc length directly rather than by three's chord-length
 * table: the points come off an alignment that is already uniformly sampled,
 * so the table would be an approximation of something exact.
 */
class PolyRoute extends THREE.Curve {
  constructor(points) {
    super();
    this.points = points;
    const n = points.length;
    this.acc = new Float32Array(n);
    let a = 0;
    for (let i = 1; i < n; i++) {
      a += points[i].distanceTo(points[i - 1]);
      this.acc[i] = a;
    }
    this.length = a;
  }

  pointAtDistance(m, target) {
    const out = target || new THREE.Vector3();
    const P = this.points, A = this.acc, n = P.length;
    if (n === 0) return out;
    if (n === 1) return out.copy(P[0]);
    const s = Math.min(A[n - 1], Math.max(0, m));
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (A[mid] <= s) lo = mid; else hi = mid;
    }
    const span = A[hi] - A[lo] || 1;
    return out.copy(P[lo]).lerp(P[hi], (s - A[lo]) / span);
  }

  pointAt(t, target) { return this.pointAtDistance(t * this.length, target); }
  getPoint(t, target) { return this.pointAtDistance(t * this.length, target); }
  getPointAt(u, target) { return this.pointAtDistance(u * this.length, target); }
  getLength() { return this.length; }
  getLengths() { return Array.from(this.acc); }

  get dockPoint() { return this.points[0]; }
  get arrivalPoint() { return this.points[this.points.length - 1]; }
}

/* ---- the subsystem ------------------------------------------------------- */

const ASPECT = {
  red:    [2.60, 0.09, 0.05],
  yellow: [2.35, 1.35, 0.10],
  green:  [0.12, 2.15, 0.62],
  dark:   [0.020, 0.021, 0.022],
};

export class Rail {
  constructor(ctx) {
    this.ctx = ctx;
    this.Tex = ctx.Tex;
    this.root = new THREE.Group();
    this.root.name = 'rail';
    this.tracks = [];
    this.lines = [];
    this._turnouts = [];           // every junction, and the two pieces it joins
    this.trunk = null;             // the one road every branch runs on to
    this.branches = [];            // {track, row, jS, tS} — one per row of benches
    this.branchOf = new Map();     // Track -> that branch record
    this.sidings = new Map();      // uid -> {track, line, sIn, sOut, sDock}
    /* How far outside the outermost bench each of a loading road's two switch
     * tips stands, measured along the branch. It was a literal `102` inside
     * `_loadingLoop`, and it is what decides whether the rake at the FIRST
     * stand hangs over the entry turnout.
     *
     * DERIVED, because 102 was the last absolute in this file's block story and
     * absolutes here go stale silently. A train stands with its head on its
     * stand and 84m of rake (`LINK_RAKE`) trailing back toward the entry, and
     * the entry's overlap reaches `leadClearRun(6, FOUL_CLEAR)` = 37.8m past
     * the tip, so the first bench has to be 121.8m along the road for the last
     * train home to be genuinely off the running line. At 102 it was at
     * 102–103.5m and the tail sat 18–20m inside the junction span
     * (`harness/rz-stand.mjs`: two stands on layout 0, one on layout 1, seven
     * on layout 2). It sat 9–11m inside before this round as well, when the
     * overlap was 28.6m — an old defect that the correct overlap made 9.2m
     * worse, not a new one. This is the failure `LINK_BLOCK_GAP` is written
     * for: a junction span under a parked train is a train `trains.js:_onRoad`
     * can never read as home.
     *
     * SWEPT BEFORE IT WAS CHANGED (`harness/rz-setback.mjs`, one page load, the
     * fleet re-planned and rail rebuilt at every step, all six of soak's own
     * layouts). `stationsServed` is the thing this can cost, because
     * `_loadingLoop` refuses a row whose tips will not stand on the straight:
     *
     *     setback   102   120   122   124   126   128   130   134   142
     *     stands over points
     *               2,1,7,5,6,1 ...  0,1,0,2,1,1  0 everywhere from 124
     *     stations served
     *               7,7,7,6,7,7 unchanged to 128 | 7,7,7,6,6,7 | 0,0,7,5,6,0
     *     branches / loops / exceptions   IDENTICAL at every step to 136
     *
     * So the window is 124–128 and it is free: nothing else in the plan moves
     * inside it. 130 costs layout 4 a station and 142 costs three layouts every
     * loading road they have. 125.8 is the derivation and it lands mid-window
     * with 2.2m of headroom above the knee — re-run the sweep if `LINK_RAKE`
     * or the loading road's frog ever changes, because the ceiling is a
     * property of the site and not of the arithmetic. */
    this.loadSetback = LINK_RAKE + leadClearRun(6, FOUL_CLEAR) + 4;
    this.rack = null;              // where a train stands to discharge
    this.rackS = NaN;              // ...as an arc length on the trunk
    this.loop = null;              // the second platform road at the terminal
    this.link = null;              // kept: older callers read it
    this._routes = new Map();
    this._cycles = new Map();
    this._meshes = [];
    this._geoms = [];
    this._struct = [];
    this._groundFinal = false;
    this.signals = [];
    this._occupied = new Map();
    this._detail = [];             // meshes the quality ladder may drop
    this._fine = [];               // meshes only ultra/high keep
    this._t = 0;
    this._dirty = false;
  }

  /* ---- lifecycle -------------------------------------------------------- */

  async build(plan) {
    this.ctx.scene.add(this.root);
    /* One parse, one dispatch: the station's own starting signal comes off and
     * stays off for as long as the train is on its road. Nothing else on this
     * railway moves, so this is the whole of its signalling — but it is a real
     * cause and a real effect rather than a light that blinks on a timer. */
    try {
      this.ctx.on?.('parse', ev => {
        const sig = this._stationSignal.get(ev?.uid);
        if (sig) sig.clearUntil = this._t + 22;
      });
    } catch { /* an event bus that refuses a listener is not fatal here */ }
    /* terrain.js re-grades the heightfield against this file's own declaration
     * and says so when it has finished. Every structure was drawn against the
     * ground as it stood BEFORE that, which is a different landform — see
     * `_reseatStructures`. Subscribed once, in `build`, rather than in
     * `_rebuild`: a listener added per relayout accumulates, and eight copies
     * of this handler is eight rebuilds of the same two meshes. */
    try { this.ctx.on?.('terrain:regraded', () => this._reseatStructures()); }
    catch { /* as above */ }
    this._stationSignal = new Map();
    this._safeRebuild(plan || this.ctx.plan);
  }

  onPlan(plan) { this._safeRebuild(plan); }

  /** Nothing about the permanent way is a rebuild risk worth taking on the
   *  render thread: a subsystem that throws mid-frame blanks the floor, and
   *  the floor is a status display before it is a rendering. */
  _safeRebuild(plan) {
    try {
      this._rebuild(plan);
    } catch (err) {
      console.warn('[rail] layout failed; the map continues without track', err);
      this._clear();
    }
  }

  onQuality(tier) {
    const name = tier?.name || 'ultra';
    const lean = name === 'low' || name === 'floor';
    const fine = name === 'ultra' || name === 'high';
    for (const m of this._detail) m.visible = !lean;
    for (const m of this._fine) m.visible = fine;
  }

  update(dt) {
    this._t += dt;
    if (this._aspectDue === undefined) this._aspectDue = 0;
    this._aspectDue -= dt;
    if (this._aspectDue <= 0) { this._aspectDue = 0.4; this._refreshAspects(); }
  }

  dispose() { this._clear(); this.root.parent?.remove(this.root); }

  /** Where the railway would like the ground to be.
   *
   *  A railway is cut as well as filled, and nothing in this file can excavate:
   *  `terrain.heightAt` is read-only to us and the terrain is built before the
   *  rail is. So the profile is pinned to a floor of "never below the ground it
   *  crosses", and what is left over — a median of 0.14m and a maximum of 1.5m
   *  of fill — is the price of that. It shows as a low bank under stretches
   *  that ought to be in shallow cutting.
   *
   *  This publishes the numbers that would close it: for every alignment, the
   *  centreline in plan and the level the BOTTOM OF THE BALLAST wants to sit at,
   *  sampled at the geometry step. Anything that can lower ground — see
   *  scratchpad/REQUESTS.md, 2026-08-07 — has everything it needs here, and
   *  needs nothing from the rest of this file.
   *
   *  `half` is the half-width that must be clear of the natural ground; past it
   *  the ballast batter is a drape and looks after itself. */
  formationCorridors() {
    const out = [];
    for (const t of this.tracks) {
      const f = t.frames;
      if (!f || !t.corridor) continue;   // roads on a slab own no ground here
      const pts = new Float32Array(f.count * 3);
      for (let i = 0; i < f.count; i++) {
        pts[i * 3] = f.pos[i * 3];
        pts[i * 3 + 1] = f.pos[i * 3 + 1] + BALLAST_TOE;
        pts[i * 3 + 2] = f.pos[i * 3 + 2];
      }
      out.push({name: t.name, count: f.count, step: f.step, points: pts,
                half: VERGE_X, batter: 1.5});
    }
    return out;
  }

  /** THE DECLARATION. What the railway needs the ground to be, span by span.
   *
   *  This is rail.js's half of the order of operations the two files now share:
   *
   *    1. terrain.js builds the natural landform, knowing nothing about rail;
   *    2. rail.js plans an alignment inside the geometry rules, preferring
   *       routes that need less earthwork;
   *    3. rail.js DECLARES its earthworks per chainage — this method;
   *    4. terrain.js applies the cut and fill with proper side slopes, so there
   *       is always earth under the track;
   *    5. rail.js builds the structures — decks, piers, abutments, portals;
   *    6. vegetation.js plants last, avoiding the finished formation.
   *
   *  Steps 4 and 6 are other people's files. This publishes what they need and
   *  nothing else: no opinion about how the ground gets there, no reproduction
   *  of anybody else's rule, and — unlike `formationCorridors()`, which said
   *  only "the formation wants to be here" — a KIND per span, because a cutting,
   *  an embankment, a tunnel and a bridge are four different things to build and
   *  three of them are not a change of height at all.
   *
   *  Each span:
   *
   *    {track, kind, from, to, length, maxDepth, half, batter, points}
   *
   *  `kind` is one of `cut` `fill` `tunnel` `viaduct` `bridge` `grade`.
   *  `from`/`to` are arc length in metres on that track; `points` is a
   *  Float32Array of `[x, y, z]` triples at the geometry step, where y is the
   *  FORMATION level — the top of the graded subgrade, which is where the ground
   *  has to be for the ballast to sit on it. `half` is the half-width that must
   *  be at that level (4.15m, out to the cess) and `batter` is the side slope
   *  beyond it: 1:1 in cut, 1:1.5 on fill.
   *
   *  A `grade` span needs nothing done. A `tunnel` or `bridge` span needs the
   *  ground LEFT ALONE — the structure spans it — and is published so terrain
   *  can avoid grading a corridor through a hill this railway is going under,
   *  which would leave a slot cut through a ridge with a tunnel mouth in it. */
  earthworks() {
    const out = [];
    for (const t of this.tracks) {
      if (!t.frames || !t.verge) continue;
      const f = t.frames;
      for (const w of t.earthworks()) {
        const n = Math.max(2, w.i1 - w.i0 + 1);
        const pts = new Float32Array(n * 3);
        for (let k = 0; k < n; k++) {
          const i = Math.min(f.count - 1, w.i0 + k);
          pts[k * 3] = f.pos[i * 3];
          pts[k * 3 + 1] = f.pos[i * 3 + 1] - FORMATION;
          pts[k * 3 + 2] = f.pos[i * 3 + 2];
        }
        out.push({track: w.track, kind: w.kind, from: w.from, to: w.to,
                  length: w.length, maxDepth: w.maxDepth, half: w.half,
                  batter: w.batter, step: f.step, points: pts});
      }
    }
    return out;
  }

  /** A one-line summary of the declaration, for the harness and for a person. */
  earthworkReport() {
    const tally = {};
    let worstCut = 0, worstFill = 0;
    for (const w of this.earthworks()) {
      const t = tally[w.kind] || (tally[w.kind] = {spans: 0, metres: 0, max: 0});
      t.spans++; t.metres += w.length; t.max = Math.max(t.max, w.maxDepth);
      if (w.kind === 'cut' || w.kind === 'tunnel') {
        worstCut = Math.max(worstCut, w.maxDepth);
      } else if (w.kind === 'fill' || w.kind === 'viaduct') {
        worstFill = Math.max(worstFill, w.maxDepth);
      }
    }
    const lines = [];
    for (const t of this.tracks) {
      if (!t.frames) continue;
      lines.push({track: t.name, klass: t.klass, length: t.length,
                  minRadius: t.minRadiusUsed, curves: t.curves,
                  minSpiral: t.spiralMin, ruling: t.ruling,
                  maxGrade: t.maxGrade, overGrade: t.overGrade,
                  vRadius: t.vRadius, maxCut: t.maxCut, maxFill: t.maxFill});
    }
    return {tally, worstCut, worstFill, lines,
            exceptions: this.exceptions || [], dead: this.deadTracks || [],
            passingLoops: this.passingLoops || {built: [], refused: []}};
  }

  /** Every track has to be part of at least one working, or it should not have
   *  been built.
   *
   *  Ryan: "there are two rails that go into no-where ... they shouldn't exist
   *  until that happens." The topology audit found the same thing from the other
   *  end: `terminal.loop` was 351.8m of correctly-handed, correctly-graded
   *  railway that no circuit on any of the ten soak layouts referenced. Track
   *  that nothing can reach is not scenery, it is a mistake with ballast on it,
   *  and it costs draw calls to say so.
   *
   *  This runs after the circuits are built and before the meshes are, so a road
   *  nothing uses is never drawn rather than drawn and apologised for. The yard
   *  spur is exempt and named: it is the reception road, and `yardRoute()` — the
   *  shunt move — is its consumer. */
  _auditTracks() {
    const used = new Set();
    for (const [uid] of this.sidings) {
      const cyc = this.cycle(uid);
      /* Every exit, not just the long way round. A passing loop is reached by
       * exactly one circuit — the variant that takes it — so counting only the
       * full-length lap would condemn the crossover as dead railway and delete
       * it, along with the two turnouts holding it on. */
      for (const c of cyc?.variants || (cyc ? [cyc] : [])) {
        for (const s of c.segments || []) used.add(s.track);
      }
    }
    if (this._spur) used.add(this._spur.name);
    this.deadTracks = this.tracks
      .filter(t => t.frames && !used.has(t.name))
      .map(t => t.name);
    if (this.deadTracks.length) {
      console.warn('[rail] built but reached by no working:',
                   this.deadTracks.join(', '));
      const dead = new Set(this.deadTracks);
      this.tracks = this.tracks.filter(t => !dead.has(t.name));
      this.lines = this.lines.filter(t => !dead.has(t.name));
      this._turnouts = this._turnouts.filter(r => !dead.has(r.track?.name) &&
                                                  !dead.has(r.child?.name));
      this.racks = (this.racks || []).filter(r => !dead.has(r.track?.name));
      if (this.loop && dead.has(this.loop.track?.name)) {
        this.loop = null; this.link = null;
      }
    }
    return this.deadTracks;
  }

  /* ---- what trains.js asks for ------------------------------------------ */

  /** Station → hub, on the top of the rail, on the centreline. */
  route(uid) {
    if (this._routes.has(uid)) return this._routes.get(uid);
    let r = null;
    try { r = this._buildRoute(uid); } catch (err) {
      console.warn('[rail] no route for', uid, err);
    }
    this._routes.set(uid, r);
    return r;
  }

  /** The whole working, as one closed one-way circuit: out of the bench's
   *  loading road, west along its branch, north up the ring's west side, east
   *  along the platform road under the rack, round the east corner, south down
   *  the return alignment, and back on to its own branch at the facing turnout
   *  it left the ring by — arriving at the loading road it started from, facing
   *  the way it faced when it left.
   *
   *  Two properties, and everything else on this railway rests on them.
   *
   *  It is CLOSED. The route's last point IS its first point and the tangent
   *  runs the same way through it, so a train's arc length simply wraps: it
   *  stands at the dock at s = 0 with its rake trailing back along the far end
   *  of the same array, pulls forward, and comes back to where it stood without
   *  anything being teleported, faded out or re-created.
   *
   *  It is ONE-WAY. No piece of track appears twice in a circuit with the
   *  direction reversed — `oneWayReport()` walks every circuit and checks it —
   *  so no two workings anywhere on this network can be pointed at each other.
   *  The branch does appear twice, because a working leaves the ring on to it
   *  and rejoins it later, but the two stretches are disjoint and both run the
   *  same way. That is the difference between a ring and a spur, and it is the
   *  reason the interlocking is now only asked to stop a train running into the
   *  back of a slower one rather than to prevent a head-on it cannot see
   *  coming.
   *
   *  `{route, terminal, loopExit, turned, line}`:
   *    terminal — arc length of the discharge stand under the gantry
   *    loopExit — arc length at which the working is past the terminal and on
   *               the return alignment. Kept for callers; nothing turns on it
   *               now that there is no direction to change.
   *    turned   — whether the working comes home without reversing. On a ring
   *               it always does; it is false only for the degenerate circuit a
   *               bench with no branch gets, and trains.js runs that one round
   *               with a run-round rather than propelling it back.
   *    variants — EVERY exit this bench can take, ordered by the arc length at
   *               which it leaves the loading road, earliest first, with
   *               `variants[variants.length - 1]` being this record's own
   *               full-length circuit. Each entry is the same shape as this one
   *               (route, closed, turned, line, segments, docks, terminals,
   *               terminal, loopExit, dockS), so a consumer picks the earliest
   *               one whose first block beyond the road is grantable, seats on
   *               to it, and everything downstream — `spans`, `blocksFor`,
   *               `blockSpans` — already works per circuit.
   *
   *               Two properties hold across the list and everything rests on
   *               them. The route arrays are IDENTICAL over the loading road up
   *               to the point where a variant leaves it, so two workings on one
   *               rank are still quoted in one coordinate and can still be
   *               compared by subtraction. And each carries its OWN `line` name,
   *               `branch0/x1`, so nothing compares arc lengths across two
   *               differently-shaped curves.
   */
  cycle(uid) {
    if (this._cycles.has(uid)) return this._cycles.get(uid);
    let out = null;
    try { out = this._buildCycle(uid); }
    catch (err) { console.warn('[rail] no working cycle for', uid, err); }
    this._cycles.set(uid, out);
    return out;
  }

  /** Every bench on a row works the SAME circuit.
   *
   *  It used to start at the bench's own stand, which made seven identical laps
   *  that happened to be rotated by a different amount each, and that rotation
   *  was quietly poisonous: two trains standing at two stands ninety metres
   *  apart both read s = 0, so arc length could not answer "is that train in
   *  front of me?" for the one pair of trains most likely to be in each other's
   *  way. Starting every lap at the loading road's entry turnout instead makes
   *  arc length a property of the ROAD rather than of the bench, so the stands
   *  are just different distances along one shared coordinate — which is what
   *  the queue on that road, and any signaller looking at it, actually needs.
   *
   *  The bench then contributes one number, `dockS`, and the circuit is built
   *  once per loading road instead of once per instrument.
   */
  _buildCycle(uid) {
    const sd = this.sidings.get(uid);
    if (!sd) return null;
    if (!this._circuits) this._circuits = new Map();
    const key = sd.track.name;
    let base = this._circuits.get(key);
    if (base === undefined) {
      base = this._buildCircuit(sd);
      this._circuits.set(key, base);
    }
    if (!base) return null;
    /* The bench's own spot under the rack, dealt by where it stands on the
     * loading road rather than at random — and in the one order that does not
     * jam. `docks` ascends in circuit arc length, so the bench with the LARGER
     * dockS has less railway in front of it and reaches the terminal first; it
     * must therefore take the spot further along, or it stops in front of the
     * one behind it and the second working can never get past. `terminals`
     * ascends too, so the two indices agree and no comparison is needed.
     *
     * The deal is taken from the FULL circuit and then applied to every variant.
     * A variant only carries the docks it actually reaches, so dealing off its
     * own list would send one bench to two different spots depending on which
     * way it happened to leave the road — and the whole point of the deal is
     * that two workings do not want the same piece of platform. */
    const {variants: raw, ...core} = base;
    const k = Math.max(0, core.docks.findIndex(d => d.uid === uid));
    const deal = c => {
      const mine = c.docks.find(d => d.uid === uid);
      const T = c.terminals;
      return {...c, dockS: mine ? mine.s : 0,
              terminal: T?.length ? T[k % T.length] : c.terminal};
    };
    const out = deal(core);
    /* Every exit this bench can take, earliest first, the full-length circuit
     * last — the shape trains.js asked for. A variant that does not reach this
     * bench's stand is not an exit it has: the crossover is in front of it. */
    const vs = (raw || []).filter(v => v.docks.some(d => d.uid === uid))
                          .map(deal);
    vs.push(deal(core));
    out.variants = vs;
    return out;
  }

  /** Arc length on a circuit of a point quoted as (track, s). */
  _arcOf(route, segs, trackName, s) {
    for (const seg of segs) {
      if (seg.track !== trackName) continue;
      const lo = Math.min(seg.s0, seg.s1), hi = Math.max(seg.s0, seg.s1);
      if (s < lo - 0.5 || s > hi + 0.5) continue;
      const a = route.acc[seg.from], b = route.acc[seg.to];
      const span = seg.s1 - seg.s0 || 1;
      return a + (b - a) * Math.min(1, Math.max(0, (s - seg.s0) / span));
    }
    return null;
  }

  _buildCircuit(sd) {
    const line = sd.line;                       // the row's branch
    const br = this.branchOf.get(line);
    const trunk = this.trunk;
    /* A branch has two ends on the ring, and both of them are what makes the
     * circuit a circuit. Without one there is no ring to run round and the
     * bench gets the degenerate out-and-back below instead. */
    const ringed = !!(trunk && br && br.teS !== undefined &&
                      br.teS > br.tS && isFinite(this.rackS));
    let pts = [];
    let marks = {};
    let segs = [];
    const push = (list, tag, seg) => {
      if (!list || !list.length) return;
      if (tag) marks[tag] = pts.length;
      const at0 = pts.length;
      for (const p of list) {
        const last = pts[pts.length - 1];
        if (last && p.distanceToSquared(last) < 0.04) continue;
        pts.push(p);
      }
      /* What physical railway that stretch of the working actually stands on.
       * A route is a path through the network, not a piece of it, and two
       * workings out of different benches share the trunk without sharing a
       * single arc length — so a signaller (and the soak) needs the mapping
       * back to the track, not just the shape. */
      if (seg && pts.length > at0) {
        segs.push({track: seg[0].name, from: at0, to: pts.length - 1,
                   s0: seg[1], s1: seg[2]});
      }
    };
    /* The loading road, entry turnout to wherever this lap leaves it, with every
     * stand up to there on it. It is one road and it is traversed one way, so
     * the stands come out as ascending arc lengths on the circuit and a train
     * can be asked, in one subtraction, whether another is in front of it.
     *
     * Sampled at a FIXED step from the entry turnout rather than at n equal
     * parts of the span, and that is the whole reason the passing loops can
     * exist. A lap that leaves the road at a mid-rank crossover covers a PREFIX
     * of the same railway; sampling by span would give it different points at
     * different arc lengths, and `trains.js:_berth` — the rule that keeps two
     * trains on one road apart — would then be subtracting arc lengths quoted on
     * two different curves, which is silently meaningless. Fixed-step makes the
     * shared stretch identical to the float on every variant. */
    const roadTo = s => this._sliceAt(sd.track, sd.entryS, s, ROUTE_STEP);
    push(roadTo(sd.exitS), null, [sd.track, sd.entryS, sd.exitS]);
    const docksOf = (route, segments) => (sd.row?.list || [])
      .map(st => ({uid: st.uid,
                   s: this._arcOf(route, segments, sd.track.name,
                                  sd.track.nearest(st.x, sd.dockZ).s)}))
      .filter(d => d.s !== null && isFinite(d.s))
      .sort((a, b) => a.s - b.s);
    if (!ringed) {
      /* No ring within reach — a bench on a row the site could not give a
       * branch. The outbound path is the whole route and trains.js turns the
       * working at the end of it with a run-round. It is the one path on this
       * railway that is still worked both ways, it exists so that no instrument
       * is ever shown without a train, and no layout the soak drives reaches
       * it. */
      const end = br ? br.jS : Math.min(line.renderTo, line.length) - 8;
      push(this._slice(line, sd.sOut, end), 'rack', [line, sd.sOut, end]);
      if (br && trunk) {
        push(this._slice(trunk, br.tS, this.rackS), null,
             [trunk, br.tS, this.rackS]);
      }
      if (pts.length < 4) return null;
      const route = new PolyRoute(pts);
      route.segments = segs;
      return {route, closed: false, turned: false, line: line.name,
              segments: segs, docks: docksOf(route, segs),
              terminal: Math.max(20, route.length - 10),
              loopExit: route.length};
    }
    /* The terminal, on whichever of the two platform roads this row is dealt.
     *
     * Taking the second road is three slices instead of one, and every one of
     * them still counts UP: `br.tS < loop.sIn` and `loop.sOut < br.teS` are
     * checked rather than assumed, because a facing turnout the working would
     * have to pass before it reached its own would be a circuit that runs
     * backwards over forty metres of ring, and `oneWayReport` would be right to
     * fail it. When the check does not hold — a layout whose branch junctions
     * fall inside the terminal throat — the working simply keeps the first road,
     * which is the behaviour this railway had for every working. */
    const stand = this._standFor(sd);
    const loop = stand?.via;
    const viaLoop = !!(loop?.track?.frames &&
                       br.tS < loop.sIn - 20 && loop.sOut < br.teS - 20);
    const useSpots = stand?.spots?.length && (stand.via ? viaLoop : true);
    const spots = useSpots ? stand.spots.map(r => r.point) : [this.rack];

    /* ONE LAP, leaving the loading road by `exit`.
     *
     * `exit` is either the road's own exit turnout — the circuit this railway
     * has always had — or a mid-rank crossover, in which case the lap covers a
     * PREFIX of the road, crosses to the branch, and joins the same run out to
     * the ring further along. Everything after the branch is byte-for-byte the
     * same railway on every variant, and everything before the crossover is
     * byte-for-byte the same road: those two facts are what let trains.js
     * compare two workings on one rank at all.
     *
     * Out along the branch to the ring's west leg, all the way round the top,
     * and back down the east leg to the branch's own facing turnout. Every one
     * of these slices counts UP: `exit.lineS < br.jS`, `br.tS < br.teS` (the
     * west leg is laid before the east one), `br.eS < sd.sIn`. There is no
     * slice anywhere on any of these circuits whose s1 is less than its s0,
     * which is the one-way property stated as arithmetic rather than as an
     * intention — and `oneWayReport` now walks the variants too. */
    const lay = exit => {
      pts = []; marks = {}; segs = [];
      push(roadTo(exit.roadS), null, [sd.track, sd.entryS, exit.roadS]);
      if (exit.link) {
        push(this._slice(exit.link, 0, exit.link.length), null,
             [exit.link, 0, exit.link.length]);
      }
      push(this._slice(line, exit.lineS, br.jS), null, [line, exit.lineS, br.jS]);
      if (viaLoop) {
        push(this._slice(trunk, br.tS, loop.sIn), 'rack',
             [trunk, br.tS, loop.sIn]);
        push(this._slice(loop.track, 0, loop.track.length), null,
             [loop.track, 0, loop.track.length]);
        push(this._slice(trunk, loop.sOut, br.teS), null,
             [trunk, loop.sOut, br.teS]);
      } else {
        push(this._slice(trunk, br.tS, br.teS), 'rack', [trunk, br.tS, br.teS]);
      }
      const trunkEnd = pts.length;
      push(this._slice(line, br.eS, sd.sIn), null, [line, br.eS, sd.sIn]);
      if (pts.length < 8) return null;
      /* Close it exactly. The last point is the entry turnout's tip on the
       * branch and the first is the same turnout's tip on the loading road —
       * the metre and a half the turnout artwork covers — and leaving the float
       * in would show as a kink once a lap. */
      const P = pts;
      P[P.length - 1] = P[0].clone();
      const route = new PolyRoute(P);
      const segments = segs;
      route.segments = segments;
      /* Where the trains stand to discharge: for each spot on this circuit's
       * road, the point on the terminal run nearest it. Found rather than
       * counted, because how much railway there is before it depends on where
       * the row is — and because the stand is no longer the same point for
       * every working. `_buildCycle` deals the benches across them. */
      const rack0 = marks.rack ?? 0;
      const terminals = spots.map(target => {
        let best = rack0, bestD = Infinity;
        for (let i = rack0; i < trunkEnd; i++) {
          const d = P[i].distanceToSquared(target);
          if (d < bestD) { bestD = d; best = i; }
        }
        return route.acc[best];
      }).sort((a, b) => a - b);
      return {route, closed: true, turned: true, line: exit.name,
              segments, docks: docksOf(route, segments), terminals,
              terminal: terminals[terminals.length - 1],
              loopExit: route.acc[Math.min(P.length - 1, trunkEnd - 1)]};
    };

    const full = lay({roadS: sd.exitS, lineS: sd.sOut, name: line.name});
    if (!full) return null;
    /* The variants, earliest exit first. Each carries its OWN `line` name —
     * `branch0/x1` — for the same reason `_runRound` relabels: `soak.mjs` gates
     * its one-line collision check on the name and then compares occupied
     * intervals, and two workings on differently-shaped routes sharing a name
     * would have incomparable arc lengths compared, which invents collisions on
     * a sound railway and can mask real ones. A distinct name makes the harness
     * fall through to the world-space fouling test, which is correct across
     * variants. */
    const links = (sd.track.links || [])
      .filter(k => k.track?.frames && k.roadS > 0 && k.roadS < sd.exitS &&
                   k.lineS > sd.sIn && k.lineS < br.jS)
      .sort((a, b) => a.roadS - b.roadS);
    full.variants = links
      .map((k, i) => lay({roadS: k.roadS, lineS: k.lineS, link: k.track,
                          name: `${line.name}/x${i + 1}`}))
      .filter(Boolean);
    return full;
  }

  /** Does any working run over any piece of track in both directions?
   *
   *  This is the property the whole rebuild exists to establish, so it is
   *  checkable from the console on any layout rather than asserted in prose:
   *
   *      __lemWorld.subsystems.get('rail').oneWayReport()
   *
   *  For every circuit, every stretch of every track it is laid on is reduced
   *  to a direction — `s1 > s0` or not — and a track that comes back with both
   *  is a conflict. It also reports whether each circuit CLOSES, because a
   *  one-way route that does not close is a train that runs out of railway.
   *
   *  `overlaps` is the stricter half. A branch legitimately appears twice in
   *  one circuit — the working leaves the ring on to it and rejoins it later —
   *  and both stretches run the same way, so a direction test alone would pass
   *  a route that ran over the same forty metres twice. These are the arc
   *  ranges that genuinely intersect. */
  oneWayReport() {
    const out = {circuits: 0, conflicts: [], open: [], overlaps: [], tracks: 0};
    const seen = new Set();
    for (const [uid, sd] of this.sidings) {
      if (seen.has(sd.track.name)) continue;
      seen.add(sd.track.name);
      let cyc = null;
      try { cyc = this.cycle(uid); } catch { cyc = null; }
      if (!cyc?.segments) continue;
      /* Every exit gets judged, not just the long way round. A passing loop is
       * a second circuit over the same railway and it is exactly the kind of
       * addition that can put a branch under traffic in two directions; a check
       * that only ever looked at the full lap would not have seen it. */
      for (const c of cyc.variants || [cyc]) {
      out.circuits++;
      const dirs = new Map();               // track -> {up, down, spans}
      for (const seg of c.segments) {
        let d = dirs.get(seg.track);
        if (!d) { d = {up: 0, down: 0, spans: []}; dirs.set(seg.track, d); }
        if (seg.s1 >= seg.s0) d.up++; else d.down++;
        d.spans.push([Math.min(seg.s0, seg.s1), Math.max(seg.s0, seg.s1)]);
      }
      out.tracks += dirs.size;
      for (const [name, d] of dirs) {
        if (d.up && d.down) {
          out.conflicts.push(`${c.line}: ${name} run ${d.up} up / ` +
                             `${d.down} down in one circuit`);
        }
        for (let i = 0; i < d.spans.length; i++) {
          for (let j = i + 1; j < d.spans.length; j++) {
            const a = d.spans[i], b = d.spans[j];
            const ov = Math.min(a[1], b[1]) - Math.max(a[0], b[0]);
            if (ov > 1) {
              out.overlaps.push(`${c.line}: ${name} covered twice over ` +
                                `${ov.toFixed(1)}m`);
            }
          }
        }
      }
      if (!c.closed) out.open.push(c.line);
      else {
        const p = c.route.points;
        const gap = p[0].distanceTo(p[p.length - 1]);
        if (gap > 0.05) out.open.push(`${c.line} closes ${gap.toFixed(3)}m short`);
      }
      }
    }
    /* And the hand of every junction on the ring itself, because a circuit can
     * be perfectly one-way and still be drawn with a turnout the wrong way
     * round — the route would run through the frog toward the switch tip, which
     * is a train setting back through a facing point. `pdir` is the sense the
     * lead runs from its tip; traffic on the ring runs with s increasing, so
     * `facing` means a train leaves the ring here and `trailing` means one
     * joins it. Every branch must have exactly one of each. */
    const hands = new Map();
    for (const rec of this._turnouts || []) {
      if (rec.track !== this.trunk) continue;
      const b = this.branchOf.get(rec.child);
      if (!b) continue;
      const k = b.track.name;
      if (!hands.has(k)) hands.set(k, {facing: 0, trailing: 0});
      hands.get(k)[rec.pdir > 0 ? 'facing' : 'trailing']++;
    }
    out.junctions = [];
    for (const [name, h] of hands) {
      out.junctions.push(`${name} ${h.facing}f/${h.trailing}t`);
      if (h.facing !== 1 || h.trailing !== 1) {
        out.conflicts.push(`${name} joins the ring ${h.facing} facing / ` +
                           `${h.trailing} trailing — a ring wants one of each`);
      }
    }
    out.oneWay = out.conflicts.length === 0 && out.overlaps.length === 0;
    out.closed = out.open.length === 0;
    return out;
  }

  /** A spare road at the terminal, for a cut of tanks being tripped up and
   *  down while nothing is parsing. */
  yardRoute() {
    if (this._yardRoute !== undefined) return this._yardRoute;
    /* From where the spur is genuinely its own track. Slicing from zero would
     * hand back the length of it that is still lying on its parent, and a cut
     * of tanks shunting up and down the loop's own rails is worse than none. */
    this._yardRoute = this._spur
      ? this._slice(this._spur, this._spur.renderFrom || 0, this._spur.length)
      : null;
    return this._yardRoute;
  }

  /** Where a train is, now. Called every frame by trains.js for every working
   *  on the road; the aspects are read off this and nothing else, so a red is
   *  a red because there is metal in front of it. */
  occupy(id, t) {
    this._occupied.set(id, t);
    this._dirty = true;
  }

  release(id) {
    if (this._occupied.delete(id)) this._dirty = true;
  }

  /** Whether that bench's own working is out on the road. A starter that
   *  clears on a timer is a light on a timer; this one clears because a train
   *  went, and goes back to danger when it is standing in the loop again —
   *  which is the difference between signalling and decoration. The parse timer
   *  stays as the fallback for a floor with no trains.js loaded. */
  starter(uid, running) {
    const sig = this._stationSignal.get(uid);
    if (!sig || sig.running === !!running) return;
    sig.running = !!running;
    this._dirty = true;
  }

  /* ---- layout ------------------------------------------------------------
   *
   * Everything below reads two promises the rest of the site already made:
   * buildings.js docks a train 26m north of a station on an east–west line,
   * and terrain.js grades one tilted plane over the whole block plus a
   * corridor to the hub. So a running line goes past the docks, the loading
   * siding is the road under the gantry that is already standing there, and
   * the way out is west then north — which is the only way to arrive at the
   * terminal's platform road running the way it runs.
   *
   * ---- why this is a trunk with branches, and not N parallel roads --------
   *
   * It used to build at most TWO through roads and hang every row of benches
   * beyond the second on the outer one. On the lab's real floor — two rows —
   * that was invisible. On any other arrangement it was the whole problem:
   * the soak drives a single long FILE of instruments, which is seven rows, and
   * five of those rows were being served by a line ninety to four hundred and
   * fifty metres away in z. `_siding` dutifully drew a control polyline from a
   * point on that line, out to the bench, and back — a quarter-kilometre S-bend
   * across open ground with a turnout at each end. Every station was reachable.
   * None of it was a railway.
   *
   * A railway solves that the way railways have always solved it. There is one
   * TRUNK, running north–south down the west side of the site and turning east
   * into the terminal's platform road, and every row of benches gets its own
   * east–west BRANCH which curves north at the west end and joins the trunk at
   * a turnout. Rows are ordered by distance to the terminal, so the nearest row
   * joins nearest the terminal and no branch has to cross another to reach it —
   * that is a property of the ordering, not something checked afterwards.
   *
   * The consequences are the point:
   *   - the topology no longer depends on how many rows there are, so a rank, a
   *     file, a scatter and the real lab are all the same network with a
   *     different number of branches;
   *   - every bench's working reaches the one rack, and reaches it by running
   *     round the ring rather than by setting back out of a dead end;
   *   - the trunk is where two workings off different rows genuinely meet, so
   *     it is a real place to signal rather than a decorative one.
   */
  _rebuild(plan) {
    this._clear();
    if (!plan?.stations?.length || !plan.hub) return;
    const ctx = this.ctx;
    const ground = ctx.ground;
    const hub = plan.hub;
    /* Where the water is, read from the file that owns it rather than guessed.
     * A guess here is not a small error: the whole difference between a bridge
     * and a causeway is the question "is this chainage wet", and terrain.js
     * answers it exactly. Absent — solo, or before terrain builds — there is no
     * water anywhere and no crossing can be declared, which is the right answer
     * for a world with no sea in it rather than a fallback. */
    const t3 = ctx?.world?.subsystems?.get?.('terrain');
    this.waterY = Number.isFinite(t3?.waterY) ? t3.waterY : -Infinity;
    this.exceptions = [];
    this.passingLoops = {built: [], refused: []};

    /* rows, nearest the hub first — the nearest one joins the trunk last, i.e.
     * closest to the terminal, which is what keeps branches from crossing. */
    const rows = new Map();
    for (const s of plan.stations) {
      const k = Math.round(s.z / 8);
      if (!rows.has(k)) rows.set(k, []);
      rows.get(k).push(s);
    }
    const order = [...rows.values()]
      .map(list => ({z: list.reduce((a, b) => a + b.z, 0) / list.length, list}))
      .sort((a, b) => Math.abs(a.z - hub.z) - Math.abs(b.z - hub.z));

    let minX = Infinity, maxX = -Infinity, maxZ = -Infinity;
    for (const s of plan.stations) {
      minX = Math.min(minX, s.x); maxX = Math.max(maxX, s.x);
      maxZ = Math.max(maxZ, s.z);
    }
    /* The west side of the site is the OUTBOUND alignment, and that much is a
     * terrain decision rather than a drawing one: terrain.js drops its valley
     * away to the east, so the ground west of the site stays on the same shelf
     * the lab is graded into and rolls by a few metres over hundreds.
     *
     * 205m and not 270. The extra 65 was the balloon loop's — its exit leg
     * reached ~186m west of the terminal before it was clear of the platform
     * road — and there is no balloon any more. It is not tidiness: every metre
     * the west leg stands out from the site is a metre each working runs the
     * wrong way before it turns for the terminal, and on the lab's own floor
     * that detour was a third of the outbound run. What is left is what the
     * branches need: their throat curve has to open out before the first
     * bench's loading-road turnout. */
    const ZY = hub.z + DOCK_OFFSET;          // the terminal's platform road
    const southEnd0 = Math.max(ZY + 250,
                               (maxZ - DOCK_OFFSET - LOOP_OFFSET) - 72 + 140);
    /* How near the site a leg may stand: the loading road's outermost turnout
     * needs 124m of clear line beyond the last bench, and the branch's throat
     * curve puts its vertex 0.86·R further out again. Anything closer and
     * `_branch` refuses the row, which is a worse answer than a longer leg. */
    const legIn = 124 + 0.86 * R_MIN_YARD + 24;
    const WX = this._legCorridor(ground, Math.min(minX - legIn, hub.x - 210),
                                 -1, ZY, southEnd0, order);
    /* And the east side is the RETURN alignment. It is not a fixed offset,
     * because east is the side the valley falls away on and a corridor picked
     * with a ruler lands in the river on a wide site. It is chosen by walking
     * the ground: six candidate corridors, scored by the relief along each, and
     * the quietest one wins. Measured across the ten soak layouts the corridor
     * it picks crosses ground at 0.06–0.27, which is the same order as the west
     * trunk's 0.075–0.167 — the return line is no worse a piece of railway than
     * the outbound one, on any layout. */
    const EX = this._legCorridor(ground, Math.max(maxX + legIn, hub.x + 210),
                                 1, ZY, southEnd0, order);
    /* One headshunt at each end, measured past the LAST JUNCTION rather than
     * past the last bench. Both legs of the ring now leave their branches
     * within ~96m of the row they serve, so a trunk carried on to the south
     * edge of the site would be a couple of hundred metres of railway at each
     * corner that nothing on the network can reach. 140m is a train and a
     * signal's worth of standage, and the buffer stops go on the end of it. */
    const southEnd = southEnd0;

    /* ---- the ring ---------------------------------------------------------
     *
     * One alignment, three legs and two corners: north up the west side, east
     * along the terminal's platform road, and south down the east side. Arc
     * length increases the whole way round, and every working joins it on the
     * west leg and leaves it on the east — so `main` is traversed in ONE
     * direction by every train on the railway, and the two ends left over are
     * headshunts with buffer stops on them.
     *
     * That is the whole reason for the shape. It used to be an out-and-back:
     * up the west side to the terminal, round a balloon, and back down the same
     * rails. Every metre of trunk carried traffic both ways, which is what made
     * single-line token working necessary and what made a head-on collision a
     * thing the interlocking had to be right about rather than a thing the
     * track could not express. On a one-way circuit two workings physically
     * cannot meet, and the block reservation is left doing the one job it is
     * genuinely good at: keeping a train off the back of a slower one. */
    /* ---- how big the ring's two corners may be ----------------------------
     *
     * This is the one radius on the railway the SITE decides rather than the
     * rules, and it is worth writing down exactly why, because the answer on
     * the lab's own floor is below the running-line minimum and that is not a
     * thing to discover from a screenshot.
     *
     * The corner turns the west leg through 90° on to the platform road. A 90°
     * fillet of radius R consumes R of the leg before the tangent point, so the
     * straight the first branch can join is R south of the platform road. That
     * branch then needs, in the same stretch of z: the turnout's own lead
     * (~22m), and its throat curve, which is another 90° and therefore another
     * ~0.83·R_branch of z at the yard minimum. All of it has to fit between the
     * platform road and the nearest row's running line — a distance the PLAN
     * fixes, not this file, because index.js derives the hub's position from
     * that row.
     *
     * On the lab's seven-instrument floor that distance is 124.4m, and
     * 90 + 22 + 46 is 158. So a 90m running-line curve at the terminal and a
     * branch to the nearest row cannot both exist there, and the branch is
     * worth more: without it the row nearest the terminal has no railway at
     * all. The corner takes what is left, the shortfall is recorded in
     * `this.exceptions`, and the ask that would remove it — 236m between the
     * platform road and the nearest row — is in scratchpad/REQUESTS.md.
     *
     * On a site with room, this returns 90 or better and there is no
     * exception. It is measured, not assumed. */
    const nearRunZ = (order[0]?.z ?? (ZY + 400)) - DOCK_OFFSET - LOOP_OFFSET;
    const legN = Math.max(0, southEnd - ZY);
    const legE = Math.max(0, EX - WX);
    const RING_MAX = 180;
    /* The arithmetic, once, so both radii come out of one budget. A 90° fillet
     * consumes its own radius of leg before its tangent point; the turnout that
     * follows consumes LEAD_Z; and the branch's throat curve, which turns the
     * remaining ~80° after the lead has already given it 9.5°, consumes 0.835 of
     * ITS radius. `TURN_Z` is that 0.835 — sin 90° − sin 9.5°, integrated along
     * the curve, and it is where every clearance on this railway comes from.
     *
     * Splitting the budget so the two come out EQUAL is deliberate: the number
     * a track plan is judged by is the smallest radius on it, so on a site with
     * less room than the rules want, the best available answer is the one where
     * nothing is the weakest link. */
    const TURN_Z = 0.835;
    /* Ten metres of slack, not four. Both ends of this budget are consumed by
     * clearances that are themselves approximations — the fillet's tangent
     * distance carries a spiral shift, and the lead's exit is not exactly
     * LEAD_Z of z — and with four the two came out equal to the gap to the
     * third decimal, so the nearest row's branch was refused by a rounding
     * error and the whole railway with it. */
    const budget = (nearRunZ - ZY) - 9 - LEAD_Z - 10;
    let ringR = Math.min(RING_MAX, budget / (1 + TURN_Z),
                         legN - 24, legE * 0.5 - 24);
    ringR = Math.max(30, ringR);
    this.ringR = ringR;
    /* And what a branch may therefore ask of the z between its junction and its
     * row. Derived rather than the constant this used to be, so a site with room
     * gets a proper throat instead of the 72m that was measured off the lab's
     * own floor and then applied to every layout in the soak. */
    this.throatMin = LEAD_Z + TURN_Z * Math.min(ringR, R_MIN_YARD) + 4;
    this.turnZ = TURN_Z;
    if (ringR < R_MIN_RUN) {
      this.exceptions.push({
        track: 'main', rule: 'minimum radius', want: R_MIN_RUN,
        got: +ringR.toFixed(1),
        why: `the platform road is ${(nearRunZ - ZY).toFixed(0)}m from the ` +
             `nearest row's running line. A ${R_MIN_RUN}m corner, a 1:6 lead ` +
             `and a ${R_MIN_RUN}m branch throat need ` +
             `${(R_MIN_RUN * (1 + TURN_Z) + LEAD_Z + 13).toFixed(0)}m, and the ` +
             `hub's position is set by index.js from that row`,
      });
    }
    const trunk = new Track('main', [
      [WX, southEnd], [WX, ZY], [EX, ZY], [EX, southEnd],
    ], {radius: ringR, hardFloor: Math.min(ringR * 0.94, R_MIN_RUN),
        maxGrade: GRADE_RULING, waterY: this.waterY, vCurve: 24});
    trunk.build(ground);
    if (!trunk.frames) return;
    this.trunk = trunk;
    this.lines.push(trunk);
    this.tracks.push(trunk);
    /* Where a train stands to discharge: under buildings.js's 130m loading
     * gantry, which straddles the platform road. Quoted as a point so the
     * circuit builder finds it by arc length rather than by counting legs. */
    this.rack = new THREE.Vector3(hub.x + 56, 0, ZY);
    this.rackS = trunk.nearest(this.rack.x, this.rack.z).s;

    const turnouts = [];
    let prevW = -Infinity, prevE = -Infinity;
    for (let j = 0; j < order.length; j++) {
      const br = this._branch(trunk, order[j], j, ground, WX, EX, ZY,
                              prevW, prevE);
      if (br) {
        this.branches.push(br); turnouts.push(...br.turnouts);
        prevW = br.zW; prevE = br.zE;
      }
    }
    if (!this.branches.length) {
      /* No row could be given a branch — a fleet packed into the corridor the
       * trunk itself uses. Rather than a railway with nothing on it, fall back
       * to serving every bench off the trunk directly. */
      const only = {z: order[0].z, list: plan.stations};
      const br = this._branch(trunk, only, 0, ground, WX, EX, ZY,
                              -Infinity, -Infinity);
      if (br) { this.branches.push(br); turnouts.push(...br.turnouts); }
    }

    /* The loading road: one per ROW, along the whole line of docks. */
    for (const br of this.branches) {
      const sd = this._loadingLoop(br.track, br.row, ground);
      if (sd) turnouts.push(...sd.turnouts);
    }

    /* The second platform road: a parallel road through the terminal, worked
     * the same way round as the one beside it. There is no balloon any more and
     * there is nothing left for one to do — the ring turns every working
     * without a single reverse move, and a loop hung off a one-way circuit
     * would be track no train could ever have a reason to take. Nothing below
     * may throw: `_safeRebuild` would drop the whole permanent way on the
     * floor. */
    const loop = this._terminalLoop(trunk, hub, ground, WX, EX);
    if (loop) turnouts.push(...loop.turnouts);

    /* The reception road: somewhere a cut of tanks can stand at the terminal
     * while the road beside it is in use. */
    const spur = this._yardSpur(trunk, hub, ground);
    if (spur) turnouts.push(...spur.turnouts);

    /* ---- and the ring is cut back to its headshunts ------------------------
     *
     * Every junction on the trunk exists by now, so the question "where does
     * this railway stop being used" finally has an answer. It is asked here
     * and nowhere else: the ALIGNMENT is left exactly as surveyed — the
     * profile, the gradient and both corridors are already fitted to it, and
     * moving a control point now would re-open all three — and only the LAID
     * extent is trimmed. Arc length is untouched, so every route, block,
     * stand and junction quoted anywhere else in this file still means what
     * it meant.
     *
     * `stopEnds` says the two ends are dead ends on purpose, because once
     * `renderFrom` is not zero the drawing can no longer tell a deliberate
     * end from a piece of alignment lying on a parent, and a buffer stop
     * planted at a junction is the single most obviously wrong thing a
     * railway can be drawn with. */
    if (trunk?.frames) {
      const js = (this._turnouts || [])
        .filter(r => r.track === trunk && Number.isFinite(r.s)).map(r => r.s);
      if (js.length) {
        const lo = Math.max(0, Math.min(...js) - HEADSHUNT);
        const hi = Math.min(trunk.length, Math.max(...js) + HEADSHUNT);
        if (hi - lo > 240) {
          this.headshunts = {from: lo, to: hi,
                             trimmed: lo + (trunk.length - hi)};
          trunk.renderFrom = lo;
          trunk.renderTo = hi;
          trunk.stopEnds = ['lo', 'hi'];
          trunk._works = null;         // the declaration follows the drawing
        }
      }
    }

    this._buildStands();

    this._sectionBlocks();
    /* Nothing is drawn before the network has been asked whether it can be
     * worked. The audit builds every circuit, so it is also the first place a
     * broken one would surface — and it runs here, before the meshes, so a road
     * no working reaches is never laid rather than laid and then explained. */
    try {
      const dead = new Set(this._auditTracks());
      if (dead.size) {
        for (let i = turnouts.length - 1; i >= 0; i--) {
          if (dead.has(turnouts[i].child?.name) ||
              dead.has(turnouts[i].track?.name)) turnouts.splice(i, 1);
        }
        this._routes.clear();
        this._cycles.clear();
      }
    } catch (err) { console.warn('[rail] track audit failed', err); }
    /* The interlocking's own view of the railway. It is worked out here, once,
     * rather than the first time a train asks — a train asks in the middle of a
     * frame, and a throw there takes the render loop with it. */
    try { this._analyseBlocks(); }
    catch (err) {
      console.warn('[rail] block analysis failed; single-line runs unknown', err);
      this._runOf = new Map(); this._runBlocks = new Map();
    }
    this._buildMeshes(turnouts, ground);
    this._buildStructures();
    this._placeTrackside();
    /* And the declaration goes out. It is published on `ctx` as well as emitted
     * because terrain builds BEFORE rail: a subsystem that only listened would
     * have to wait for a relayout to hear anything, and one that only read the
     * field would have to poll. Emitting is the live channel and the field is
     * the record — whoever grades the ground can use either. */
    try {
      ctx.railEarthworks = this.earthworks();
      ctx.emit?.('rail:earthworks',
                 {spans: ctx.railEarthworks,
                  report: this.earthworkReport()});
    } catch (err) { console.warn('[rail] could not publish earthworks', err); }
    if (ctx.engine) ctx.engine.shadowNeedsUpdate = true;
  }

  /** The discharge stands, and why there is now more than one of them.
   *
   *  ---- one road, one stand, seven benches --------------------------------
   *
   *  The topology audit put it plainly: every circuit's `terminal` resolved to
   *  the same world point, so seven workings shared one road and one stand
   *  under the rack. That is a throughput ceiling written into the plan — not
   *  something the signalling can relieve, because the second train's problem
   *  is not permission, it is that the place it is going is occupied — and it
   *  is why workings queued on the ring.
   *
   *  The same audit found `terminal.loop` — 351.8m, correctly handed at both
   *  ends, built on every layout — referenced by no circuit at all. The two
   *  faults are each other's answer. The second platform road is a road under
   *  the same gantry: give it a stand and route half the rows over it, and the
   *  terminal discharges two trains at once with nothing new built.
   *
   *  Rows are dealt round-robin by branch index, so on the lab's own two-row
   *  floor one row takes each road, and a rank of seven alternates. It is
   *  deliberately not by *bench*: every bench on a row shares one circuit (see
   *  `_buildCycle`), and splitting a row across two roads would mean two
   *  circuits per road and arc length would stop being a property of the road.
   */
  _buildStands() {
    this.racks = [];
    const trunk = this.trunk;
    if (!trunk?.frames || !isFinite(this.rackS)) return;
    /* Two SPOTS on each road, not one.
     *
     * A rack road holds a cut, not a train: the first working pulls right up
     * under the far end of the gantry and the second stops behind it, and both
     * discharge. Measured on the lab's own floor before this, a road turned
     * over one working every 48 seconds — 34 to run in and 14 to unload — and
     * every other bench on that row stood on the branch waiting for it. That is
     * what "one discharge stand for the whole railway" costs.
     *
     * 42m either side of the middle of the rack. buildings.js's gantry is 134m
     * of steel over the platform road, so both spots are genuinely under it,
     * and 84m apart is the longest consist trains.js builds — the working
     * behind stands clear of the one in front rather than into the back of it.
     *
     * Order is not controlled and does not need to be: the circuit is one-way,
     * so a working sent to the far spot while the near one is occupied is held
     * by the block behind it exactly as it is today, and nothing is worse than
     * the single stand. When they arrive in the other order — which is half the
     * time — the terminal is working two trains at once. */
    const road = (track, s, via) => {
      const lo = (track.renderFrom || 0) + 24;
      const hi = Math.min(track.renderTo, track.length) - 24;
      if (!(hi > lo)) return;
      for (const d of [42, -42]) {
        const at = track.at(Math.min(hi, Math.max(lo, s + d)));
        this.racks.push({point: at.position.clone(), via, track});
      }
    };
    road(trunk, this.rackS, null);
    const loop = this.loop;
    if (!loop?.track?.frames) return;
    /* The point on the second road abreast of the rack. Found by asking the
     * road rather than by offsetting the first stand sideways: the two roads
     * are parallel through the terminal and not anywhere else, and a stand
     * quoted in metres off a centreline lands in the four-foot at the throat. */
    const near = loop.track.nearest(this.rack.x, this.rack.z);
    if (!(near.distance < 3 * LINE_SPACING)) return;
    road(loop.track, near.s, loop);
  }

  /** Which ROAD a row's workings use, and the spots on it.
   *
   *  By row rather than by bench: every bench on a row shares one circuit (see
   *  `_buildCycle`), so splitting a row across two roads would mean two
   *  circuits on one road and arc length would stop being a property of the
   *  road — which is the whole reason the circuit is built per road. The benches
   *  are dealt across the SPOTS instead, which is a number on one circuit. */
  _standFor(sd) {
    const racks = this.racks;
    if (!racks?.length) return null;
    const br = this.branchOf.get(sd.line);
    const i = br && Number.isFinite(br.index) ? br.index : 0;
    const roads = [];
    for (const r of racks) {
      let g = roads.find(q => q.via === r.via);
      if (!g) roads.push(g = {via: r.via, spots: []});
      g.spots.push(r);
    }
    return roads[((i % roads.length) + roads.length) % roads.length];
  }

  /** What a straight run between two points would cost in earthworks, per metre,
   *  once it has been graded to a ruling gradient.
   *
   *  This is the scoring function the route choice needs and did not have. The
   *  old one walked the ground and added up its RELIEF, which is not the same
   *  question: a corridor that climbs 30m steadily has a great deal of relief
   *  and needs no earthwork at all, and one that is flat with a 6m gully across
   *  it has almost none and needs a bridge. What costs money — and what shows —
   *  is the area between the ground and the profile a train can work over it,
   *  and that is exactly `|fit(G) − G|`.
   *
   *  It is the same operator the profile itself is built from, so a corridor is
   *  scored by the thing that will actually be built on it. */
  _legCost(ground, ax, az, bx, bz, grade = GRADE_RULING) {
    const L = Math.hypot(bx - ax, bz - az);
    if (!(L > 1)) return 0;
    const n = Math.max(4, Math.min(220, Math.round(L / 12)));
    const G = new Float64Array(n + 1);
    try {
      for (let i = 0; i <= n; i++) {
        const t = i / n;
        G[i] = (ground ? ground(ax + (bx - ax) * t, az + (bz - az) * t) : 0) || 0;
      }
    } catch { return 0; }
    const d = grade * (L / n);
    const A = new Float64Array(n + 1), B = new Float64Array(n + 1);
    A[0] = G[0]; B[0] = G[0];
    for (let i = 1; i <= n; i++) {
      A[i] = Math.max(G[i], A[i - 1] - d);
      B[i] = Math.min(G[i], B[i - 1] + d);
    }
    for (let i = n - 1; i >= 0; i--) {
      A[i] = Math.max(A[i], A[i + 1] - d);
      B[i] = Math.min(B[i], B[i + 1] + d);
    }
    let sum = 0;
    for (let i = 0; i <= n; i++) sum += Math.abs((A[i] + B[i]) * 0.5 - G[i]);
    return sum / (n + 1);
  }

  /** Where a leg of the ring stands, chosen by walking the ground rather than
   *  with a ruler.
   *
   *  Both legs, now, and scored by what the whole railway that depends on them
   *  would cost rather than by the leg alone: a corridor 30m further out that
   *  puts every branch across a gully is a worse answer than one 30m nearer that
   *  does not, and the leg's own profile cannot see that. So each candidate is
   *  charged for its own earthworks AND for the earthworks on the stretch of
   *  every branch that has to reach it.
   *
   *  `floorX` is the nearest the leg may come to the site — the loading road's
   *  outer turnout plus the branch's own throat curve — and is a hard bound
   *  rather than a preference: closer than that and `_branch` refuses the row,
   *  which is a worse answer than a longer leg. */
  _legCorridor(ground, floorX, dir, ZY, southEnd, rows) {
    let best = floorX, bestScore = Infinity;
    for (let k = 0; k < 7; k++) {
      const x = floorX + dir * k * 26;
      let sc = this._legCost(ground, x, ZY, x, southEnd) * 1.6;
      for (const r of rows) {
        const rz = r.z - DOCK_OFFSET - LOOP_OFFSET;
        sc += this._legCost(ground, x, rz, floorX - dir * 40, rz);
      }
      /* A small preference for the near ones, so a site does not sprawl for a
       * few centimetres of imaginary earthwork. */
      sc += k * 0.06;
      if (sc < bestScore) { bestScore = sc; best = x; }
    }
    return best;
  }

  /** One row of benches, its running line, and the two junctions that put it on
   *  the ring — one at each end.
   *
   *  ---- why a branch has two ends now --------------------------------------
   *
   *  It used to have one. The branch left the trunk at the west, ran east past
   *  the docks and stopped at a headshunt, and a working went out along it and
   *  came back down the same rails. Dumping the circuit showed the trunk
   *  traversed 316→553 outbound and 763→897 on the way home: the same track,
   *  the same working, opposite directions. That is what made a head-on
   *  possible at all, and no amount of interlocking makes it *impossible* —
   *  only unlikely, and only while the interlocking is right.
   *
   *  So the branch runs from a facing turnout on the ring's EAST leg, west past
   *  the docks, and on to a trailing turnout on the ring's WEST leg. A train
   *  runs it one way, joins the trunk pointing north, and is carried round the
   *  terminal and back down the east side to the same facing turnout it left
   *  from. Nothing reverses and nothing meets.
   *
   *  Both junctions sit ~96m up the trunk from the row, which puts each
   *  branch's two diagonals on parallel offsets ordered by row: branch j's
   *  diagonal is strictly inboard of branch i's for every j > i, so no branch
   *  can cross another. That is a property of the ordering rather than a check.
   */
  _branch(trunk, row, index, ground, WX, EX, ZY, prevW, prevE) {
    if (!trunk?.frames || !row?.list?.length) return null;
    const runZ = row.z - DOCK_OFFSET - LOOP_OFFSET;
    const gap = runZ - ZY;
    /* Enough z between the platform road and this row to hold the ring's own
     * corner, a turnout lead and a throat curve. Derived from what the ring
     * actually came out at rather than the 122 this used to assume. */
    if (!(gap > (this.ringR || 42) + 9 + this.throatMin)) return null;
    let headX = -Infinity, tailX = Infinity;
    for (const st of row.list) {
      headX = Math.max(headX, st.x); tailX = Math.min(tailX, st.x);
    }

    /* Where this row joins, at each end.
     *
     * ---- standage, which this used not to have ---------------------------
     *
     * A topology audit measured consecutive junctions on the ring 67.5m apart
     * against consists of 64.5–84.0m, and said the obvious thing about it: the
     * plan is tighter than its own rolling stock, so there is nowhere on the
     * ring a working can be held clear of the junction behind it. The floor
     * was `prev + 40` and was not even the binding constraint — the nearest
     * row's junction is pinned at ZY+52 by the ring's corner, and the next
     * row's fell wherever `runZ − 96` put it.
     *
     * So the junctions are now pushed apart to `MIN_STANDAGE`, and the price is
     * paid where a railway pays it: in the length of the diagonal. Pushing a
     * junction away from the terminal shortens the throat it has to turn in, so
     * the push is capped at `THROAT_MIN` and degrades gracefully rather than
     * refusing a branch — a row that cannot be given standage still gets a
     * railway, it just gets it at the natural spacing.
     *
     * The ceiling is not mine. Once every junction is against its throat cap
     * the spacing between two of them IS the spacing between two rows, which
     * the plan sets at 2.05 bays — 90m. That clears the longest consist and
     * not by much, and the honest statement is that a fleet laid out in rows
     * closer than a train is long cannot be given a railway that holds one.
     */
    const place = (prev) => {
      let z = Math.max(runZ - 96, prev + MIN_STANDAGE);
      z = Math.min(z, runZ - this.throatMin);
      /* Standage is not worth a hairpin. Pushing junctions MIN_STANDAGE apart
       * moves each one closer to the row it serves — rows are 90m apart and the
       * standage target is 104 — so every branch after the first was paying 14m
       * of throat, and therefore about 17m of radius, for standage it was only
       * getting because the row behind it had taken some. The push is capped
       * here at the throat a full yard-minimum curve needs; what is left is
       * still longer than the longest consist trains.js builds. */
      z = Math.min(z, Math.max(ZY + (this.ringR || 42) + 9,
                               runZ - (LEAD_Z + this.turnZ * R_MIN_YARD + 4)));
      /* Never inside the ring's own corner: a turnout laid inside a fillet is
       * two lines crossing, not a junction. The clearance is the corner's own
       * tangent distance — which for a 90° fillet is its radius — plus a few
       * metres of straight for the stock rail to be planed against, rather than
       * the 52 this used to assume when the corner was always 42m. */
      z = Math.max(z, ZY + (this.ringR || 42) + 9);
      return runZ > z + this.throatMin - 1e-6 ? z : null;
    };
    const zW = place(prevW), zE = place(prevE);
    if (zW === null || zE === null) return null;

    /* The turnouts first, then the road that leaves them. Each lead's exit port
     * is one of the branch's control points and the leg into it is laid along
     * the lead's own tangent, so the branch arrives at each switch already
     * pointing the way its blades do.
     *
     * 1:6 rather than 1:8, and the reason is arithmetic rather than taste. The
     * nearest row is ALWAYS 124.4m from the platform road — hub.z is derived
     * from that row — and 52m of that is the clearance the turnout needs from
     * the trunk's own corner. What is left has to hold the lead AND the throat
     * curve that swings the branch round to face it, and a 1:8 lead is 28m of
     * it against 1:6's 22m. Six metres decides whether the nearest row gets a
     * railway at all.
     *
     * The hand of each is read off the geometry by `makeLead` rather than
     * declared, so the east one comes out right-hand — a southbound train
     * diverging west — and the west one is the trailing merge that puts the
     * branch back on the northbound trunk. Get that pair the wrong way round
     * and the ring is drawn with two turnouts a train would have to set back
     * through. */
    const leadE = makeLead(trunk, {x: EX, z: zE}, {x: EX - 260, z: runZ}, 6);
    const leadW = makeLead(trunk, {x: WX, z: zW}, {x: WX + 260, z: runZ}, 6);
    if (!leadE || !leadW) return null;
    /* The hand of each junction, checked rather than assumed.
     *
     * `pdir` is the direction along the trunk the lead runs from its tip, which
     * is the direction a train faces when it takes the switch facing. Traffic
     * on the ring always runs with s increasing, so the EAST junction — where
     * the working leaves the ring — has to be facing (+1), and the WEST one —
     * where it rejoins — has to be trailing (−1). Get that pair the wrong way
     * round and the ring is drawn with two turnouts a train would have to set
     * back through, which is the whole defect this rebuild removes. It falls
     * out of the geometry, and this is where it is made to say so. */
    if (leadE.pdir !== 1 || leadW.pdir !== -1) return null;
    /* Arc length on the ring has to INCREASE from the west junction to the east
     * one, or the working would be asked to run round it backwards. It always
     * does — the west leg is laid first — but a circuit is not the place to
     * find out that an assumption about parametrisation was wrong. */
    if (!(leadE.tipS > leadW.tipS + 60)) return null;

    const cE = rayHit(leadE.exit, leadE.tan, {x: 0, z: runZ}, {x: 1, z: 0});
    const cW = rayHit(leadW.exit, leadW.tan, {x: 0, z: runZ}, {x: 1, z: 0});
    if (!cE || !cW || !(cE.t > 24) || !(cW.t > 24)) return null;

    /* And each throat radius is capped by the leg it has to fit in, not only by
     * the site. A fillet needs R·tan(θ/2) plus half its easement of straight on
     * each side; `_solve` will shrink a corner that does not fit, and it will
     * shrink it all the way to the floor and hand back a hairpin. Sizing it
     * here means the branch is refused when the ground genuinely has no room,
     * which is a thing the caller can route around. */
    const room = Math.min((cE.t - 2) / 0.96, (cW.t - 2) / 0.96);
    if (!(room > 34)) return null;
    /* And by the z it has to turn in: the branch leaves the ring on the lead's
     * own 9.5° and then swings the remaining ~80° on to the row's line, which
     * costs TURN_Z·R of z. Sizing it here, from the room that genuinely exists,
     * rather than letting `_solve`'s fit pass shrink whatever it was handed, is
     * what stops this quietly producing the 30m hairpins the audit found. */
    const swing = Math.min(runZ - zE, runZ - zW);
    const turnR = Math.max(0, swing - LEAD_Z - 4) / this.turnZ;
    const R = Math.min(140, room, turnR);
    if (!(R >= 34)) return null;
    if (R < R_MIN_YARD) {
      this.exceptions.push({
        track: `branch${index}`, rule: 'minimum radius', want: R_MIN_YARD,
        got: +R.toFixed(1),
        why: `${swing.toFixed(0)}m of throat between the junction and the row, ` +
             `where a ${R_MIN_YARD}m curve after a 1:6 lead needs ` +
             `${(R_MIN_YARD * this.turnZ + LEAD_Z + 4).toFixed(0)}m`,
      });
    }
    /* The straight between the two corners has to hold the loading road's own
     * points with room in front of them, or the row gets a branch it cannot be
     * served off. Refusing here rather than there keeps the two answers
     * together. */
    if (!(cE.x - cW.x > 240) || !(cE.x - 0.86 * R > headX + 124) ||
        !(cW.x + 0.86 * R < tailX - 124)) return null;

    const t = new Track(`branch${index}`, [
      [leadE.exit.x, leadE.exit.z], [cE.x, cE.z],
      [cW.x, cW.z], [leadW.exit.x, leadW.exit.z],
    ], {radius: R, klass: 'yard', hardFloor: Math.min(R * 0.94, R_MIN_YARD),
        maxGrade: GRADE_RULING, waterY: this.waterY,
        prefix: leadE.pts, suffix: leadW.pts.slice().reverse()});
    t.build(ground);
    if (!t.frames || t.tight) return null;
    if (!(t.length - leadE.len - leadW.len > 200)) return null;
    t.renderFrom = leadE.len;
    t.renderTo = t.length - leadW.len;
    t.row = row;
    this.lines.push(t);
    this.tracks.push(t);
    const recE = this._joint(t, leadE, 'start');
    const recW = this._joint(t, leadW, 'end');

    /* The junctions. Arc length on the branch is its own two ends — the
     * alignment runs to each switch tip and stops there, on the trunk's own
     * centreline — so the two can never be quoted differently. */
    trunk.blocks.push(junctionBlock(leadE), junctionBlock(leadW));
    trunk.overlaps.push(junctionOverlap(leadE), junctionOverlap(leadW));
    t.blocks.push([-2, leadE.len + 26],
                  [t.length - leadW.len - 26, t.length + 2]);
    t.overlaps.push(childOverlap(t, leadE, 'start', leadE.len + 26),
                    childOverlap(t, leadW, 'end', leadW.len + 26));

    const out = {track: t, row, index, zW, zE,
                 jS: t.length, tS: leadW.tipS,   // west end: on to the trunk
                 eS: 0, teS: leadE.tipS,         // east end: off the trunk
                 turnouts: [recE, recW]};
    this.branchOf.set(t, out);
    return out;
  }

  /** The loading road: one loop off the row's branch, running the length of the
   *  docks, with a stand on it for every bench in the row.
   *
   *  ---- why it is one road for a row and not one siding per bench ----------
   *
   *  It used to be per bench, and the geometry was a lie that a screenshot
   *  could not see. The dock line is 8.4m off the running line (that offset is
   *  buildings.js's — it is what clears the gantry footings), the turnouts sat
   *  52m either side of the bench and the road came parallel 38m either side.
   *  So the alignment was asked to move 8.4m sideways in 14m of length: 1 in
   *  1.7, where a slow yard turnout is 1 in 6 and a running-line one is 1 in
   *  12. `_solve`'s fit pass dutifully shrank the corner until it fitted, all
   *  the way to a 30m radius, and drew a smooth curve no track could be laid
   *  on. It also did not fit: benches are 90m apart and each of those loops
   *  claimed 104m, so in a rank every siding overlapped its neighbours'
   *  turnouts.
   *
   *  Both faults have the same cause — a loop needs ~50m of transition at each
   *  end, plus somewhere to stand, and 90m of bench spacing has not got 150m in
   *  it. The prototype does not try: a row of loading points gets ONE road past
   *  all of them, off the running line at each end of the row. Each bench is a
   *  stand along it. That leaves 56m for each transition (1 in 6.7, and gentler
   *  again after the easement), it never overlaps anything, and it is what a
   *  rail-served loading rack actually looks like.
   *
   *  Every bench in the row shares the road, which is not a compromise but the
   *  reason the block sections below matter: two workings off the same row are
   *  now genuinely in each other's way, and the railway can say so.
   */
  _loadingLoop(line, row, ground) {
    if (!line?.frames || !row?.list?.length) return null;
    const dockZ = row.z - DOCK_OFFSET;
    let x0 = Infinity, x1 = -Infinity;
    for (const st of row.list) { x0 = Math.min(x0, st.x); x1 = Math.max(x1, st.x); }
    /* The line runs whichever way it runs; only arc length is trustworthy.
     * Deciding "west first" from x would put the two ends of the road in the
     * wrong order on a line whose s counts down in x, and a control polyline
     * that doubles back folds the alignment on itself. */
    const back = this.loadSetback;
    const sa = line.nearest(x0 - back, dockZ).s;
    const sb = line.nearest(x1 + back, dockZ).s;
    const sA = Math.min(sa, sb), sB = Math.max(sa, sb);
    /* Bounded by the length of line that is LAID, not by the alignment. A
     * branch runs on past its junction and lies on the trunk from there, and a
     * loading loop whose far turnout falls in that overrun would have its
     * points standing in the middle of the main road. */
    const lo = line.renderFrom || 0;
    const hi = Math.min(line.renderTo, line.length);
    if (!(sB - sA > 120) || sA < lo + 10 || sB > hi - 40) return null;
    const pin = line.at(sA), pout = line.at(sB);
    /* And both tips have to stand on the STRAIGHT past the docks, not on the
     * corner that brings the branch down off the ring. The branch has a corner
     * at each end now, so `nearest` will happily return a point on one of them
     * for a road whose turnout wanted to be further out than the straight
     * reaches — and a lead planted on a 76m curve is a turnout laid inside a
     * fillet. The row is served off the branch or it is not served; it is never
     * served badly. */
    const runZ = row.z - DOCK_OFFSET - LOOP_OFFSET;
    if (Math.abs(pin.position.z - runZ) > 3 ||
        Math.abs(pout.position.z - runZ) > 3) return null;
    /* The two turnouts, before the road that runs between them. Both are 1:6 —
     * this is a loading road, worked at a walk, and a shorter lead keeps its
     * points clear of its neighbours' on a rank of benches 90m apart. */
    /* The point each lead diverges TOWARD is the far end of the road, not the
     * dock line straight out to the side. A running line past a row of benches
     * is very nearly parallel to that road, so a sideways `away` tells the lead
     * which hand it turns and nothing whatever about which way a train faces
     * through it — the dot product that decides that comes out at zero and the
     * turnout is laid pointing whichever way the float landed. */
    const leadIn = makeLead(line, {x: pin.position.x, z: pin.position.z},
                            {x: pout.position.x, z: dockZ}, 6);
    const leadOut = makeLead(line, {x: pout.position.x, z: pout.position.z},
                             {x: pin.position.x, z: dockZ}, 6);
    if (!leadIn || !leadOut) return null;
    /* Where each lead's own tangent reaches the line of docks. Aiming the road
     * at the benches directly would ask the alignment to close the last six
     * metres of offset in whatever was left, which is how the per-bench siding
     * used to end up at 1 in 1.7; running out on the turnout's own angle until
     * it arrives is what a yard actually does. */
    const dockLine = {x: 1, z: 0};
    const pA = rayHit(leadIn.exit, leadIn.tan, {x: 0, z: dockZ}, dockLine);
    const pB = rayHit(leadOut.exit, leadOut.tan, {x: 0, z: dockZ}, dockLine);
    if (!pA || !pB) return null;
    /* The stands have to fall between the two transitions, or a bench is served
     * by a piece of road that is still turning off the running line. */
    if (!(Math.min(pA.x, pB.x) < x0 - 24 && Math.max(pA.x, pB.x) > x1 + 24)) {
      return null;
    }
    const sd = new Track(`load:${Math.round(row.z)}`, [
      [leadIn.exit.x, leadIn.exit.z], [pA.x, pA.z],
      [pB.x, pB.z], [leadOut.exit.x, leadOut.exit.z],
    ], {radius: 210, klass: 'yard', minRadius: R_MIN_YARD, verge: true,
        maxGrade: GRADE_RULING, waterY: this.waterY,
        prefix: leadIn.pts, suffix: leadOut.pts.slice().reverse()});
    sd.build(ground);
    if (!sd.frames || sd.tight) return null;
    if (!(sd.length - leadIn.len - leadOut.len > 90)) return null;
    sd.renderFrom = leadIn.len;
    sd.renderTo = sd.length - leadOut.len;
    const ds = row.list.map(st => sd.nearest(st.x, dockZ).s)
                       .sort((p, q) => p - q);
    this.tracks.push(sd);
    const tIn = this._joint(sd, leadIn, 'start');
    const tOut = this._joint(sd, leadOut, 'end');
    /* The passing loops. AFTER the road's own two junctions and not before, and
     * the reason is measured: `_joint` pins each end of this road to the branch,
     * and on the lab's own floor that moves the road's profile at mid-rank from
     * 11.3m to 2.97m — the road is designed against natural ground and then
     * pulled eight metres down on to the railway it joins. A crossover built off
     * the unpinned road took its levels from a surface that no longer existed by
     * the time anything looked at it, and `jointReport` read 7308mm of gap.
     *
     * They are pushed on to `this.tracks` from in there, so this road is on the
     * list first and the rank reads west to east down it. */
    const links = this._midRankLinks(sd, line, ds);
    /* The apron. It covers the stands and thirty metres either side of the
     * outermost of them — the frontage a works actually pours — and stops well
     * clear of every set of points, because a slab across a switch is a
     * different (and much more expensive) piece of engineering than a slab
     * across plain line, and drawing one here would be claiming a detail the
     * geometry has not got. The taper at each end does the joining.
     *
     * With a crossover mid-rank that is no longer one slab, it is two or three
     * with the switches left in ballast between them — which is what a works
     * yard looks like, and it is the reason `_loadingLoop` would not build a
     * mid-rank turnout before the stand pitch grew. The stand immediately
     * behind a crossover keeps its slab under the RAKE, which is the part being
     * loaded; it is the locomotive at the head that ends up standing on stone
     * over the points, and that is where a locomotive stands. */
    {
      const a = Math.max(sd.renderFrom + 14, Math.min(...ds) - 30);
      const b = Math.min(sd.renderTo - 14, Math.max(...ds) + 30);
      const slabs = [];
      let at = a;
      for (const k of links) {
        const gap = [k.roadS - 3, k.roadS + k.leadLen + 4];
        if (gap[0] > at + 3 * PAVE_TAPER) slabs.push([at, gap[0]]);
        at = Math.max(at, gap[1]);
      }
      if (b > at + 3 * PAVE_TAPER) slabs.push([at, b]);
      if (slabs.length) sd.paved = slabs;
    }
    sd.links = links;

    /* Arc lengths on the road are quoted from the switch TIPS, which are where
     * its alignment genuinely begins and ends — on the running line's own
     * centreline. `entryS`/`exitS` are therefore 0 and the length, and a route
     * stitched through them steps from one road to the other without a chord
     * across anything. */
    const tipIn = {s: leadIn.tipS}, tipOut = {s: leadOut.tipS};
    for (const st of row.list) {
      this.sidings.set(st.uid, {
        track: sd, line, sIn: tipIn.s, sOut: tipOut.s,
        sDock: sd.nearest(st.x, dockZ).s, dockZ,
        entryS: 0, exitS: sd.length, uid: st.uid, station: st, row,
      });
    }

    line.blocks.push(junctionBlock(leadIn), junctionBlock(leadOut));
    line.overlaps.push(junctionOverlap(leadIn), junctionOverlap(leadOut));
    /* The road's own turnout blocks — bearers at the entry, bearers at the
     * exit, and that is all `blocks` has ever meant here.
     *
     * The OVERLAP is the number that was wrong. The entry one was
     * `leadIn.len + 6` — "a few metres past its heel the train is genuinely
     * off" — and the heel is not where a train is off. `leadClearAt(6, 28.6)`
     * is 3.75m, and `rz-pair.mjs` duly measured `branch0#2* / load:0#1` at
     * **3.77m** on layout 0: a rake standing on the loading road with its tail
     * just inside the road's first plain block, a working running past on the
     * branch, both correctly signalled, 3.77m apart. The reasoning behind the
     * short block was right — the last train home has to stand clear of the
     * running line — but six metres was a guess and the fouling point is
     * 37.8m, and 37.8 is the number that makes a train stand genuinely off
     * rather than nominally off. */
    sd.blocks.push([-2, leadIn.len + 6],
                   [sd.length - leadOut.len - 26, sd.length + 2]);
    sd.overlaps.push(childOverlap(sd, leadIn, 'start'),
                     childOverlap(sd, leadOut, 'end', leadOut.len + 26));
    /* And a section boundary just AHEAD of each stand, which is where the gap
     * between two stabled trains actually is. A train stands with its head on
     * its stand and its rake trailing back toward the entry, so at the 90m bay
     * pitch the clear ground is the few metres in front of the head — the cut
     * used to be thirty metres further on, which put it inside the body of the
     * next train along and made two perfectly-parked neighbours contend for one
     * block. */
    for (const st of row.list) {
      const s = sd.nearest(st.x, dockZ).s;
      sd.blocks.push([s + 3, s + 3.1]);
    }
    const turnouts = [tIn, tOut];
    for (const k of links) turnouts.push(...k.turnouts);
    return {track: sd, turnouts, links};
  }

  /** THE PASSING LOOPS. Crossovers from a loading road back on to the row's own
   *  branch, sited between stands, so a working boxed in behind a bench that is
   *  not parsing has somewhere to go other than through it.
   *
   *  `ds` is the stands, ascending in arc length on the road. A crossover just
   *  in front of stand i releases everything at stands 0..i without waiting for
   *  stand i+1, so the crossovers cut the rank into stretches and the deepest
   *  stretch is the queue — 4 stands 3 deep becomes 2, and 7 stands 6 deep
   *  becomes 3. Both measured, on soak.mjs's own layouts.
   *
   *  It is REFUSED rather than squeezed when the arithmetic does not close, and
   *  the refusal carries its numbers: see the LINK_* block at the head of this
   *  file. The thing that closes or does not is the fouling point, and it is
   *  measured from the tail of the rake standing at the NEXT stand, because that
   *  is the metal the departing train has to get past. */
  _midRankLinks(road, line, ds) {
    const out = [];
    const n = ds.length;
    if (!this.passingLoops) this.passingLoops = {built: [], refused: []};
    if (n < LINK_MIN_STANDS) return out;
    /* One at the midpoint, two at the thirds for a long rank. That is
     * trains.js's rule and it is the rule that halves and then thirds the
     * queue; anything finer buys less than one train's worth of depth and costs
     * another hole in the apron. */
    const want = n >= LINK_TWO_STANDS
      ? [Math.ceil(n / 3) - 1, Math.ceil((2 * n) / 3) - 1]
      : [Math.ceil(n / 2) - 1];
    for (const i of [...new Set(want)].sort((a, b) => a - b)) {
      if (i < 0 || i + 1 >= n) continue;
      const sM = ds[i] + LINK_EPS;
      const tail = ds[i + 1] - LINK_RAKE;      // where the next rake ends
      const g = tail - sM;
      const clear = leadClearAt(LINK_FROG, g);
      if (!(clear >= LINK_CLEAR)) {
        this.passingLoops.refused.push({
          road: road.name, afterStand: i + 1, ofStands: n,
          pitch: +(ds[i + 1] - ds[i]).toFixed(1), rake: LINK_RAKE,
          runToNextTail: +g.toFixed(1), clearance: +clear.toFixed(2),
          wanted: LINK_CLEAR, frog: LINK_FROG,
          needsRun: +leadClearRun(LINK_FROG, LINK_CLEAR).toFixed(1),
          needsPitch: +(LINK_RAKE + LINK_EPS +
                        leadClearRun(LINK_FROG, LINK_CLEAR)).toFixed(1),
        });
        continue;
      }
      const k = this._crossover(road, line, sM, tail);
      if (!k) continue;
      k.after = i;
      this.passingLoops.built.push({
        road: road.name, link: k.track.name, afterStand: i + 1, ofStands: n,
        pitch: +(ds[i + 1] - ds[i]).toFixed(1),
        clearance: +clear.toFixed(2), frog: LINK_FROG,
        ruling: +(k.track.ruling || 0).toFixed(4),
        queueWas: n - 1,
      });
      out.push(k);
    }
    /* What the rank's queue actually is now: the crossovers cut it into
     * stretches and the deepest one is what a working at the back of it has to
     * wait for. Computed over ALL of them rather than per crossover, because two
     * loops on a rank of seven do not each halve it — they third it. */
    if (out.length) {
      const cuts = out.map(k => k.after);
      let prev = -1, worst = 0;
      for (const c of [...cuts, n - 1]) { worst = Math.max(worst, c - prev); prev = c; }
      for (const rec of this.passingLoops.built) {
        if (rec.road === road.name) rec.queueNow = worst;
      }
    }
    return out;
  }

  /** One crossover: a turnout off the road, a straight, and a turnout on to the
   *  branch, both the same hand and the same frog so the piece between them is
   *  genuinely straight and there is no corner to fillet.
   *
   *  The only unknown is where the branch-side tip goes, and it is SOLVED rather
   *  than computed. The nominal answer — 2·u₀ + (spacing − 2·v₀)/tan φ past the
   *  point on the branch opposite the tip — assumes the two roads are parallel,
   *  and a row the site had to skew puts that out by the better part of a metre,
   *  which shows as a kink at the heel and as a `jointReport` angle. Four
   *  secant steps on the perpendicular residual close it to a millimetre. */
  _crossover(road, line, sM, nextTail) {
    if (!road?.frames || !line?.frames) return null;
    const f = road.at(sM);
    const near = line.nearest(f.position.x, f.position.z);
    if (!(near.distance > 4 && near.distance < 14)) return null;
    /* Forward along the parent, and sideways to the other road, as two SEPARATE
     * components. `_loadingLoop` aims its leads at a point out in front and off
     * to the side, which is fine there because the two are the length of a rank
     * apart; over the thirty metres this one needs, a skewed row puts enough
     * lateral into the "forward" term to decide the HAND of the turnout, and it
     * has no business deciding that. */
    const side = (frame, target) => {
      const h = Math.hypot(frame.tangent.x, frame.tangent.z) || 1;
      const ux = frame.tangent.x / h, uz = frame.tangent.z / h;
      const dx = target.x - frame.position.x, dz = target.z - frame.position.z;
      const along = dx * ux + dz * uz;
      return {ux, uz, x: dx - along * ux, z: dz - along * uz};
    };
    const aim = (frame, target, back) => {
      const s = side(frame, target);
      return {x: frame.position.x + s.ux * 30 * back + s.x * 3,
              z: frame.position.z + s.uz * 30 * back + s.z * 3};
    };
    const sR = side(f, line.at(near.s).position);
    const spacing = Math.hypot(sR.x, sR.z);
    const leadR = makeLead(road, {x: f.position.x, z: f.position.z},
                           aim(f, line.at(near.s).position, 1), LINK_FROG);
    if (!leadR || leadR.pdir !== 1) return null;

    const R = 2 * GAUGE * LINK_FROG * LINK_FROG;
    const phi = (Math.sqrt(2 * R * GAUGE) + 5.4) / R;
    const v0 = R * (1 - Math.cos(phi)), u0 = R * Math.sin(phi);
    if (!(spacing > 2 * v0 + 1)) return null;
    /* Which way the branch counts. Read, not assumed. `_loadingLoop` lays the
     * road so that its own s and the branch's run together — the entry tip is
     * the LOWER arc length on both — and the circuit slices every leg upward on
     * the strength of it. A site that broke that would give a crossover running
     * backwards over the branch, so it is checked here and refused rather than
     * built and then found by `oneWayReport`. */
    const bt = line.at(near.s).tangent;
    if (!(f.tangent.x * bt.x + f.tangent.z * bt.z > 0)) return null;
    const rr = rightOf(leadR.tan);
    const resid = L => (L.exit.x - leadR.exit.x) * rr.x +
                       (L.exit.z - leadR.exit.z) * rr.z;
    const build = s => {
      if (!(s > 0 && s < line.length)) return null;
      const g = line.at(s);
      const L = makeLead(line, {x: g.position.x, z: g.position.z},
                         aim(g, f.position, -1), LINK_FROG);
      return L && L.pdir === -1 ? L : null;
    };
    let sL = near.s + 2 * u0 + (spacing - 2 * v0) / Math.tan(phi);
    let leadB = build(sL);
    if (!leadB) return null;
    for (let it = 0; it < 6 && Math.abs(resid(leadB)) > 0.002; it++) {
      const r0 = resid(leadB);
      const alt = build(sL + 0.5);
      if (!alt) break;
      const slope = (resid(alt) - r0) / 0.5;
      if (!(Math.abs(slope) > 1e-6)) break;
      const next = build(sL - r0 / slope);
      if (!next) break;
      sL -= r0 / slope;
      leadB = next;
    }
    if (Math.abs(resid(leadB)) > 0.05) return null;
    /* The piece between the two heels has to be a real straight, running the
     * right way and long enough to be one. */
    const along = (leadB.exit.x - leadR.exit.x) * leadR.tan.x +
                  (leadB.exit.z - leadR.exit.z) * leadR.tan.z;
    if (!(along > 3 && along < 90)) return null;
    const dot = -(leadB.tan.x * leadR.tan.x + leadB.tan.z * leadR.tan.z);
    if (!(dot > 0.9985)) return null;               // under 3 degrees of kink

    /* The ground a crossover stands on is the two FORMATIONS it joins, not the
     * hillside. That is not a convenience, it is the only correct answer, and
     * the first version of this got it wrong in a way worth recording: a 45m
     * connection fitted to natural ground came out 7.31m above the loading road
     * it starts on, because the road is in a seven-metre cutting there and a
     * forty-five-metre alignment has no length in which to be. `pinEnd` could
     * not rescue it either — the anchor clamp is g-Lipschitz at the ruling
     * gradient, so 45m of connection can move its ends 1.1m and no more, and
     * `jointReport` read 7308mm of gap.
     *
     * So the surface is the railhead of whichever parent is nearer, blended by
     * distance: exactly the parents' level at each tip, a straight ramp between.
     * The two roads are 8.4m apart and each already asks terrain for 8.3m of
     * graded formation, so this is also what the ground under it will BE. Which
     * is why it declares no earthworks of its own (`verge: false`): asking a
     * third time for a strip that two corridors already cover would double-count
     * the excavation, and the four inches between those corridors is inside
     * terrain's own batter. */
    const shim = (x, z) => {
      const pa = road.at(road.nearest(x, z).s).position;
      const pb = line.at(line.nearest(x, z).s).position;
      const da = Math.hypot(x - pa.x, z - pa.z);
      const db = Math.hypot(x - pb.x, z - pb.z);
      const t = da / (da + db || 1);
      return pa.y * (1 - t) + pb.y * t - FORMATION;
    };
    const link = new Track(`link:${road.name}@${Math.round(sM)}`, [
      [leadR.exit.x, leadR.exit.z], [leadB.exit.x, leadB.exit.z],
    ], {radius: 210, klass: 'yard', minRadius: R_MIN_YARD,
        verge: false, corridor: true,
        maxGrade: GRADE_YARD, waterY: this.waterY,
        prefix: leadR.pts, suffix: leadB.pts.slice().reverse()});
    link.build(shim);
    if (!link.frames || link.tight) return null;
    link.renderFrom = leadR.len;
    link.renderTo = link.length - leadB.len;
    if (!(link.renderTo > link.renderFrom + 2)) return null;
    this.tracks.push(link);
    const tA = this._joint(link, leadR, 'start');
    const tB = this._joint(link, leadB, 'end');

    /* The blocks. On the BRANCH the standard overlap is right — nothing stables
     * there. On the ROAD it is not: the overlap reaches past the tip, and the
     * tail of the rake standing at the next stand is 28.5m past it at this
     * pitch. A junction span under a parked train is a train that
     * `trains.js:_onRoad` can never read as home, and a working that never
     * reads as home never starts again — so this one is sized from the room
     * there actually is. It still covers the whole lead and its bearers.
     *
     * The cap now trims 0.8m rather than 4.99m, because the overlap is derived
     * from the 1:4.5 frog (27.8m) instead of the flat 32m this used to fight.
     * What is left buys `leadClearAt(4.5, 27.0)` = 5.51m — over soak's 5.00 and
     * under this file's own 5.75 target, which is the honest state of a yard
     * throat at a 115.5m stand pitch and is reported as such. */
    const jb = junctionOverlap(leadR), jd = junctionBlock(leadR);
    road.blocks.push([jd[0], Math.min(jd[1], nextTail - LINK_BLOCK_GAP)]);
    road.overlaps.push([jb[0], Math.min(jb[1], nextTail - LINK_BLOCK_GAP)]);
    line.blocks.push(junctionBlock(leadB));
    line.overlaps.push(junctionOverlap(leadB));
    /* And the crossover is ONE block. It is one piece of interlocked apparatus:
     * a train on it holds both sets of points, which is the only correct answer
     * — there is nowhere on forty metres of connection for a second one to
     * stand clear of either. `_coupleThroats` now makes that literally true:
     * the connection and BOTH its parents' junction spans carry one block id. */
    link.blocks.push([-2, link.length + 2]);
    link.overlaps.push([-2, link.length + 2]);
    return {track: link, roadS: leadR.tipS, lineS: leadB.tipS,
            leadLen: leadR.len, turnouts: [tA, tB]};
  }

  /* ---- the terminal --------------------------------------------------------
   *
   * There was a balloon loop here, and it is gone, and what replaced it is the
   * whole point of this round of work.
   *
   * The balloon turned a train at the terminal so it could go home down the
   * rails it had arrived on. That worked — every working got home nose-first —
   * but it left the trunk carrying traffic in BOTH directions, which is the one
   * thing a railway may not do on a single line without a token, and it is why
   * two workings could ever be pointed at each other at all. The circuit dump
   * said it plainly: `main 316→553` outbound, `main 763→897` returning, one
   * working, one piece of track.
   *
   * A one-way circuit does not need turning. The train runs out along its
   * branch, north up the west side, east along the platform road under the
   * loading gantry, and then keeps going — round the east corner and south down
   * the return alignment to the facing turnout that puts it back on its own
   * branch. It arrives home the way it left, having never reversed, and two
   * workings on this railway cannot meet head-on because there is nowhere left
   * for them to do it. That is what a loop terminal is for on a real petroleum
   * unit-train system, and it is why Factorio's right-hand-drive loops are safe
   * by construction rather than by signalling.
   *
   * What remains at the terminal is what a terminal is: the platform road with
   * the rack over it, a second road beside it, and a reception road to stable a
   * cut of tanks on.
   */

  /** The second platform road, on the far side of the terminal's island
   *  platform.
   *
   *  It is what makes the terminal a terminal rather than one road with a shed
   *  on it. Every branch runs on to the same ring, so the platform road is the
   *  one piece of this railway where a working off one row and a working off
   *  another are close enough together to matter — and a throat that can only
   *  hold one arrival turns the second one into a train standing on the main.
   *
   *  It is worked the same way round as the road beside it — a facing turnout
   *  at the west end and a trailing one at the east, both taken in the same
   *  direction as everything else on the ring — so it is a second road and not
   *  a passing loop. On a one-way circuit there is nothing to pass.
   *
   *  It is skipped rather than forced when there is no room between the two
   *  corners: a 40m refuge would hold nothing and would put a turnout inside a
   *  fillet. */
  _terminalLoop(trunk, hub, ground, WX, EX) {
    if (!trunk?.frames) return null;
    const ZY = hub.z + DOCK_OFFSET;
    const D = LINE_SPACING;
    /* It spans the terminal, and both its points have to stand on the straight
     * part of the platform road — clear of the ring's own corners at each end,
     * which is what the two `nearest` probes below bound it by. */
    /* Bounded by the ring's corners rather than by a fixed offset from the hub:
     * the platform road is only as long as the site is wide, and on a
     * single-column fleet a turnout 205m west of the terminal would be laid
     * inside the west corner's fillet. */
    const w = trunk.nearest(Math.max(hub.x - 205, WX + 92), ZY);
    const e = trunk.nearest(Math.min(hub.x + 145, EX - 92), ZY);
    if (w.distance > 3 || e.distance > 3) return null;
    const sWest = w.s, sEast = e.s;
    if (!(sEast - sWest > 180)) return null;
    if (!(sWest > trunk.nearest(WX + 62, ZY).s + 4)) return null;
    if (!(sEast < trunk.nearest(EX - 62, ZY).s - 4)) return null;
    const pW = trunk.at(sWest).position, pE = trunk.at(sEast).position;
    /* 1:6 at both ends. This is a refuge worked at a walk, and a shorter lead
     * leaves more of the road between the two transitions actually straight —
     * a passing loop that is all transition holds nothing. */
    const leadA = makeLead(trunk, {x: pW.x, z: pW.z},
                           {x: pW.x + 54, z: pW.z - D}, 6);
    const leadB = makeLead(trunk, {x: pE.x, z: pE.z},
                           {x: pE.x - 54, z: pE.z - D}, 6);
    if (!leadA || !leadB) return null;
    const road = {x: 1, z: 0};
    const pA = rayHit(leadA.exit, leadA.tan, {x: 0, z: pW.z - D}, road);
    const pB = rayHit(leadB.exit, leadB.tan, {x: 0, z: pE.z - D}, road);
    if (!pA || !pB || !(pB.x - pA.x > 60)) return null;

    const t = new Track('terminal.loop', [
      [leadA.exit.x, leadA.exit.z], [pA.x, pA.z], [pB.x, pB.z],
      [leadB.exit.x, leadB.exit.z],
    ], {radius: 130, klass: 'yard', minRadius: R_MIN_YARD, maxGrade: GRADE_RULING,
        waterY: this.waterY,
        prefix: leadA.pts, suffix: leadB.pts.slice().reverse()});
    t.build(ground);
    if (!t.frames || t.tight) return null;
    if (!(t.length - leadA.len - leadB.len > 90)) return null;
    t.renderFrom = leadA.len;
    t.renderTo = t.length - leadB.len;
    this.tracks.push(t);
    const tA = this._joint(t, leadA, 'start');
    const tB = this._joint(t, leadB, 'end');

    trunk.blocks.push(junctionBlock(leadA), junctionBlock(leadB));
    trunk.overlaps.push(junctionOverlap(leadA), junctionOverlap(leadB));
    t.blocks.push([-2, leadA.len + 26],
                  [t.length - leadB.len - 26, t.length + 2]);
    t.overlaps.push(childOverlap(t, leadA, 'start', leadA.len + 26),
                    childOverlap(t, leadB, 'end', leadB.len + 26));
    this.loop = {track: t, line: trunk, sIn: leadA.tipS, sOut: leadB.tipS,
                 from: 0, to: t.length};
    this.link = this.loop;          // the name older callers read
    return {track: t, turnouts: [tA, tB]};
  }

  /** The reception road at the terminal: a stub off the inside line's platform
   *  road, ending in a buffer stop. It exists so the yard is a yard — a single
   *  road into a terminal is a siding with a shed at the end of it. */
  _yardSpur(line, hub, ground) {
    if (!line?.frames) return null;
    const ZY = hub.z + DOCK_OFFSET;
    const a = line.nearest(hub.x - 74, ZY);
    if (a.distance > 24 || a.s < 30 || a.s > line.length - 60) return null;
    const pin = line.at(a.s).position;
    const lead = makeLead(line, {x: pin.x, z: pin.z},
                          {x: hub.x + 24, z: ZY + 15}, 6);
    if (!lead) return null;
    const P = rayHit(lead.exit, lead.tan, {x: 0, z: ZY + 15}, {x: 1, z: 0});
    if (!P || !(P.t > 16)) return null;
    const spur = new Track('yard.spur', [
      [lead.exit.x, lead.exit.z], [P.x, P.z], [hub.x + 96, ZY + 15],
    ], {radius: 140, klass: 'yard', minRadius: R_MIN_YARD,
       maxGrade: GRADE_RULING, waterY: this.waterY, prefix: lead.pts});
    spur.build(ground);
    if (!spur.frames) return null;
    if (!(spur.length - lead.len > 40)) return null;
    spur.renderFrom = lead.len;
    this.tracks.push(spur);
    this._spur = spur;
    const rec = this._joint(spur, lead, 'start');
    line.blocks.push(junctionBlock(lead));
    line.overlaps.push(junctionOverlap(lead));
    spur.blocks.push([-2, lead.len + 26]);
    spur.overlaps.push(childOverlap(spur, lead, 'start', lead.len + 26));
    this._buffers = this._buffers || [];
    this._buffers.push({track: spur, s: spur.length - 1.4, back: false});
    return {track: spur, turnouts: [rec]};
  }

  /** Close a road on to the one it leaves, and record the join.
   *
   *  The plan and the tangent already close: the road's alignment was built
   *  with the lead spliced into it, so its arc length zero IS the switch tip on
   *  the parent's own centreline. What is left is the level — `Track.build`
   *  grades every road on its own — and the bookkeeping that stops the road's
   *  own rails being laid over the turnout's.
   *
   *  Returns the turnout record `_buildMeshes` draws from. Anything that cannot
   *  be closed has already returned null further up: a caller that gets no lead
   *  routes differently rather than emitting a join that does not meet. */
  _joint(child, lead, which) {
    const drop = child.pinEnd(which, lead.y, lead.grade,
                              Math.min(90, child.length * 0.34));
    const rec = {track: lead.parent, s: lead.tipS, pdir: lead.pdir,
                 hand: lead.hand, N: lead.N, R: lead.R, aFrog: lead.aFrog,
                 leadLen: lead.len, child, which, closed: true, drop};
    this._turnouts.push(rec);
    return rec;
  }

  /** Every join on the railway, and how far it misses.
   *
   *  The audit that started this work measured the old generator's branch
   *  turnouts and found every one of them joining two railheads that did not
   *  meet. This is the answer to that, kept in the file rather than in a
   *  harness, so the claim is checkable from the console on any layout:
   *
   *      __lemWorld.subsystems.get('rail').jointReport()
   *
   *  `gap` is the distance from the switch tip — the point on the through
   *  road's centreline where the turnout begins — to where the diverging road's
   *  alignment actually starts. `level` is the vertical part of it on its own,
   *  because a join can be perfect in plan and still be a step. `angle` is the
   *  difference between the two tangents there. All three are zero when the
   *  piece closes. */
  jointReport() {
    const out = [];
    const P = new THREE.Vector3(), Q = new THREE.Vector3();
    for (const rec of this._turnouts || []) {
      const parent = rec.track, child = rec.child;
      if (!parent?.frames || !child?.frames) continue;
      const f = parent.at(rec.s);
      const c = child.at(rec.which === 'start' ? 0 : child.length);
      P.copy(f.position); Q.copy(c.position);
      const gap = P.distanceTo(Q);
      const level = Math.abs(P.y - Q.y);
      /* The child's tangent runs away from the junction at a 'start' and into
       * it at an 'end'; the parent's runs the way a train faces through the
       * switch. Compared as travel directions, they must agree. */
      const cd = rec.which === 'start' ? 1 : -1;
      const dot = (f.tangent.x * c.tangent.x + f.tangent.y * c.tangent.y +
                   f.tangent.z * c.tangent.z) * rec.pdir * cd;
      const angle = Math.acos(Math.max(-1, Math.min(1, dot))) * 180 / Math.PI;
      /* Split, because the two halves fail for different reasons and a single
       * number cannot say which: the plan angle is the alignment, the grade
       * difference is the profile. */
      const hp = Math.hypot(f.tangent.x, f.tangent.z) || 1;
      const hc = Math.hypot(c.tangent.x, c.tangent.z) || 1;
      const hdot = ((f.tangent.x / hp) * (c.tangent.x / hc) +
                    (f.tangent.z / hp) * (c.tangent.z / hc)) * rec.pdir * cd;
      const plan = Math.acos(Math.max(-1, Math.min(1, hdot))) * 180 / Math.PI;
      out.push({turnout: `${parent.name}@${rec.s.toFixed(1)}→${child.name}`,
                N: rec.N, gapMm: gap * 1000, levelMm: level * 1000, angle, plan,
                gradeP: (f.tangent.y / hp) * rec.pdir,
                gradeC: (c.tangent.y / hc) * cd,
                pinnedMm: (rec.drop || 0) * 1000});
    }
    const worst = out.reduce((a, b) => (b.gapMm > (a?.gapMm ?? -1) ? b : a), null);
    return {joins: out.length, worstGapMm: worst ? worst.gapMm : 0,
            worstAngle: out.reduce((a, b) => Math.max(a, b.angle), 0),
            worstLevelMm: out.reduce((a, b) => Math.max(a, b.levelMm), 0),
            worst, detail: out};
  }

  /** And the same question asked of the BLOCK TABLE rather than the geometry:
   *  is every turnout one block on both of its roads, and does that block reach
   *  to the fouling point on each?
   *
   *      __lemWorld.subsystems.get('rail').throatReport()
   *
   *  `overlapM` is how far past the tip the parent's junction span runs and
   *  `clearM` is what `leadClearAt` says that buys. Anything under
   *  `FOUL_CLEAR` is a throat where two correctly-signalled workings could be
   *  closer than this file intends, which is the defect this report exists to
   *  make un-missable — it was 4.49m at every 1:6 junction on the railway and
   *  nobody could see it, because the number that produced it was `32`. */
  throatReport() {
    /* every section's effective id, so a class can be found from either side */
    const idOf = (name, i) => {
      const l = this._sections?.get(name);
      return l ? (l[i].id || (name + '#' + i)) : null;
    };
    const members = id => {
      const out = [];
      for (const [name, l] of this._sections || [])
        for (let i = 0; i < l.length; i++)
          if (idOf(name, i) === id) out.push({name, i, sec: l[i]});
      return out;
    };
    const byParent = new Map();
    for (const th of this._throats || []) byParent.set(th.parent, th);
    const out = [];
    for (const rec of this._turnouts || []) {
      const parent = rec.track, child = rec.child;
      if (!parent?.name || !child?.name) continue;
      const list = this._sections?.get(parent.name) || [];
      let pi = -1;
      for (let i = 0; i < list.length; i++)
        if (list[i].junction && rec.s >= list[i].a - 0.05 &&
            rec.s <= list[i].b + 0.05) { pi = i; break; }
      const id = pi < 0 ? null : idOf(parent.name, pi);
      const mine = id ? members(id) : [];
      /* the parent's own contiguous junction run, from the tip outwards */
      let far = 0;
      for (const m of mine) {
        if (m.name !== parent.name) continue;
        far = Math.max(far, rec.pdir > 0 ? m.sec.b - rec.s : rec.s - m.sec.a);
      }
      const roads = [...new Set(mine.map(m => m.name))];
      out.push({turnout: `${parent.name}@${rec.s.toFixed(1)}→${child.name}`,
                N: rec.N, id: id || 'NONE',
                overlapM: +far.toFixed(1),
                clearM: +leadClearAt(rec.N, far).toFixed(2),
                roads, coupled: roads.includes(child.name)});
    }
    return {turnouts: out.length,
            uncoupled: out.filter(r => !r.coupled).length,
            worstClearM: out.reduce((a, r) => Math.min(a, r.clearM), Infinity),
            want: FOUL_CLEAR, detail: out};
  }

  /* ---- geometry ---------------------------------------------------------- */

  _buildMeshes(turnouts, ground) {
    const Tex = this.Tex;
    const ballast = new Mesher(true);
    const pave = new Mesher(true);
    const rail = new Mesher(false);
    const kit = new Mesher(true);
    const concrete = new Mesher(true);
    const bearers = [];

    /* How much railway there actually is. The lab's own floor lays about 1.5km
     * of it; a fleet scattered over fourteen bays lays six or seven, because
     * the site is that much bigger and the branches have that much further to
     * go. Track cost is per metre and the triangle budget is not, so the
     * permanent way thins with distance the way a model railway's would: wider
     * sleeper spacing, a coarser ballast ribbon, fewer rings through curves,
     * and no rail clips at all. None of it is visible, because a site seven
     * kilometres across is never being looked at from four metres away — and
     * shedding it is the difference between holding 2.5M triangles and not. */
    let laid = 0;
    for (const t of this.tracks) {
      if (!t.frames) continue;
      /* A bore is already "somewhere a sleeper may not stand", which is what
       * `blocks` means — so the tunnels are declared to the sleeper loop in the
       * language it already speaks rather than through a second mechanism. It
       * happens here because `_sectionBlocks` has finished with the list. */
      try {
        t.earthworks();
        for (const b of t.bores || []) t.blocks.push([b[0], b[1]]);
      } catch { /* an undeclarable road simply gets its sleepers */ }
      laid += Math.min(t.renderTo, t.length) - (t.renderFrom || 0);
    }
    const thin = laid > 4200 ? Math.min(2.8, laid / 4200) : 1;
    const pitch = SLEEPER_PITCH * thin;

    /* Rails before turnouts, and not only for tidiness: `railPair` is what
     * decides `railFrom`/`railTo`, and a turnout's closure rails have to stop
     * on exactly the frame the road's own rails start from. */
    for (const t of this.tracks) {
      const step2 = 2.0 * Math.min(2, thin);
      /* A road with an apron on it is ballasted in two pieces with the slab
       * between them, so the stone is never laid under the concrete: two
       * surfaces a millimetre apart over four hundred metres is z-fighting, and
       * a paved road with gravel flickering through it is worse than either. */
      /* Nothing at all is laid inside a tunnel bore. The track is under the
       * hill, the hill is already drawn by terrain.js, and ballast and rails
       * buried in it are triangles nobody will ever see — so a bore is a gap in
       * the permanent way with a portal at each end of it, which is also
       * exactly what a tunnel looks like from outside. Sleepers use the same
       * fact through `t.blocks`, which already means "no timber here". */
      /* `paved` is a LIST of slabs now, because a road with a crossover in it
       * is poured in two or three pieces with the switches left in stone. The
       * walk below lays ballast up to each slab, the slab, and stone again after
       * the last one — so every metre of the road gets exactly one surface, and
       * a road with no apron at all still takes the plain branch. */
      for (const [a, b] of visibleSpans(t)) {
        const slabs = (t.paved || []).filter(s => s[1] > s[0] + 1);
        if (slabs.length) {
          let at = a;
          for (const [q0, q1] of slabs) {
            const p0 = Math.max(a, q0), p1 = Math.min(b, q1);
            if (!(p1 > p0 + 1)) continue;
            if (p0 > at) ballastRibbon(t, ballast, ground, step2, at, p0);
            pavedDeck(t, pave, ground, p0, p1, step2);
            at = Math.max(at, p1);
          }
          if (b > at) ballastRibbon(t, ballast, ground, step2, at, b);
        } else {
          ballastRibbon(t, ballast, ground, step2, a, b);
        }
        railPair(t, rail, thin, a, b);
      }
    }
    for (const t of turnouts) {
      try { buildTurnout(rail, kit, ballast, bearers, t); }
      catch (err) { console.warn('[rail] turnout skipped', err); }
    }

    /* Sleepers and their fastenings. Two meshes, because at `medium` and below
     * the 60mm clips are shed and the timbers are not. */
    const slots = [];
    const M = new THREE.Matrix4(), B = new THREE.Matrix4();
    const jitter = this.ctx.seededRandom('rail/sleepers');
    const R3 = new THREE.Vector3(), U3 = new THREE.Vector3(), T3 = new THREE.Vector3();
    const fr = {position: new THREE.Vector3(), tangent: T3, up: U3, right: R3, k: 0};
    for (const t of this.tracks) {
      if (!t.frames || !t.sleepers) continue;
      const from = t.renderFrom, to = Math.min(t.renderTo, t.length);
      /* Under the apron the timbers are buried, which is what "embedded" means.
       * The taper is where they stop being visible — a third of the way in the
       * slab is already above the sleeper top — so that is where they stop
       * being drawn, and the last few emerge from the concrete rather than
       * being cut off at a line. */
      const bury = (t.paved || []).map(
        p => [p[0] + PAVE_TAPER * 0.34, p[1] - PAVE_TAPER * 0.34]);
      for (let s = from + 0.4; s < to - 0.4; s += pitch) {
        if (t.blocked(s)) continue;
        if (bury.some(p => s > p[0] && s < p[1])) continue;
        t.at(s, fr);
        B.makeBasis(R3, U3, T3);
        /* Per-sleeper variation, the detail the reference names first: a
         * degree of skew, a few centimetres of shove, and a colour of its own.
         * The rail seats ride the frame's own basis, so none of it unseats a
         * baseplate. */
        const a = (jitter() - 0.5) * 0.030;
        const ca = Math.cos(a), sa = Math.sin(a);
        M.set(ca, 0, sa, 0, 0, 1, 0, 0, -sa, 0, ca, 0, 0, 0, 0, 1);
        B.multiply(M);
        /* Along the track as well as across it. The shove used to be purely
         * lateral, which moves a sleeper without changing the gap to its
         * neighbours — so the PITCH was still perfect to the millimetre, and
         * "perfectly uniform pitch" is what a critic put first in the list.
         * ±45mm on a 610mm pitch is what a tamper leaves and is the difference
         * between a rank of ties and a comb. */
        const along = (jitter() - 0.5) * 0.09;
        B.setPosition(fr.position.x + R3.x * (jitter() - 0.5) * 0.05 + T3.x * along,
                      fr.position.y - jitter() * 0.012,
                      fr.position.z + R3.z * (jitter() - 0.5) * 0.05 + T3.z * along);
        /* Per-sleeper tone and wear. The spread is deliberately wide — a rank
         * of ties all one value is the thing a critic named as "printed hatch
         * marks" — but it is centred below 1 and the hue jitter is small: three
         * channels each free to swing 16% turn a brown timber into a red one,
         * and a railway of randomly red sleepers is worse than a uniform one.
         * The tone carries the variation; the hue only tilts it toward
         * weathered grey or toward fresh oil. */
        const g = 0.62 + jitter() * 0.62;
        const oil = jitter();
        slots.push({m: B.clone(), v: Math.min(2, (jitter() * 3) | 0),
                    c: [g * (0.99 + oil * 0.08), g * (0.99 + oil * 0.02),
                        g * (1.03 - oil * 0.09)]});
      }
    }

    const add = (geo, mat, list, bucket) => {
      if (!list.length) return null;
      const mesh = new THREE.InstancedMesh(geo, mat, list.length);
      for (let i = 0; i < list.length; i++) {
        mesh.setMatrixAt(i, list[i].m);
        if (list[i].c) mesh.setColorAt(i, _col.setRGB(...list[i].c));
      }
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.castShadow = true; mesh.receiveShadow = true;
      mesh.frustumCulled = false;
      this.root.add(mesh);
      this._meshes.push(mesh);
      this._geoms.push(geo);
      if (bucket) bucket.push(mesh);
      return mesh;
    };
    const _col = new THREE.Color();

    const sleeperMat = sleeperMaterial(Tex);
    /* Three timbers, not one. Every sleeper on the railway shared a single
     * geometry and therefore a single set of UVs, so every sleeper had
     * identical grain, identical splits and identical adze marks — which is
     * what a rank of them reading as "printed hatch marks at perfectly uniform
     * pitch" actually is. Three prototypes take three windows of the same map,
     * cost two extra draw calls out of a 450 budget, and are dealt out by the
     * same jitter stream that already varies the tone. */
    const VARIANTS = 3;
    for (let v = 0; v < VARIANTS; v++) {
      const mine = slots.filter(s => s.v === v);
      add(sleeperPrototype(v, thin < 1.35), sleeperMat, mine);
    }
    if (thin < 1.35) {
      add(fastenerPrototype(), sleeperMat, slots.map(s => ({m: s.m, c: s.c})),
          this._fine);
    }

    for (const b of bearers) b.m.scale(new THREE.Vector3(b.len, 1, 1));
    add(bearerPrototype(), sleeperMat,
        bearers.map((b, i) => {
          const g = 0.70 + ((Math.sin(i * 43.1) + 1) * 0.5) * 0.40;
          return {m: b.m, c: [g * 1.00, g * 0.99, g * 1.00]};
        }));

    const solid = (mesher, mat, bucket) => {
      if (mesher.empty) return;
      const geo = mesher.geometry();
      const mesh = new THREE.Mesh(geo, mat);
      mesh.castShadow = true; mesh.receiveShadow = true;
      this.root.add(mesh);
      this._meshes.push(mesh);
      this._geoms.push(geo);
      if (bucket) bucket.push(mesh);
    };
    solid(ballast, ballastMaterial(Tex));
    solid(pave, paveMaterial(Tex));
    solid(rail, railMaterial(Tex));
    solid(kit, kitMaterial(Tex));
    solid(concrete, concreteMaterial(Tex));

    this._add = add;
  }

  /* ---- the structures ------------------------------------------------------
   *
   * Step 5 of the order of operations, and the half of the declaration this
   * file can honour on its own. Where the alignment declared a viaduct or a
   * bridge it gets a deck, piers and abutments; where it declared a tunnel it
   * gets a portal at each end and no permanent way at all in between, because
   * the track is inside the hill and the hill is already drawn.
   *
   * The reason a structure is BUILT rather than approximated with a taller bank
   * is the thing Ryan actually complained about. A line that can only fill has
   * exactly one answer to a valley, and at six metres deep that answer is a
   * sand-coloured wall standing in a field with a railway on top of it. It was
   * on the map for three rounds and it was mistaken for a lighting bug twice.
   *
   * Nothing here reads the terrain except to find the ground a pier stands on:
   * the deck is hung off the alignment's own frames, so it cannot drift out of
   * register with the track sitting on it, and the ballast ribbon is told to
   * stop draping over the side (`Track.decks`) so the stone ends at the parapet
   * instead of pouring 20m down into the valley, which is what it did the first
   * time this was tried.
   */
  /** Draw the structures again, against the ground that now exists.
   *
   *  ---- the ordering trap, and the only honest way out of it ---------------
   *
   *  Build order is terrain → rail, and terrain RE-GRADES after rail publishes
   *  `rail:earthworks`. So every structure in this file was, until this round,
   *  designed against a landform that stops existing about a millisecond later.
   *  The previous round knew this and paid for it in margin: every footing was
   *  driven four metres past the deepest ground sample it could find, on the
   *  reasoning that a builder who runs before the excavator has no other
   *  answer. That is sound for a FOOT — you can always go deeper — and no use
   *  at all for anything whose correctness depends on where the ground ends up:
   *  the top of a retaining wall, and the height of an abutment.
   *
   *  Measured, `harness/rr-abut.mjs`, at the `from` end of branch0's viaduct:
   *  rail sampled ground at −11.21 and declared 6.73 m of fill; the ground
   *  terrain finally built there is −4.52. Six and three quarter metres of
   *  earth arrived under the deck after the deck was drawn — the neighbouring
   *  embankment's batter, which reaches about 1.5× its own height past the end
   *  of the span that asked for it. Terrain is doing exactly what it says it
   *  does, and no margin in this file can see it coming.
   *
   *  terrain.js emits `terrain:regraded` when it has finished. Rail listens and
   *  draws its structures a second time. It costs one rebuild of two meshes —
   *  4,900 triangles, no track geometry, no declaration, no relayout — and it
   *  turns `ctx.ground` from a guess into the answer. Everything below that
   *  reads the ground reads it through `_gnd`, and `_groundFinal` says which of
   *  the two worlds it is in.
   */
  _reseatStructures() {
    if (!this.tracks?.length) return;
    if (this._reseating) return;                 // emitted from inside a rebuild
    this._reseating = true;
    try { this._groundFinal = true; this._buildStructures(); }
    catch (err) { console.warn('[rail] structures could not be re-seated', err); }
    finally { this._reseating = false; }
  }

  /** The ground, as it will be when everything has finished moving earth.
   *  `ctx.ground` delegates straight to `terrain.heightAt`, so after
   *  `terrain:regraded` this is the real surface; before it, it is the surface
   *  terrain built without knowing there was a railway. */
  _gnd(x, z, fallback = null) {
    const g = this.ctx.ground ? this.ctx.ground(x, z) : null;
    return Number.isFinite(g) ? g : fallback;
  }

  _buildStructures() {
    const Tex = this.Tex;
    /* Anything drawn by a previous pass goes first. This method is called twice
     * — once during `_rebuild` and once when terrain has finished re-grading —
     * and two sets of portals in the same hole is worse than either. */
    for (const m of (this._struct || [])) {
      this.root.remove(m);
      m.dispose?.();
      const i = this._meshes.indexOf(m);
      if (i >= 0) this._meshes.splice(i, 1);
      if (m.geometry) {
        const j = this._geoms.indexOf(m.geometry);
        if (j >= 0) this._geoms.splice(j, 1);
        m.geometry.dispose?.();
      }
    }
    this._struct = [];
    /* Two meshers, and therefore one draw call more than the single structures
     * mesh this replaced, because a steel bridge painted like its own piers is
     * most of why the old one read as a plank. Masonry and steelwork are
     * different materials in the world and there is no honest way to say so
     * with one. Measured by ablation in a single session (`harness/rr-cost.mjs`,
     * median of ten frames): the two together are 2 draw calls and ~4,900
     * triangles of 135 and 853,000 at cam=wide, ultra. */
    const con = new Mesher(true);   // masonry, concrete, ballast walls
    const stl = new Mesher(true);   // painted steelwork
    /* Every founded element records where its foot ended up, so the claim
     * "nothing stands in the air" is a number rather than a screenshot:
     * `harness/rr-float.mjs` compares each of these against the ground the
     * terrain finally builds. A geometric test cannot do it — a bridge soffit
     * and a coping course are both downward faces high above the ground, and
     * both are supposed to be.
     *
     * EVERY foot, and the word is doing work. Until this round the list held
     * the centre of each wing-wall block and the centre of each pier, and
     * nothing else: no headwall, no quoin pilaster, no abutment, and one point
     * per block rather than its corners. So the probe reported "0 of 68 above
     * ground" while an operator was looking at a wing wall with daylight under
     * it, and the probe was not wrong — it was silent. An instrument that is
     * silent about the thing beside the thing it measures reads exactly like an
     * instrument that has cleared it. */
    this._footings = [];
    this.abutments = [];
    let spans = 0;
    for (const t of this.tracks) {
      if (!t.frames) continue;
      let works;
      try { works = t.earthworks(); } catch { continue; }
      for (const w of works) {
        try {
          if (w.kind === 'bridge' || w.kind === 'viaduct') {
            this._span(con, stl, t, w); spans++;
          } else if (w.kind === 'tunnel') {
            this._portal(con, t, w, -1);
            this._portal(con, t, w, 1);
            spans++;
          }
        } catch (err) { console.warn('[rail] structure skipped', w.kind, err); }
      }
    }
    this.structures = spans;
    for (const [mesher, mat, name] of [[con, concreteMaterial(Tex), 'rail.structures.masonry'],
                                       [stl, kitMaterial(Tex), 'rail.structures.steel']]) {
      if (mesher.empty) continue;
      const geo = mesher.geometry();
      const mesh = new THREE.Mesh(geo, mat);
      /* Named so the cost of the bridges and the portals can be ablated in one
       * session rather than inferred from two scene totals taken while three
       * other subsystems were being edited. */
      mesh.name = name;
      mesh.castShadow = true; mesh.receiveShadow = true;
      this.root.add(mesh);
      this._meshes.push(mesh);
      this._geoms.push(geo);
      this._struct.push(mesh);
    }
    if (this.ctx.engine) this.ctx.engine.shadowNeedsUpdate = true;
  }

  /** One structural member: a box run between two world points, squared to a
   *  reference up vector. Every diagonal, chord, stiffener and cross girder
   *  below is one of these, which is why a truss costs a page rather than a
   *  file. */
  _member(m, a, b, w, h, upRef) {
    const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
    const L = Math.hypot(dx, dy, dz);
    if (!(L > 0.02)) return;
    const ax = dx / L, ay = dy / L, az = dz / L;
    let sx = upRef[1] * az - upRef[2] * ay;
    let sy = upRef[2] * ax - upRef[0] * az;
    let sz = upRef[0] * ay - upRef[1] * ax;
    let sl = Math.hypot(sx, sy, sz);
    if (!(sl > 1e-6)) { sx = 1; sy = 0; sz = 0; sl = 1; }
    sx /= sl; sy /= sl; sz /= sl;
    const ux = ay * sz - az * sy, uy = az * sx - ax * sz, uz = ax * sy - ay * sx;
    slab(m, [a[0] - sx * w / 2 - ux * h / 2,
             a[1] - sy * w / 2 - uy * h / 2,
             a[2] - sz * w / 2 - uz * h / 2],
         [dx, dy, dz], [sx * w, sy * w, sz * w], [ux * h, uy * h, uz * h], 1.2);
  }

  /** A wing wall: the retaining wall that takes a headwall or an abutment into
   *  the bank beside it.
   *
   *  ---- what was wrong, and it was not the footings ------------------------
   *
   *  The previous round built it as stepped solids whose bases were driven well
   *  below the ground, and that part works: `harness/rr-daylight.mjs`, which
   *  rasterises every masonry triangle into a plan grid and keeps the LOWEST
   *  surface in each column, finds 0.6 m2 of non-deck masonry with nothing
   *  under it. The wall is not hanging.
   *
   *  What it was doing is standing. The top of each block came off a straight
   *  ramp from the headwall down to a fixed height, with no reference to the
   *  ground at all, so on the side where the hillside falls away the wall
   *  carried on out into the open at full height with a void behind it — and
   *  what you see then is the BACK of a stepped wall, in shadow, with hillside
   *  visible through the gaps under each step. That is what "the wing walls
   *  float" is a description of, and it is why the footing probe could report
   *  zero in perfect good faith.
   *
   *  A wing wall is a retaining wall. Its top belongs to the earth it retains,
   *  not to a ramp: where the bank behind stands high the wall is tall, and
   *  where the bank has fallen away there is nothing to retain and the wall
   *  should stop. So each block's top is the LOWER of the design ramp and the
   *  retained ground plus a 550mm parapet, held monotonically descending so the
   *  courses still step; and the run ends at the first block whose top has come
   *  down to within 300mm of the ground in front of it. The wall dies into the
   *  hillside instead of walking out of it.
   *
   *  Which side is "behind" is not assumed — both sides are sampled and the
   *  higher one is the bank. `side` is handed in as a perpendicular whose sign
   *  depends on which way the splay went, and reasoning about that sign is how
   *  this kind of code gets built inside out. */
  _wing(m, from, dir, side, len, yTop, yDrop, thick, tone, yFloor) {
    const fb = yTop - 8;
    const at = (x, z) => this._gnd(x, z, fb);
    m.tint(tone[0], tone[1], tone[2]);
    const STEPS = 5;
    let prevTop = Infinity, last = null;
    for (let k = 0; k < STEPS; k++) {
      const a = k / STEPS, b = (k + 1) / STEPS;
      const pa = [from[0] + dir[0] * len * a, 0, from[2] + dir[2] * len * a];
      const pb = [from[0] + dir[0] * len * b, 0, from[2] + dir[2] * len * b];
      /* The LOWEST ground under the block's own footprint, not the height at
       * its middle. A wing wall splays out of a cutting and across a falling
       * hillside, so the middle of a two-metre block can stand several metres
       * above its own outer corner. Nine samples, and the worst of them wins. */
      let g = Infinity;
      for (let q = 0; q <= 2; q++) {
        for (let r = -1; r <= 1; r++) {
          const u = q / 2;
          const sx = pa[0] + (pb[0] - pa[0]) * u + side[0] * r * thick * 0.6;
          const sz = pa[2] + (pb[2] - pa[2]) * u + side[2] * r * thick * 0.6;
          g = Math.min(g, at(sx, sz));
        }
      }
      if (!isFinite(g)) g = fb;
      /* The two flanks, a metre clear of the wall on each side, three samples
       * along each. The higher flank is the bank being retained; the lower one
       * is the face that will be looked at. */
      const flank = sg => {
        let s = 0;
        for (let q = 0; q <= 2; q++) {
          const u = q / 2;
          s += at(pa[0] + (pb[0] - pa[0]) * u + side[0] * sg * (thick / 2 + 0.9),
                  pa[2] + (pb[2] - pa[2]) * u + side[2] * sg * (thick / 2 + 0.9));
        }
        return s / 3;
      };
      const f1 = flank(1), f2 = flank(-1);
      const gBack = Math.max(f1, f2), gFront = Math.min(f1, f2);
      /* The design ramp, and then the earth's opinion of it. The first block
       * always keeps the ramp: it is bonded into the headwall and has to leave
       * it at the headwall's own height whatever the ground is doing. */
      const ramp = yTop - yDrop * ((a + b) / 2);
      let top = k === 0 ? ramp : Math.min(prevTop, ramp, gBack + 0.55);
      if (k > 0 && top < gFront + 0.30) break;   // nothing left to retain
      prevTop = top;
      /* Down to the floor of the thing it retains, and four metres further.
       *
       * Two separate traps, both paid for by an earlier round. Taking the LOWER
       * of "ground" and "top minus a bit" leaves a slab hanging in mid-air the
       * moment the hillside behind stands higher than the wall, which at a
       * tunnel mouth it always does: the hill is why there is a tunnel. And on
       * the FIRST pass `ctx.ground` is the ground as it stands while rail is
       * building, while terrain applies rail's own declaration afterwards — so
       * a footing sized to that sample can stand over its own excavation. Four
       * metres of buried masonry costs twelve triangles nobody will ever see.
       * The margin is kept even on the re-seated pass, because it is free and
       * because `_groundFinal` is a claim about terrain, not about buildings.js
       * or anything else that may yet move earth. */
      const bot = Math.min(g - 1.4, yFloor) - 4.0;
      /* All four corners of the base, not the centre of it. One point per block
       * cannot see a block that is founded on a slope, which is the case this
       * whole method exists to handle. */
      for (const [px, pz] of [pa, pb].flatMap(p => [
        [p[0] - side[0] * thick / 2, p[2] - side[2] * thick / 2],
        [p[0] + side[0] * thick / 2, p[2] + side[2] * thick / 2]])) {
        this._footings?.push([px, bot, pz, at(px, pz)]);
      }
      slab(m, [pa[0] - side[0] * thick / 2, bot, pa[2] - side[2] * thick / 2],
           [pb[0] - pa[0], 0, pb[2] - pa[2]],
           [side[0] * thick, 0, side[2] * thick],
           [0, top - bot, 0], 1.7);
      /* A coping course along the top, proud each side. It is 120mm of stone
       * and it is what stops a wall reading as an extruded rectangle. */
      m.tint(tone[0] * 1.06, tone[1] * 1.05, tone[2] * 1.03);
      slab(m, [pa[0] - side[0] * (thick / 2 + 0.14), top,
               pa[2] - side[2] * (thick / 2 + 0.14)],
           [pb[0] - pa[0], 0, pb[2] - pa[2]],
           [side[0] * (thick + 0.28), 0, side[2] * (thick + 0.28)],
           [0, 0.26, 0], 1.4);
      m.tint(tone[0], tone[1], tone[2]);
      last = {pb, bot, top, g};
    }
    /* ---- and the wall dies into the bank -----------------------------------
     *
     * A stepped wall that simply stops leaves its last coping course sticking
     * out of the hillside as a stone shelf with a shadow under it, which is the
     * detached-slab read the whole of this method is trying to get rid of. The
     * real detail is a wall that ramps its top down below the ground over its
     * last couple of metres, so the masonry disappears into the bank rather
     * than being cut off by it. `slab` cannot do it — its top is flat — so this
     * is a swept box with two different ring heights, which is what the ramp
     * is. No coping on it: a coping that goes underground is a coping nobody
     * asked for. */
    if (last) {
      const dieLen = len / STEPS * 0.85;
      const pc = [last.pb[0] + dir[0] * dieLen, 0, last.pb[2] + dir[2] * dieLen];
      const gEnd = Math.min(at(pc[0], pc[2]),
                            at(pc[0] + side[0] * thick, pc[2] + side[2] * thick),
                            at(pc[0] - side[0] * thick, pc[2] - side[2] * thick));
      const topB = Math.min(last.top, gEnd - 0.35);
      const bot = Math.min(last.bot, topB - 1.2);
      const ring = (p, ty) => [
        [p[0] - side[0] * thick / 2, bot, p[2] - side[2] * thick / 2],
        [p[0] + side[0] * thick / 2, bot, p[2] + side[2] * thick / 2],
        [p[0] + side[0] * thick / 2, ty, p[2] + side[2] * thick / 2],
        [p[0] - side[0] * thick / 2, ty, p[2] - side[2] * thick / 2]];
      const ctr = (p, ty) => [p[0], (bot + ty) / 2, p[2]];
      const A = ring(last.pb, last.top), B = ring(pc, topB);
      ringSweep(m, A, B, ctr(last.pb, last.top), ctr(pc, topB), 0, 1.6);
      facing(m, B[0], B[1], B[2], B[3], dir);
      for (const p of [A[0], A[1], B[0], B[1]]) {
        this._footings?.push([p[0], bot, p[2], at(p[0], p[2])]);
      }
    }
  }

  /** A pier or an abutment: a battered masonry shaft with a plinth at the foot
   *  and an impost cap at the head, squared to the track rather than to the
   *  world.
   *
   *  Two things it now does that the old prism did not. It stands on the LOWEST
   *  of its four footprint corners rather than on the height at its own centre,
   *  so a leg on a side slope is buried at the high corner instead of floating
   *  at the low one — that gap is why the old viaduct's single pier hung in the
   *  air. And where the span is a water crossing it is taken below the water
   *  plane as well, because a pier that stops at the surface is a pier standing
   *  on the water. */
  _pier(m, f, i, hw, hd, top, tone, wet) {
    const rx = f.right[i * 3], rz = f.right[i * 3 + 2];
    const tx = f.tan[i * 3], tz = f.tan[i * 3 + 2];
    const cx = f.pos[i * 3], cz = f.pos[i * 3 + 2];
    const rl = Math.hypot(rx, rz) || 1, tl = Math.hypot(tx, tz) || 1;
    const ux = rx / rl, uz = rz / rl, vx = tx / tl, vz = tz / tl;
    const at = (a, b, y) => [cx + ux * a + vx * b, y, cz + uz * a + vz * b];
    const corners = [];
    let g = Infinity;
    for (const a of [-hw, hw]) {
      for (const b of [-hd, hd]) {
        const p = at(a, b, 0);
        const gg = this._gnd(p[0], p[2], 0);
        corners.push([p[0], p[2], gg]);
        g = Math.min(g, gg);
      }
    }
    if (!isFinite(g)) g = top - 6;
    /* 2.8m and not 0.3: on the first pass the ground sampled here is the ground
     * BEFORE terrain applies this file's own earthworks, so a leg founded tight
     * to the sample can end up standing over a cutting that was dug after it
     * was drawn. */
    let base = g - 2.8;
    if (Number.isFinite(wet)) base = Math.min(base, wet - 2.6);
    base = Math.min(base, top - 1.6);
    /* Four feet, one per corner of the plinth, rather than one at the middle. */
    for (const [px, pz, gg] of corners) this._footings?.push([px, base, pz, gg]);

    const CAP = 0.62, PL = Math.min(1.0, (top - CAP - base) * 0.28);
    const shaftTop = top - CAP, shaftBot = base + PL;
    /* The batter: 3.5% off each face over the height of the shaft, so the leg
     * is visibly thicker at the bottom. */
    const h = Math.max(0.6, shaftTop - shaftBot);
    const k = Math.min(0.22, 0.035 * h / Math.max(hw, 0.7));
    const ring = (a, b, y) => [at(-a, -b, y), at(a, -b, y), at(a, b, y), at(-a, b, y)];
    const ctr = y => [cx, y, cz];
    m.tint(tone, tone * 0.995, tone * 0.965);
    ringSweep(m, ring(hw, hd, shaftBot), ring(hw * (1 - k), hd * (1 - k), shaftTop),
              ctr(shaftBot), ctr(shaftTop), 0, h / 2.6);
    /* Plinth and cap, both oversailing, both flat-topped. */
    if (PL > 0.05) {
      m.tint(tone * 0.93, tone * 0.93, tone * 0.90);
      const P0 = ring(hw + 0.30, hd + 0.30, base), P1 = ring(hw + 0.30, hd + 0.30, shaftBot);
      ringSweep(m, P0, P1, ctr(base), ctr(shaftBot), 0, 0.5);
      const t4 = ring(hw + 0.30, hd + 0.30, shaftBot);
      facing(m, t4[0], t4[1], t4[2], t4[3], [0, 1, 0]);
    }
    m.tint(tone * 1.05, tone * 1.04, tone * 1.0);
    const C0 = ring(hw + 0.26, hd + 0.26, shaftTop), C1 = ring(hw + 0.26, hd + 0.26, top);
    ringSweep(m, C0, C1, ctr(shaftTop), ctr(top), 0, 0.4);
    facing(m, C1[0], C1[1], C1[2], C1[3], [0, 1, 0]);
    return {at, base, top};
  }

  /** A span: a ballasted deck carried on riveted steel trusses, standing on
   *  masonry piers and landing on an abutment with wing walls at each end.
   *
   *  ---- why a truss and not a slab -----------------------------------------
   *
   *  The brief is "more fun classic train set bridge please", and the operator
   *  is right that the old one failed the only test that matters: at 60m it was
   *  a flat dark plank with a rectangle under it, and nothing in the silhouette
   *  said bridge. A bridge is read from its SIDE and from its skyline, and the
   *  two things that carry that read are the truss standing above the deck and
   *  the legs going down into whatever is being crossed. `refs/trainsim-05.jpg`
   *  is the shape: a through truss with the train inside it, top laterals
   *  across the sky, square piers walking into the water.
   *
   *  So: two Pratt trusses with inclined end posts, one each side of the road,
   *  verticals and diagonals at every panel point, a lateral frame across the
   *  top, cross girders under the deck, and a portal frame at each end that the
   *  train runs through. The deck between them is the trough the ballast
   *  already knows to stay inside (`Track.decks`), so the stone ends at the
   *  kerb instead of pouring down the valley.
   *
   *  Everything is hung off the alignment's own frames, so a truss on a curve
   *  curves with the track and cannot drift out of register with it.
   *
   *  ---- and why the ends of it are not the ends of the span -----------------
   *
   *  There are three lengths here and they are deliberately different. The
   *  DECLARED span is what terrain must not grade. The DECK TROUGH is built
   *  over all of it, because it is what the ballast sits in. The ABUTMENTS, the
   *  wing walls and the truss are placed where the ground actually falls away
   *  from the soffit, which is inside the span by however far the neighbouring
   *  embankment's batter reaches — and between the two there is solid masonry
   *  (`skirt`) rather than a deck soffit with a shadow under it. Before this
   *  round all three were the same length, and the visible consequence was four
   *  bridges out of four with their abutments underground. */
  _span(m, s, t, w) {
    const f = t.frames;
    const i0 = Math.max(0, w.i0), i1 = Math.min(f.count - 1, w.i1);
    if (!(i1 > i0 + 1)) return;
    const wet = w.kind === 'bridge' && Number.isFinite(this.waterY)
      ? this.waterY : null;
    /* Relative to the railhead. The ballast toe is 627mm down, so the deck
     * surface is just under it and the kerb stands proud of the shoulder. */
    const DECK = BALLAST_TOE - 0.03, SOFFIT = DECK - 1.05;
    const KERB = SHOULDER_TOP + 0.26;
    const HW = TOE_X + 0.30, IW = SHOULDER_X + 0.10;
    const TL = HW + 0.24;                 // where the truss stands
    const BC = SOFFIT + 0.42;             // bottom chord
    const TC = 5.05;                      // top chord: over the loading gauge
    const P = (i, lat, ver) => {
      const q = Math.max(0, Math.min(f.count - 1, i));
      return [f.pos[q * 3] + f.right[q * 3] * lat + f.up[q * 3] * ver,
              f.pos[q * 3 + 1] + f.right[q * 3 + 1] * lat + f.up[q * 3 + 1] * ver,
              f.pos[q * 3 + 2] + f.right[q * 3 + 2] * lat + f.up[q * 3 + 2] * ver];
    };
    const upAt = i => {
      const q = Math.max(0, Math.min(f.count - 1, i));
      return [f.up[q * 3], f.up[q * 3 + 1], f.up[q * 3 + 2]];
    };
    const stride = Math.max(1, Math.round(2.4 / f.step));

    /* ---- where the abutments go, and why it is not the end of the span ------
     *
     * The declaration and the drawing answer two different questions. The span
     * is the chainage that must not be graded, and it is correctly the whole
     * length over which the line is above the valley: at branch0's viaduct the
     * fill rail sampled at the `from` end was 6.73 m, comfortably past the 6 m
     * that makes a viaduct rather than an embankment.
     *
     * The abutment is a different thing: it is the wall at the point where the
     * deck actually leaves the ground, and that point is not where the span
     * ends, because terrain's grading of the NEIGHBOURING fill span reaches
     * past its own end by roughly one and a half times its height. Measured
     * before this change (`harness/rr-abut.mjs`): all four deck ends in the
     * world had their soffit BELOW the finished ground — worst −1.11 m — with
     * ground beyond the end sitting exactly at formation level. The abutments
     * were not badly drawn, they were underground, and a viaduct with no
     * visible abutment reads as a length of track that stops rather than a
     * bridge that spans.
     *
     * So the deck trough is still built over the whole declared span — the last
     * few metres of it are inside the bank and cost nothing to leave there —
     * and the abutments, the wing walls and the truss start where there is a
     * hole to span. `_gnd` is the ground terrain finally built, because
     * `_reseatStructures` draws all of this again once terrain says it has
     * finished; on the first pass it is the pre-grade ground, which reads the
     * valley at full depth and puts the abutments at the span ends exactly as
     * before. */
    const SHOW = 2.0;                     // abutment face wanted above ground
    const clearAt = i => {
      const q = Math.max(0, Math.min(f.count - 1, i));
      const g = this._gnd(f.pos[q * 3], f.pos[q * 3 + 2]);
      return g === null ? 99 : f.pos[q * 3 + 1] + SOFFIT - g;
    };
    let a0 = i0, a1 = i1;
    for (const want of [SHOW, SHOW * 0.6]) {
      let b0 = i0, b1 = i1;
      while (b0 < i1 && clearAt(b0) < want) b0++;
      while (b1 > b0 && clearAt(b1) < want) b1--;
      /* A deck shorter than 10 m is not a bridge, it is a culvert with a truss
       * on it. If trimming to this clearance leaves less than that, the span
       * does not cross anything worth an abutment and the declared ends stand. */
      if ((b1 - b0) * f.step >= 10) { a0 = b0; a1 = b1; break; }
    }

    /* ---- the deck ---------------------------------------------------------
     * Three convex prisms rather than one concave trough section: a concave
     * section swept as one ring gives its inside faces an outward test that
     * points the wrong way, and the wrong way is black. */
    const prism = (lat0, lat1, v0, v1, tone) => {
      m.tint(tone[0], tone[1], tone[2]);
      const ring = i => [P(i, lat0, v0), P(i, lat1, v0), P(i, lat1, v1), P(i, lat0, v1)];
      const ctr = i => P(i, (lat0 + lat1) / 2, (v0 + v1) / 2);
      let prev = i0;
      for (let i = i0 + stride; ; i += stride) {
        const b = Math.min(i, i1);
        ringSweep(m, ring(prev), ring(b), ctr(prev), ctr(b),
                  prev * f.step / 3.2, b * f.step / 3.2);
        prev = b;
        if (b >= i1) break;
      }
      const A = ring(i0), B = ring(i1);
      facing(m, A[0], A[1], A[2], A[3],
             [-f.tan[i0 * 3], -f.tan[i0 * 3 + 1], -f.tan[i0 * 3 + 2]]);
      facing(m, B[0], B[1], B[2], B[3],
             [f.tan[i1 * 3], f.tan[i1 * 3 + 1], f.tan[i1 * 3 + 2]]);
    };
    prism(-HW, HW, SOFFIT, DECK, [1.06, 1.05, 1.01]);
    prism(-HW, -IW, DECK, KERB, [1.14, 1.12, 1.08]);
    prism(IW, HW, DECK, KERB, [1.14, 1.12, 1.08]);

    /* ---- the approach walls ------------------------------------------------
     *
     * Between the abutment and the end of the declared span the deck trough is
     * still there — it carries the ballast — but it is over ground that has
     * risen to meet it, so its soffit hangs a metre or two clear with a shadow
     * under it and nothing holding it up. On a real bridge that length is solid:
     * the abutment is the front wall of a mass of masonry that the embankment is
     * tipped against. So it is solid here, swept between the deck's own edges,
     * from the soffit down to below whatever ground is under each station. It
     * is buried at the bank end and shows a foot or two of wall beside the
     * abutment, which is exactly the transition that was missing. */
    const skirt = (iA, iB) => {
      const lo = Math.min(iA, iB), hi = Math.max(iA, iB);
      if (!(hi > lo)) return;
      m.tint(1.02, 1.01, 0.975);
      const ring = i => {
        const a = P(i, -HW + 0.05, SOFFIT), b = P(i, HW - 0.05, SOFFIT);
        const bot = Math.min(this._gnd(a[0], a[2], a[1] - 4),
                             this._gnd(b[0], b[2], b[1] - 4), a[1], b[1]) - 1.8;
        this._footings?.push([(a[0] + b[0]) / 2, bot, (a[2] + b[2]) / 2,
                              this._gnd((a[0] + b[0]) / 2, (a[2] + b[2]) / 2, bot)]);
        return [[a[0], bot, a[2]], [b[0], bot, b[2]], b, a];
      };
      const ctr = R => [(R[0][0] + R[2][0]) / 2, (R[0][1] + R[2][1]) / 2,
                        (R[0][2] + R[2][2]) / 2];
      let prev = ring(lo);
      const first = prev;
      for (let i = lo + stride; ; i += stride) {
        const c = Math.min(i, hi);
        const R = ring(c);
        ringSweep(m, prev, R, ctr(prev), ctr(R), 0, 1.4);
        prev = R;
        if (c >= hi) break;
      }
      /* Both ends capped: one is against the abutment and one is in the bank,
       * and an open tube read from inside renders at ambient — near black —
       * which is the failure the whole structures pass was rebuilt to fix. */
      const tA = [f.tan[lo * 3], f.tan[lo * 3 + 1], f.tan[lo * 3 + 2]];
      facing(m, first[0], first[1], first[2], first[3], [-tA[0], -tA[1], -tA[2]]);
      const tB = [f.tan[hi * 3], f.tan[hi * 3 + 1], f.tan[hi * 3 + 2]];
      facing(m, prev[0], prev[1], prev[2], prev[3], tB);
    };
    skirt(i0, a0);
    skirt(a1, i1);

    /* ---- piers and abutments ----------------------------------------------
     * A bay is about 24m, which is a plate girder's honest reach and also the
     * length of truss this site can carry without looking like a toy. */
    const bays = Math.max(1, Math.round((a1 - a0) * f.step / 24));
    const cut = [];
    for (let q = 0; q <= bays; q++) cut.push(Math.round(a0 + (a1 - a0) * (q / bays)));
    for (let q = 0; q <= bays; q++) {
      const i = cut[q];
      const end = q === 0 || q === bays;
      const hw = end ? HW + 0.85 : 2.15, hd = end ? 2.30 : 1.30;
      const top = f.pos[i * 3 + 1] + SOFFIT + 0.02;
      const pier = this._pier(m, f, i, hw, hd, top, end ? 1.06 : 1.14, wet);
      if (!end) continue;
      /* Where the abutment actually ended up, published so it can be measured.
       * `harness/rr-abut.mjs` was reporting the soffit against the ground at the
       * declared span END, which after this change is not where the abutment
       * stands — and an instrument aimed at the old answer reports no change
       * however much changed. It reads this, and takes the ground from terrain
       * itself rather than from anything said here. */
      (this.abutments || (this.abutments = [])).push(
        {track: t.name, s: i * f.step, x: f.pos[i * 3], z: f.pos[i * 3 + 2],
         railhead: f.pos[i * 3 + 1], soffit: top, base: pier.base,
         spanFrom: w.from, spanTo: w.to, kind: w.kind});
      /* The abutment's wing walls, splayed back along the approach bank at
       * 40 degrees. Without them the deck lands on a block and the embankment
       * simply stops beside it, which is the other half of "not bridgey". */
      const away = q === 0 ? -1 : 1;
      const rx = f.right[i * 3], rz = f.right[i * 3 + 2];
      const tx = f.tan[i * 3], tz = f.tan[i * 3 + 2];
      const rl = Math.hypot(rx, rz) || 1, tl = Math.hypot(tx, tz) || 1;
      const ux = rx / rl, uz = rz / rl;
      const vx = (tx / tl) * away, vz = (tz / tl) * away;
      const C = Math.cos(0.70), S = Math.sin(0.70);
      for (const sg of [-1, 1]) {
        const dir = [vx * C + ux * sg * S, 0, vz * C + uz * sg * S];
        const side = [-dir[2], 0, dir[0]];
        this._wing(m, pier.at(sg * (hw - 0.5), away * (hd - 0.4), 0), dir, side,
                   9.5, top - 0.10, 2.9, 1.05, [1.12, 1.10, 1.06],
                   pier.base + 0.4);
      }
    }

    /* ---- the steelwork ----------------------------------------------------- */
    const STEEL = [0.30, 0.375, 0.335];      // works green, and it is on purpose:
    const LIGHT = [0.40, 0.475, 0.425];      // nothing else on this site is green
    for (let q = 0; q < bays; q++) {
      const a = cut[q], b = cut[q + 1];
      if (!(b > a + 1)) continue;
      const bayLen = (b - a) * f.step;
      const n = Math.max(3, Math.min(9, Math.round(bayLen / 4.4)));
      const node = k => a + Math.round((b - a) * (k / n));
      const up = upAt(Math.round((a + b) / 2));
      /* chords, verticals, diagonals — one truss each side */
      for (const sg of [-1, 1]) {
        const lat = sg * TL;
        s.tint(STEEL[0], STEEL[1], STEEL[2]);
        for (let k = 0; k < n; k++) {
          this._member(s, P(node(k), lat, BC), P(node(k + 1), lat, BC),
                       0.36, 0.44, up);
        }
        s.tint(LIGHT[0], LIGHT[1], LIGHT[2]);
        for (let k = 1; k < n - 1; k++) {
          this._member(s, P(node(k), lat, TC), P(node(k + 1), lat, TC),
                       0.34, 0.34, up);
        }
        /* inclined end posts — the members the train actually runs between */
        s.tint(STEEL[0], STEEL[1], STEEL[2]);
        this._member(s, P(node(0), lat, BC), P(node(1), lat, TC), 0.40, 0.40, up);
        this._member(s, P(node(n), lat, BC), P(node(n - 1), lat, TC), 0.40, 0.40, up);
        for (let k = 1; k < n; k++) {
          this._member(s, P(node(k), lat, BC), P(node(k), lat, TC), 0.28, 0.28, up);
        }
        /* Pratt diagonals: they lean toward the middle from both ends, which is
         * the pattern a truss is recognised by even by somebody who has never
         * named one. */
        for (let k = 1; k < n - 1; k++) {
          const lo = k < n / 2 ? k + 1 : k;
          const hi = k < n / 2 ? k : k + 1;
          this._member(s, P(node(lo), lat, BC), P(node(hi), lat, TC), 0.24, 0.24, up);
        }
      }
      /* top lateral frame, and the cross girders under the deck */
      s.tint(LIGHT[0], LIGHT[1], LIGHT[2]);
      for (let k = 1; k < n; k++) {
        this._member(s, P(node(k), -TL, TC), P(node(k), TL, TC), 0.26, 0.26, up);
      }
      for (let k = 1; k < n - 1; k++) {
        this._member(s, P(node(k), -TL, TC), P(node(k + 1), TL, TC), 0.16, 0.16, up);
        this._member(s, P(node(k), TL, TC), P(node(k + 1), -TL, TC), 0.16, 0.16, up);
      }
      s.tint(STEEL[0], STEEL[1], STEEL[2]);
      for (let k = 0; k <= n; k++) {
        this._member(s, P(node(k), -TL, SOFFIT - 0.24), P(node(k), TL, SOFFIT - 0.24),
                     0.32, 0.50, up);
      }
      /* knee braces in the portal frame at each end of the bay: the diagonal
       * gusset between the end post and the first top lateral. */
      for (const kEnd of [1, n - 1]) {
        for (const sg of [-1, 1]) {
          this._member(s, P(node(kEnd), sg * TL, TC - 0.9),
                       P(node(kEnd), sg * (TL - 1.15), TC), 0.20, 0.20, up);
        }
      }
    }
  }

  /** A tunnel mouth.
   *
   *  ---- what was wrong with the old one ------------------------------------
   *
   *  Every part of it was a single quad. The headwall was one flat card with an
   *  arch-shaped hole; the jambs were cards; each wing wall was ONE quad with no
   *  thickness splayed six metres into the hill; the parapet was a card. There
   *  was no barrel at all, so the arch was a hole in a wall with the hillside
   *  visible through it — a black decal, exactly as the brief says. And the
   *  whole assembly was wound so its faces looked into the hill, so the one
   *  thing it did have, a face, rendered at ambient and read as a slab of soot.
   *
   *  ---- what a portal is ----------------------------------------------------
   *
   *  Five things, and all five are here now:
   *
   *    the BARREL   fifteen metres of arched bore, faces turned inward and
   *                 painted near black, so the opening is a hole with depth in
   *                 it rather than a shape cut out of a wall;
   *    the REVEAL   the headwall has THICKNESS, so the arch is recessed by most
   *                 of a metre and throws its own shadow across the bore;
   *    the RING     an archivolt standing 200mm proud of the face, with an
   *                 impost band at the springing and pilasters at the quoins —
   *                 the three lines that make masonry read as masonry;
   *    the COPING   a course across the top, oversailing both ways;
   *    the WINGS    two splayed retaining walls at 40 degrees, built as stepped
   *                 solids whose bases follow the ground down, holding back the
   *                 cutting the line runs in to reach the bore.
   *
   *  `dir` is +1 when the bore runs on past this chainage in the direction of
   *  increasing arc length and −1 when it runs back — it decides which way the
   *  face looks, and getting it wrong builds a portal facing into its own hill.
   */
  _portal(m, t, w, dir) {
    const f = t.frames;
    const s = dir < 0 ? w.from : w.to;
    const i = Math.max(1, Math.min(f.count - 2, Math.round(s / f.step)));
    const rx = f.right[i * 3], rz = f.right[i * 3 + 2];
    const tx = f.tan[i * 3], tz = f.tan[i * 3 + 2];
    const rl = Math.hypot(rx, rz) || 1, tl = Math.hypot(tx, tz) || 1;
    const ux = rx / rl, uz = rz / rl;
    /* `out` is out of the hill. `dir` is −1 at the bore's `from`, where the
     * bore runs on with s INCREASING and the daylight is therefore behind us,
     * and +1 at its `to`. Get this backwards and the whole portal is built
     * inside out: the barrel points at the sky and the face looks into the
     * hill. It was got backwards once during this round and the picture is
     * unmistakable, which is the only reason it is worth a comment. */
    const vx = (tx / tl) * dir, vz = (tz / tl) * dir;
    const cx = f.pos[i * 3], cy = f.pos[i * 3 + 1], cz = f.pos[i * 3 + 2];
    const base = cy + BALLAST_TOE - 0.22;          // the invert
    const P = (lat, y, out) =>
      [cx + ux * lat + vx * out, base + y, cz + uz * lat + vz * out];

    /* The bore: a semicircle on two straight legs, which is the horseshoe every
     * single-track masonry portal in the world is built to. 2.6m of half-width
     * clears the loading gauge with the 100mm a tunnel actually leaves. */
    const HB = 2.60, SPR = 2.72, ARCH = 2.58;
    const FACE = 5.05, TOP = SPR + ARCH + 1.18;
    const THK = 0.95;                     // the reveal
    const RING = 0.55, PROUD = 0.20;      // the archivolt
    const BORE = Math.min(16, Math.max(6, (w.to - w.from) * 0.5));

    /* How far down the wall goes. Sampled across the whole face and both sides
     * of the reveal, so the headwall is buried where the ground is low and
     * never stands on a plinth of air. */
    let g = Infinity;
    for (const lat of [-FACE, -FACE / 2, 0, FACE / 2, FACE]) {
      for (const out of [0, THK]) {
        const p = P(lat, 0, out);
        g = Math.min(g, this._gnd(p[0], p[2], base - 4));
      }
    }
    if (!isFinite(g)) g = base - 4;
    const DOWN = Math.max(5.2, Math.min(16, base - (g - 1.8)));
    /* The headwall's own feet, at the four corners of its base, and the two
     * quoin pilasters that run down the outer edges of the face. These are
     * masonry that meets ground and until this round none of them was in
     * `_footings` at all — the probe reported on wing walls and piers and was
     * silent about the largest single piece of stone in the world. */
    for (const lat of [-FACE, FACE]) {
      for (const out of [0, THK]) {
        const p = P(lat, -DOWN, out);
        this._footings?.push([p[0], p[1], p[2], this._gnd(p[0], p[2], p[1])]);
      }
      const q = P(lat * 0.94, -DOWN, THK + 0.08);   // the pilaster's own foot
      this._footings?.push([q[0], q[1], q[2], this._gnd(q[0], q[2], q[1])]);
    }

    const arc = [];
    for (let q = 0; q <= 14; q++) {
      const a = (q / 14) * Math.PI;
      arc.push([-Math.cos(a) * HB, SPR + Math.sin(a) * ARCH]);
    }
    const bore = [[-HB, 0], ...arc, [HB, 0]];
    const ringAt = (prof, out) => prof.map(([lat, y]) => P(lat, y, out));
    const boreCtr = out => P(0, SPR * 0.6, out);

    /* ---- the barrel: fifteen metres of hole ------------------------------- */
    m.tint(0.075, 0.075, 0.085);
    ringSweep(m, ringAt(bore, THK), ringAt(bore, -BORE),
              boreCtr(THK), boreCtr(-BORE), 0, BORE / 3, true);
    m.tint(0.03, 0.03, 0.035);
    {
      const back = ringAt(bore, -BORE);
      const c = boreCtr(-BORE);
      for (let q = 0; q < back.length; q++) {
        m.tri(c, back[q], back[(q + 1) % back.length]);
      }
    }

    /* ---- the headwall ------------------------------------------------------
     * The face is the band between the bore and the rectangle around it, fanned
     * from the springing so the arch has a soffit ring rather than a cut edge.
     * The four outer walls give it its thickness, and the thickness is the
     * reveal. */
    const outer = ([lat, y]) => {
      const dx = lat, dy = y - SPR;
      const k = Math.max(Math.abs(dx) / FACE,
                         dy > 0 ? dy / (TOP - SPR) : -dy / (SPR + DOWN));
      return k > 1e-6 ? [dx / k, SPR + dy / k] : [FACE, SPR];
    };
    m.tint(1.22, 1.20, 1.15);
    for (let q = 0; q < bore.length; q++) {
      const a = bore[q], b = bore[(q + 1) % bore.length];
      const A = outer(a), B = outer(b);
      facing(m, P(a[0], a[1], THK), P(b[0], b[1], THK),
             P(B[0], B[1], THK), P(A[0], A[1], THK), [vx, 0, vz],
             0, 0, 1.6, 1.6);
    }
    const rect = [[-FACE, -DOWN], [FACE, -DOWN], [FACE, TOP], [-FACE, TOP]];
    m.tint(1.16, 1.14, 1.10);
    ringSweep(m, ringAt(rect, 0), ringAt(rect, THK),
              P(0, (TOP - DOWN) / 2, 0), P(0, (TOP - DOWN) / 2, THK), 0, 0.4);

    /* ---- the archivolt, the impost band and the quoins -------------------- */
    const grown = arc.map(([lat, y]) => {
      const dx = lat, dy = y - SPR;
      const L = Math.hypot(dx, dy) || 1;
      return [lat + dx / L * RING, y + dy / L * RING];
    });
    m.tint(1.44, 1.41, 1.35);
    for (let q = 0; q < arc.length - 1; q++) {
      const a = arc[q], b = arc[q + 1], g0 = grown[q], g1 = grown[q + 1];
      /* radially out of the arch, in world axes: the ring's inside faces the
       * bore and its outside faces the face. */
      const mlat = (a[0] + b[0]) / 2, mv = (a[1] + b[1]) / 2 - SPR;
      const L = Math.hypot(mlat, mv) || 1;
      const nO = [ux * mlat / L, mv / L, uz * mlat / L];
      const nI = [-nO[0], -nO[1], -nO[2]];
      facing(m, P(a[0], a[1], THK), P(b[0], b[1], THK),
             P(b[0], b[1], THK + PROUD), P(a[0], a[1], THK + PROUD), nI,
             0, 0, 0.7, 0.7);
      facing(m, P(g0[0], g0[1], THK), P(g1[0], g1[1], THK),
             P(g1[0], g1[1], THK + PROUD), P(g0[0], g0[1], THK + PROUD), nO,
             0, 0, 0.7, 0.7);
      facing(m, P(a[0], a[1], THK + PROUD), P(b[0], b[1], THK + PROUD),
             P(g1[0], g1[1], THK + PROUD), P(g0[0], g0[1], THK + PROUD),
             [vx, 0, vz], 0, 0, 1.2, 1.2);
    }
    /* impost band at the springing, and a quoin pilaster at each outer edge */
    m.tint(1.38, 1.36, 1.30);
    slab(m, P(-FACE, SPR - 0.18, THK),
         [P(FACE, 0, 0)[0] - P(-FACE, 0, 0)[0], 0, P(FACE, 0, 0)[2] - P(-FACE, 0, 0)[2]],
         [vx * 0.17, 0, vz * 0.17], [0, 0.36, 0], 1.3);
    for (const sg of [-1, 1]) {
      const w0 = P(sg * FACE, -DOWN, THK), w1 = P(sg * (FACE - 0.62), -DOWN, THK);
      slab(m, w0, [w1[0] - w0[0], 0, w1[2] - w0[2]],
           [vx * 0.16, 0, vz * 0.16], [0, TOP + DOWN, 0], 1.3);
    }
    /* the coping across the top, oversailing both ways */
    m.tint(1.46, 1.43, 1.37);
    {
      const a0 = P(-(FACE + 0.30), TOP, -0.45);
      const a1 = P(FACE + 0.30, TOP, -0.45);
      slab(m, a0, [a1[0] - a0[0], 0, a1[2] - a0[2]],
           [vx * (THK + 0.45 + 0.45), 0, vz * (THK + 0.45 + 0.45)],
           [0, 0.52, 0], 1.5);
    }

    /* ---- the wing walls ---------------------------------------------------- */
    const C = Math.cos(0.70), S = Math.sin(0.70);
    for (const sg of [-1, 1]) {
      const dir2 = [vx * C + ux * sg * S, 0, vz * C + uz * sg * S];
      const side = [-dir2[2], 0, dir2[0]];
      const from = P(sg * (FACE - 0.5), 0, THK - 0.1);
      this._wing(m, from, dir2, side, 8.0, base + TOP - 0.15, TOP - 1.9, 1.15,
                 [1.10, 1.08, 1.04], base - 1.2);
    }
  }

  /* ---- trackside --------------------------------------------------------- */

  _placeTrackside() {
    const Tex = this.Tex;
    const add = this._add;
    if (!add) return;
    const kitMat = kitMaterial(Tex);
    const conMat = concreteMaterial(Tex);

    const signals = [], cabinets = [], mileposts = [], troughs = [];
    const posts = [], buffers = [];
    const lens = [];
    this.signals = [];

    const B = new THREE.Matrix4();
    const nT = new THREE.Vector3(), nU = new THREE.Vector3(0, 1, 0);
    const nR = new THREE.Vector3();

    /** A post standing beside the track at arc length s, `lat` metres to the
     *  right, facing back down the line at an approaching train. */
    const beside = (track, s, lat, flip = 1) => {
      const f = track.at(s);
      nT.copy(f.tangent).multiplyScalar(-flip);
      nR.copy(f.right).multiplyScalar(flip);
      nT.y = 0; nT.normalize();
      const m = new THREE.Matrix4().makeBasis(nR, nU, nT);
      m.setPosition(f.position.x + f.right.x * lat * flip,
                    f.position.y + SLEEPER_BOT - 0.12,
                    f.position.z + f.right.z * lat * flip);
      return {m, frame: f};
    };

    const SIG_LAT = SLEEPER_LEN / 2 + 2.5;
    const putSignal = (track, s, lat, flip, key) => {
      const b = beside(track, s, lat, flip);
      const idx = signals.length;
      signals.push({m: b.m});
      const rec = {index: idx, aspect: 'green', clearUntil: 0, key,
                   pos: b.frame.position.clone()};
      this.signals.push(rec);
      /* three lenses, top to bottom, hung on the same basis as the mast */
      for (let i = 0; i < 3; i++) {
        const y = 4.05 + (1 - i) * 0.415;
        const lm = b.m.clone();
        lm.multiply(new THREE.Matrix4().makeTranslation(0, y, 0.315));
        lens.push({m: lm, c: ASPECT.dark.slice()});
      }
      rec.lens = idx * 3;
      /* a relay cabinet behind every signal — signals do not work alone. It
       * stands a good way out: a cabinet inside the six-foot between two roads
       * reads as an obstruction, which is the one thing lineside kit must not. */
      const cb = beside(track, s + 7 * flip, lat + 3.6, flip);
      cabinets.push({m: cb.m, c: [1, 1, 1]});
      return rec;
    };

    /* A starting signal at the exit of every station's siding, keyed to that
     * instrument, plus a home signal at the terminal throat and section
     * signals along the running lines. */
    for (const [uid, sd] of this.sidings) {
      /* Ahead of that bench's OWN stand, not at the end of the road. Several
       * benches share one loading road now, and seven signals in a heap at its
       * exit would be seven signals for one movement — what a driver leaving
       * any one stand needs is a starter they can see from it. */
      const rec = putSignal(sd.track,
                            Math.max(sd.entryS + 8,
                                     Math.min(sd.exitS - 10, sd.sDock + 26)),
                            SIG_LAT, 1, 'station:' + uid);
      rec.aspect = 'red';
      this._stationSignal.set(uid, rec);
    }
    for (const line of this.lines) {
      /* Only the length of a line that is actually LAID. A branch's alignment
       * runs on past its junction and lies on the trunk from there — putting a
       * signal, a trough run or a milepost out there stands lineside kit in the
       * middle of the main road, on ballast that was never drawn. */
      const lo = line.renderFrom || 0;
      const hi = Math.min(line.renderTo, line.length);
      const span = hi - lo;
      if (!(span > 40)) continue;
      const n = Math.max(2, Math.round(span / 210));
      for (let i = 1; i <= n; i++) {
        const s = lo + (span * i) / (n + 1);
        if (line.blocked(s)) continue;
        putSignal(line, s, SIG_LAT, 1, `${line.name}:${i}`);
      }
      const home = putSignal(line, hi - Math.min(96, span * 0.3), SIG_LAT, 1,
                             line.name + ':home');
      home.aspect = 'yellow';

      /* Cable troughing down one side, mileposts down the other, and a fence
       * on the open stretch beyond the site. */
      const step = 1.14;
      for (let s = lo + 3; s < hi - 3; s += step) {
        const b = beside(line, s, TOE_X + 0.75, 1);
        troughs.push({m: b.m, c: [1, 1, 1]});
      }
      for (let s = lo + 120; s < hi - 40; s += 250) {
        const b = beside(line, s, SLEEPER_LEN / 2 + 1.9, -1);
        mileposts.push({m: b.m, c: [1, 1, 1]});
      }
      const fenceFrom = lo + span * 0.62;
      const wire = new Mesher(true);
      let prev = null;
      for (let s = fenceFrom; s < hi - 8; s += 4.6) {
        for (const side of [-1, 1]) {
          const b = beside(line, s, VERGE_X + 1.1, side);
          posts.push({m: b.m, c: [1, 1, 1]});
        }
        const f = line.at(s);
        const cur = [];
        for (const side of [-1, 1]) {
          cur.push([f.position.x + f.right.x * (VERGE_X + 1.1) * side,
                    f.position.y + SLEEPER_BOT - 0.12,
                    f.position.z + f.right.z * (VERGE_X + 1.1) * side]);
        }
        if (prev) {
          wire.tint(0.34, 0.33, 0.30);
          for (let k = 0; k < 2; k++) {
            for (let w = 0; w < 3; w++) {
              const h = 0.42 + w * 0.36;
              wire.quad([prev[k][0], prev[k][1] + h, prev[k][2]],
                        [cur[k][0], cur[k][1] + h, cur[k][2]],
                        [cur[k][0], cur[k][1] + h + 0.022, cur[k][2]],
                        [prev[k][0], prev[k][1] + h + 0.022, prev[k][2]],
                        0, 0, 1, 0.05);
            }
          }
        }
        prev = cur;
      }
      if (!wire.empty) {
        const geo = wire.geometry();
        const mesh = new THREE.Mesh(geo, kitMat);
        mesh.receiveShadow = true;
        this.root.add(mesh);
        this._meshes.push(mesh); this._geoms.push(geo);
        this._detail.push(mesh);
      }
    }

    /* The terminal's own home signal, on the approach to the rack. It is the
     * one signal on this railway that protects something a train has to STOP
     * at rather than merely pass, and on a one-way ring it is where the whole
     * site's traffic converges — every working off every row runs under it. */
    if (this.trunk?.frames && isFinite(this.rackS) && this.rackS > 150) {
      const rec = putSignal(this.trunk, this.rackS - 120, SIG_LAT, 1,
                            'terminal:home');
      rec.aspect = 'green';
      this._loopSignal = rec;
    }

    /* Buffer stops: every dead end on the railway, and there are more of them
     * than a first look suggests — both ends of every running line, and the
     * reception road at the terminal. */
    const stops = [];
    for (const line of this.lines) {
      /* A dead end, not merely an end. A branch's alignment stops because it
       * has run on to the trunk, and a buffer stop planted at a junction is the
       * single most obviously wrong thing a railway can be drawn with. */
      const lo = line.renderFrom || 0;
      const hi = Math.min(line.renderTo, line.length);
      /* `stopEnds`, where a line has one, is authoritative: the trunk is cut
       * back to its headshunts after every junction on it is known, so from
       * `renderFrom` alone the drawing can no longer tell a deliberate end
       * from a lead lying on a parent. Without one the old test stands — the
       * laid extent reaching the alignment's own end. */
      const ends = line.stopEnds ||
        [lo < 0.5 ? 'lo' : '', line.renderTo >= line.length ? 'hi' : ''];
      if (ends.includes('lo')) stops.push({track: line, s: lo + 1.6, flip: -1});
      if (ends.includes('hi')) stops.push({track: line, s: hi - 1.6, flip: 1});
    }
    for (const b of this._buffers || []) stops.push({track: b.track, s: b.s, flip: 1});
    /* Published, not re-derived. A harness that reproduces this rule in order
     * to check it is checking its own copy of the rule, which is how an
     * instrument comes back confident and wrong: the first census of dead ends
     * this round did exactly that and reported the trunk's two new headshunts
     * as unterminated while the buffer stops were standing on them. */
    this.bufferStops = stops.map(st => {
      const q = st.track.at(st.s);
      return {track: st.track.name, s: st.s,
              x: q.position.x, y: q.position.y, z: q.position.z};
    });
    for (const st of stops) {
      const f = st.track.at(st.s);
      nT.copy(f.tangent).multiplyScalar(st.flip);
      nR.copy(f.right).multiplyScalar(st.flip);
      const m = new THREE.Matrix4().makeBasis(nR, f.up, nT);
      m.setPosition(f.position.x, f.position.y, f.position.z);
      buffers.push({m, c: [1, 1, 1]});
    }

    add(signalPrototype(), kitMat, signals);
    add(bufferPrototype(), kitMat, buffers);
    add(cabinetPrototype(), kitMat, cabinets, this._detail);
    add(troughPrototype(), conMat, troughs, this._detail);
    add(milepostPrototype(), kitMat, mileposts, this._detail);
    add(fencePostPrototype(), kitMat, posts, this._detail);
    this._lensMesh = add(lensGeometry(), lensMaterial(), lens);
    if (this._lensMesh) {
      this._lensMesh.castShadow = false;
      this._lensMesh.receiveShadow = false;
    }
    this._refreshAspects(true);
  }

  /** Aspects, driven by where the trains actually are.
   *
   *  Two rings rather than one, because a three-aspect signal that only ever
   *  shows red or green is a two-aspect signal with a spare lamp: a train
   *  inside 70m puts the signal in front of it back, and one inside 190m turns
   *  it to caution, so an approaching driver sees yellow before red the way
   *  they would on the ground. A station's starter is additionally held at
   *  danger unless its own instrument's working is out on the road, which is
   *  the one aspect on this railway that means something to an operator. */
  _refreshAspects(force = false) {
    const mesh = this._lensMesh;
    if (!mesh || !mesh.instanceColor) return;
    const col = new THREE.Color();
    let changed = force || this._dirty;
    for (const rec of this.signals) {
      let want = rec.key.startsWith('station:')
        ? ((rec.running !== undefined ? rec.running
                                      : this._t < rec.clearUntil) ? 'green' : 'red')
        : rec.aspect;
      let near = Infinity;
      for (const [, t] of this._occupied) {
        if (!t || typeof t.x !== 'number') continue;
        near = Math.min(near, rec.pos.distanceToSquared(t));
      }
      if (near < 4900) want = 'red';
      else if (near < 36100 && want !== 'red') want = 'yellow';
      if (rec.shown === want && !force) continue;
      rec.shown = want;
      changed = true;
      const order = ['red', 'yellow', 'green'];
      for (let i = 0; i < 3; i++) {
        const c = order[i] === want ? ASPECT[want] : ASPECT.dark;
        mesh.setColorAt(rec.lens + i, col.setRGB(c[0], c[1], c[2]));
      }
    }
    if (changed) { mesh.instanceColor.needsUpdate = true; this._dirty = false; }
  }

  /* ---- routes ------------------------------------------------------------ */

  /** A slice sampled at a FIXED step from `s0`, with the far end appended.
   *
   *  `_slice` divides the span into n equal parts, which is right for a leg
   *  whose two ends are both fixed and wrong for the loading road, where two
   *  laps of the same rank end at different places. Stepping from `s0` means the
   *  shorter lap's points ARE the longer one's, to the float, for as far as the
   *  two share railway — so a train standing at a stand is at the same arc
   *  length on every circuit that reaches it, which is the only thing that makes
   *  two workings on one road comparable at all. */
  _sliceAt(track, s0, s1, step) {
    const f = track.frames;
    if (!f) return null;
    const dir = s1 >= s0 ? 1 : -1;
    const span = Math.abs(s1 - s0);
    const n = Math.floor(span / step);
    const tmp = {position: new THREE.Vector3(), tangent: new THREE.Vector3(),
                 up: new THREE.Vector3(), right: new THREE.Vector3(), k: 0};
    const pts = [];
    for (let i = 0; i <= n; i++) {
      track.at(s0 + dir * step * i, tmp);
      pts.push(tmp.position.clone());
    }
    if (span - n * step > 1e-6) {
      track.at(s1, tmp);
      pts.push(tmp.position.clone());
    }
    return pts.length > 1 ? pts : null;
  }

  _slice(track, s0, s1) {
    const pts = [];
    const f = track.frames;
    if (!f) return null;
    const dir = s1 >= s0 ? 1 : -1;
    const span = Math.abs(s1 - s0);
    const n = Math.max(2, Math.ceil(span / ROUTE_STEP));
    const tmp = {position: new THREE.Vector3(), tangent: new THREE.Vector3(),
                 up: new THREE.Vector3(), right: new THREE.Vector3(), k: 0};
    for (let i = 0; i <= n; i++) {
      track.at(s0 + dir * (span * i) / n, tmp);
      pts.push(tmp.position.clone());
    }
    return pts;
  }

  _buildRoute(uid) {
    const trunk = this.trunk;
    const pts = [];
    const add = list => {
      for (const p of list || []) {
        const last = pts[pts.length - 1];
        if (last && p.distanceToSquared(last) < 0.04) continue;
        pts.push(p);
      }
    };
    /* Where the outbound run finishes: under the loading rack. */
    const railhead = isFinite(this.rackS) ? this.rackS
                                          : Math.max(0, (trunk?.length || 0) - 10);
    const sd = this.sidings.get(uid);
    if (!sd) {
      /* No siding of its own — a bench in a row the site could not give a
       * branch. Put it on the nearest railway there is and run it in from
       * there rather than refusing: a train on the wrong road is a smaller lie
       * than an instrument the floor never shows a train for, and this is the
       * one path that guarantees every station in every layout is reachable. */
      const st = this.ctx.plan?.byUid.get(uid);
      if (!st) return null;
      let best = null;
      for (const l of this.lines) {
        if (!l.frames) continue;
        const a = l.nearest(st.x, st.z - DOCK_OFFSET);
        if (!best || a.distance < best.d) best = {line: l, s: a.s, d: a.distance};
      }
      if (!best) return null;
      const br = this.branchOf.get(best.line);
      if (br && trunk) {
        add(this._slice(best.line, best.s, br.jS));
        add(this._slice(trunk, br.tS, railhead));
      } else {
        add(this._slice(best.line, best.s, best.line.length));
      }
      return pts.length > 1 ? new PolyRoute(pts) : null;
    }
    const br = this.branchOf.get(sd.line);
    add(this._slice(sd.track, sd.sDock, sd.exitS));
    if (br && trunk) {
      add(this._slice(sd.line, sd.sOut, br.jS));
      add(this._slice(trunk, br.tS, railhead));
    } else {
      add(this._slice(sd.line, sd.sOut, Math.min(sd.line.renderTo, sd.line.length)));
    }
    return pts.length > 1 ? new PolyRoute(pts) : null;
  }

  /* ---- blocks, and why they are here --------------------------------------
   *
   * Every working out of every bench now runs over the same trunk. That is what
   * makes the network a network, and it is also the moment the railway acquires
   * a way to have two trains in the same place: before this, a working never
   * left the two roads its own row owned. Arc length along a train's own route
   * cannot answer the question — two workings out of different benches are at
   * completely different arc lengths while standing on the same forty metres of
   * the platform road.
   *
   * So the railway is cut into BLOCKS at the places a real one is cut: either
   * side of every junction — `track.overlaps` records exactly those, with
   * `track.blocks` (the bearers) folded in so a turnout whose overlap was
   * trimmed for room still cuts at its own timbers — and every ~180m of plain
   * line between them. (The tunnel bores pushed on to `blocks` later, in
   * `_buildMeshes`, are after this has run and have never been section cuts.)
   * A cycle's `segments` map arc length on the route back to (track, s),
   * so `blocksFor` turns "this train occupies 40m at s=310" into a set of block
   * ids, and `reserve` refuses a set that intersects one somebody else holds.
   *
   * That is OpenTTD's path reservation in miniature and it is the only kind of
   * anti-collision worth having: a train that cannot get a reservation does not
   * enter, so two trains in one block is not something to detect afterwards. It
   * is not something the network can be asked for.
   */
  _sectionBlocks() {
    this._sections = new Map();
    for (const t of this.tracks) {
      if (!t.frames) continue;
      const spans = t.blocks.concat(t.overlaps || []);
      const cuts = new Set([0, t.length]);
      for (const b of spans) {
        cuts.add(Math.max(0, Math.min(t.length, b[0])));
        cuts.add(Math.max(0, Math.min(t.length, b[1])));
      }
      const sorted = [...cuts].sort((a, b) => a - b);
      const out = [];
      const inTurnout = s => spans.some(b => s > Math.min(b[0], b[1]) &&
                                             s < Math.max(b[0], b[1]));
      for (let i = 0; i < sorted.length - 1; i++) {
        const a = sorted[i], b = sorted[i + 1];
        if (b - a < 1) continue;
        /* Plain line between junctions is cut again so a long section does not
         * lock out a whole branch for one train. */
        const n = Math.max(1, Math.round((b - a) / 180));
        /* A section that came out of `t.overlaps` (or `t.blocks`, which is the
         * bearers and the tunnel bores) IS the turnout — the ground a train may
         * not be standing on when it stops, because standing there fouls the
         * road it diverges from. Recorded rather than re-derived, because it is
         * what the chain-signal rule keys off. */
        const junction = inTurnout((a + b) / 2);
        for (let k = 0; k < n; k++) {
          out.push({a: a + ((b - a) * k) / n, b: a + ((b - a) * (k + 1)) / n,
                    junction});
        }
      }
      this._sections.set(t.name, out);
    }
    this._coupleThroats();
  }

  /* ---- and a turnout is ONE block, on both of its roads --------------------
   *
   * The half of this the table could not say. Sizing every overlap from
   * `leadClearRun` gets a train that is STANDING to stand clear; it does
   * nothing about the pair of blocks that meet AT the switch tip, because they
   * are on two different tracks and every id in this table is `track#index`.
   * Measured on the built railway before this existed (`harness/rz-pair.mjs`,
   * layout 0, minimum distance between the metal of two blocks that can be
   * held by two different trains at the same time):
   *
   *     main#1*  / branch1#7*            0.00 m
   *     main#3*  / branch0#8*            0.00 m
   *     main#5*  / terminal.loop#0*      0.00 m
   *     main#11* / branch0#0*            0.00 m
   *     branch0#2* / load:0#0*           0.00 m      ...fourteen of them.
   *
   * Zero, because they are the same set of points. Two workings, one on each
   * road, holding one each, both correctly signalled, with nothing in the
   * ledger able to say they are in the same place — which is exactly what
   * trains.js caught in the act and could find no rule for.
   *
   * The rule is the oldest one there is: a turnout is one piece of interlocked
   * apparatus and only one movement may hold it. `_midRankLinks` already says
   * so for the crossover ("the crossover is ONE block") and this is the same
   * sentence applied to every other junction on the railway. The child's
   * junction sections adopt the PARENT's block id, so `blocksOn` and
   * `blockSpans` hand back the same string on both roads and `reserve`'s
   * existing all-or-nothing test does the rest. No new mechanism, nothing for
   * trains.js to consume, and the chain rule keys off `junction` exactly as
   * before.
   *
   * WHY NOT `runFor`. It is the other grouping this file already has, and it is
   * the wrong one: trains.js reads a multi-id answer from `runFor` as a
   * single-line TOKEN and holds it for the rest of the working (`_advance`
   * keeps `tokenIds` until the working is `back`). A throat held from the
   * moment a train passes it until it gets home would serialise four junctions
   * per lap. An id is held only while a body is on it, and released the metre
   * the tail is clear — which is what an interlocking does.
   *
   * WHY IT DOES NOT DEADLOCK. A group is one turnout, 28–40m on each road, and
   * `main` is a one-way ring: no working ever asks for rail behind it, so no
   * two claims can ever be pointed at each other. The one way a group could be
   * held for ever is a train STABLING with part of its body inside it, which is
   * the failure `LINK_BLOCK_GAP` was written for; `rz-stand.mjs` measures the
   * margin from every stand's rake tail to the nearest junction span and it is
   * reported below, not asserted. */
  _coupleThroats() {
    this._throats = [];
    /* Union-find over block ids, because one road can be a leg of two turnouts
     * at once. The crossover is exactly that — 45m of connection with a set of
     * points at each end — and it is why this cannot be a single alias written
     * into the child: `_midRankLinks` already declares the crossover one block,
     * and the honest reading of that is that a train on it holds the road's
     * points AND the branch's. So the two throats merge, and the union does it
     * without anyone having to notice the case. */
    const up = new Map();
    const find = x => { while (up.get(x) !== undefined && up.get(x) !== x) x = up.get(x); return x; };
    const union = (keep, join) => {                 // `keep`'s name wins
      const a = find(keep), b = find(join);
      if (a === b) { up.set(a, a); return a; }
      up.set(a, a); up.set(b, a);
      return a;
    };
    const at = (name, s) => {
      const list = this._sections?.get(name);
      if (!list) return -1;
      for (let i = 0; i < list.length; i++)
        if (s >= list[i].a - 0.05 && s <= list[i].b + 0.05 && list[i].junction)
          return i;
      return -1;
    };
    for (const rec of this._turnouts || []) {
      const parent = rec.track, child = rec.child;
      if (!parent?.name || !child?.name) continue;
      const pList = this._sections?.get(parent.name);
      const cList = this._sections?.get(child.name);
      if (!pList || !cList) continue;
      /* The parent's junction section is the one the tip stands in. A tip that
       * landed on a section boundary picks the junction side. */
      let pi = at(parent.name, rec.s);
      if (pi < 0) pi = at(parent.name, rec.s + 0.5 * rec.pdir);
      if (pi < 0) continue;
      const pid = parent.name + '#' + pi;
      /* The whole of the parent's junction run, not just the section the tip
       * landed in. A stand marker (`[s+3, s+3.1]`) sitting inside a turnout
       * splits its overlap into a 2m sliver and the rest, and coupling only the
       * sliver would leave 26.9m of the same set of points uncoupled — which is
       * how `load:0#3*`/`load:0#4*` are actually cut on layout 0. Contiguity
       * tolerates a metre because that is what `_sectionBlocks` drops. */
      for (let i = pi - 1; i >= 0 && pList[i].junction &&
                           pList[i].b >= pList[i + 1].a - 1.05; i--)
        union(pid, parent.name + '#' + i);
      for (let i = pi + 1; i < pList.length && pList[i].junction &&
                           pList[i].a <= pList[i - 1].b + 1.05; i++)
        union(pid, parent.name + '#' + i);
      /* Every junction section of the child at the end that joins here, and
       * only those: the walk stops at the first plain block, which is the
       * first place a train can stand without fouling the road it left. */
      const took = [];
      const order = rec.which === 'start'
        ? cList.map((s, i) => i)
        : cList.map((s, i) => i).reverse();
      for (const i of order) {
        if (!cList[i].junction) break;
        union(pid, child.name + '#' + i);
        took.push(child.name + '#' + i);
      }
      if (took.length) this._throats.push({parent: pid, child: took});
    }
    /* Stamp the class representative on every section in a class of two or
     * more. A section with no `id` keeps `track#index`, so nothing that was
     * already one block changes name. */
    for (const [name, list] of this._sections) {
      for (let i = 0; i < list.length; i++) {
        const self = name + '#' + i;
        const root = find(self);
        if (root !== self) list[i].id = root;
      }
    }
    for (const th of this._throats) th.id = find(th.parent);
  }

  /** The block ids covering `s0..s1` metres of `track` (by name). */
  blocksOn(trackName, s0, s1) {
    const list = this._sections?.get(trackName);
    if (!list) return [];
    const a = Math.min(s0, s1), b = Math.max(s0, s1);
    const out = [];
    for (let i = 0; i < list.length; i++) {
      if (list[i].a < b && a < list[i].b) {
        const id = list[i].id || (trackName + '#' + i);
        if (!out.includes(id)) out.push(id);
      }
    }
    return out;
  }

  /** Every block a circuit passes through, in that circuit's own arc length.
   *
   *  `blocksFor` answers "which blocks is this train standing on"; this answers
   *  the other half — "and what is the next one, and where does it begin" —
   *  which is what a train needs before it may move. Both read the same
   *  sections, so there is one description of the railway and not two.
   *
   *  A block appears once per traversal, so the trunk shows up twice on a
   *  circuit that runs out and back over it: same id, two arc-length ranges,
   *  which is exactly right — it is the same rail both times, and a train that
   *  holds it going out still holds it coming back.
   */
  blockSpans(cycle) {
    const segs = cycle?.segments, route = cycle?.route;
    if (!segs || !route) return [];
    const out = [];
    for (const seg of segs) {
      const list = this._sections?.get(seg.track);
      if (!list) continue;
      const A = route.acc[seg.from], B = route.acc[seg.to];
      const span = seg.s1 - seg.s0 || 1;
      const lo = Math.min(seg.s0, seg.s1), hi = Math.max(seg.s0, seg.s1);
      for (let i = 0; i < list.length; i++) {
        const sec = list[i];
        if (sec.b <= lo || sec.a >= hi) continue;
        const u0 = (Math.max(sec.a, lo) - seg.s0) / span;
        const u1 = (Math.min(sec.b, hi) - seg.s0) / span;
        const a = A + (B - A) * Math.min(u0, u1);
        const b = A + (B - A) * Math.max(u0, u1);
        if (b - a < 0.25) continue;
        const id = sec.id || (seg.track + '#' + i);
        out.push({id, a, b, junction: sec.junction,
                  run: this._runOf?.get(id) || null});
      }
    }
    out.sort((x, y) => x.a - y.a);
    return out;
  }

  /* ---- which rail is worked both ways --------------------------------------
   *
   * Block working alone is not enough on single track, and this railway is
   * single track everywhere that matters. Two trains approaching each other,
   * each holding the block it stands on and each asking for the one the other
   * is on, is a deadlock that no amount of per-block correctness prevents —
   * both refusals are correct and nothing moves again.
   *
   * The fix a real railway uses is a token: the whole of the single line is one
   * thing, and you may not enter it without holding it. So the analysis below
   * finds which rail is genuinely worked in both directions — by looking at the
   * direction each circuit traverses each section, which falls out of the
   * slices the routes were cut from — and groups the contiguous stretches of it
   * on one track into a RUN. A run is claimed whole or not at all.
   *
   * Runs deliberately stop at track boundaries. If they did not, the branch and
   * the trunk would merge into one token and a train would have to hold the
   * whole network before leaving its loop; keeping them apart is what gives a
   * working somewhere to stand — on its own branch, which nothing else can
   * want — while it waits for the throat.
   */
  _analyseBlocks() {
    this._runOf = new Map();
    this._runBlocks = new Map();
    const dirs = new Map();               // block id -> bitmask 1:up 2:down
    const seen = new Set();
    for (const [uid, sd] of this.sidings) {
      if (seen.has(sd.track.name)) continue;
      seen.add(sd.track.name);
      let cyc = null;
      try { cyc = this.cycle(uid); } catch { cyc = null; }
      if (!cyc?.segments) continue;
      for (const c of cyc.variants || [cyc]) {
        for (const seg of c.segments || []) {
          const list = this._sections?.get(seg.track);
          if (!list) continue;
          const lo = Math.min(seg.s0, seg.s1), hi = Math.max(seg.s0, seg.s1);
          /* A circuit that could not be closed is run out and set back down its
           * whole length, so every section of it is worked both ways whatever
           * the slices say. */
          const bit = c.closed ? (seg.s1 >= seg.s0 ? 1 : 2) : 3;
          for (let i = 0; i < list.length; i++) {
            if (list[i].b <= lo || list[i].a >= hi) continue;
            const id = list[i].id || (seg.track + '#' + i);
            dirs.set(id, (dirs.get(id) || 0) | bit);
          }
        }
      }
    }
    for (const [name, list] of this._sections || []) {
      let k = 0, start = -1;
      const close = end => {
        if (start < 0) return;
        const run = 'run:' + name + '#' + (k++);
        const ids = [];
        for (let i = start; i < end; i++) {
          const id = list[i].id || (name + '#' + i);
          if (ids.includes(id)) continue;
          this._runOf.set(id, run);
          ids.push(id);
        }
        this._runBlocks.set(run, ids);
        start = -1;
      };
      for (let i = 0; i < list.length; i++) {
        if (dirs.get(list[i].id || (name + '#' + i)) === 3) { if (start < 0) start = i; }
        else close(i);
      }
      close(list.length);
    }
  }

  /** Every block in the run `id` belongs to, or just `id` when it is on rail
   *  that is only ever worked one way. */
  runFor(id) {
    const run = this._runOf?.get(id);
    return run ? this._runBlocks.get(run) : null;
  }

  /** Who holds a block right now — null when it is clear. */
  heldBy(id) { return this._held?.get(id) ?? null; }

  /** The blocks a train occupies, given its cycle and where its head and tail
   *  are along that cycle's route. `cycle` is what `cycle(uid)` returned. */
  blocksFor(cycle, headS, tailS) {
    const segs = cycle?.segments;
    const route = cycle?.route;
    if (!segs || !route) return [];
    const acc = route.acc;
    const lo = Math.min(headS, tailS), hi = Math.max(headS, tailS);
    const out = new Set();
    for (const seg of segs) {
      const a = acc[seg.from], b = acc[seg.to];
      if (b < lo || a > hi) continue;
      /* Which part of that stretch of track the overlap corresponds to. The
       * mapping is linear inside a segment because `_slice` samples at a
       * uniform step. */
      const span = b - a || 1;
      const u0 = Math.max(0, (lo - a) / span), u1 = Math.min(1, (hi - a) / span);
      const s0 = seg.s0 + (seg.s1 - seg.s0) * u0;
      const s1 = seg.s0 + (seg.s1 - seg.s0) * u1;
      for (const id of this.blocksOn(seg.track, s0, s1)) out.add(id);
    }
    return [...out];
  }

  /** Claim a set of blocks for `id`. Refuses — and changes nothing — if any of
   *  them is held by somebody else. Reserving again with a new set is how a
   *  train moves: the old claim is dropped only once the new one is granted. */
  reserve(id, blockIds) {
    if (!this._held) this._held = new Map();
    for (const b of blockIds) {
      const who = this._held.get(b);
      if (who !== undefined && who !== id) return false;
    }
    for (const [b, who] of this._held) if (who === id) this._held.delete(b);
    for (const b of blockIds) this._held.set(b, id);
    return true;
  }

  /** Whether `id` could hold those blocks — the question a train asks before it
   *  starts, so it stands at its signal instead of setting off into a section
   *  it will have to stop in the middle of. */
  clear(id, blockIds) {
    if (!this._held) return true;
    for (const b of blockIds) {
      const who = this._held.get(b);
      if (who !== undefined && who !== id) return false;
    }
    return true;
  }

  unreserve(id) {
    if (!this._held) return;
    for (const [b, who] of this._held) if (who === id) this._held.delete(b);
  }

  /* ---- teardown ---------------------------------------------------------- */

  _clear() {
    for (const m of this._meshes) {
      this.root.remove(m);
      m.dispose?.();
    }
    for (const g of this._geoms) g.dispose?.();
    this._meshes = [];
    this._geoms = [];
    this._detail = [];
    this._fine = [];
    /* The structure meshes are held a second time so they can be replaced on
     * their own when terrain has finished moving earth. They have just been
     * disposed with the rest, so the second list has to be emptied too or the
     * next re-seat removes meshes that are already gone. And the ground is a
     * guess again until terrain says otherwise. */
    this._struct = [];
    this._groundFinal = false;
    this.tracks = [];
    this.lines = [];
    this._turnouts = [];
    this.trunk = null;
    this.branches = [];
    this.branchOf = new Map();
    this.sidings.clear();
    this._routes.clear();
    this._cycles.clear();
    this._circuits = new Map();
    this._sections = new Map();
    this._throats = [];
    this._runOf = new Map();
    this._runBlocks = new Map();
    /* Reservations do NOT survive a relayout: every id in the table names rail
     * that has just been torn up. trains.js re-asserts, from the workings that
     * are still out, immediately afterwards — it is the only thing that knows
     * what is really standing where. */
    this._held = new Map();
    this.rack = null;
    this.rackS = NaN;
    this.deadTracks = [];
    this.exceptions = [];
    this.passingLoops = {built: [], refused: []};
    this.structures = 0;
    /* The stands name points on track that has just been torn up; a circuit
     * built after a relayout would otherwise be sent to a spot on the old
     * terminal. `_buildStands` refills this at the end of every `_rebuild`. */
    this.racks = [];
    this.loop = null;
    this.link = null;
    this._loopSignal = null;
    this.signals = [];
    this._stationSignal = new Map();
    this._buffers = [];
    this._spur = null;
    this._yardRoute = undefined;
    this._lensMesh = null;
    this._add = null;
  }
}

export default Rail;
