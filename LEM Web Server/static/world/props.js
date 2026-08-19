/* props.js — the things that make the island look inhabited rather than
 * rendered: a worn approach down to the beach, umbrellas on the dry sand with
 * their shade under them, a pier with a shadow stripe under it, boats moored at
 * its head, and whatever else a region says belongs there.
 *
 * ════════════════════════════════════════════════════════════════════════════
 *  INHABITATION IS NOT OBJECTS, IT IS EVIDENCE OF USE. Read this before adding
 *  a prop type; it is the correction that shaped everything below.
 * ════════════════════════════════════════════════════════════════════════════
 *
 * A blind art director judged the first full set and returned one sentence that
 * is worth more than the rest of this header:
 *
 *   "None of these props add inhabitation because INHABITATION IS NOT OBJECTS,
 *    IT IS EVIDENCE OF USE — paths, wear, orientation, repetition with
 *    variation, and shadow. You added five object types and zero evidence.
 *    This is worse than no props, because each one now advertises that the
 *    world does not know what people do."
 *
 * Every specific charge was reproducible with an instrument, and each one is
 * answered at the code that answers it:
 *
 *   THE APPROACH — "no path, no track, no parking, no worn line through the
 *   dune vegetation connecting any road to that beach … A DESIRE LINE IS NEARLY
 *   FREE AND IT WOULD DO MORE THAN ALL THE PROPS COMBINED." It is, and it does.
 *   See `_pathRoute`. It is the cheapest thing in the file and the only one that
 *   is a record of movement rather than an object.
 *
 *   THE TIDE LINE — the ten umbrellas stood at a MEDIAN 0.53 m of elevation
 *   above the waterline, inside a wet-sand tone that terrain paints full below
 *   1.12 m. They were below the tide line, on the seaward side of the wash,
 *   because `beachnessAt` rewarded "lowest and flattest" and the lowest flattest
 *   ground on a beach is the bit the sea is on. See `WASH_LINE` and the `low`
 *   half-window in `beachnessAt` — one placement bug and one classifier bug
 *   that turned out to be the same bug.
 *
 *   THE SHADE — "an umbrella's entire job in an aerial frame is to cast a disc
 *   of shade; the disc is what makes it an umbrella rather than a dot. Without
 *   it they are litter." They cast nothing: measured, every mesh in this file
 *   came back `castShadow: false` at ultra, and an umbrella cannot reach the
 *   cascade that covers the beach. So the shade is DRAWN, from the same sun
 *   vector gi casts with. See `_buildDecals`.
 *
 *   THE PIER — "you have drawn the deck and omitted the thing that proves it is
 *   above the surface", and "it stops in water too shallow to berth anything".
 *   Both true. It now lays a shadow stripe on the water, has a head, and its
 *   launch point is a SEARCH for depth rather than an accident of the anchor.
 *
 *   THE GULLS — cut. The arithmetic is written out where they used to be.
 *
 * THE GENERAL LESSON, for whoever adds the next prop type: a prop earns its
 * place by what it implies about people, and it is legible at the operator's
 * camera or it is cost. Both halves are testable before you build it. The
 * angular size of the thing at 900 m is one line of arithmetic, and "what does
 * this say somebody did" is a question you can answer in the design comment.
 *
 * It is also where REGION lives — the classification layer over the landform
 * that says which of those things a piece of ground has earned. Regions are
 * published for every populator, not just for this file; roads and cars are a
 * `city` consumer and belong to a later round.
 *
 * ── Why props are their own module ──────────────────────────────────────────
 *
 * They are not vegetation: vegetation is scattered by biome and must hold an
 * appearance invariant with camera distance that props do not share. They are
 * not buildings: a building is an instrument and carries a status. A prop
 * carries nothing and means nothing — it is there so the eye believes the rest.
 *
 * ── The operator's requirements, verbatim, because they are the spec ────────
 *
 *   "Birds, boats and other things don't cost a lot, but they add a ton of
 *    character."
 *   "Umbrellas on beaches with towels don't cost much and they add a lot of
 *    character."
 *   "Regions would be nice — city, country, beach, each with their generation
 *    models and rules. So if terrain equals beach, umbrellas and other things
 *    can spawn in; if it's a city, then roads and cars in between each
 *    equipment."
 *   "Not like it's a different design every time, but if the terrain hits a
 *    certain criteria it has the ability to spawn."
 *   "Obviously the less quality then the less objects — 1 umbrella and towels
 *    instead of 10."
 *
 * Three things follow from that and they are not negotiable:
 *
 *   1. REGION IS A FUNCTION OF THE TERRAIN, NOT A ROLL. The operator was
 *      explicit that this is not a per-load reroll. Given the same landform the
 *      same region map must come out, so a prop appears because the ground
 *      earned it. Region should follow from measurable terrain properties —
 *      elevation, slope, distance to water, proximity to the plant — the same
 *      way vegetation's biome does.
 *
 *   2. DENSITY SCALES WITH THE QUALITY TIER, NOT WITH CAMERA DISTANCE. One
 *      umbrella at the floor tier is correct. An umbrella that vanishes as the
 *      camera pulls back is the bug that took four rounds to get out of
 *      vegetation.js, and re-introducing it here would be worse, because a prop
 *      popping is more visible than a tree thinning.
 *
 *   3. THE COST CLAIM IS UNMEASURED. "Doesn't cost a lot" is the operator's
 *      estimate and is probably right, but nobody has measured it, and this
 *      laptop cannot tell you — at 1080p the frame is vsync-pinned and
 *      everything reads as free. That is exactly how the grove LOD's cost was
 *      got wrong. Measure one prop type through the paired, repeated,
 *      zero-control method in `harness/vlodcost3.mjs`, at 4K, before building
 *      the full set.
 *
 * ── What already exists to build against ────────────────────────────────────
 *
 *   ctx.ground(x, z)                 terrain height, the sampler everyone uses
 *   terrain.biomeAt(x, z)            elevation, slope, aspect (RADIANS — see
 *                                    REQUESTS.md; reading it as a signed unit
 *                                    cost vegetation an 82%-conifer island),
 *                                    moisture, flow, kind
 *   ctx.siteBenches / 'site:benches' the plant's terraces, published by index.js
 *   ctx.railEarthworks               where the railway has moved earth
 *   rail.tracks, rail.bufferStops    where not to put anything
 *   gi.requestLight({...})           a pooled point light, honest about whether
 *                                    it won a slot; pool is 10 at ultra, 0 at
 *                                    floor, and only one yard flood per station
 *                                    currently uses it
 *   gi.setExposureLocked(true)       freeze the stop before measuring colour
 *
 * ════════════════════════════════════════════════════════════════════════════
 *  THE REGIONS CONTRACT, v1 — published on `ctx.regions` and as the event
 *  `world:regions`, the way rail publishes earthworks and index publishes
 *  benches. Both channels carry the SAME object. Republished on every
 *  `onPlan`, so a consumer must be idempotent.
 * ════════════════════════════════════════════════════════════════════════════
 *
 *   ctx.regions = {
 *     version: 1,
 *     source: 'terrain' | 'none',   // 'none' ⇒ no terrain in the room; `at()`
 *                                   //   answers 'country' everywhere and says
 *                                   //   so, rather than inventing a coastline
 *     names: ['water', 'beach', 'city', 'country'],
 *
 *     at(x, z)        -> one of `names`.  Pure, cheap (one height read, one
 *                        lattice read, one biomeAt in the coastal band only),
 *                        and DETERMINISTIC: same landform, same answer, for
 *                        ever. No RNG is consulted anywhere in it.
 *     scoreAt(x, z)   -> {region, beachness, dWater, dPlant, altitude, slope,
 *                         height, kind}  — everything the classifier looked at,
 *                        for a consumer that wants to threshold differently.
 *     dWaterAt(x, z)  -> metres to the nearest water cell, 0 in water.
 *     dPlantAt(x, z)  -> metres to the nearest instrument.
 *
 *     waterY, cellM, shoreW, cityR,   // the operating numbers, in metres
 *     ranges:    {dWater, bandSlope, bandAlt},  // each [p05, p50, p95] AS
 *                                     // MEASURED on this island, this build
 *     histogramDomain: 'land, 20 m lattice',
 *     histogram: {beach, city, country},   // counts on the probe lattice —
 *                                     // LAND ONLY, which is why there is no
 *                                     // `water` key. `at()` still returns
 *                                     // 'water' for a submerged point.
 *     fraction:  {...},               // the same, as a fraction OF LAND
 *     landSamples, bounds: {x0, z0, x1, z1},
 *     warnings: []                    // non-empty ⇒ a rule went inert
 *   }
 *
 *  WHAT EACH REGION MEANS, so a consumer does not have to guess:
 *
 *    water    below the waterline. Boats; nothing that stands.
 *    beach    shelving coast — inside the measured shore band, flat for that
 *             band, and low for that band. A CLIFF COAST IS NOT A BEACH and
 *             this is the whole reason the rule reads slope: on this island the
 *             coastal band's slope runs p25 0.117 / p50 0.299 / p75 0.516, so
 *             the coast is genuinely half strand and half face, and a rule that
 *             ignored slope would put umbrellas on a cliff top.
 *    city     the plant and the ground between its instruments — the apron a
 *             road round can lay tarmac and level crossings on. Sized from the
 *             MEDIAN NEAREST-NEIGHBOUR SPACING OF THE INSTRUMENTS THEMSELVES,
 *             so it follows a bay-size retune instead of going stale under one.
 *             THIS ROUND PLACES NOTHING IN IT. It is defined, measured and
 *             published so the roads round has something to consume.
 *    country  everything else. Vegetation's ground; props stay off it.
 *
 *  WHY EVERY KNEE IS A MEASURED PERCENTILE AND NOT A NUMBER THAT SOUNDS RIGHT:
 *  see REQUESTS.md, "THE PATTERN — five inert rules, one cause". Six rules in
 *  this project have silently become constants because they were written
 *  against another module's absolute values and that module was retuned. So:
 *
 *    - the shore band is `min(terrain.beachW, p25 of the measured dWater)` —
 *      both ends of it move with terrain;
 *    - the flat/low gates are median-centred against the distribution INSIDE
 *      the band, through the same two-segment `_mapField` vegetation uses, so
 *      the mapped median of each is exactly 0.5 whatever shape the raw field
 *      has;
 *    - the city radius is a fraction of the instruments' own median spacing;
 *    - and `_regionReport()` refuses to stay quiet: it classifies a lattice of
 *      the whole island at build time, publishes the histogram, and WARNS if
 *      any region claims 90% of the land or if an island with a coast produced
 *      no beach at all. A rule with no variance is a bug even when its value is
 *      plausible.
 *
 *  Measured on the demo fleet, 2026-08-08, at the bottom of this file's own
 *  `_regionReport()`:  beach 8.0%, city 14.2%, country 77.8% of land.
 *
 * ── How the quality tier is spent: THE STABLE PREFIX ────────────────────────
 *
 * Every prop set is one deterministic ORDERING, computed in full at every tier,
 * and the tier takes a PREFIX of it. Nothing is re-rolled, re-sited or moved
 * when the tier changes; the tail is simply not built. So the one umbrella at
 * the floor tier stands exactly where umbrella #1 stands at ultra, and walking
 * the ladder up adds neighbours around it rather than rearranging the beach.
 *
 * And nothing in this file reads the camera. There is no LOD, no distance fade
 * and no per-frame visibility decision anywhere in it — `update()` bobs boats,
 * and re-merges the drawn shade when THE SUN has moved far enough to make it
 * wrong, and never touches a count. That is non-negotiable #2, and it is
 * enforced by construction: the counts are computed once, in `build()`.
 *
 * The sun-follow is the one rebuild trigger outside `onPlan`/`onQuality`, and it
 * is admissible for the reason the camera reads are not: the same hour gives the
 * same shade at every tier and every distance. It is hysteretic — four degrees
 * of azimuth, about a quarter hour of a summer morning — so a static frame never
 * pays for it. See `_resunDecals`.
 *
 * The tier handle is `quality.props ?? quality.particles`. `particles` is the
 * engine's own "how many small things should there be" ladder and runs exactly
 * 1.00 / 0.80 / 0.55 / 0.30 / 0.15 down the five tiers, which gives the
 * operator's ten-umbrellas-to-one almost exactly; reading it rather than the
 * tier NAME means an engine retune moves prop density with it. If engine.js
 * ever grows a `props` field it takes precedence automatically.
 *
 * ════════════════════════════════════════════════════════════════════════════
 *  WHAT THE PROPS COST. Measured, not assumed — `harness/pr-cost.mjs`,
 *  2026-08-08, at 3840x2160 with `--disable-gpu-vsync
 *  --disable-frame-rate-limit`, paired, 25 repeats, median of the paired
 *  differences, uncertainty quoted as half the inter-quartile range of those
 *  differences.
 * ════════════════════════════════════════════════════════════════════════════
 *
 *  THESE FIGURES ARE FROM THE ROUND BEFORE THE EVIDENCE ROUND and the ablation
 *  has NOT been re-run against the current set. What changed, and why the
 *  conclusion survives unchanged:
 *
 *    prop types      5 -> 4      gulls out, decals in
 *    draw calls      4 -> 4      and now 4 AT EVERY TIER, which it was not:
 *                                gi was auto-enrolling the pier as a caster at
 *                                medium and low only, so the count read 4/4/5/5/4
 *    triangles   1,160 -> 1,812  at ultra, measured by `pr-inspect.mjs`:
 *                                umbrellas  260 ->   260
 *                                pier       540 ->   828  (head, bollards,
 *                                                          four head piles)
 *                                boats      216 ->   192  (6 -> 4 hulls, each
 *                                                          +12 for the interior)
 *                                gulls      144 ->     0  cut
 *                                decals       0 ->   532  path, discs, stripe
 *
 *  1,812 triangles at the measured 0.0007 us each is 1.3 microseconds, and the
 *  draw count did not move. The set is still an order of magnitude below the
 *  0.05 ms noise floor and re-measuring it would produce the same "under the
 *  floor" sentence. `harness/pr-cost.mjs` is the instrument if that is ever in
 *  doubt; what would actually justify re-running it is a new DRAW, not more
 *  triangles.
 *
 *  The set as measured then — 4 prop types, 4 draws, 1,160 triangles:
 *
 *    control  (hide nothing)              0.000 +/- 0.000 ms   <- the floor
 *    props    (everything this file made) 0.000 +/- 0.050 ms   <- under it
 *    geometry (60,000 inst, 1 draw)       1.100 +/- 0.050 ms
 *    draws    (2,000 meshes, 2,000 draws) 0.100 +/- 0.100 ms
 *
 *  The shipped set does not register. THAT IS NOT "IT COSTS 0.00 ms" — that is
 *  the sentence the grove LOD's ablation produced for a LOD with zero triangles
 *  in it, and it was wrong. The honest statement is "under 0.05 ms", and the
 *  only way to get a number smaller than the floor is to measure something big
 *  and divide, which is what the last two rows are for. The state verification
 *  in that harness proves each ablation really does change what the renderer
 *  submits (332 -> 2002 draws, 1.82 M -> 3.38 M triangles), because an
 *  instrument that cannot see the field it switched off measures nothing.
 *
 *  DIVIDED OUT, on this machine at 4K:
 *
 *    per triangle          0.0007 us
 *    per prop instance     0.018 us   (an umbrella is 26 triangles)
 *    per DRAW CALL         0.06 us    <- the one that governs the budget
 *
 *  So a prop TYPE — one instanced mesh, one draw, plus one more if it casts a
 *  shadow — costs well under a microsecond before it draws anything, and the
 *  whole set here prices at roughly 0.001 ms: 5 draws and 1,160 triangles. To
 *  spend 1% of a 16.7 ms frame this file would need on the order of a thousand
 *  more draw calls, or nine thousand more umbrellas.
 *
 *  THE OPERATOR IS RIGHT: on the GPU these are free, and the estimate needed no
 *  correction. WHERE THEY ARE NOT FREE IS THE BOOT. Time-to-first-frame is
 *  already over its bar and the region layer is a whole-island measurement; see
 *  `buildMs` and the boot figures at the end of this file's round notes. That
 *  is the number to watch, not the frame.
 *
 * ════════════════════════════════════════════════════════════════════════════
 *  THE EVIDENCE ROUND, VERIFIED — 2026-08-08. Every claim, its instrument, and
 *  the number before and after. Nothing here is an estimate.
 * ════════════════════════════════════════════════════════════════════════════
 *
 *  harness/pr-inspect.mjs    STABLE PREFIX  PASS -> PASS,  faults 0 -> 0
 *                            counts 10/8/5/3/1 -> 10/8/5/3/1 (unchanged)
 *                            prop DRAW CALLS  4/4/5/5/4 -> 4/4/4/4/4
 *                              — the 5s were gi auto-enrolling the pier as a
 *                                caster at medium and low only. Now decided.
 *                            prop triangles at ultra  1,160 -> 1,812
 *
 *  harness/pr-clear.mjs      0 faults, 0 console errors -> 0 and 0, with a NEW
 *                            check the old gate did not have: nothing below the
 *                            tide line. Under it the SHIPPED set failed —
 *                            umbrella elevations ran a median 0.53 m against a
 *                            2.95 m wash line. Now 2.96 - 3.07 m minimum across
 *                            all six layouts.
 *                            piers built      4 of 6 -> 6 of 6
 *                            pier head depth  0.14 m (and two refusals) ->
 *                                             1.76 - 8.16 m
 *                            approach path    absent -> 259 - 371 m, every one
 *                                             of the six
 *
 *  harness/soak.mjs          PASS, all eight counters 0, 498 parses / 6 layouts
 *                            --parses 500 --layouts 6.  NOTE: soak does not
 *                            load props, so this is a whole-world regression
 *                            gate and is silent about anything in this file.
 *                            `pr-clear.mjs` exists because of that.
 *
 *  pytest tests/ -q          1043 passed, 7 skipped -> 1043 passed, 7 skipped
 *
 *  harness/pr-boot.mjs       paired median delta  +26 ms -> +31 ms, spread
 *                            [-76, +78] on 6 interleaved passes. The spread
 *                            straddles zero, so the honest statement is
 *                            UNCHANGED WITHIN THE NOISE. The module's own build
 *                            clock is the number that actually moved, and it is
 *                            the one to quote: 11.3 ms -> 12.0 ms
 *                            (regions 7.9 -> 8.2, props 3.4 -> 3.8, the extra
 *                            being the fine placement pass).
 *
 *  harness/pr-sun.mjs        the drawn shade's elevation equals
 *                            `gi.sunDirection`'s at every hour tested, and a
 *                            STATIC frame triggers ZERO rebuilds — the
 *                            hysteresis holds. A full day sweep costs eleven.
 *
 *  THE SUN ELEVATION AT time=9 IS 23.82 DEGREES, measured off `gi.sunDirection`
 *  — the vector the shadow map is actually built from. Three rounds of this
 *  project have quoted 36.4, 24 and 19.7 for this hour. This file does not pick
 *  one: `_sun()` reads the live vector, so the drawn shade agrees with every
 *  real shadow beside it whatever the number turns out to be.
 */
import * as THREE from 'three';

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
function smoothstep(a, b, x) {
  const t = clamp((x - a) / (b - a || 1e-6), 0, 1);
  return t * t * (3 - 2 * t);
}

/** A stable hash — the only source of "randomness" in this file, and it is a
 *  pure function of the integer cell a thing stands in, so it is not a roll.
 *  Same landform, same lattice, same jitter, for ever. */
function h1(a, b, s) {
  let h = Math.imul(a | 0, 374761393) + Math.imul(b | 0, 668265263) +
          Math.imul(s | 0, 2246822519);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

/* ---- geometry plumbing ---------------------------------------------------
 *
 * `BufferGeometryUtils` is in three/addons, which is not vendored — buildings.js
 * says the same and carries its own. This one carries COLOUR instead of uv,
 * because a prop is painted, not textured: one merged prototype, one
 * InstancedMesh, one draw call, and per-instance variation from `instanceColor`
 * multiplied over the baked vertex colour. */
function paint(g, r, gr, bl) {
  const n = g.attributes.position.count;
  const c = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { c[i * 3] = r; c[i * 3 + 1] = gr; c[i * 3 + 2] = bl; }
  g.setAttribute('color', new THREE.BufferAttribute(c, 3));
  return g;
}

function mergeParts(list) {
  if (!list || !list.length) return null;
  let vTotal = 0, iTotal = 0;
  for (const g of list) {
    if (!g.attributes.normal) g.computeVertexNormals();
    if (!g.attributes.color) paint(g, 1, 1, 1);
    if (!g.getIndex()) {
      const n = g.attributes.position.count;
      const seq = new Uint32Array(n);
      for (let i = 0; i < n; i++) seq[i] = i;
      g.setIndex(new THREE.BufferAttribute(seq, 1));
    }
    vTotal += g.attributes.position.count;
    iTotal += g.getIndex().count;
  }
  const pos = new Float32Array(vTotal * 3);
  const nor = new Float32Array(vTotal * 3);
  const col = new Float32Array(vTotal * 3);
  const idx = vTotal > 65000 ? new Uint32Array(iTotal) : new Uint16Array(iTotal);
  let vo = 0, io = 0;
  for (const g of list) {
    const p = g.attributes.position;
    pos.set(p.array, vo * 3);
    nor.set(g.attributes.normal.array, vo * 3);
    col.set(g.attributes.color.array, vo * 3);
    const gi = g.getIndex();
    for (let i = 0; i < gi.count; i++) idx[io + i] = gi.getX(i) + vo;
    vo += p.count; io += gi.count;
    g.dispose();
  }
  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  out.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  out.setAttribute('color', new THREE.BufferAttribute(col, 3));
  out.setIndex(new THREE.BufferAttribute(idx, 1));
  out.computeBoundingSphere();
  return out;
}

/* ---- the numbers ----------------------------------------------------------
 *
 * Only two absolute constants survive in the region rule and both are lengths
 * in metres of the instrument, not of the landform: the lattice pitch and the
 * spacing between two umbrellas. Everything a landform could retune out from
 * under is a measured percentile. */

/** The land-mask / distance-transform pitch. 10 m is a quarter of the narrowest
 *  strand terrain draws (`beachW` is 134 m here, `cliffW` 44 m), so the shore
 *  band is resolved several cells deep everywhere it exists, and the whole
 *  transform is ~19k height reads — measured at the bottom of this file. */
const MASK_CELL = 10;

/** The PLACEMENT pitch, used only inside one cluster radius of the anchor.
 *  Half the umbrella spacing, so the greedy pass has real choices to make
 *  between pitches instead of taking whatever the region lattice happened to
 *  leave standing. See the fine pass in `_beachSites`. */
const FINE_CELL = 4.5;

/** Two umbrellas, in metres. Below this they read as one object. */
const UMBRELLA_SPACING = 9;

/** The full set at ultra. The operator asked for ten. */
const UMBRELLA_MAX = 10;

/** How far a beach reaches, for the purpose of deciding which stretch of coast
 *  is THE beach. Ten umbrellas at nine metres is ninety metres of sand, so the
 *  cluster radius is the set's own footprint and not an independent number. */
const CLUSTER_R = UMBRELLA_MAX * UMBRELLA_SPACING * 0.8;

/** Nothing stands within this of a rail, a buffer stop or an instrument. */
const CLEAR_RAIL = 9;
const CLEAR_PLANT = 14;

/** THE WASH LINE, in metres of elevation above the waterline: the top of the
 *  surf-wash tone terrain paints, and therefore the lowest a thing that stands
 *  may stand.
 *
 *  IT IS NOT A NUMBER THAT SOUNDS RIGHT — it is read off terrain.js's own wet
 *  band, which is a function of elevation and nothing else:
 *
 *      strand  = smoothstep(10.0, 0.0, h - waterY)          terrain.js:4712
 *      wetSand = smoothstep(0.79,  0.965, strand)           terrain.js:7605
 *      damp    = smoothstep(0.52,  0.83,  strand)           terrain.js:7606
 *
 *  Inverting the outer smoothstep: `wetSand` is full below 1.12 m of elevation
 *  and reaches zero at 2.95 m; the capillary `damp` fringe above it runs out at
 *  4.87 m. So 2.95 m is where the visibly darker, wetter sand ENDS and the pale
 *  dry strand begins, and it is the line a deck chair goes above.
 *
 *  THE PREVIOUS VALUE WAS 0.9 m, WHICH IS INSIDE THE SATURATED BAND. Measured
 *  (`harness/pr-tide.mjs`, 2026-08-08) the ten shipped umbrellas stood at a
 *  median 0.53 m and a maximum 1.79 m of elevation — every one of them below
 *  the wash, on the seaward side of the tone, which is exactly what the art
 *  director saw. The old constant was defending against standing IN THE WATER
 *  (freeboard) and it did that correctly; it was never a tide line and it was
 *  read as one.
 *
 *  It mirrors an absolute elevation in another module, so it is exactly the
 *  kind of rule that goes inert under a retune — see REQUESTS.md, "THE PATTERN".
 *  The guard is that `_beachSites` WARNS when nothing clears it: a wash line
 *  that admits the whole band, or none of it, is reported rather than obeyed.
 *  The fix is for terrain to publish its strand window; that is filed. */
const WASH_LINE = 2.95;

/** How much of terrain's `forest` mask a piece of sand may carry and still be
 *  somewhere you would put a deck chair. Same form as the `hard > 0.25` gate
 *  beside it: an absolute knee on a field that is normalised 0..1 by
 *  construction, not on a raw noise field. See `_beachSites`. */
const OPEN_SAND = 0.30;

/** Jitter amplitude, as a fraction of the mask cell. */
const JITTER = 0.55;

/** The full sets at ultra, floored by the tier ladder to 1 at the bottom.
 *  Boats were six and are four: see `_buildBoats`. There is no BIRD_MAX any
 *  more — the gulls were cut, and the reason is at the head of this file. */
const BOAT_MAX = 4;

/** The pier. Length is a target, not a promise — it stops at whichever comes
 *  first, this or the point where the seabed stops falling away. Lengthened
 *  from 54 m so the head has a chance of reaching water deep enough to berth
 *  the boats that moor at it; the head's actual depth is measured and
 *  published on `this.pier` rather than assumed. */
const PIER_LEN = 78;
const PIER_BAY = 6;          // metres between pile pairs
const PIER_W = 3.2;
const PIER_DECK_H = 2.1;     // deck above the waterline
const PIER_HEAD_W = 2.3;     // the head, as a multiple of PIER_W
const PIER_HEAD_L = 8;       // …and how far back along the deck it runs

/* ---- the approach ---------------------------------------------------------
 *
 * A desire line is not decoration and it is not cheap-looking: it is the single
 * strongest statement a frame can make that people come here, because it is the
 * only prop that is a RECORD OF MOVEMENT rather than an object. It also costs
 * less than the umbrellas it leads to. */

/** How far the path will walk inland looking for something to connect to.
 *  Beyond this it has stopped being an approach and started being a road, and
 *  roads belong to the `city` round. */
const PATH_MAX_LEN = 420;
const PATH_STEP = 7;          // metres between spine samples
const PATH_W_INLAND = 2.0;    // one person wide, up in the dune
const PATH_W_BEACH = 5.5;     // …fanning where it comes out onto open sand
const PATH_LIFT = 0.06;       // metres proud of the ground

/** How dark a decal gets at its centre, as a multiplier on whatever the ground
 *  already is. These are the only appearance constants in the file that were
 *  chosen by eye rather than measured, and they are stated as multipliers so
 *  they cannot go inert: 1.0 is "no change" whatever terrain repaints itself. */
/* Trodden ground: darker, warmer, duller. Judged on the operator's own frame
 * and strengthened once from 0.80/0.77/0.72 — at 900 m a 2 m track is under
 * four pixels wide, so the only thing carrying it is VALUE, and against tree
 * shadow on a dune it was losing. Bare compacted earth really is this much
 * darker than the grass beside it. */
const WEAR_MUL = [0.71, 0.67, 0.61];
const SHADE_MUL = [0.62, 0.66, 0.78];   // shade: darker and BLUER, because the
                                        //   only light in a shadow is the sky
const HULL_SHADE_MUL = [0.70, 0.74, 0.84];

/** Below this solar elevation there is no disc to draw: the penumbra is wider
 *  than the umbrella and the shadow has stopped being a shape. In degrees. */
const SHADE_MIN_ELEV = 7;

/** How far the sun may move before the drawn shade is rebuilt. Degrees of
 *  azimuth and of elevation. This is the ONLY thing in this file that can
 *  trigger a rebuild outside `onPlan`/`onQuality`, it is keyed on the SUN and
 *  never on the camera, and the hysteresis is what stops a time sweep
 *  re-merging geometry every frame. */
const SHADE_REBUILD_AZ = 4;
const SHADE_REBUILD_EL = 2;

export class Props {
  constructor(ctx) {
    this.ctx = ctx;
    this.group = null;
    /* THE PUBLISHED REGION CONTRACT — see the header. Null until `build()` has
     * measured the land; a consumer may read `ctx.regions` instead, which is
     * the same object. */
    this.regions = null;
    this._meshes = [];
    this._terrain = null;
    this._mask = null;
    this._warnings = [];
    /* THERE ARE NO GULLS, AND THE COUNTER SAYS SO FROM THE START. It is set
     * here as well as in `_clearScene` because `build()` does not clear, so an
     * instrument reading it before the first relayout would otherwise get
     * `undefined` — which reads as "the probe broke" where 0 reads as "there
     * are none", and only one of those is true. */
    this.birdCount = 0;
  }

  /* ======================================================================== *
   *  BUILD
   * ======================================================================== */

  async build(plan) {
    this.group = new THREE.Group();
    this.group.name = 'props';
    this.group.matrixAutoUpdate = false;
    this.ctx.scene.add(this.group);
    this._rebuild(plan);
  }

  /** The landform is a function of the plan, so the regions are too. Idempotent
   *  by construction — everything is torn down and re-measured. */
  onPlan(plan) {
    if (!this.group) return;
    this._clearScene();
    this._rebuild(plan);
  }

  _rebuild(plan) {
    const t0 = (typeof performance !== 'undefined') ? performance.now() : 0;
    this._warnings = [];
    this._terrain = this.ctx?.world?.subsystems?.get?.('terrain') || null;
    this._plan = plan || this.ctx?.plan || null;
    this._measure();
    this._publish();
    const t1 = (typeof performance !== 'undefined') ? performance.now() : 0;
    this._buildProps();
    const t2 = (typeof performance !== 'undefined') ? performance.now() : 0;
    this.buildMs = {regions: +(t1 - t0).toFixed(1), props: +(t2 - t1).toFixed(1)};
  }

  /** Everything the tier can change, and nothing it cannot.
   *
   *  THE DECALS GO LAST AND THAT IS STRUCTURAL, not tidiness: the drawn shade
   *  is a function of where the umbrellas, the pier and the boats ended up, so
   *  it cannot be built until they have been. It is also the only pass that
   *  reads the sun. */
  _buildProps() {
    this._buildKeepOut();
    this._builtShare = this._tierShare();
    /* A SECOND WARNING CHANNEL, and it exists because the first one could not
     * carry these. `_warnings` is snapshotted into the published contract by
     * `_publish()`, which runs BEFORE this method — so anything a prop pass
     * discovered ("the pier head stands in 0.1 m of water") went into an array
     * nobody would ever read again. Measured: that warning fired on layout 0 of
     * `pr-clear.mjs` and appeared in no output anywhere. A warning that goes
     * nowhere is worse than no warning, because it looks like diligence. */
    this.propWarnings = [];
    this._buildUmbrellas();
    this._buildPier();
    this._buildBoats();
    this._buildPath();
    this._buildDecals();
    if (this.propWarnings.length) {
      console.warn('[props]', this.propWarnings.join(' | '));
    }
  }

  /** THE LIGHT THAT ACTUALLY CASTS, read live, never stored as a constant.
   *
   *  Three rounds of this project have quoted three different sun elevations
   *  for the same hour — 36.4, 24 and 19.7 degrees — and at least two of them
   *  were confidently wrong. The way out is not to pick one: it is to read the
   *  SAME VECTOR the renderer casts shadows with, so a drawn shade lines up
   *  with every real shadow in the frame by construction and the number stops
   *  mattering. That vector is `gi.sunDirection`, which gi copies from
   *  `solarDirection` (gi.js:1917), which is `sky.sunDirection` whenever the
   *  sky has one. Measured through it at time=9: elevation 23.82 deg, azimuth
   *  124.51 deg, so ten metres of height throws 22.6 m of shadow and a 2.06 m
   *  umbrella throws 4.7 m — a real ellipse at the operator's camera.
   *
   *  Returns null when there is no sun to throw anything: below the horizon gi
   *  turns the key light into the moon, and moonlight does not draw discs. */
  _sun() {
    const gi = this.ctx?.world?.subsystems?.get?.('gi');
    const d = gi?.sunDirection;
    if (!d || !Number.isFinite(d.x) || !Number.isFinite(d.y)) return null;
    const horiz = Math.hypot(d.x, d.z);
    if (horiz < 1e-4) return null;
    const elev = Math.atan2(d.y, horiz) * 180 / Math.PI;
    if (!(elev > SHADE_MIN_ELEV)) return null;
    if (!(gi.dayFactor === undefined || gi.dayFactor > 0.15)) return null;
    const t = Math.tan(elev * Math.PI / 180);
    const s = Math.sin(elev * Math.PI / 180);
    return {
      /* the direction a SHADOW runs: away from the sun, horizontally */
      sx: -d.x / horiz, sz: -d.z / horiz,
      elev, azDeg: Math.atan2(d.x, -d.z) * 180 / Math.PI,
      /* metres of shadow per metre of height, and how much a horizontal disc
       * stretches along the shadow */
      throwPerM: 1 / t, stretch: 1 / s,
      /* how hard the shade is: a low sun through a lot of atmosphere makes a
       * weaker, longer, softer shadow, and drawing a full-strength disc at
       * 8 degrees is the thing that reads as a sticker. */
      strength: clamp(smoothstep(SHADE_MIN_ELEV, 26, elev), 0, 1),
    };
  }

  /** ONE material for every prop, shared. They are all painted vertex colours
   *  on a rough dielectric; giving each its own would be four shader programs
   *  and four gi registrations for one appearance. Geometry is what makes them
   *  separate draws, and there is no way round that — see the cost block. */
  _material() {
    if (!this._mat) {
      this._mat = new THREE.MeshStandardMaterial({
        vertexColors: true, roughness: 0.86, metalness: 0,
        side: THREE.DoubleSide, dithering: true,
      });
      this.ctx?.world?.subsystems?.get?.('gi')?.applyGI?.(this._mat);
    }
    return this._mat;
  }

  /** THE SECOND MATERIAL, and the only one — every decal in the file shares it
   *  and they are all merged into ONE mesh, so the whole evidence layer is one
   *  extra draw call. At the measured 0.06 us per draw that is 6% of a
   *  microsecond.
   *
   *  IT MULTIPLIES, IT DOES NOT PAINT. A shade disc and a worn path are both
   *  DARKENINGS of whatever the ground already is — sand, wet sand, grass,
   *  shingle, whatever terrain repaints itself as next round — and an opaque
   *  brown ellipse laid over sand is a sticker, which is precisely the "coloured
   *  confetti" reading the last set earned. With `MultiplyBlending` the vertex
   *  colour IS the multiplier: 1.0 is invisible, so every decal in this file
   *  fades to nothing at its own edge for free, with no alpha attribute and no
   *  soft-edge texture, and it composites correctly over ground it has never
   *  been told about.
   *
   *  `toneMapped: false` because the value is a multiplier and not a colour —
   *  tone-mapping it before the blend would darken the darkening. `fog: false`
   *  for the same reason: fog mixes toward the fog colour, which on a bright
   *  day would lift a shadow toward white at exactly the 900 m the operator's
   *  camera watches from, and the whole point of the disc is that it survives
   *  that distance. `depthWrite: false` and a negative polygon offset so it
   *  cannot z-fight the ground it lies on, and it never casts or receives —
   *  a shadow that catches a shadow is a second shadow. */
  _decalMaterial() {
    if (!this._decalMat) {
      this._decalMat = new THREE.MeshBasicMaterial({
        vertexColors: true, transparent: true, depthWrite: false,
        blending: THREE.MultiplyBlending, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4,
        toneMapped: false, fog: false,
        /* three's WebGLState refuses to set up MultiplyBlending without this
         * and says so, once per frame, for ever: "THREE.WebGLState:
         * MultiplyBlending requires material.premultipliedAlpha = true".
         * Measured: five of those in `pr-inspect.mjs`'s console capture at
         * every tier on the first build of this material. Every fragment here
         * has alpha 1, so premultiplied and straight are the same numbers and
         * the flag only picks the blend equation three wants. */
        premultipliedAlpha: true,
      });
    }
    return this._decalMat;
  }

  /** A soft-edged ellipse of darkening, as one fan: a dark centre vertex and a
   *  white rim, so the radial gradient comes out of the vertex colours and
   *  costs nothing. `ax` runs along the shadow, `az` across it.
   *
   *  IT DRAPES. A shadow at 23.8 degrees of sun is 6.4 m long, and the umbrella
   *  sites this island offers have slopes around 0.20 — so over the ellipse's
   *  own semi-major axis the ground moves about 0.7 m, and a FLAT disc drawn at
   *  one ground sample would be buried at the uphill end and floating at the
   *  downhill one. Every rim vertex gets its own `ground()` read: fifteen per
   *  umbrella, a hundred and fifty for the whole set, once per build. */
  static _shadeDisc(groundAt, cx, cz, ax, az, dirX, dirZ, mul, k, lift) {
    const SEG = 14;
    const pos = new Float32Array((SEG + 2) * 3);
    const col = new Float32Array((SEG + 2) * 3);
    const cy = groundAt(cx, cz);
    if (!Number.isFinite(cy)) return null;
    pos[0] = cx; pos[1] = cy + lift; pos[2] = cz;
    /* the centre carries the full multiplier, scaled by how hard the light is */
    col[0] = 1 - (1 - mul[0]) * k;
    col[1] = 1 - (1 - mul[1]) * k;
    col[2] = 1 - (1 - mul[2]) * k;
    for (let n = 0; n <= SEG; n++) {
      const a = (n / SEG) * Math.PI * 2;
      const u = Math.cos(a) * ax, v = Math.sin(a) * az;
      const o = (n + 1) * 3;
      const px = cx + dirX * u - dirZ * v;
      const pz = cz + dirZ * u + dirX * v;
      const py = groundAt(px, pz);
      pos[o] = px;
      pos[o + 1] = (Number.isFinite(py) ? py : cy) + lift;
      pos[o + 2] = pz;
      col[o] = col[o + 1] = col[o + 2] = 1;      // white rim: no change at all
    }
    const idx = new Uint16Array(SEG * 3);
    for (let n = 0; n < SEG; n++) {
      idx[n * 3] = 0; idx[n * 3 + 1] = n + 1; idx[n * 3 + 2] = n + 2;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.computeVertexNormals();
    return g;
  }

  /** A soft-edged RIBBON of darkening down a polyline: four columns across —
   *  white, dark, dark, white — so it has the same free gradient at both edges
   *  that the disc has at its rim. `stations` are {x, y, z, hw, mul} and the
   *  half-width and strength may vary down the run, which is what lets one
   *  routine draw both a footpath that fans out and a pier's shadow stripe. */
  static _shadeRibbon(stations) {
    const n = stations.length;
    if (n < 2) return null;
    const pos = new Float32Array(n * 4 * 3);
    const col = new Float32Array(n * 4 * 3);
    const idx = new Uint16Array((n - 1) * 3 * 6);
    const COLS = [-1, -0.42, 0.42, 1];
    for (let i = 0; i < n; i++) {
      const s = stations[i];
      const a = stations[Math.max(0, i - 1)], b = stations[Math.min(n - 1, i + 1)];
      let tx = b.x - a.x, tz = b.z - a.z;
      const L = Math.hypot(tx, tz) || 1;
      tx /= L; tz /= L;
      const nx = -tz, nz = tx;                    // the across direction
      for (let c = 0; c < 4; c++) {
        const o = (i * 4 + c) * 3;
        pos[o] = s.x + nx * COLS[c] * s.hw;
        pos[o + 1] = s.y;
        pos[o + 2] = s.z + nz * COLS[c] * s.hw;
        const edge = (c === 0 || c === 3);
        for (let ch = 0; ch < 3; ch++) {
          col[o + ch] = edge ? 1 : 1 - (1 - s.mul[ch]) * s.k;
        }
      }
    }
    let w = 0;
    for (let i = 0; i < n - 1; i++) {
      for (let c = 0; c < 3; c++) {
        const a = i * 4 + c, b = a + 1, d = a + 4, e = d + 1;
        idx[w++] = a; idx[w++] = d; idx[w++] = b;
        idx[w++] = b; idx[w++] = d; idx[w++] = e;
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.computeVertexNormals();
    return g;
  }

  /* ======================================================================== *
   *  THE REGION LAYER
   * ======================================================================== */

  /** Measure the land, then set every knee at a measured percentile of it.
   *
   *  Order matters and it is the order the pattern note prescribes: build the
   *  mask, transform it, sample the fields ON LAND (never over the bounding
   *  square — an island's square is two thirds sea and terrain reports moisture
   *  near 1 under water, which is how one probe read the whole island as wet),
   *  take percentiles, and only then write a rule. */
  _measure() {
    const t = this._terrain;
    const ground = (x, z) => {
      const h = this.ctx.ground ? this.ctx.ground(x, z) : NaN;
      return Number.isFinite(h) ? h : NaN;
    };
    this.waterY = Number.isFinite(t?.waterY) ? t.waterY
                : Number.isFinite(t?.waterLevel) ? t.waterLevel : null;

    /* No terrain, no landform, no regions. `source: 'none'` and one flat
     * answer, which is the failure mode a missing field HAS to have: a rule
     * that quietly returns a plausible constant is the bug this project has
     * paid for six times. */
    if (!t || this.waterY === null || typeof t.biomeAt !== 'function') {
      this._mask = null;
      this._source = 'none';
      this._warnings.push('no terrain: regions are unmeasured and `at()` ' +
                          'answers country everywhere');
        this._hist = {beach: 0, city: 0, country: 0};
      this._landSamples = 0;
      return;
    }
    this._source = 'terrain';

    /* ---- the land mask, and the distance transform over it ---------------- */
    const cx = Number.isFinite(t.cx) ? t.cx : 0;
    const cz = Number.isFinite(t.cz) ? t.cz : 0;
    const R = (Number.isFinite(t.islandR) ? t.islandR : 500)
            + (Number.isFinite(t.coastWobble) ? Math.abs(t.coastWobble) : 0) + 40;
    const N = Math.ceil(2 * R / MASK_CELL) + 1;
    const x0 = cx - R, z0 = cz - R;
    const land = new Uint8Array(N * N);
    let nLand = 0;
    for (let j = 0; j < N; j++) {
      const z = z0 + j * MASK_CELL;
      for (let i = 0; i < N; i++) {
        const h = ground(x0 + i * MASK_CELL, z);
        const L = (h > this.waterY) ? 1 : 0;
        land[j * N + i] = L; nLand += L;
      }
    }
    /* Chamfer, two passes, seeded 0 on every water cell. The grid's own border
     * is sea by construction of R, so the transform is bounded and no land cell
     * can report an unbounded distance. In CELLS; multiplied out on read. */
    const d = new Float32Array(N * N);
    for (let k = 0; k < N * N; k++) d[k] = land[k] ? 1e9 : 0;
    const A = 1, B = Math.SQRT2;
    for (let j = 0; j < N; j++) {
      for (let i = 0; i < N; i++) {
        const k = j * N + i; if (!land[k]) continue;
        let v = d[k];
        if (i > 0) v = Math.min(v, d[k - 1] + A);
        if (j > 0) v = Math.min(v, d[k - N] + A);
        if (i > 0 && j > 0) v = Math.min(v, d[k - N - 1] + B);
        if (i < N - 1 && j > 0) v = Math.min(v, d[k - N + 1] + B);
        d[k] = v;
      }
    }
    for (let j = N - 1; j >= 0; j--) {
      for (let i = N - 1; i >= 0; i--) {
        const k = j * N + i; if (!land[k]) continue;
        let v = d[k];
        if (i < N - 1) v = Math.min(v, d[k + 1] + A);
        if (j < N - 1) v = Math.min(v, d[k + N] + A);
        if (i < N - 1 && j < N - 1) v = Math.min(v, d[k + N + 1] + B);
        if (i > 0 && j < N - 1) v = Math.min(v, d[k + N - 1] + B);
        d[k] = v;
      }
    }
    this._mask = {N, x0, z0, cell: MASK_CELL, land, d, cx, cz, R};

    /* ---- the plant, and how big its apron is ------------------------------ */
    const st = (this._plan?.stations || [])
      .filter(s => Number.isFinite(s?.x) && Number.isFinite(s?.z))
      .map(s => ({x: s.x, z: s.z}));
    this._stations = st;
    const nn = [];
    for (const a of st) {
      let m = Infinity;
      for (const c of st) {
        if (c === a) continue;
        const dx = a.x - c.x, dz = a.z - c.z;
        const q = Math.sqrt(dx * dx + dz * dz);
        if (q < m) m = q;
      }
      if (Number.isFinite(m)) nn.push(m);
    }
    nn.sort((a, b) => a - b);
    /* THE INSTRUMENTS' OWN SPACING, not a number that sounds right. Three
     * quarters of it, so two adjacent bays' aprons overlap and the plant comes
     * out as one connected block rather than seven discs — which is what a road
     * round needs to lay a network in. */
    const nnMed = nn.length ? nn[nn.length >> 1] : 90;
    this._cityR = nnMed * 0.75;
    this._nnMed = nnMed;

    /* ---- the fields, ON LAND, and their percentiles ----------------------- */
    const pct3 = (v, name, floor) => {
      if (v.length < 24) {
        this._warnings.push(name + ': only ' + v.length + ' samples, rule off');
        return null;
      }
      v.sort((a, b) => a - b);
      const at = f => v[Math.min(v.length - 1, Math.floor(v.length * f))];
      const lo = at(0.05), mid = at(0.50), hi = at(0.95);
      if (hi - lo > floor && mid > lo && hi > mid) return [lo, mid, hi];
      this._warnings.push(name + ' is flat (' + lo.toFixed(3) + '..' +
                          hi.toFixed(3) + '); that rule is off');
      return null;
    };

    const STEP = 2;                       // every other cell — a 20 m lattice
    const pts = [];
    for (let j = 0; j < N; j += STEP) {
      for (let i = 0; i < N; i += STEP) {
        if (!land[j * N + i]) continue;
        pts.push(x0 + i * MASK_CELL, z0 + j * MASK_CELL);
      }
    }
    const dwAll = [], slAll = [], alAll = [];
    for (let q = 0; q < pts.length; q += 2) {
      const s = t.biomeAt(pts[q], pts[q + 1]);
      if (!s || !Number.isFinite(s.altitude)) continue;
      dwAll.push(this.dWaterAt(pts[q], pts[q + 1]));
      slAll.push(s.slope); alAll.push(s.altitude);
    }
    this._landSamples = dwAll.length;
    this._dWaterRange = pct3([...dwAll], 'dWater', 4);

    /* THE SHORE BAND. Two independent bounds and the tighter wins, so neither
     * can go stale on its own: terrain's published strand width (`beachW`, a
     * fraction of `islandR`, so it shrinks with the island) and the nearer
     * quarter of the land as actually measured. On the demo fleet that is
     * min(134.7, 51.3) = 51.3 m. */
    const dwS = [...dwAll].sort((a, b) => a - b);
    const dwP25 = dwS.length ? dwS[Math.floor(dwS.length * 0.25)] : 50;
    this._shoreW = Math.min(Number.isFinite(t.beachW) ? t.beachW : Infinity, dwP25);
    if (!Number.isFinite(this._shoreW) || this._shoreW <= 0) this._shoreW = dwP25 || 50;

    /* THE POPULATION THE BEACH RULE ACTUALLY SORTS is the band, not the island.
     * Centring the flat/low gates on the whole island's slope would put the
     * knee up on the hillsides, where no beach can be, and the rule would fire
     * on every coastal cell — a constant wearing a number. */
    const bs = [], ba = [];
    for (let q = 0; q < dwAll.length; q++) {
      if (dwAll[q] <= this._shoreW) { bs.push(slAll[q]); ba.push(alAll[q]); }
    }
    this._bandSlopeRange = pct3(bs, 'coastal-band slope', 0.01);
    this._bandAltRange = pct3(ba, 'coastal-band altitude', 0.3);

    this._regionReport(pts);
  }

  /** Two straight segments meeting at the measured median, so the mapped median
   *  of any field is exactly 0.5 whatever shape the raw one has. Vegetation's
   *  `_mapField`, and for the same reason — a linear stretch between p05 and
   *  p95 is not a normalisation on a skewed field. A null range means the field
   *  was never measured, and the honest answer is the neutral half. */
  static _mapField(v, r) {
    if (!r) return 0.5;
    if (v <= r[1]) return clamp((v - r[0]) / (r[1] - r[0] || 1e-6), 0, 1) * 0.5;
    return 0.5 + clamp((v - r[1]) / (r[2] - r[1] || 1e-6), 0, 1) * 0.5;
  }

  /** Metres to the nearest water. 0 in water, and the honest `Infinity` when
   *  there is no mask to read. */
  dWaterAt(x, z) {
    const m = this._mask;
    if (!m) return Infinity;
    const i = clamp(Math.round((x - m.x0) / m.cell), 0, m.N - 1);
    const j = clamp(Math.round((z - m.z0) / m.cell), 0, m.N - 1);
    return m.d[j * m.N + i] * m.cell;
  }

  /** Metres to the nearest instrument. */
  dPlantAt(x, z) {
    let best = Infinity;
    for (const s of this._stations || []) {
      const dx = x - s.x, dz = z - s.z;
      const q = dx * dx + dz * dz;
      if (q < best) best = q;
    }
    return best === Infinity ? Infinity : Math.sqrt(best);
  }

  /** How much of a beach a piece of ground is, 0..1.
   *
   *  Three median-centred terms multiplied: flat FOR THIS BAND, low ENOUGH for
   *  this band, and near the water. Zero outside the band, so the product is a
   *  coastal field by construction and nothing inland can score. The `0.35`
   *  floor under the nearness term is there so a wide shelving strand does not
   *  taper to nothing halfway across itself — it is a floor on one of three
   *  factors, not a threshold.
   *
   *  ── THE `low` TERM IS A HALF-WINDOW AND THAT IS A CORRECTION ─────────────
   *
   *  It was `1 - _mapField(altitude, bandAlt)`, strictly descending, which says
   *  "the lower the better, all the way to the waterline". That is not what a
   *  beach is. A beach is THE WHOLE STRAND — the wet foreshore AND the dry sand
   *  above it are equally beach — and a rule that peaks at the waterline is a
   *  rule that thinks the beach is the surf.
   *
   *  Measured (`harness/pr-band.mjs`, 2026-08-08), on the demo island's 1,512
   *  coastal-band cells, that bias was total and not marginal:
   *
   *      elevation      cells   scored beach   mean beachness
   *      0.00 - 0.50      247        247            0.679
   *      0.50 - 1.12      196        181            0.437
   *      1.12 - 2.00      158         71            0.287
   *      2.00 - 2.95      134         12            0.185
   *      2.95 - 4.00      104          0            0.107   <- the dry strand
   *      4.00 - 5.50       99          0            0.065
   *
   *  Not one cell above the wash line was a beach. So there was nowhere legal
   *  to put an umbrella that was not in the surf, and the freeboard gate could
   *  not be raised without emptying the set — the placement bug and the
   *  classifier bug were the same bug.
   *
   *  The half-window is the smallest honest fix and it adds NO new constant:
   *  `low` is flat at 1 for everything at or below the band's own measured
   *  median altitude, and only then falls away to 0 at p95. So the rule now
   *  says "the lower HALF of the coastal band's elevation range, all of it
   *  equally" — still a measured percentile, still moves with the land, and it
   *  removes only the preference for the waterline. Verified against the
   *  histogram in `_regionReport`, which is what stops this being a widening
   *  that quietly swallowed the island. */
  beachnessAt(x, z, site) {
    const dw = this.dWaterAt(x, z);
    if (!(dw <= this._shoreW)) return 0;
    const s = site || this._terrain?.biomeAt?.(x, z);
    if (!s || !Number.isFinite(s.slope)) return 0;
    const flat = 1 - Props._mapField(s.slope, this._bandSlopeRange);
    const aMap = Props._mapField(s.altitude, this._bandAltRange);
    const low = 1 - Math.max(0, (aMap - 0.5) * 2);
    const near = 1 - Props._mapField(dw, [0, this._shoreW * 0.5, this._shoreW]);
    return flat * low * Math.max(0.35, near);
  }

  /** The knee on `beachnessAt`. It is a threshold on a field whose median
   *  INSIDE THE BAND is 0.5 by construction of `_mapField`, so it is a
   *  percentile in disguise and moves with the land — not an absolute. */
  static BEACH_T = 0.28;

  /** THE CLASSIFIER. Pure, deterministic, no RNG. */
  regionAt(x, z) {
    if (!this._mask) return 'country';
    const h = this.ctx.ground ? this.ctx.ground(x, z) : NaN;
    if (!(h > this.waterY)) return 'water';
    if (this.dPlantAt(x, z) <= this._cityR) return 'city';
    return this.beachnessAt(x, z) >= Props.BEACH_T ? 'beach' : 'country';
  }

  /** Everything the classifier looked at, for a consumer that wants to
   *  threshold differently — a roads round wanting a wider city, say. */
  scoreAt(x, z) {
    const h = this.ctx.ground ? this.ctx.ground(x, z) : NaN;
    const s = this._terrain?.biomeAt?.(x, z) || null;
    const dw = this.dWaterAt(x, z), dp = this.dPlantAt(x, z);
    return {
      region: this.regionAt(x, z),
      beachness: this._mask && h > this.waterY ? this.beachnessAt(x, z, s) : 0,
      dWater: dw, dPlant: dp, height: h,
      altitude: s ? s.altitude : NaN, slope: s ? s.slope : NaN,
      kind: s ? s.kind : null,
    };
  }

  /** ASSERT THE RULE IS NOT A CONSTANT.
   *
   *  Twenty instruments on this project have given confident wrong answers, and
   *  the cheapest guard against being the twenty-first is to print the rule's
   *  own distribution over the domain it governs. A classifier where one region
   *  claims the island is the seventh inert rule wearing a name. */
  _regionReport(pts) {
    /* NO `water` KEY. The lattice is land cells only — that is deliberate, it
     * is the domain every rule here is about — and a `water: 0` sitting in a
     * published histogram is an invitation to read "this island has no sea".
     * Twenty instruments on this project have been confidently wrong; a key
     * that can only ever be zero is one more waiting to happen. */
    const hist = {beach: 0, city: 0, country: 0};
    for (let q = 0; q < pts.length; q += 2) {
      const r = this.regionAt(pts[q], pts[q + 1]);
      hist[r] = (hist[r] || 0) + 1;
    }
    const n = pts.length / 2;
    this._hist = hist;
    this._landSamples = n;
    if (!n) return;
    for (const k of Object.keys(hist)) {
      if (hist[k] / n >= 0.90) {
        this._warnings.push('region "' + k + '" claims ' +
          (100 * hist[k] / n).toFixed(1) + '% of the land — the classifier is ' +
          'a constant, not a rule');
      }
    }
    /* An island whose coast is entirely cut face genuinely has no beach, and
     * saying "0 beach" is then correct. But it is also exactly what a broken
     * slope gate looks like, so it is reported either way and the band count is
     * carried with it so the two can be told apart. */
    if (!hist.beach) {
      this._warnings.push('no beach anywhere: either this coast is all cliff, ' +
        'or the flat/low gates are inverted. shoreW=' + this._shoreW.toFixed(1) +
        'm, bandSlope=' + JSON.stringify(this._bandSlopeRange));
    }
    if (this._warnings.length) {
      console.warn('[props] regions:', this._warnings.join(' | '));
    }
  }

  /** Publish, on both channels, exactly as rail and index do. */
  _publish() {
    const f = {};
    const n = this._landSamples || 0;
    for (const k of Object.keys(this._hist || {})) {
      f[k] = n ? +(this._hist[k] / n).toFixed(4) : 0;
    }
    const m = this._mask;
    const payload = {
      version: 1,
      source: this._source,
      names: ['water', 'beach', 'city', 'country'],
      at: (x, z) => this.regionAt(x, z),
      scoreAt: (x, z) => this.scoreAt(x, z),
      dWaterAt: (x, z) => this.dWaterAt(x, z),
      dPlantAt: (x, z) => this.dPlantAt(x, z),
      waterY: this.waterY,
      cellM: MASK_CELL,
      shoreW: this._shoreW ?? null,
      cityR: this._cityR ?? null,
      stationSpacingM: this._nnMed ?? null,
      beachThreshold: Props.BEACH_T,
      ranges: {
        dWater: this._dWaterRange || null,
        bandSlope: this._bandSlopeRange || null,
        bandAlt: this._bandAltRange || null,
      },
      /* Counts and fractions OVER LAND, on a 20 m lattice. `water` is not a key:
       * see `_regionReport`. `at()` still returns 'water' for a submerged
       * point — the histogram's domain and the classifier's range are two
       * different things and this is the one place they differ. */
      histogramDomain: 'land, 20 m lattice',
      histogram: this._hist || null,
      fraction: f,
      landSamples: n,
      bounds: m ? {x0: m.x0, z0: m.z0, x1: m.x0 + m.N * m.cell,
                   z1: m.z0 + m.N * m.cell} : null,
      warnings: this._warnings.slice(),
    };
    this.regions = payload;
    try {
      this.ctx.regions = payload;
      this.ctx.emit?.('world:regions', payload);
    } catch (err) { console.warn('[props] could not publish regions', err); }
  }

  /* ======================================================================== *
   *  THE PROPS
   * ======================================================================== */

  /** The tier's share of the full set.
   *
   *  READ THE LIVE TIER, NOT `ctx.quality`. This cost the first build of this
   *  file its whole quality ladder and it is the same bug as the five inert
   *  rules: `ctx.quality` is a snapshot of `engine.tier` taken when the ctx
   *  object was made, and `engine.setTier()` REPLACES `this.tier` with a
   *  different object rather than mutating it — so the reference on `ctx` is
   *  frozen at whatever tier the engine happened to be on at construction.
   *  Since the adaptive ladder now STARTS AT THE FLOOR TIER AND CLIMBS, that
   *  snapshot is always `floor`. Measured: `_tierShare()` returned 0.15 at
   *  ultra, high, medium, low AND floor — five different settings, one answer,
   *  two umbrellas on every one of them. Nothing errored and the number was
   *  plausible, which is precisely the failure mode.
   *
   *  So: the tier handed to `onQuality` first, `engine.tier` (the live field,
   *  not the copy) second, `ctx.quality` only as a last resort.
   *
   *  `particles` is the engine's own "how many small things should there be"
   *  ladder — 1.00 / 0.80 / 0.55 / 0.30 / 0.15 down the five tiers — so reading
   *  it rather than the tier NAME means an engine retune moves prop density
   *  with it. A `props` field on the tier takes precedence if one ever lands. */
  _tier() {
    return this._liveTier || this.ctx?.engine?.tier || this.ctx?.quality || null;
  }

  _tierShare() {
    const q = this._tier();
    const v = Number.isFinite(q?.props) ? q.props
            : Number.isFinite(q?.particles) ? q.particles : 1;
    return clamp(v, 0, 1);
  }

  /** How many of a set to build. FLOOR, not round, and never zero: the operator
   *  asked for "1 umbrella and towels instead of 10" at the bottom of the
   *  ladder, and 0.15 x 10 rounds to two. Floored it is exactly one, and the
   *  ladder reads 10 / 8 / 5 / 3 / 1. */
  _count(max) {
    return Math.max(1, Math.floor(max * this._tierShare() + 1e-6));
  }

  /** The engine's ladder moved. Rebuild the PROPS — not the regions, which are
   *  a function of the landform and cannot change because a shadow map got
   *  smaller. This is the channel the density requirement actually rides on;
   *  see `_tierShare` for what reading `ctx.quality` instead cost. */
  onQuality(tier) {
    this._liveTier = tier || this._liveTier;
    if (!this.group || !this._mask) return;
    if (this._tierShare() === this._builtShare) return;
    this._clearScene();
    this._buildProps();
  }

  _buildKeepOut() {
    /* Track centre-lines, sampled from the frames rail already publishes rather
     * than re-derived — a harness that reproduces another module's rule is
     * checking its own copy of it. Coarse-binned so the test is O(1). */
    const pts = [];
    const rail = this.ctx?.world?.subsystems?.get?.('rail');
    for (const t of rail?.tracks || []) {
      const f = t?.frames;
      if (!f || !f.pos) continue;
      const stride = Math.max(1, Math.round(4 / (f.step || 2)));
      for (let i = 0; i < f.count; i += stride) {
        pts.push(f.pos[i * 3], f.pos[i * 3 + 2]);
      }
    }
    for (const b of rail?.bufferStops || []) pts.push(b.x, b.z);
    this._railPts = pts;
  }

  _offRail(x, z) {
    const p = this._railPts;
    if (!p) return true;
    const r2 = CLEAR_RAIL * CLEAR_RAIL;
    for (let i = 0; i < p.length; i += 2) {
      const dx = x - p[i], dz = z - p[i + 1];
      if (dx * dx + dz * dz < r2) return false;
    }
    return true;
  }

  /** THE ORDERING. Every beach cell on the lattice, scored, best first, with a
   *  minimum spacing enforced greedily — so it is one stable list and the
   *  quality tier takes a prefix of it. Ties broken on the lattice index, which
   *  is a total order, so the list cannot depend on iteration luck. */
  _beachSites(spacing, want) {
    const m = this._mask;
    if (!m || !this._terrain) return [];
    const cand = [];
    const veg = this.ctx?.world?.subsystems?.get?.('vegetation');
    /* VEGETATION'S OWN BARE-BEACH BAND, READ LIVE, NEVER COPIED.
     *
     * `_shore()` returns `{beach, salt, edge, exposure}` for a point, where
     * `beach` is "bare sand and shingle" — the band vegetation itself refuses
     * to plant in. Calling it is the only way to agree with vegetation about
     * where the trees stop without keeping a second copy of `SHORE_BEACH` here,
     * and a second copy is precisely how six rules in this project went inert.
     *
     * It is a private method, so it is used as a PREFERENCE and never as a
     * gate: measured, only 7 of the 770 cells above the wash line clear both
     * `beach > 0.15` and the beachness knee on this island, so hard-gating on
     * it would leave a 10-umbrella set with 7 places to stand. As a ranking
     * term it pulls the cluster onto the barest sand available and degrades to
     * "no opinion" the day the method is renamed. */
    let shoreOf = null;
    if (typeof veg?._shore === 'function') {
      try {
        const probe = veg._shore({coast: 20, x: m.x0 + m.N * m.cell * 0.5,
                                  z: m.z0 + m.N * m.cell * 0.5});
        if (probe && Number.isFinite(probe.beach)) {
          shoreOf = (x, z, dw) => {
            try {
              const r = veg._shore({coast: dw, x, z});
              return Number.isFinite(r?.beach) ? r.beach : 0.5;
            } catch (err) { return 0.5; }
          };
        }
      } catch (err) { shoreOf = null; }
    }
    if (!shoreOf) {
      this._warnings.push('vegetation._shore() unavailable: the umbrella ' +
        'ordering has no opinion about where the trees stop');
    }
    let inBand = 0, aboveWash = 0, openEnough = 0;
    for (let j = 0; j < m.N; j++) {
      for (let i = 0; i < m.N; i++) {
        if (!m.land[j * m.N + i]) continue;
        const x = m.x0 + i * m.cell, z = m.z0 + j * m.cell;
        const dw = m.d[j * m.N + i] * m.cell;
        if (dw > this._shoreW) continue;
        if (this.dPlantAt(x, z) <= this._cityR + CLEAR_PLANT) continue;
        const s = this._terrain.biomeAt(x, z);
        if (!s || s.hard > 0.25 || s.kind === 'hardstanding') continue;
        /* NOT IN THE CREEK. `kind: 'stream'` is terrain's flow field over 0.55
         * — a channel with water running down it. Raising the gate to the wash
         * line pushed the candidate set up the beach and straight into one:
         * `pr-inspect.mjs` put umbrella #8 of ten on a `stream` cell at
         * (249.01, 246.50). Nothing else in the file would have caught it,
         * because it is dry land, above the wash, flat, off the rail and inside
         * the beach region — every existing test passes and it is still a deck
         * chair in a brook. */
        if (s.kind === 'stream') continue;
        inBand++;
        /* ---- AND NOT IN THE WOODS ------------------------------------------
         *
         * Raising the gate to the wash line moved the whole cluster up the
         * beach and straight into the treeline, which on this coast starts
         * almost exactly where the dry sand does. Measured (`_prdbg7`, ten
         * shipped sites): terrain's `forest` mask read 0.44, 0.72, 0.76, 0.77
         * and 0.80 at five of the ten — deck chairs under a canopy — against
         * 0.00-0.17 at the five that were genuinely on open sand. The signal is
         * completely clean and nothing else in the file was reading it.
         *
         * `forest` is a normalised 0..1 terrain mask, the same shape of field
         * as `hard`, which this loop has always gated at an absolute 0.25 for
         * the same reason: a mask that is defined to run 0..1 does not move
         * under a landform retune the way a raw fbm does, so an absolute knee
         * on it is not the inert-rule pattern. It is percentile-checked below
         * all the same, because that assumption is worth one `if`. */
        if (s.forest > OPEN_SAND) continue;
        openEnough++;
        /* ABOVE THE WASH. `altitude` is metres above the waterline — a physical
         * quantity in a stated unit, not a knob on a noise field, so a number
         * in metres is the right form here and cannot go inert under a retune
         * the way a threshold on an fbm can.
         *
         * This gate has been wrong twice in opposite directions. The first
         * build had none, and put umbrellas at 0.06 m and 0.10 m above the
         * water — standing them IN the sea. The second set it at 0.9 m, which
         * kept them out of the water and left every one of them inside the
         * saturated wash tone, on the seaward side of the visible tide line:
         * "Nobody puts a chair where the water comes in." It is now the wash
         * line itself, derived from the band terrain actually paints. */
        if (!(s.altitude >= WASH_LINE)) continue;
        aboveWash++;
        const b = this.beachnessAt(x, z, s);
        if (b < Props.BEACH_T) continue;
        if (!this._offRail(x, z)) continue;
        /* HOW OPEN THIS PATCH OF SAND IS, 0..1. Terrain's forest mask says
         * whether anything grows here; vegetation's own beach band says whether
         * IT would plant here. Both, multiplied, and the second is floored so a
         * missing or renamed `_shore` weakens the preference instead of
         * inverting it. */
        const vb = shoreOf ? shoreOf(x, z, dw) : 0.5;
        const open = (1 - s.forest) * (0.45 + 0.55 * clamp(vb, 0, 1));
        cand.push({x, z, b, i, j, k: j * m.N + i, alt: s.altitude, slope: s.slope,
                   forest: s.forest, open});
      }
    }
    this.beachCandidates = cand.length;
    this.beachBandCells = inBand;
    this.beachOpenCells = openEnough;
    /* THE GATE MUST NOT BE ABLE TO GO SILENTLY INERT IN EITHER DIRECTION.
     * `WASH_LINE` mirrors an elevation window that lives in terrain.js and is
     * not published, so it is exactly the shape of rule this project has had go
     * quietly constant six times. It cannot be measured from here — but its
     * EFFECT can, and an effect of "everything" or "nothing" is reportable. */
    if (inBand >= 24) {
      const kept = aboveWash / inBand;
      if (aboveWash === 0) {
        this._warnings.push('wash line (' + WASH_LINE + 'm) rejected all ' +
          inBand + ' coastal-band cells: either this coast is entirely ' +
          'foreshore, or terrain retuned its wet band out from under it');
      } else if (kept > 0.97) {
        this._warnings.push('wash line (' + WASH_LINE + 'm) rejected ' +
          (100 * (1 - kept)).toFixed(1) + '% of the coastal band — it is not ' +
          'gating anything and has become a constant');
      }
      /* and the same test on the openness gate, for the same reason: `forest`
       * is asserted to be a normalised 0..1 mask, and this is what checks it */
      if (aboveWash >= 24) {
        const op = openEnough / aboveWash;
        if (openEnough === 0) {
          this._warnings.push('openness gate (forest <= ' + OPEN_SAND +
            ') rejected all ' + aboveWash + ' dry cells: this coast is wooded ' +
            'to the waterline, or `forest` is no longer a 0..1 mask');
        } else if (op > 0.99) {
          this._warnings.push('openness gate (forest <= ' + OPEN_SAND +
            ') rejected nothing across ' + aboveWash + ' dry cells — it has ' +
            'become a constant');
        }
      }
    }
    /* ---- ONE BEACH, NOT TEN LONE UMBRELLAS -------------------------------
     *
     * Ordering the whole island's candidates by beachness alone put the ten at
     * (-51,266) (69,336) (-151,-344) (189,326) (-31,-344) (-271,-294) … —
     * every one of them on a different stretch of coast, hundreds of metres
     * apart, one umbrella each. That is not what the operator described and it
     * does not read as one: a single umbrella on an empty shore reads as litter
     * blown up the beach, and ten of them read as ten pieces of litter.
     *
     * So find the best BEACH first — the candidate with the most beach around
     * it, scored as the beachness-weighted count of its neighbours inside
     * `CLUSTER_R` — and then order by nearness to that anchor with beachness as
     * the modifier. The result is one populated stretch, which is what a beach
     * with people on it looks like, and it makes the tier ladder mean something
     * better as well: the floor tier's single umbrella stands in the middle of
     * the beach the ultra tier fills, rather than on whichever lonely cell
     * happened to score highest. */
    const R2 = CLUSTER_R * CLUSTER_R;
    for (const c of cand) {
      let w = 0;
      for (const o of cand) {
        const dx = c.x - o.x, dz = c.z - o.z;
        const q = dx * dx + dz * dz;
        /* OPENNESS IS IN THE ANCHOR WEIGHT, not only in the ordering. The
         * anchor decides which beach this is, and it is also what the pier and
         * the approach are hung off — so a wooded anchor does not just misplace
         * one umbrella, it drags the whole set into the trees, which is exactly
         * what happened. Weighting the neighbours by their openness makes the
         * anchor the middle of the barest stretch of sand. */
        if (q <= R2) w += o.b * o.open * (1 - q / R2);
      }
      c.w = w;
    }
    /* Index is the tiebreak everywhere, never a bare score compare: two cells
     * with identical scores on a symmetric coast would otherwise swap on a V8
     * sort change and the floor tier's one umbrella would move between builds
     * with nothing in the source having altered. */
    cand.sort((a, b) => (b.w - a.w) || (a.k - b.k));
    const anchor = cand[0];
    this.beachAnchor = anchor ? {x: +anchor.x.toFixed(1), z: +anchor.z.toFixed(1),
                                 weight: +anchor.w.toFixed(2)} : null;
    if (!anchor) return [];

    /* ---- AND NOW A FINE PASS, ON THAT BEACH ONLY -------------------------
     *
     * THE COARSE LATTICE PICKS THE BEACH; IT CANNOT PLACE ON IT. `MASK_CELL` is
     * 10 m and two umbrellas stand 9 m apart, so at the coarse pitch almost
     * every surviving cell is its own pitch and the greedy spacing pass has no
     * choices to make — it just takes them in order, wherever they are.
     *
     * That was survivable while there were 124 candidates. Once the wash line
     * and the openness gate had done their work there were SIXTEEN on the whole
     * island, and measured (`pr-inspect.mjs`) the ten came out at (127,375)
     * through (366,244) — 280 metres of coast, which is the "ten lone
     * umbrellas" failure this section was written to prevent, arriving by a new
     * route. The gates were right and the lattice was too coarse to spend them.
     *
     * So the anchor is chosen on the coarse lattice, which is what it is good
     * at, and the PLACEMENT is then resampled at `FINE_CELL` inside one cluster
     * radius of it, with every gate applied again at full strength. The result
     * is one beach with ten pitches on it. The cost is one biomeAt per fine
     * cell over a single disc — measured in `buildMs.props` below, and it is
     * the reason that number is quoted per tier.
     *
     * The tiebreak is the fine lattice index, which is a total order, so the
     * stable-prefix property survives the change of pitch. */
    const FR = CLUSTER_R * 1.15;
    const NF = Math.ceil((2 * FR) / FINE_CELL) + 1;
    const fx0 = anchor.x - FR, fz0 = anchor.z - FR;
    const fine = [];
    for (let j = 0; j < NF; j++) {
      for (let i = 0; i < NF; i++) {
        const x = fx0 + i * FINE_CELL, z = fz0 + j * FINE_CELL;
        const dx = x - anchor.x, dz = z - anchor.z;
        const q2 = dx * dx + dz * dz;
        if (q2 > FR * FR) continue;
        const h = this.ctx.ground(x, z);
        if (!Number.isFinite(h) || h <= this.waterY) continue;
        const dw = this.dWaterAt(x, z);
        if (dw > this._shoreW) continue;
        if (this.dPlantAt(x, z) <= this._cityR + CLEAR_PLANT) continue;
        const s = this._terrain.biomeAt(x, z);
        if (!s || s.hard > 0.25 || s.kind === 'hardstanding') continue;
        if (s.kind === 'stream') continue;
        if (!(s.altitude >= WASH_LINE)) continue;
        if (s.forest > OPEN_SAND) continue;
        const b = this.beachnessAt(x, z, s);
        if (b < Props.BEACH_T) continue;
        if (!this._offRail(x, z)) continue;
        const vb = shoreOf ? shoreOf(x, z, dw) : 0.5;
        const open = (1 - s.forest) * (0.45 + 0.55 * clamp(vb, 0, 1));
        fine.push({x, z, b, open, i, j, k: j * NF + i, cell: FINE_CELL,
                   alt: s.altitude, slope: s.slope, forest: s.forest,
                   rank: b * open / (1 + Math.sqrt(q2) / CLUSTER_R)});
      }
    }
    this.beachFineCandidates = fine.length;
    /* If the fine pass finds nothing at all — a beach one coarse cell wide, or
     * a gate that only the coarse sample happened to pass — fall back to the
     * coarse candidates rather than building no umbrellas. A thin beach is a
     * real thing; an empty one because of a resampling artefact is not. */
    const pool = fine.length ? fine : cand.map(c => ({...c, cell: m.cell,
      rank: c.b * c.open /
            (1 + Math.hypot(c.x - anchor.x, c.z - anchor.z) / CLUSTER_R)}));
    if (!fine.length) {
      this._warnings.push('the fine placement pass found nothing within ' +
        FR.toFixed(0) + 'm of the anchor; falling back to the ' + MASK_CELL +
        'm lattice, so the set will be spread out');
    }
    /* Index is the tiebreak everywhere, never a bare score compare: two cells
     * with identical scores on a symmetric coast would otherwise swap on a V8
     * sort change and the floor tier's one umbrella would move between builds
     * with nothing in the source having altered. */
    pool.sort((a, b) => (b.rank - a.rank) || (a.k - b.k));
    const out = [], sp2 = spacing * spacing;
    for (const c of pool) {
      let ok = true;
      for (const o of out) {
        const dx = c.x - o.x, dz = c.z - o.z;
        if (dx * dx + dz * dz < sp2) { ok = false; break; }
      }
      if (!ok) continue;
      out.push(c);
      if (out.length >= want) break;
    }
    /* HOW WIDE THE SET ACTUALLY CAME OUT, published so "one beach" is a
     * measurement and not a claim. */
    let spread = 0;
    for (const a2 of out) for (const b2 of out) {
      spread = Math.max(spread, Math.hypot(a2.x - b2.x, a2.z - b2.z));
    }
    this.beachSpreadM = +spread.toFixed(1);
    return out;
  }

  /** One umbrella, one pole, two towels, merged into one prototype. Vertex
   *  colours are the BASE; `instanceColor` tints the set, so a beach comes out
   *  as several different-coloured pitches from one draw call. */
  static _umbrellaProto() {
    const parts = [];
    const canopy = new THREE.ConeGeometry(1.30, 0.52, 10, 1, true);
    canopy.translate(0, 2.06, 0);
    parts.push(paint(canopy, 1.00, 1.00, 1.00));

    const pole = new THREE.CylinderGeometry(0.035, 0.035, 2.15, 6, 1, true);
    pole.translate(0, 1.08, 0);
    parts.push(paint(pole, 0.34, 0.25, 0.17));

    /* Two towels, offset either side, 0.035 m proud of the sand so they cannot
     * z-fight the ground on a shelving strand. */
    for (const [tx, tz, rot] of [[0.85, 0.30, 0.22], [-0.62, -0.75, -0.55]]) {
      const towel = new THREE.PlaneGeometry(0.82, 1.85);
      towel.rotateX(-Math.PI / 2);
      towel.rotateY(rot);
      towel.translate(tx, 0.035, tz);
      parts.push(paint(towel, 0.94, 0.92, 0.88));
    }
    return mergeParts(parts);
  }

  _buildUmbrellas() {
    if (!this._mask || !this.group) return;
    const want = this._count(UMBRELLA_MAX);
    /* The FULL ordering is computed and then sliced. `want` is passed as the
     * greedy cap only so the spacing pass can stop early; the ordering ahead of
     * it is identical at every tier, which is the stable-prefix property. */
    const sites = this._beachSites(UMBRELLA_SPACING, UMBRELLA_MAX).slice(0, want);
    /* ---- THE JITTER IS RESOLVED HERE, ONCE, AND THEN PUBLISHED -------------
     *
     * It used to be applied inside the instancing loop while `umbrellaSites`
     * published the LATTICE CELL, and that was two bugs waiting:
     *
     *   1. every placement gate — `pr-clear.mjs`, `pr-inspect.mjs`, and the
     *      wash-line and rail tests in this file — was checking the cell and
     *      not the umbrella. The jitter is +/- 2.75 m, which is enough to walk
     *      a site off the dry sand or inside the rail clearance AFTER it
     *      passed, and nothing would ever have said so;
     *   2. the drawn shade has to be centred on the object. Publishing the cell
     *      and drawing the jitter puts the shadow three metres off its pole,
     *      which is the single most obvious way to make a drawn shadow read as
     *      a sticker.
     *
     * So: jitter, then RE-TEST, and fall back to the unjittered cell — which
     * has already passed every gate — when the jittered point fails. */
    this.umbrellaSites = sites.map(s => {
      const r1 = h1(s.i, s.j, 11), r2 = h1(s.i, s.j, 29), r3 = h1(s.i, s.j, 47);
      /* the jitter is a fraction of THE PITCH THIS SITE CAME OFF — the fine
       * placement lattice, or the coarse one on the fallback path. Using the
       * mask's 10 m here would throw a 4.5 m site 2.75 m off its own cell. */
      const cell = s.cell || this._mask.cell;
      let x = s.x + (r1 - 0.5) * cell * JITTER;
      let z = s.z + (r2 - 0.5) * cell * JITTER;
      const bio = this._terrain?.biomeAt?.(x, z);
      const ok = bio && bio.hard <= 0.25 && bio.kind !== 'hardstanding' &&
                 bio.altitude >= WASH_LINE && this._offRail(x, z) &&
                 this.dPlantAt(x, z) > this._cityR + CLEAR_PLANT &&
                 this.regionAt(x, z) === 'beach';
      if (!ok) { x = s.x; z = s.z; }
      const y = this.ctx.ground(x, z);
      return {x: +x.toFixed(2), z: +z.toFixed(2),
              y: Number.isFinite(y) ? y : s.alt + this.waterY,
              scale: +(0.88 + r1 * 0.28).toFixed(3),
              yaw: r3 * Math.PI * 2,
              hue: Math.floor(r3 * 5) % 5,
              jittered: ok,
              beachness: +s.b.toFixed(3),
              altitude: +(ok ? bio.altitude : s.alt).toFixed(2),
              slope: +s.slope.toFixed(3)};
    });
    if (!sites.length) return;

    const geo = Props._umbrellaProto();
    const mesh = new THREE.InstancedMesh(geo, this._material(), sites.length);
    mesh.name = 'props:umbrellas';
    mesh.frustumCulled = true;
    mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
    /* EXPLICITLY NOT A CASTER, and that is a decision rather than an omission.
     * An umbrella is 2.06 m of rise and 2.6 m of size against gi's cascade
     * gates of `CSM_MIN_RISE = [2.0, 5.0]` / `CSM_MIN_SIZE = [1.6, 4.0]`, so it
     * can only ever reach the NEAR cascade — which does not cover a beach four
     * hundred metres from the plant. Its shade is drawn instead (see
     * `_buildDecals`), and setting both bits here stops gi's own sweep
     * (gi.js:3428) enrolling it later and laying a second, real shadow on top
     * of the drawn one at close range. One umbrella, one shadow. */
    mesh.castShadow = false;
    mesh.receiveShadow = true;

    const mtx = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const pos = new THREE.Vector3();
    const scl = new THREE.Vector3();
    const col = new THREE.Color();
    /* Deck-chair colours: saturated, because at 900 m through the haze an
     * unsaturated umbrella is a grey dot. The hue comes off the cell hash, so
     * the same pitch is the same colour for ever. */
    const HUES = [0.02, 0.09, 0.55, 0.97, 0.14];
    /* THE PUBLISHED SITE IS THE ONLY SOURCE OF POSITION. The loop reads
     * `umbrellaSites` rather than re-deriving the hash, so the mesh, the gates
     * and the shade cannot drift apart — which they could the moment two copies
     * of the jitter existed. */
    this.umbrellaSites.forEach((s, n) => {
      pos.set(s.x, s.y, s.z);
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), s.yaw);
      scl.set(s.scale, s.scale, s.scale);
      mtx.compose(pos, q, scl);
      mesh.setMatrixAt(n, mtx);
      col.setHSL(HUES[s.hue], 0.62, 0.54);
      mesh.setColorAt(n, col);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();

    this.group.add(mesh);
    this._meshes.push(mesh);
  }

  /* ---- the pier ---------------------------------------------------------- */

  /** Which way is the sea from here?
   *
   *  The distance transform rises inland, so its NEGATIVE gradient points at
   *  the nearest water. Read off the lattice rather than by marching rays,
   *  because the lattice is the same field the beach was classified from and
   *  two representations of one fact is how the "trees on water" bug survived
   *  three probes. Returns a unit vector, or null if the field is flat here. */
  _seaward(x, z) {
    const m = this._mask;
    if (!m) return null;
    const i = clamp(Math.round((x - m.x0) / m.cell), 1, m.N - 2);
    const j = clamp(Math.round((z - m.z0) / m.cell), 1, m.N - 2);
    const k = j * m.N + i;
    const gx = (m.d[k + 1] - m.d[k - 1]) * 0.5;
    const gz = (m.d[k + m.N] - m.d[k - m.N]) * 0.5;
    const len = Math.hypot(gx, gz);
    if (len < 1e-4) return null;
    return {x: -gx / len, z: -gz / len};
  }

  /** One pier, off the beach the umbrellas are on, because that is where a
   *  pier goes and because the two together read as a seaside rather than as
   *  two unrelated objects on the same coast.
   *
   *  It is ONE static merged geometry, not an instanced set: there is one of
   *  it, and a single merged mesh is one draw where a deck plus sixteen piles
   *  as separate objects would be seventeen. */
  _buildPier() {
    /* NEVER JUST ABSENT. Every refusal below records why, because a structure
     * that silently is not there is indistinguishable from one that failed to
     * build — the depth rule above was wrong for two soak layouts and nothing
     * said so; the pier simply was not in the frame. */
    this.pierRefusal = null;
    const a = this.beachAnchor;
    if (!a || !this._mask) { this.pierRefusal = 'no beach anchor'; return; }
    const wy = this.waterY;

    /* ---- WHERE TO LAUNCH IT FROM, and it is a SEARCH now -----------------
     *
     * "It … stops in water too shallow to berth anything."
     *
     * It did, and it was structural rather than unlucky. The old rule took the
     * one bearing seaward of the anchor, walked to the last dry cell, and built
     * whatever it got — so the pier inherited the anchor's accident. Once the
     * umbrellas moved up the beach the anchor moved with them, and measured
     * (`pr-frame.mjs`) the result was a 30 m pier whose head stood in 0.14 m of
     * water: it had run out along a bar, which is exactly the place a pier is
     * never built.
     *
     * A real pier is sited where the water is, so this tries a handful of
     * launch points along the shore either side of the anchor and keeps the one
     * that reaches the deepest water, breaking ties on length and then on the
     * offset itself so the choice is total and cannot depend on sort order.
     * Every candidate still has to pass the same rail and city refusals; they
     * are applied per-candidate rather than to the anchor, so one fouled
     * bearing no longer costs the whole structure.
     *
     * Depth is capped at BERTH_D in the score: past about three metres a
     * pleasure pier does not care, and without the cap the rule would march the
     * pier out to the deepest water on the coast rather than to the nearest
     * water deep enough. */
    const BERTH_D = 3.0;
    const OFFSETS = [0, 14, -14, 28, -28, 42, -42, 56, -56];
    let best = null;
    for (const off of OFFSETS) {
      /* step along the shore, perpendicular to the seaward direction at the
       * anchor, then re-read the seaward direction where we land */
      const d0 = this._seaward(a.x, a.z);
      if (!d0) { this.pierRefusal = 'distance field flat at the anchor'; return; }
      const ax = a.x - d0.z * off, az = a.z + d0.x * off;
      const dir = this._seaward(ax, az);
      if (!dir) continue;
      let sx = ax, sz = az, wet = false;
      for (let s = 0; s < 140; s += 2) {
        const nx = ax + dir.x * s, nz = az + dir.z * s;
        const h = this.ctx.ground(nx, nz);
        if (!Number.isFinite(h)) break;
        if (h <= wy) { wet = true; break; }
        sx = nx; sz = nz;
      }
      if (!wet) continue;
      if (!this._offRail(sx, sz)) continue;
      if (this.dPlantAt(sx, sz) <= this._cityR) continue;
      let len = 0, deepest = 0;
      for (let s = PIER_BAY; s <= PIER_LEN; s += PIER_BAY) {
        const h = this.ctx.ground(sx + dir.x * s, sz + dir.z * s);
        if (!Number.isFinite(h) || h > wy) break;
        len = s; deepest = Math.max(deepest, wy - h);
      }
      if (len < PIER_BAY * 2) continue;
      const headDepth = wy - this.ctx.ground(sx + dir.x * len, sz + dir.z * len);
      const score = Math.min(headDepth, BERTH_D) * 100 + len;
      if (!best || score > best.score ||
          (score === best.score && Math.abs(off) < Math.abs(best.off))) {
        best = {sx, sz, dir, len, headDepth, deepest, score, off};
      }
    }
    if (!best) {
      this.pierRefusal = 'none of the ' + OFFSETS.length + ' launch points within ' +
        Math.max(...OFFSETS.map(Math.abs)) +
        'm of the anchor at (' + a.x.toFixed(0) + ',' + a.z.toFixed(0) + ') ' +
        'reached ' + (PIER_BAY * 2) + 'm of open water clear of the rail and the plant';
      return;
    }
    const sx = best.sx, sz = best.sz, dir = best.dir;

    const parts = [];
    const DECK = wy + PIER_DECK_H;
    /* The deck. One box, its length decided by where the ground comes back UP
     * OUT OF THE WATER — a pier that keeps going over a sandbar and out the far
     * side is a pier to nowhere, and on a lobed coast that happens.
     *
     * The test is the waterline and nothing deeper, and that is a correction:
     * it was a 0.3 m minimum depth, and on a gently shelving strand the FIRST
     * bay is shallower than that, so the whole pier was refused. Measured on
     * the soak's own layout set, 2 of 6 islands got no pier at all for that
     * reason and the refusal was invisible — the structure simply was not
     * there. A pleasure pier over half a metre of water is a pier. */
    const len = best.len;
    /* HOW DEEP IT IS AT THE HEAD, measured and published rather than assumed.
     * "It also terminates in nothing … and it stops in water too shallow to
     * berth anything." The length rule can only stop the pier going too far; it
     * cannot make the seabed drop. So the depth is reported, the boats are
     * moored against it, and if the head really is in a puddle that is now a
     * visible fact instead of a silent one. */
    const headBed = this.ctx.ground(sx + dir.x * len, sz + dir.z * len);
    const headDepth = Number.isFinite(headBed) ? wy - headBed : null;
    this.pier = {x: +sx.toFixed(1), z: +sz.toFixed(1), length: len,
                 dir: {x: +dir.x.toFixed(3), z: +dir.z.toFixed(3)},
                 deckY: +DECK.toFixed(2),
                 headDepth: headDepth === null ? null : +headDepth.toFixed(2),
                 headW: +(PIER_W * PIER_HEAD_W).toFixed(2),
                 /* PUBLISHED, because `_buildDecals` has to widen the shadow
                  * over exactly the length the deck widens over. It read
                  * `PIER_HEAD_L` directly, which is the UNCLAMPED target — so
                  * on any pier shorter than 20 m the widened shadow was longer
                  * than the widened deck it belonged to. */
                 headL: +Math.min(PIER_HEAD_L, len * 0.4).toFixed(2),
                 launchOffset: best.off};
    if (headDepth !== null && headDepth < 1.2) {
      this.propWarnings?.push('pier head stands in ' + headDepth.toFixed(2) +
        'm of water at (' + sx.toFixed(0) + ',' + sz.toFixed(0) + '): it has ' +
        'run out onto a bar and nothing can berth there');
    }

    const ang = Math.atan2(dir.x, dir.z);
    const place = (g, ox, oy, oz) => {
      /* local (across, up, along) -> world */
      const wx = sx + dir.x * oz - dir.z * ox;
      const wz = sz + dir.z * oz + dir.x * ox;
      g.rotateY(ang);
      g.translate(wx, oy, wz);
      return g;
    };
    const deck = new THREE.BoxGeometry(PIER_W, 0.26, len);
    parts.push(paint(place(deck, 0, DECK, len / 2), 0.44, 0.38, 0.31));
    /* A FASCIA UNDER EACH EDGE. Without it the whole structure photographs as a
     * plank lying on the water — a 0.26 m deck seen from anywhere but underneath
     * has no visible thickness and no shadow line, and the first shot of this
     * pier read as a raft for exactly that reason. A dark stringer under the
     * edge is what makes a deck look like it is standing ON something. */
    for (const side of [-1, 1]) {
      const fascia = new THREE.BoxGeometry(0.24, 0.52, len);
      parts.push(paint(place(fascia, side * (PIER_W / 2 - 0.11), DECK - 0.36,
                             len / 2), 0.31, 0.24, 0.18));
      const rail = new THREE.BoxGeometry(0.13, 0.13, len);
      parts.push(paint(place(rail, side * (PIER_W / 2 - 0.14), DECK + 0.98,
                             len / 2), 0.66, 0.57, 0.45));
    }
    /* ---- THE HEAD ---------------------------------------------------------
     *
     * "It also terminates in nothing: no head, no widening, no mooring cleats."
     *
     * All three, and they are the same three things a pleasure pier ends in
     * because they are what it is FOR — you cannot berth against a 3.2 m deck
     * with nothing to make fast to. The widening is what makes the far end read
     * as a destination in plan rather than as a line that stopped, which is the
     * only part of it that survives to 900 m; the bollards are for the street
     * camera. Its shadow widens with it, in `_buildDecals`. */
    const hL = Math.min(PIER_HEAD_L, len * 0.4);
    const hW = PIER_W * PIER_HEAD_W;
    const head = new THREE.BoxGeometry(hW, 0.26, hL);
    parts.push(paint(place(head, 0, DECK, len - hL / 2), 0.44, 0.38, 0.31));
    for (const side of [-1, 1]) {
      const hf = new THREE.BoxGeometry(0.24, 0.52, hL);
      parts.push(paint(place(hf, side * (hW / 2 - 0.11), DECK - 0.36,
                             len - hL / 2), 0.31, 0.24, 0.18));
      /* MOORING BOLLARDS, two a side on the head. Squat, dark, and set in from
       * the edge — they are 0.4 m objects and they exist for the street and
       * yard cameras, not for the far one. */
      for (const along of [len - hL * 0.72, len - hL * 0.22]) {
        const bol = new THREE.CylinderGeometry(0.17, 0.20, 0.62, 6, 1, false);
        parts.push(paint(place(bol, side * (hW / 2 - 0.42), DECK + 0.44, along),
                         0.26, 0.24, 0.22));
      }
    }
    /* the cross fascia at the very end, so the head has a face and not an open
     * edge — one of it, not one per side */
    const endf = new THREE.BoxGeometry(hW, 0.52, 0.24);
    parts.push(paint(place(endf, 0, DECK - 0.36, len - 0.12), 0.31, 0.24, 0.18));
    /* THE PILES. Four under the head instead of two, because it is twice as
     * wide and a widening carried on the same two legs is a table on a stick. */
    for (let s = 0; s <= len + 0.01; s += PIER_BAY) {
      const h = this.ctx.ground(sx + dir.x * s, sz + dir.z * s);
      const bed = Number.isFinite(h) ? h : wy - 4;
      const pileH = Math.max(1.4, DECK - bed + 0.5);
      const onHead = s > len - hL;
      const w = onHead ? hW : PIER_W;
      for (const side of [-1, 1]) {
        const pile = new THREE.CylinderGeometry(0.24, 0.24, pileH, 6, 1, true);
        parts.push(paint(place(pile, side * (w / 2 - 0.28),
                               DECK - 0.3 - pileH / 2, s), 0.30, 0.23, 0.17));
        if (!onHead) {
          const post = new THREE.BoxGeometry(0.13, 1.05, 0.13);
          parts.push(paint(place(post, side * (PIER_W / 2 - 0.14), DECK + 0.52, s),
                           0.66, 0.57, 0.45));
        }
      }
    }
    const geo = mergeParts(parts);
    if (!geo) { this.pierRefusal = 'geometry merge produced nothing'; return; }
    const mesh = new THREE.Mesh(geo, this._material());
    mesh.name = 'props:pier';
    mesh.matrixAutoUpdate = false;
    mesh.updateMatrix();
    /* THE PIER OWNS ITS OWN SHADOW AND IT IS DRAWN, at every tier.
     *
     * Measured (`harness/pr-inspect.mjs`, 2026-08-08) this mesh came back
     * `castShadow: false` at ultra, high and floor and `true` at medium and
     * low — gi's auto-enrolment reaching a different answer on different
     * ladders, which meant the structure was a different structure at
     * different quality settings and the prop draw count wobbled 4/5/5/4.
     * Deciding both bits here takes it out of gi's sweep (gi.js:3428) for
     * good, and the shadow stripe in `_buildDecals` — which is the thing that
     * actually proves the deck is above the water — is then the same at every
     * tier and correct at every camera distance. */
    mesh.castShadow = false;
    mesh.receiveShadow = true;
    this.group.add(mesh);
    this._meshes.push(mesh);
  }

  /* ---- boats ------------------------------------------------------------- */

  /** A small open boat: tapered hull, transom, thwart, and a stub of a cabin.
   *  38 triangles, which at 0.00077 us each is not the reason anything is
   *  slow. */
  static _boatProto() {
    const parts = [];
    const hull = new THREE.BoxGeometry(1.55, 0.86, 4.6, 1, 1, 1);
    /* Taper the bow and round the bilge by moving the box's own corners —
     * cheaper than a lathe and it is a dinghy, not a yacht. */
    const p = hull.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
      const bow = clamp((z + 2.3) / 4.6, 0, 1);          // 0 stern, 1 bow
      const nar = 1 - smoothstep(0.55, 1.0, bow) * 0.78;
      const keel = y < 0 ? 0.62 : 1;
      p.setXYZ(i, x * nar * keel, y + (1 - keel) * 0.1, z);
    }
    hull.computeVertexNormals();
    /* ---- THE HULL IS DARK NOW, and that is the whole fix -----------------
     *
     * "I cannot find them … they contribute noise, not inhabitation."
     *
     * The hull was painted 0.90/0.90/0.87 and tinted to 0.62 lightness, so a
     * boat came out around 0.55 — a mid-value object sitting on bright, lively
     * water at 900 m. It was not too small: a 4.6 m hull is 7.5 px at the
     * operator's camera, half again the size of an umbrella canopy. It was
     * INVISIBLE BECAUSE IT HAD NO CONTRAST AGAINST WHAT IT SAT ON, and a
     * mid-value speck on moving water is indistinguishable from a highlight.
     *
     * From above, a small boat on open water is read as a DARK MARK. That is
     * the honest appearance as well as the legible one — you are looking into
     * an open hull, which is shadow. So the topsides stay pale (they are what
     * gives the mark its edge) and the interior goes dark, and the tint below
     * drops from 0.62 lightness to 0.34. */
    parts.push(paint(hull, 0.86, 0.86, 0.83));
    const inner = new THREE.BoxGeometry(1.12, 0.06, 3.5);
    inner.translate(0, 0.30, -0.1);
    parts.push(paint(inner, 0.16, 0.16, 0.18));

    const cabin = new THREE.BoxGeometry(1.0, 0.55, 1.25);
    cabin.translate(0, 0.65, -0.55);
    parts.push(paint(cabin, 0.52, 0.14, 0.11));

    const thwart = new THREE.BoxGeometry(1.15, 0.08, 0.34);
    thwart.translate(0, 0.34, 0.85);
    parts.push(paint(thwart, 0.55, 0.44, 0.32));
    return mergeParts(parts);
  }

  /** Moored off the beach. Water cells, ordered by nearness to the pier head,
   *  a minimum distance offshore so no hull is half in the sand, and the same
   *  stable-prefix ordering everything else here uses. */
  _boatSites(want) {
    const m = this._mask;
    if (!m) return [];
    const anchor = this.pier
      ? {x: this.pier.x + this.pier.dir.x * this.pier.length,
         z: this.pier.z + this.pier.dir.z * this.pier.length}
      : this.beachAnchor;
    if (!anchor) return [];
    const cand = [];
    for (let j = 0; j < m.N; j++) {
      for (let i = 0; i < m.N; i++) {
        const k = j * m.N + i;
        if (m.land[k]) continue;
        const x = m.x0 + i * m.cell, z = m.z0 + j * m.cell;
        const dx = x - anchor.x, dz = z - anchor.z;
        const dA = Math.sqrt(dx * dx + dz * dz);
        /* TIGHTER THAN IT WAS (150 m). Four boats spread over three hundred
         * metres of water are four unexplained specks; four boats within sight
         * of the pier head they are made fast to are a MOORING, and a mooring
         * is a fact about people. Grouping is most of legibility at this
         * distance — the same argument the umbrellas' cluster rule makes. */
        if (dA > 85) continue;
        /* Far enough offshore that the hull is afloat, near enough that the
         * boat belongs to this beach. Both are lengths of the BOAT, not of the
         * landform: a 4.6 m hull needs about half its length of clearance. */
        let dLand = Infinity;
        for (let jj = -3; jj <= 3; jj++) {
          for (let ii = -3; ii <= 3; ii++) {
            const kk = (j + jj) * m.N + (i + ii);
            if (j + jj < 0 || j + jj >= m.N || i + ii < 0 || i + ii >= m.N) continue;
            if (!m.land[kk]) continue;
            dLand = Math.min(dLand, Math.hypot(ii, jj) * m.cell);
          }
        }
        if (dLand < 6) continue;
        const depth = this.waterY - this.ctx.ground(x, z);
        if (!(depth > 1.2)) continue;
        cand.push({x, z, k, dA, depth});
      }
    }
    cand.sort((a, b) => (a.dA - b.dA) || (a.k - b.k));
    const out = [];
    for (const c of cand) {
      let ok = true;
      for (const o of out) {
        if (Math.hypot(c.x - o.x, c.z - o.z) < 11) { ok = false; break; }
      }
      if (ok) out.push(c);
      if (out.length >= want) break;
    }
    return out;
  }

  _buildBoats() {
    if (!this._mask || !this.group) return;
    const want = this._count(BOAT_MAX);
    const sites = this._boatSites(BOAT_MAX).slice(0, want);
    this.boatSites = sites.map(s => ({x: +s.x.toFixed(1), z: +s.z.toFixed(1),
                                      depth: +s.depth.toFixed(1)}));
    if (!sites.length) return;
    const geo = Props._boatProto();
    const mesh = new THREE.InstancedMesh(geo, this._material(), sites.length);
    mesh.name = 'props:boats';
    /* A moored boat moves. `update()` rewrites these matrices every frame — six
     * of them at ultra, one at the floor — so the buffer is dynamic. It NEVER
     * rewrites the count. */
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.castShadow = false;
    mesh.receiveShadow = true;
    const col = new THREE.Color();
    this._boats = sites.map((s, n) => {
      const r1 = h1(Math.round(s.x), Math.round(s.z), 7);
      const r2 = h1(Math.round(s.x), Math.round(s.z), 23);
      /* 0.34 lightness, down from 0.62: see `_boatProto`. */
      col.setHSL([0.58, 0.11, 0.02, 0.34][n % 4], 0.30, 0.34);
      mesh.setColorAt(n, col);
      return {x: s.x, z: s.z, yaw: r1 * Math.PI * 2, phase: r2 * Math.PI * 2,
              scale: 0.9 + r2 * 0.3};
    });
    this._boatMesh = mesh;
    this._writeBoats(0);
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
    this.group.add(mesh);
    this._meshes.push(mesh);
  }

  _writeBoats(t) {
    const mesh = this._boatMesh;
    if (!mesh) return;
    const mtx = this._m4 || (this._m4 = new THREE.Matrix4());
    const q = this._q || (this._q = new THREE.Quaternion());
    const e = this._e || (this._e = new THREE.Euler());
    const pos = this._v3 || (this._v3 = new THREE.Vector3());
    const scl = this._v3b || (this._v3b = new THREE.Vector3());
    this._boats.forEach((b, n) => {
      const ph = t * 0.9 + b.phase;
      /* A moored boat rides the swell: a little heave, a little roll, a little
       * yaw as she swings on her warp. All three from one phase, so she does
       * not look like three independent animations bolted together. */
      e.set(Math.sin(ph * 0.83) * 0.045, b.yaw + Math.sin(ph * 0.31) * 0.10,
            Math.sin(ph) * 0.075);
      q.setFromEuler(e);
      /* Sat DOWN in the water, not on it. A hull whose whole 0.8 m of topside
       * is above the surface reads as a raft; a third of it under does not. */
      pos.set(b.x, this.waterY - 0.34 + Math.sin(ph * 0.71) * 0.09, b.z);
      scl.set(b.scale, b.scale, b.scale);
      mtx.compose(pos, q, scl);
      mesh.setMatrixAt(n, mtx);
    });
    mesh.instanceMatrix.needsUpdate = true;
  }

  /* ---- the gulls, and why there are none ---------------------------------
   *
   * CUT. Twenty-four instanced gulls, six triangles each, on stable orbits over
   * the water off the pier head, with a per-frame matrix rewrite. They are
   * gone, and this note is here so the next round does not put them back
   * without answering the arithmetic.
   *
   * The charge was "not legible at all — indistinguishable from sensor noise",
   * with a stated remedy: "bird flocks in an aerial frame only work if they are
   * between camera and ground and read as a moving dark speckle WITH SHADOWS ON
   * THE SAND BELOW — otherwise cut them."
   *
   * The remedy is buildable — the flock could be moved in over the strand and
   * given shadow decals from the same drawn-shade machinery the umbrellas now
   * use, at one more draw call. It was not built, because of the angular size:
   *
   *   the operator's camera is /floor cam=far — fov 42 deg, distance 900 m
   *   1080 px over 42 deg                    = 0.679 mrad per pixel
   *   a 1.3 m gull at 900 m                  = 1.44 mrad = 2.1 px
   *
   * Two pixels. That is the same 2-3 px speck the umbrellas were condemned for
   * being, and adding a second 2 px speck beside it to prove the first one is a
   * bird does not make a flock — it makes twice as much confetti. The only way
   * to get a legible gull at 900 m is to draw a four-metre gull, and a
   * four-metre gull is a lie about scale, which is a worse defect than an
   * absent bird. For comparison the umbrella survives the same arithmetic
   * because it is not judged on its own 2.6 m canopy (4 px) but on the 6.4 m
   * ellipse of shade it now throws (10 px), and the shade is the thing the eye
   * actually finds.
   *
   * The operator asked for birds and this round is not delivering them. That is
   * a real cost and it is recorded as one. They become worth building the
   * moment there is a camera that watches the strand from under about 300 m, at
   * which point a gull is 6 px and its shadow is a separate 6 px, and the flock
   * is the cheapest inhabitation in the file. Recovered here: one draw call,
   * 144 triangles, and twenty-four `compose` calls a frame.
   */

  /* ---- the approach ------------------------------------------------------
   *
   * "A beach with fifteen umbrellas and no way to walk to it is the clearest
   *  possible statement that the props were scattered by a placement rule
   *  rather than reasoned about. A DESIRE LINE IS NEARLY FREE AND IT WOULD DO
   *  MORE THAN ALL THE PROPS COMBINED."
   *
   * That is the whole brief for this section and it is right, because a path is
   * the only thing in this file that is not an object. Every other prop asserts
   * that someone put a thing here. The path is a record that someone WALKED
   * here, repeatedly, and chose this line over every other line — which is
   * inhabitation in the sense the critic means it, and no umbrella can be.
   *
   * IT IS NOT A ROAD AND IT MUST NOT BECOME ONE. Roads belong to the `city`
   * round and to the region layer this file publishes for it. This is a foot
   * track: it walks INLAND from the beach toward the nearest instrument, which
   * is where the only people in this world are, and it STOPS at the plant's
   * apron or at the railway fence rather than crossing either. A path that
   * stops at a railway fence is what a real one does.
   */

  /** The route, as a polyline of ground samples. Deterministic: it is a walk
   *  down a fixed bearing from a hashed jitter, with no RNG anywhere in it, so
   *  the same landform gives the same track for ever. */
  _pathRoute() {
    const a = this.beachAnchor;
    if (!a || !this._mask) return null;
    /* WHERE IT GOES. The nearest instrument, because that is the only place in
     * this world anybody is coming from. Falling back to straight inland when
     * there are no instruments at all keeps the track buildable in an empty
     * room rather than making it another thing that is silently absent. */
    let target = null, best = Infinity;
    for (const s of this._stations || []) {
      const q = Math.hypot(s.x - a.x, s.z - a.z);
      if (q < best) { best = q; target = s; }
    }
    const inland = this._seaward(a.x, a.z);
    if (!target && !inland) return null;
    let bx, bz;
    if (target) {
      bx = target.x - a.x; bz = target.z - a.z;
      const L = Math.hypot(bx, bz) || 1; bx /= L; bz /= L;
    } else {
      bx = -inland.x; bz = -inland.z;
    }

    /* THE SEAWARD FOOT. The track does not start at the umbrellas — it starts
     * down where it dies out on the open wet sand, because that is where a real
     * one stops being a track: people fan out the moment there is nothing to
     * walk around. Walk seaward from the anchor until we are under the wash
     * line, then turn round and build from there. */
    const sea = this._seaward(a.x, a.z);
    let fx = a.x, fz = a.z;
    if (sea) {
      for (let s = PATH_STEP; s <= 60; s += PATH_STEP) {
        const nx = a.x + sea.x * s, nz = a.z + sea.z * s;
        const h = this.ctx.ground(nx, nz);
        if (!Number.isFinite(h) || h <= this.waterY) break;
        fx = nx; fz = nz;
        if (h - this.waterY < WASH_LINE * 0.45) break;
      }
    }

    const d0 = Math.hypot(a.x - fx, a.z - fz);
    const y0 = this.ctx.ground(fx, fz);
    const pts = [{x: fx, z: fz, y: y0, along: 0, dAnchor: d0}];

    /* ---- IT CONTOURS. IT DOES NOT WALK THE BEARING ------------------------
     *
     * The first version stepped straight down the bearing and gave up the
     * moment one 7 m step rose more than 4.3 m. That is not how a track gets
     * worn and it does not survive contact with a real island: measured
     * (`pr-clear.mjs`, 6 layouts) it produced a 21 m stub on layout 3 and
     * stopped at "the ground got too steep to walk" on three of the six, which
     * is a path that is not there — the exact defect this whole section exists
     * to fix, reintroduced one level down.
     *
     * A desire line is the CHEAPEST line, not the shortest: people contour
     * round a bank rather than climb it, and the wear follows the choice. So
     * each step is a small search over five bearings and the cheapest wins,
     * where cost is (climb) + (how far off the direct line it takes you). One
     * step of lookahead is enough — this is a footpath, not a railway
     * alignment, and rail.js is where route optimisation lives. */
    const FAN = [0, 0.30, -0.30, 0.62, -0.62, 0.95, -0.95];
    let cx = fx, cz = fz, cy = Number.isFinite(y0) ? y0 : this.waterY;
    let along = 0;
    let stop = 'reached the length limit without finding anything';
    const jit = h1(Math.round(a.x), Math.round(a.z), 5);
    while (along < PATH_MAX_LEN) {
      /* re-aim every step, so contouring round something does not leave the
       * track pointing at nothing once it is past */
      let ax = bx, az = bz;
      if (target) {
        const tx = target.x - cx, tz = target.z - cz;
        const L = Math.hypot(tx, tz);
        if (L > 1) { ax = tx / L; az = tz / L; }
      }
      let bestP = null, bestC = Infinity;
      let blocked = 'the ground got too steep to walk';
      for (const off of FAN) {
        const co = Math.cos(off), si = Math.sin(off);
        const dx = ax * co - az * si, dz = az * co + ax * si;
        const px = cx + dx * PATH_STEP, pz = cz + dz * PATH_STEP;
        const h = this.ctx.ground(px, pz);
        if (!Number.isFinite(h)) { blocked = 'ran off the heightfield'; continue; }
        if (h <= this.waterY) { blocked = 'met water: this beach is on a spit'; continue; }
        /* THE TWO THINGS IT MAY NOT CROSS, and it stops rather than detouring
         * round them: a footpath that walks up to a fence and ends is honest;
         * one that threads a railway is a level crossing, and a level crossing
         * is a `city` object belonging to the roads round. */
        if (!this._offRail(px, pz)) { blocked = 'stopped at the railway'; continue; }
        if (this.dPlantAt(px, pz) <= this._cityR) {
          blocked = 'reached the plant apron'; continue;
        }
        const climb = Math.abs(h - cy);
        /* nobody wears a line up a 32 degree face, whichever bearing it is on */
        if (climb > PATH_STEP * 0.62) continue;
        /* A MEANDER, as a thumb on the scale rather than as an offset applied
         * afterwards: on flat ground every bearing costs the same and this is
         * what breaks the tie, so the track wanders where nothing forces it and
         * goes straight where something does — which is the right way round. */
        const wobble = Math.sin((along + jit * 300) / 41) * 0.55;
        const cost = climb * 1.7 + Math.abs(off) * 2.2 + off * wobble * 2.6;
        if (cost < bestC) { bestC = cost; bestP = {x: px, z: pz, y: h}; }
      }
      if (!bestP) { stop = blocked; break; }
      along += PATH_STEP;
      cx = bestP.x; cz = bestP.z; cy = bestP.y;
      pts.push({x: cx, z: cz, y: cy, along, dAnchor: Math.abs(along - d0)});
      if (target && Math.hypot(cx - target.x, cz - target.z) < this._cityR + CLEAR_PLANT) {
        stop = 'reached the plant'; break;
      }
    }
    if (pts.length < 6) return null;
    return {pts, stop, target: target ? {x: target.x, z: target.z} : null,
            length: pts[pts.length - 1].along};
  }

  /** Build the route and record it. The GEOMETRY is not built here — it is a
   *  decal and it is merged with the rest of them in `_buildDecals`, so the
   *  whole evidence layer stays one draw call. */
  _buildPath() {
    this.pathRefusal = null;
    this._path = null;
    const r = this._pathRoute();
    if (!r) {
      /* NEVER JUST ABSENT — the same rule the pier carries. An approach that
       * silently is not there is indistinguishable from one that was never
       * asked for, and "no way to walk to it" was the single worst thing in the
       * last set. */
      this.pathRefusal = 'no beach anchor, or the route died inside six steps';
      this.propWarnings?.push('NO APPROACH PATH: ' + this.pathRefusal);
      return;
    }
    this._path = r;
    this.path = {
      length: +r.length.toFixed(0), samples: r.pts.length, stoppedBecause: r.stop,
      from: {x: +r.pts[0].x.toFixed(1), z: +r.pts[0].z.toFixed(1)},
      to: {x: +r.pts[r.pts.length - 1].x.toFixed(1),
           z: +r.pts[r.pts.length - 1].z.toFixed(1)},
    };
  }

  /* ---- the drawn shade ---------------------------------------------------
   *
   * "An umbrella's entire job in an aerial frame is to cast a disc of shade;
   *  the disc is what makes it an umbrella rather than a dot. Without it they
   *  are litter."
   *
   * "A pier is read from above almost entirely by THE SHADOW STRIPE IT LAYS ON
   *  THE WATER — you have drawn the deck and omitted the thing that proves it
   *  is above the surface."
   *
   * WHY IT IS DRAWN AND NOT CAST. Measured (`harness/pr-tide.mjs`, 2026-08-08),
   * every mesh in this file came back `castShadow: false` at ultra — and the
   * pier came back `true` at medium and low, which is worse, because it means
   * the structure looked like a different structure at different tiers. That is
   * gi.js deciding, correctly, on its own rules: the cascades gate on
   * `CSM_MIN_RISE = [2.0, 5.0]` and `CSM_MIN_SIZE = [1.6, 4.0]`, an umbrella is
   * 2.06 m of rise and 2.6 m of size so it can only ever reach the NEAR
   * cascade, and the near cascade does not extend to a beach 400 m from the
   * camera. There is no honest way to get a real cast shadow onto that sand
   * from here, and this file does not own gi.js.
   *
   * So the shade is drawn, and drawn is not a cheat as long as it is drawn from
   * the real sun: `_sun()` reads the same `gi.sunDirection` the shadow map is
   * built from, so every disc in the frame points the same way as every real
   * shadow beside it, changes with the hour, and disappears when the sun does.
   * What is being faked is the RENDERING of the shadow, not its geometry.
   *
   * The whole layer — path, discs, pier stripe, hull shadows — is one merged
   * geometry and ONE DRAW CALL.
   */

  _buildDecals() {
    if (!this.group) return;
    this.shade = null;
    const sun = this._sun();
    /* RECORDED BEFORE ANYTHING CAN RETURN EARLY. `_resunDecals` compares
     * against it to decide whether the shade is stale, and treats "never built"
     * as "rebuild now" — so leaving it unset on the no-props path (no beach, no
     * pier, no route) means a rebuild attempt EVERY FRAME, for ever, on exactly
     * the islands that have nothing to draw. */
    this._builtSun = sun ? {az: sun.azDeg, el: sun.elev} : {az: 1e9, el: 1e9};
    const parts = [];

    /* THE PATH FIRST, and it does not depend on the sun: wear is not a shadow.
     * It is drawn at night, in fog, and in December. */
    if (this._path) {
      const pts = this._path.pts;
      const st = [];
      for (const p of pts) {
        /* THE FAN. A track is one person wide where it is squeezed between
         * things and spreads where it is not, so the half-width is a function
         * of how far up the beach we are — widest at the seaward foot, down to
         * a single file inland. The taper length is the cluster radius, i.e.
         * the beach's own footprint, not an independent number. */
        const t = clamp(p.along / CLUSTER_R, 0, 1);
        const hw = (PATH_W_BEACH + (PATH_W_INLAND - PATH_W_BEACH) *
                    smoothstep(0, 1, t)) * 0.5;
        /* and it fades out at the very bottom, where it has stopped being a
         * path at all */
        const k = 0.34 + 0.66 * smoothstep(0, 26, p.along);
        const y = Number.isFinite(p.y) ? p.y : this.ctx.ground(p.x, p.z);
        if (!Number.isFinite(y)) continue;   // see the bounding-sphere note below
        st.push({x: p.x, y: y + PATH_LIFT, z: p.z, hw, mul: WEAR_MUL, k});
      }
      const g = Props._shadeRibbon(st);
      if (g) parts.push(g);

      /* A BRAID TO THE PIER. Desire lines split where there are two things
       * worth walking to, and a pier with no way onto it is the same defect the
       * beach had. One spur, from wherever the spine passes closest to the pier
       * root, to the root. */
      if (this.pier) {
        let bestI = -1, bestD = Infinity;
        for (let i = 0; i < pts.length; i++) {
          const q = Math.hypot(pts[i].x - this.pier.x, pts[i].z - this.pier.z);
          if (q < bestD) { bestD = q; bestI = i; }
        }
        if (bestI >= 0 && bestD > 9 && bestD < 130) {
          const a = pts[bestI];
          const n = Math.max(3, Math.round(bestD / PATH_STEP));
          const sp = [];
          for (let i = 0; i <= n; i++) {
            const u = i / n;
            const x = a.x + (this.pier.x - a.x) * u;
            const z = a.z + (this.pier.z - a.z) * u;
            const y = this.ctx.ground(x, z);
            if (!Number.isFinite(y) || y <= this.waterY) break;
            sp.push({x, y: y + PATH_LIFT, z, hw: 1.15,
                     mul: WEAR_MUL, k: 0.55 * (1 - u * 0.25)});
          }
          const gs = Props._shadeRibbon(sp);
          if (gs) parts.push(gs);
        }
      }
    }

    /* ---- and now everything that depends on where the sun is -------------- */
    if (sun) {
      /* THE UMBRELLA DISCS. A horizontal disc of radius r at height H throws an
       * ellipse: semi-axis r ACROSS the shadow, r/sin(elev) ALONG it, centred
       * H/tan(elev) downsun of the pole. At the measured 23.8 degrees a 1.30 m
       * canopy 2.06 m up gives a 6.4 m by 2.6 m ellipse 4.7 m from its own
       * pole — which is 10 px at the operator's camera against the umbrella's
       * own 4, and is the entire reason the umbrella reads as an umbrella. */
      const gnd = (x, z) => this.ctx.ground(x, z);
      for (const s of this.umbrellaSites || []) {
        const sc = s.scale || 1;
        const cx = s.x + sun.sx * (2.06 * sc) * sun.throwPerM;
        const cz = s.z + sun.sz * (2.06 * sc) * sun.throwPerM;
        const g = Props._shadeDisc(
          gnd, cx, cz,
          1.30 * sc * sun.stretch, 1.30 * sc,
          sun.sx, sun.sz, SHADE_MUL, sun.strength, PATH_LIFT * 0.7);
        if (g) parts.push(g);
      }

      /* THE PIER'S STRIPE. A horizontal deck's shadow is its own plan shape,
       * translated downsun by deck-height / tan(elev) — 4.8 m here. It is drawn
       * along the WHOLE pier including the shore end, which is deliberate: the
       * part of it that falls on sand IS the "darker wet band where it meets
       * the sand" the critic asked for, and one ribbon gives both. The stations
       * drape onto whichever is higher, the seabed or the water. */
      if (this.pier) {
        const p = this.pier, off = PIER_DECK_H * sun.throwPerM;
        const st = [];
        const NS = Math.max(4, Math.round(p.length / 5));
        for (let i = 0; i <= NS; i++) {
          const s = (p.length * i) / NS;
          const x = p.x + p.dir.x * s + sun.sx * off;
          const z = p.z + p.dir.z * s + sun.sz * off;
          const g = this.ctx.ground(x, z);
          /* NEVER let a bad read reach the buffer — see the bounding-sphere
           * check at the bottom of this method. The waterline is always a
           * finite, correct-enough answer for a stripe that lies on the sea. */
          const y = Math.max(Number.isFinite(g) ? g : this.waterY, this.waterY);
          /* the head is wider, so its shadow is */
          const head = s > p.length - (p.headL ?? PIER_HEAD_L);
          st.push({x, y: y + PATH_LIFT * 0.5, z,
                   hw: PIER_W * (head ? PIER_HEAD_W : 1) * 0.5,
                   mul: SHADE_MUL, k: sun.strength * 0.92});
        }
        const g = Props._shadeRibbon(st);
        if (g) parts.push(g);
      }

      /* THE HULLS. A boat on bright water from 900 m is found by being darker
       * than the water, not by its shape, and a patch of shadow under it is
       * what says "this object is on the surface" rather than "this is a
       * texture". Weaker than the pier's, because a hull sits IN the water and
       * most of its shadow is under it. */
      const sea = () => this.waterY;      // the sea is flat: nothing to drape on
      for (const s of this.boatSites || []) {
        const g = Props._shadeDisc(
          sea, s.x + sun.sx * 0.9, s.z + sun.sz * 0.9,
          3.1, 1.5, sun.sx, sun.sz, HULL_SHADE_MUL, sun.strength, 0.05);
        if (g) parts.push(g);
      }
    }

    if (!parts.length) return;
    const geo = mergeParts(parts);
    if (!geo) return;
    /* ONE NaN VERTEX HIDES THE WHOLE LAYER, SILENTLY.
     *
     * `computeBoundingSphere` propagates a single non-finite position into a NaN
     * radius, and three then fails the frustum test for the entire merged mesh —
     * so a bad `ground()` read anywhere on a 300 m path would delete the shade,
     * the stripe and the track together, with no error and nothing in the
     * console. This session lost an hour to a DIFFERENT invisible-decal cause
     * and this one was still live behind it; a check is two lines. */
    const bs = geo.boundingSphere;
    if (!bs || !Number.isFinite(bs.radius) || !Number.isFinite(bs.center.x)) {
      geo.dispose();
      this.propWarnings?.push('the decal layer merged to a non-finite bounding ' +
        'sphere — a ground() read returned NaN somewhere in the path or the ' +
        'pier stripe, and the whole evidence layer would have been invisible');
      return;
    }
    const mesh = new THREE.Mesh(geo, this._decalMaterial());
    mesh.name = 'props:decals';
    mesh.matrixAutoUpdate = false;
    mesh.updateMatrix();
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    /* gi.js sweeps the scene and decides `castShadow` for anything that has not
     * decided for itself (gi.js:3428). Both bits are set explicitly above so it
     * leaves this alone, and `lemKeepShadow` is NOT set — there is nothing here
     * to keep. */
    mesh.renderOrder = -1;
    this.group.add(mesh);
    this._meshes.push(mesh);
    this._decalMesh = mesh;
    this.shade = sun ? {elevDeg: +sun.elev.toFixed(2), azDeg: +sun.azDeg.toFixed(2),
                        throwPerM: +sun.throwPerM.toFixed(2),
                        strength: +sun.strength.toFixed(2)} : null;
  }

  /** THE SUN MOVED, so the shade is wrong. Rebuild the decal mesh and NOTHING
   *  else — this is the one rebuild trigger in the file outside `onPlan` and
   *  `onQuality`, it is keyed on the sun's own angle, and it never touches a
   *  count. It is emphatically not a camera read: the same hour gives the same
   *  shade from every distance and every tier.
   *
   *  The hysteresis is what makes it affordable. Four degrees of azimuth is
   *  about sixteen minutes of a summer morning, so a full dawn-to-dusk sweep
   *  re-merges this geometry on the order of forty times, not once a frame. */
  _resunDecals() {
    const had = this._builtSun;
    if (!had) return;                       // nothing built yet; not our job
    const sun = this._sun();
    const hadSun = had.az < 1e8;
    if (!sun && !hadSun) return;            // dark before, dark now
    if (sun && hadSun) {
      let dAz = Math.abs(sun.azDeg - had.az) % 360;
      if (dAz > 180) dAz = 360 - dAz;
      if (dAz < SHADE_REBUILD_AZ && Math.abs(sun.elev - had.el) < SHADE_REBUILD_EL) return;
    }
    if (this._decalMesh) {
      this.group?.remove(this._decalMesh);
      this._decalMesh.geometry?.dispose?.();
      const i = this._meshes.indexOf(this._decalMesh);
      if (i >= 0) this._meshes.splice(i, 1);
      this._decalMesh = null;
    }
    this._buildDecals();
  }

  /* ======================================================================== *
   *  LIFECYCLE
   * ======================================================================== */

  /** WHAT MAY NOT GO IN HERE, and it is non-negotiable #2: anything that reads
   *  the camera, changes an instance count, or toggles visibility. Density is a
   *  build-time decision. This method moves matrices that already exist and
   *  nothing else — at ultra that is four boats, four `compose` calls a frame.
   *
   *  `_resunDecals` is the one thing here that can rebuild geometry, and it is
   *  allowed for exactly the reason the camera reads are not: IT IS A FUNCTION
   *  OF THE SUN. Same hour, same shade, at every tier and every distance. It is
   *  hysteretic (see `SHADE_REBUILD_AZ`) so a static frame never pays it at
   *  all, and it changes no count — the same umbrellas throw the same number of
   *  discs in a different direction. */
  update(_dt, time) {
    const t = Number.isFinite(time) ? time : 0;
    if (this._boatMesh) this._writeBoats(t);
    if (this._mask && this.group) this._resunDecals();
  }

  /** Geometry is per-mesh and goes. THE MATERIAL IS SHARED AND STAYS: disposing
   *  it here would leave the next rebuild — and a rebuild happens on every
   *  quality step — drawing against a disposed program. It is released in
   *  `dispose()`, which is the only place that owns it. */
  _clearScene() {
    for (const m of this._meshes) {
      this.group?.remove(m);
      m.geometry?.dispose?.();
    }
    this._meshes = [];
    this._boatMesh = null; this._decalMesh = null;
    this._boats = [];
    this._builtSun = null;
    this.pier = null; this.boatSites = []; this.umbrellaSites = [];
    this.path = null; this._path = null; this.shade = null;
    /* Kept at 0 rather than deleted: `harness/pr-clear.mjs` reports it, and a
     * counter that goes `undefined` reads as "the probe broke" where a 0 reads
     * as "there are none", which is the true statement. */
    this.birdCount = 0;
  }

  dispose() {
    this._clearScene();
    this._mat?.dispose?.();
    this._mat = null;
    this._decalMat?.dispose?.();
    this._decalMat = null;
    if (this.group) this.ctx.scene.remove(this.group);
    this.group = null;
    if (this.ctx && this.ctx.regions === this.regions) this.ctx.regions = null;
    this.regions = null;
    this._mask = null;
  }
}
