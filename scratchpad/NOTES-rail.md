# rail.js — what was built, what it costs, what is still weak

`LEM Web Server/static/world/rail.js` — `export class Rail` (+ default export).
Imports only `three`. Every texture is generated on the client from
`world/textures.js`. `build()` cannot throw: the whole layout runs inside
`_safeRebuild`, and a failure logs and leaves an empty group.

## What it is

Splines first. `Track` turns a polyline of control points into a real alignment
— straights joined by clothoid-easement corners integrated from a curvature
schedule — resamples it at a uniform arc-length step, grades it onto the
terrain to a ruling gradient and cants it by curvature. Ballast, sleepers,
rails, turnouts, signals, trackside kit and the routes trains sample are all
read off that one frame array, so nothing is positioned twice and nothing can
drift out of register.

## 2026-08-07 — "the railways need to make sense in any layout"

Ryan's report was about trains running into each other and about a map with a
lip in it, but the third clause — *"smooth uniform procedural generation that
logics its way through any layout"* — was rail's, and rail did not.

**The network was built for two rows.** `_rebuild` laid at most TWO through
roads and hung every row of benches beyond the second on the outer one. On the
lab's real floor (two rows) that was invisible. The soak drives ten layouts, and
six of them have five, six or seven rows — a single long FILE of instruments is
seven rows. Five of those rows were being served by a running line ninety to
four hundred and fifty metres away in z, and `_siding` dutifully drew a control
polyline from a point on that line, out to the bench and back: a
quarter-kilometre S-bend across open ground with a turnout at each end. Every
station was "reachable". None of it was a railway.

It is now a **trunk with branches**, which is how railways have always solved
this:

- one **trunk** down the west side of the site, turning east into the terminal's
  platform road, with a headshunt at its south end;
- one **branch** per row, east–west past the docks, curving north at the west
  end and joining the trunk at a turnout. Rows are ordered by distance to the
  terminal so the nearest row joins nearest to it, and **no branch can cross
  another** — that is a property of the ordering, not a check;
- one **loading road** per row, off its own branch, with a stand on it for every
  bench in that row;
- the **balloon loop** at the terminal, a **second platform road** beside it so
  the throat can hold two arrivals, and the **reception road** in the loop's
  belly for a cut of tanks to stand clear.

Rows do not change the shape of any of it. A rank, a file, a scatter, two
machines on one bay and the lab's real floor are one network with a different
number of branches on it.

**Every bench now reaches the loop.** Before, only rows 0 and 1 could turn and
everything else had to set back out of a dead end. Measured across all ten soak
layouts: 7/7 sidings, 7/7 working cycles, 7/7 `turned: true`, in every one.

### The loading road serves a ROW, not a bench

The per-bench siding was a lie a screenshot could not see. The dock line is 8.4m
off the running line (buildings.js's offset, set by the gantry footings), the
turnouts sat 52m either side of the bench and the road came parallel 38m either
side — so the alignment was asked to move 8.4m sideways in 14m: **1 in 1.7**,
where a slow yard turnout is 1 in 6. `_solve`'s fit pass shrank the corner until
it fitted, all the way down to a 30m radius, and drew a smooth curve no track
could be laid on. It also did not fit: benches are 90m apart and each of those
loops claimed 104m, so in a rank every siding overlapped its neighbours'
turnouts.

Both faults are the same arithmetic — a loop needs ~50m of transition at each
end plus somewhere to stand, and 90m of bench spacing has not got 150m in it. So
a row gets one road past all of it, with 56m transitions (1 in 6.7, gentler
after the easement) and a stand per bench. `Track` now also *reports* when a
corner bottoms out (`minRadius`, `tight`), so this class of fault cannot be
silent again: `_branch`, `_loadingLoop` and `_terminalLoop` refuse rather than
lay a hairpin.

### Gradients

`Track.build` now grades to a **ruling gradient** as well as a smooth one. The
profile is raised to the smallest g-Lipschitz function above it (two sweeps of a
max-plus cone — exact, and it only ever raises, so the existing "never cut below
the ground it crosses" invariant survives). Two caps, both learned the hard way:

- **the fill is capped at 4m of bank.** An unbounded cone is global: one hill
  anywhere on a 1.7km branch raises every metre of it, and the first version put
  a line thirty-eight metres in the air. It showed in the harness as a 38m
  *vertical* chord where a route stepped off the short loading road (not lifted)
  on to the long branch (lifted).
- **the formation is capped at 3.6m above the ground it covers.** Nothing before
  this stopped it rising, and where terrain drops a bluff beside the line the
  smoothed profile carried the track out over the fall with the ballast's pinned
  outer edge hanging down the cliff behind it. The ballast batter now also stops
  at 4m rather than draping to wherever the ground is.

Where the ground is workable the line holds exactly 1 in 40 (measured: branches
2–6 of the file layout come out at 0.025 on ground at 0.12–0.19). Where it is
not, it follows the ground — see "still weak".

### Blocks, and why they are here

Every working now runs over the same trunk, which is what makes the network a
network and also the moment it acquires a way to have two trains in one place.
Arc length along a train's own route cannot express that: two workings out of
different benches stand on the same forty metres of platform road at completely
different `s`. So:

- `cycle(uid).segments` — `[{track, from, to, s0, s1}]`, which physical track
  each stretch of the route is laid on;
- `rail.blocksFor(cycle, headS, tailS)` → block ids;
- `rail.clear(id, blocks)` / `reserve(id, blocks)` / `unreserve(id)` — path
  reservation, atomic, refuses anything held by another train.

Blocks are cut where a real railway cuts them: either side of every turnout,
between consecutive stands on a loading road, and every ~180m of plain line.
That is OpenTTD's path signalling in four methods. **Nothing in trains.js calls
it yet** — see `scratchpad/REQUESTS.md`, which also explains how to turn on the
soak's junction check (rail's routes have always had `getPointAt`/`length`;
trains.js's `sampleRoute` is where they are lost).

### Smaller, but they were wrong

- Buffer stops were planted at both ends of every line. A branch's alignment
  ends because it has run on to the trunk — a buffer stop at a junction is
  possibly the most obviously wrong thing a railway can be drawn with. Stops now
  go only where a track is genuinely trimmed at neither end.
- Signals, cable troughing, mileposts and fencing ran to `line.length`, which
  for a branch is out past its junction and down the middle of the trunk. All of
  it is now bounded by `renderFrom`/`renderTo`.
- Routes were stitched from the control points a road was drawn from rather than
  from its trimmed tips, which laid a straight chord across the ~25m difference —
  a train cutting the corner over grass, on every departure. Arc lengths on the
  branch are now quoted from the tips.
- The permanent way **thins with the size of the site** (`thin` in
  `_buildMeshes`): a 14-bay scatter lays 11km of railway where the lab lays 3.6,
  and track cost is per metre while the triangle budget is not.

## Measured

Whole scene `sky,gi,terrain,buildings,rail,trains,vegetation,weather` at
`cam=yard`, ultra, 1920×1080: **180 draws · 1.26M triangles**, 1.81 MB
transferred, first frame 2.4s, no console errors. (fps is meaningless on an M5
Max.) Budget is 450 / 2.5M.

`terrain,buildings,rail` across all ten soak layouts: 37–128 draws, 0.74–0.95M
triangles, 3.2km–12.2km of railway. The largest layout is the *cheapest* in
triangles because `thin` bites.

## Harnesses added

- `harness/railcheck.mjs` — the topology gate. Per layout: branches, sidings,
  cycles, how many can turn, whether the balloon / terminal loop / reception
  road were built, total railway laid, draws and triangles; and it *asserts*
  every station has a route and a cycle, every route is continuous (no chord
  over 30m), every alignment holds its minimum radius, the block map covers
  every cycle, and a held block is never granted twice. Grade faults are only
  raised when the track is steeper than the ground under it — grading cannot
  invent a shelf that is not there, and the measurement says which it is.
- `harness/_grade.mjs` — kept: it reports rail gradient *and* ground gradient
  per track, which is what attributed the steep-line faults to terrain rather
  than to grading. `harness/_chord.mjs` dumps the worst chord in every route
  with its two endpoints, which is how the 38m vertical one was found.

## Screenshots

- `shots/rail-yard-v2.png` — the mandated run, whole scene, `cam=yard`.
- `shots/rail-plan-v2.png` — plan over the terminal: platform road, balloon,
  reception road in its belly, second platform road, and two branches with their
  loading roads.
- `shots/rail-macro-v3.png` — a tank train at a stand on the loading road with
  the branch alongside; sleepers, troughing, a starter.
- `shots/rail-turnout-v3.png` — the east headshunt, and the terrain bluff the
  grading has to live with.

## Still weak

1. **On the sparse layouts the line crosses ground at 1 in 2.** Measured
   directly (`harness/_grade.mjs`): the *ground* under the trunk and the
   terminal is at 0.5–0.6 in places, and a 3.6m formation cannot bridge that.
   The track is never steeper than what it crosses, which is the most that can
   be claimed from this file. It is the same defect as terrain's map-edge step,
   seen from the rail side.
2. **Ballast reads sandy rather than grey** at close range and picks up a green
   cast in shade from `gi.js`'s grass bounce. tf2-03's is a clear stop darker
   and cooler.
3. **Sleepers read faint against the ballast** at grazing angles — the crib
   depth and the timber value are coupled and this is a compromise.
4. **No weeds, no spoil, no fishplates, no rail joints.**
5. **Signals are still decorative.** The aspect logic is real, `occupy`/
   `release`/`starter` are wired, and the block reservation is implemented and
   correct — but nothing calls `reserve`, so no signal has ever gone back
   because a route was refused.
6. **The second platform road is a passing loop nobody uses.** It exists,
   it has a facing and a trailing connection, and `cycle()` never routes over
   it. Routing an arrival into it when the rack is occupied is the obvious next
   move and needs the reservation system to be live first.
7. **Two roads 8.4m apart each carry their own ballast**, so there is a shallow
   trough between the branch and its loading road instead of one shared
   formation. Prototypical either way, but the six-foot version would read
   better.

## 2026-08-07 — the pieces physically join

An audit against Factorio's rail planner returned **unsound**, for a reason none
of the earlier work had looked at:

> "The network's pieces do not physically join. Factorio's rail planner has
> exactly one non-negotiable invariant — a piece is emitted only if it connects
> exactly, at the same point and the same level, to its neighbour, and the
> planner refuses rather than emitting a join that does not close. Measured on
> the lab's own real seven-instrument layout, every branch turnout in this
> generator joins two railheads that do not meet."

It was right, and the mechanism was `_trimAgainst`. Every road that left another
road was drawn from its own control polyline, graded on its own, and then
trimmed back to wherever it first stood **1.9m clear** of its parent. The
turnout artwork was pasted at the nearest point on the parent and asked to
bridge the difference. So:

- the diverging road's railhead began 1.9m sideways of the through road's;
- `div(0)` was 1.9m, so the switch blades — which are supposed to taper to
  nothing against the stock rail — tapered to nothing 1.18m out in the
  four-foot;
- the two roads had been graded independently, so the join was also a step in
  height that nobody had ever measured;
- the "turnout" laid over the first 26m of a branch's own 46m-radius merging
  curve reached a crossing angle of about **1 in 1.8**. No turnout is 1 in 1.8.

### The fix inverts the order of construction

The turnout is generated FIRST, from the parent's own frame (`makeLead`), and
the road that leaves there is planned from the turnout's exit port:

- a constant-radius curve leaving the stock rail **tangentially** — there is no
  lateral step at a switch tip on any real railway; the blade lies against the
  stock rail and the divergence is a²/2R;
- radius fixed by the frog number as `2·G·N²`, which is the textbook
  construction: on one constant radius from the tip the gauge faces cross
  exactly where the offset reaches a full gauge, and that *is* the 1:N crossing.
  1:8 comes out at 184m and 1:6 at 103m — radii a real yard would recognise,
  which is the check that the construction is right rather than merely
  self-consistent;
- the exit port, position and tangent, becomes the road's first (or last)
  control point, and the lead itself is spliced into the road's own alignment
  (`Track.prefix` / `Track.suffix` → `_splice`). **The road's arc length zero IS
  the switch tip.** There is nothing left to bridge, and every consumer —
  ballast, rails, sleepers, blocks, the routes trains run — reads one continuous
  alignment.

Height is closed by `Track.pinEnd`, a quintic vertical transition curve that
moves the end on to the parent's rail level AND the parent's gradient and dies
to nothing over ~80m. Quintic rather than cubic because the frames read gradient
as a difference between samples 1.5m apart, and a blend with curvature at the
junction reports a gradient that is not the one it was asked for — worth a whole
degree of tangent mismatch on the sparse layouts, where the ground under the
trunk really does run at 1 in 2. The gradient is also quoted per metre of FLAT
arc, because every alignment here is parametrised by plan length; handing it the
3D tangent's y asked for a grade 13% gentler than the parent's.

`buildTurnout` was rewritten to match. Its closure rails are no longer
`p(a, div(a))` reconstructed in the through road's straight frame — that is
short by a³/6R², eleven centimetres at the end of a 1:8 lead — but are swept
along the **diverging road's own frames**, ending on exactly the frame
`railPair` starts that road from (`railSpan` records `railFrom`/`railTo` and
both halves quote it). The frog is drawn on the diverging road's frames at the
crossing, half a gauge off its centreline; there is now a check rail opposite
the frog on **each** road, which is where the prototype puts them.

And it **refuses**. `makeLead` returns null when there is not railway behind the
tip for a stock rail; `_branch` sizes its throat radius from the leg the lead
actually leaves it and returns null when 46m will not fit; `_loadingLoop`
refuses when the stands would not fall between the two transitions; `_balloon`
*solves for* its exit tip rather than fixing it, because the 55° diagonal and
the lead's 7° tangent leave the same place and never cross again — which is
exactly why that end used to be closed with a trim instead of a junction.

### Measured

`harness/joints.mjs`, all ten soak layouts:

| | before | after |
|---|---|---|
| worst join gap | **1.92 m** | **0.183 mm** |
| worst level step at a join | not measured | 0.106 mm |
| worst tangent mismatch | — | 0.468° |

0.15mm is float32 at coordinates of a kilometre — the joins close to the
precision the geometry is stored in. The 0.468° is frame discretisation: both
sides read their tangent as a finite difference over one 1.5m frame, and half a
step of a 184m-radius lead is 0.23°.

Nothing was dropped to get there. Every layout still returns 7/7 stations
routed, 7 working cycles, 7 able to turn, and the balloon, the second platform
road and the reception road all still build:

```
L0: 11 joins  branches 2 sidings 7  balloon y loop y spur y  7/7 routed, 7 cycles, 7 turn
L2: 26 joins  branches 7 sidings 7  balloon y loop y spur y  7/7 routed, 7 cycles, 7 turn
L9:  8 joins  branches 1 sidings 7  balloon y loop y spur y  7/7 routed, 7 cycles, 7 turn
```

`node soak.mjs --parses 500 --layouts 10` → **PASS**, every counter zero.

### The look, which the blind critics named

Both critics picked the reference and both said the same five things. Four of
them were this file's:

1. **Two-tone rail.** The material was already two-tone; the band was in the
   wrong place. `u` walks the section's perimeter from the crown, and the head
   is 53mm of a 700mm perimeter — the first and last 7.5% of u, not the first
   fifteen. The old 6.4 spread the polish a third of the way down the web, which
   is the same as having none: what reads as a rail is a bright LINE with a dark
   section under it, and a gradient has no line in it. Also dropped the head's
   metalness from 1.0 to 0.78 — a fully metallic surface has no diffuse term, so
   a polished railhead came back as whatever the sky reflection happened to be.
2. **Ballast reads as stone, not sand.** It was mixed 1.15/1.02/0.80 over a
   0.315 base — a red-to-blue ratio of 1.44. Now near-neutral, slightly cool,
   and a stop darker. The warmth is where it belongs: the iron-stained minority
   of stones and the fines washed down between them.
3. **The shoulder has a profile.** One extra vertex per side rounds the crest
   (convex, 0.30m out and 0.14 down against the 0.19 a straight batter would
   be), and the hash-driven wander now applies to the shoulder as well — it used
   not to, because the drape and the wander were the two arms of one `else`, so
   every point outside the sleeper end came out geometrically perfect. That band
   is exactly what a raking sun reads, and a perfect one is what "a hard
   polygonal shoulder facet" means.
4. **The bank is a slope.** The section's width was fixed at 3.3m either side
   while the fill was allowed to be 4m deep — a batter of better than 1 in 1,
   i.e. a cliff, and it drew as a sand-coloured wall standing in a field. The
   toe now walks out with the depth of fill at about 1 in 1.5, and the
   across-section texture coordinate is accumulated from the offsets the ring
   actually used rather than from the nominal section (six metres of ground
   wearing 1.7m of texture is how a stone map reads as brown corduroy).
5. **The ties are not orange.** Two tie plates 420 x 320 are a third of the
   visible area of a sleeper from above, and mixed 2.5 red to blue they *were*
   the colour of the permanent way. They are 360 x 225 now and the plate band is
   dark; the timber is darker and much less saturated; per-sleeper variation is
   wider in tone and narrower in hue, because three channels each free to swing
   16% turn a brown timber into a red one.

### Measured (budgets)

Whole scene `sky,gi,terrain,buildings,rail,trains,vegetation,weather` at
`cam=yard`, ultra, 1920×1080: **295 draws · 1.94M triangles**, 1.93 MB
transferred, first frame 2.6s, no console errors. Budget is 450 / 2.5M / 8MB.

### Screenshots

- `shots/rail-yard-v3.png` — the mandated run, whole scene, `cam=yard`.
- `shots/turnout-g.png` — a 1:6 loading-road turnout at 20m: blades, closure
  rails, bearers lengthening to the heel, point machine, ballast apron.
- `shots/turnout-b.png` — a branch joining the trunk, from above.
- `shots/train-contact.png` — a rake at the terminal, for the rail-head polish
  and the rolling stock's contact shadows.

### Harnesses added

- `harness/joints.mjs` — the join gate. Per layout: every junction's gap, level
  step and tangent mismatch, plus what the network came out as, because a join
  that closes because the connection was silently dropped is not a fix.
- `harness/turnoutshot.mjs` — asks rail where its junctions are and stands the
  camera on one, rather than hoping a camera preset frames a turnout.
- `harness/whyno.mjs` — re-runs the guards on the live layout and says which one
  refused a connection. "There is no branch here" is now a legitimate answer and
  therefore a thing that has to be checkable.

### Still weak

1. **A black band across the middle distance.** Narrowed but not cleared: it is
   rail geometry (gone with `rail.root.visible = false`), it is not a cast
   shadow (unchanged with `castShadow` off everywhere), and it collapses to a
   thin line the moment the merged ballast mesh is hidden. It did not respond to
   lightening the cess by two stops or to easing the ballast normal map from
   0.72 to 0.58. What is left is indirect light on a large surface facing away
   from the sun at grazing incidence, which is `gi.js`'s side of the line. Full
   repro in `REQUESTS.md`; it is very probably a small clean instance of the
   "casterless dark patches" that CLAUDE.md names as the top open item.
2. **The formation still stands up to 3.6m above ground on the sparse layouts**
   — unchanged, and more visible now the batter is drawn as a real slope.
3. **No weeds, no spoil, no fishplates, no rail joints.** Unchanged.
4. **The second platform road is still a passing loop nobody routes over.**
   Unchanged; it now has real turnouts at both ends.
5. **Two roads 8.4m apart still each carry their own ballast.** Unchanged.

## 2026-08-07 — it is a ring now, not a spur

Ryan, on the running map: *"make sure they are judging the logical structure of
the track too, like it looping back on itself is not good (that's how you get
train collisions) ... It should loop to labcore and then continue on, and out
and go into the machines again. Like a loop instead of having the trains go back
down the line."*

He was right and it was measurable. Dumping one working's circuit gave:

    load:0            0 → 217
    branch0         218 → 315
    main            316 → 553      <-- outbound
    terminal.balloon 554 → 762
    main            763 → 897      <-- SAME TRACK, returning

`main` traversed in both directions by one working. The balloon turned the train
at the terminal, but the trunk was still an out-and-back — which is precisely
why opposing movements existed, why single-line token working had to be invented
on top of the block reservation, and why a head-on was ever possible.

### The shape

**`main` is one alignment with three legs and two corners**: north up the west
side of the site, east along the terminal's platform road under the loading
gantry, and south down a return alignment on the east side. Arc length increases
the whole way round. Every branch leaves the ring on the **east** leg at a
facing turnout, runs west past its row's docks, and rejoins on the **west** leg
at a trailing one. The two ends left over are headshunts with buffer stops.

So a working runs: loading road → branch → west leg → platform road → the rack →
east leg → its own branch again → home to the loading road it started on, facing
the way it faced when it left. It never reverses and there is nowhere on the
network for two workings to meet head-on.

**The balloon loop is gone**, and so is the reception road that lived in its
belly. On a one-way circuit there is nothing to turn; a loop hung off it would be
track no train could have a reason to take. The terminal keeps its second
platform road (worked the same way round as the one beside it — a second road,
not a passing loop) and a reception road off the platform road.

### The east side is chosen, not measured out with a ruler

The old comment said the trunk went west because terrain drops its valley away
to the east. That is true, and it is also why a fixed east offset does not work:
on a wide site `maxX + 220` lands in the river. `_returnCorridor` walks
`ctx.ground` down six candidate corridors and scores each by the worst and RMS
gradient along it, with a small preference for the near ones. Measured across
the ten soak layouts it picks ground at **0.063–0.266**, against the west
trunk's **0.075–0.167** — the return line is no worse a piece of railway than
the outbound one, on any layout. (`harness/eastprobe.mjs` is the measurement.)

`WX` came in from `minX − 270` to `minX − 205`. The extra 65m was the balloon's
— its exit leg reached ~186m west of the terminal — and it is not tidiness:
every metre the west leg stands out from the site is a metre each working runs
the wrong way before it turns for the terminal. On the lab's own floor that
detour was a third of the outbound run.

### The check, and what it says

`Rail.oneWayReport()` walks every circuit and reduces every stretch of every
track it is laid on to a direction. It reports:

- **conflicts** — a track run both ways in one circuit. Zero on all ten layouts.
- **overlaps** — a track covered twice in one circuit even in the same
  direction. Zero. (A branch legitimately appears twice — the working leaves the
  ring on to it and rejoins later — so a direction test alone is not enough; the
  two stretches are disjoint and this is what proves it.)
- **open** — a circuit that does not close, or closes short. Zero; every circuit
  closes to under 50mm.
- **junctions** — the hand of every connection to the ring. Every branch is
  `1f/1t`: exactly one facing turnout (where the working leaves) and one
  trailing (where it rejoins). `_branch` refuses outright if `leadE.pdir !== 1
  || leadW.pdir !== -1`, so a ring drawn with two turnouts a train would have to
  set back through cannot be built.

`harness/oneway.mjs` runs all of that over the ten soak layouts and also
re-checks the joins, because a topology that closes because a connection was
silently dropped is not a fix.

    L0 oneWay=true closed=true circuits=2 | 7/7 routed, 7 cycles, 7 turn | 3.94km | joins 11 gap 0.152mm
    L2 oneWay=true closed=true circuits=7 | 7/7 routed, 7 cycles, 7 turn | 7.38km | joins 31 gap 0.150mm
    L7 oneWay=true closed=true circuits=6 | 7/7 routed, 7 cycles, 7 turn | 16.95km | joins 27 gap 0.183mm

**The join tolerance is unchanged**: worst gap 0.152mm on the lab's own floor
(the number the previous round achieved), 0.183mm across all ten layouts, worst
level step 0.106mm, worst tangent mismatch 0.468°. Nothing was traded for the
topology.

### Smaller, and they were wrong

- **`junctionBlock(lead)`.** The arc range a turnout occupies on its parent is
  one-sided and the side depends on `pdir`. Two call sites wrote it out by hand
  and one of them had the sign inverted, so the block sat on the clear rail
  *beyond* a branch turnout while the turnout itself was drawn over plain
  sleepers. Derived from the lead now, so it cannot disagree with the geometry
  it protects.
- **The headshunts were measured from the last bench.** They are measured from
  the last junction now: the branches leave the ring within ~96m of their own
  row, so a trunk carried on to the south edge of the site was a couple of
  hundred metres of railway at each corner that nothing could reach.
- **`_loadingLoop` now refuses a tip that is not on the straight.** The branch
  has a corner at each end, so `nearest` will happily return a point on one of
  them for a road whose turnout wanted to be further out than the straight
  reaches — and a lead planted on a 76m curve is a turnout laid inside a fillet.
- **Branch junctions sit ~96m from their row at both ends**, which puts every
  branch's two diagonals on parallel offsets ordered by row: branch j's diagonal
  is strictly inboard of branch i's for j > i, so no branch can cross another.
  That is a property of the ordering, not a check. Visible in
  `shots/r4-plan-L2.png`: seven chords, none touching.

### Measured

Whole scene `sky,gi,terrain,buildings,rail,trains,vegetation,weather`,
`cam=yard`, ultra, 1920×1080: **344 draws · 1.98M triangles**, 2.03 MB
transferred, first frame 3.0s, no console errors. Budget 450 / 2.5M / 8MB.
(Previous round: 295 / 1.94M — the ring is ~10% more railway on the lab's floor
and the return alignment is the cost.)

Across layouts at 1920×1080: 153–182 draws, 1.12–1.88M triangles, 3.9–17.0km of
railway. The 17km layout is still the cheapest per metre because `thin` bites.

`node soak.mjs --parses 500 --layouts 10` → **PASS**, every counter zero,
12 arrivals, 37.5km run.

### Screenshots

- `shots/r4-plan-L0.png` — the lab's own floor as a plan of the bare track: the
  ring, the two branches as chords, the loading roads with trains standing on
  them, the terminal at the top.
- `shots/r4-plan-L2.png` — a single file of instruments: seven chords between
  the two legs, none crossing.
- `shots/r4-ring/f-*.png` — the ring in motion, plan view, three workings out.
- `shots/r4-turnout-0.png` / `-1.png` — the east (facing, `pdir +1`) and west
  (trailing, `pdir −1`) junctions of branch 0.
- `shots/r4-yard.png`, `shots/r4-film-sheet.png` — the mandated runs.

### Harnesses added

- `harness/oneway.mjs` — the topology gate: one-way, closed, no double
  coverage, hands correct, joins still met, network still complete.
- `harness/planshot.mjs` — a plan view framed on the RAILWAY rather than on the
  lab, because the network is now half a kilometre wider than the site.
- `harness/ringfilm.mjs` — the same view, in motion, while traffic runs.
- `harness/eastprobe.mjs` — scores candidate return corridors by the ground
  along them; it is the measurement `_returnCorridor` implements.
- `harness/laps.mjs` — circuit and outbound-leg lengths per layout, and the
  journey time they imply.
- `harness/tier.mjs` — prints the quality tier every four seconds. It is how the
  "headless never leaves `floor`" note in REQUESTS.md was found.

### Still weak

1. **Trees stand in the four-foot at both ends of the ring.** Photographed at
   both junctions. Pre-existing on the west leg; the return alignment doubles
   it. vegetation.js's, raised in `REQUESTS.md` with the exact query to use.
2. **Neither leg of the ring is on graded ground.** terrain.js grades
   station→hub corridors and a yard box; the trunk has never been in either.
   Unchanged in kind, doubled in length. Also in `REQUESTS.md`.
3. **The second platform road is still a road nobody routes over.** It now has
   the right hand for one-way working at both ends, which it did not before, but
   `cycle()` still takes every working down the main. Routing alternate rows
   over it is the obvious next move and would double the terminal's capacity.
4. **No weeds, no spoil, no fishplates, no rail joints.** Unchanged.
5. **Two roads 8.4m apart still each carry their own ballast.** Unchanged.
6. **The black band across the middle distance** is unchanged and still
   `gi.js`'s side of the line.

---

## Round 7 (2026-08-07) — the permanent way, and three faults the audit found

The topology from round 6 is untouched and re-measured: worst join 0.183mm,
worst level step 0.106mm, worst tangent 0.469°, 7/7 routed with 7 closed
circuits on all ten soak layouts (`harness/joints.mjs`).

### What two blind critics said, and what each turned out to be

**"Sleepers are flat, zero-thickness quads of near-identical tone at perfectly
uniform pitch that cast nothing."** Three separate causes, none of them the one
the words suggest. The geometry always had thickness and always cast; what it
did not have was (a) a top face with the grain running the right way —
`Mesher.box` hands its +y face a UV *rect* tied to the a→b edge, which on a
sleeper is the 300mm width, so a two-metre baulk wore its grain across itself;
(b) any face-specific paint — the end was painted with stretched long grain and
therefore read as a cut-off nothing; (c) any exposure — `BALLAST_CRIB` buried
54% of a 155mm tie, so the side that carries the thickness was two texels tall.
And the pitch really was perfect: the per-sleeper shove was *lateral*, which
moves a tie without changing the gap to its neighbours.

Now: `sleeperPrototype` is built from explicit quads (`faceUV`) with a chamfered
top arris; the map is four bands — top / side / **end grain** / tie plate — with
the side dirtying downward into the crib and the end carrying rings and radial
checks; the crib is 105mm; the shove is along the track as well as across it;
and there are **three prototypes** taking three windows of the map, so a rank of
ties is no longer one timber repeated. Two extra draw calls out of 450.

**"The ballast is uniform high-frequency sandpaper noise — no individual stones,
no size variation, no scattered larger chips."** Correct, and the cause was that
`Tex.cells` returns only `f1`/`f2` — distance fields — so every chip was shaded
from its own centre outward and the field read as dirt. `stoneField()` is a
local Worley that also returns the **cell's identity**, which lets each stone
take one flat tone. Three layers: a sparse coarse one (only the ~37% of cells
whose id says so, ≈130mm), the working ballast (≈52mm) and fines (≈21mm);
whichever layer owns a texel gives it its tone and its joint. Value raised about
a stop — the old mix sat at 0.35, a wet-tarmac value, against which the fines
read as sand.

**"The rail reads pink/violet, not steel, at every distance."** Arithmetic:
the web reached 0.43 red against 0.12 blue (ratio 3.6 — traffic-cone rust), the
head's albedo peaked at **1.02**, and the head was 78% metallic with
`envMapIntensity` 1.5 under a blue sky. Bright blue specular over a red web is
violet. Now: web halved in value at a 1.7 ratio, head albedo 0.44–0.64 with a
dark rolled edge either side of it, metalness 0.70, roughness 0.185 (burnished
by wheels, not lapped), env 0.85, and the head's own tint faintly *warm*.

**"The paved track is a printed decal — no rails, no flangeway groove, no
thickness."** There was no paved track: the critic was reading ballasted track
at fifty metres. But the fair conclusion is that a rail-served loading rack
inside a works has no business being ballasted, so the loading roads are now
genuinely **embedded**: `pavedDeck` sweeps a slab section with a 48×70mm
flangeway on the gauge side of each rail, a 130mm kerb edge, 3m poured bays
(`paveMaterial`), oil down the four-foot, and a smoothstep taper at each end
that ramps the deck from ballast-crown level to slab level over 9m. Sleepers are
suppressed under it — that is what embedded means — and `ballastRibbon` takes
`from`/`to` so no stone is laid under the concrete to z-fight through it.

**"From ~x=840 the track becomes a shimmering white speckle band."** A trackbed
is always seen at a grazing angle running away from the camera, which is exactly
the case an isotropic mip chain cannot serve. Every rail map is now built at
`aniso: 16` rather than the library default of 8.

### The three operational faults

1. **Junction standage.** Was 67.5m against consists of 64.5–84.0m. `MIN_STANDAGE`
   (104m) now pushes consecutive junctions apart, capped by `THROAT_MIN` so a
   branch is never refused for it. Measured: the lab's own floor 67.5 → **91.5m**;
   a seven-row file 66–91.5 → **90–91.5m**, the 66m outlier gone. `THROAT_MIN` is
   72 and not the 54 the corner arithmetic suggests, because the 1:6 lead eats
   22m of the throat before the corner starts — sizing it from the corner alone
   refused a third of the branches. **The ceiling is the plan's, not this file's:**
   once every junction is against its throat cap the spacing between two of them
   IS the spacing between two rows, which the floor sets at 2.05 bays = 90m.
2. **`terminal.loop` was dead track in all ten layouts.** Rows are now dealt
   round-robin across the two platform roads and `_buildCircuit` routes half of
   them over it (three ascending slices instead of one; the two guards that keep
   it one-way are checked, not assumed). `oneWayReport` still returns zero
   conflicts, zero overlaps, zero open circuits.
3. **One discharge stand for the whole railway.** Now four: **two roads × two
   spots** 42m either side of the middle of buildings.js's 134m gantry, so both
   spots are genuinely under the rack and 84m apart — the longest consist. Benches
   are dealt across the spots by their position on the loading road, in the only
   order that does not jam: the bench with less railway in front of it arrives
   first and takes the spot further along, so the one behind stops clear of it
   rather than in front of it.

### Measured

| | before | after |
|---|---|---|
| soak arrivals (500 parses, 10 layouts) | 8 — **FAIL**, dead railway | **12 — PASS** |
| metres run | 31,561 | 37,254 |
| longest stand | 1204 frames | 884 |
| discharges in 75s, all 7 benches parsed | 2 | 3 |
| junction standage, lab floor | 67.5m | 91.5m |
| collisions / reversals / floating / console errors | 0 | 0 |

Whole scene `sky,gi,terrain,buildings,rail,trains,vegetation,weather`, ultra,
1920×1080: `cam=street` **121 draws · 1.19M triangles · 2.03 MB · 114 fps**;
`cam=yard` 183 draws · 1.30M triangles. Budget 450 / 2.5M / 8MB. Film gate:
12 frames, 117–127 fps, no console errors. Rail alone at `cam=street`: 50–65
draws, 0.82–0.90M triangles.

### Screenshots

- `shots/r6-judged-street.png` — the mandated pinned-ultra frame.
- `shots/r6-pave-final2.png` — embedded track: slab, poured bays, flangeways,
  kerb, and the ballasted road beyond it for comparison.
- `shots/r6-pw-macro3.png` — the permanent way at three metres: discrete stones
  with size variation, timber with end grain and per-tie tone, two-tone rail.
- `shots/r6-yard-v2.png`, `shots/r6-film-sheet.png` — the whole scene.
- `shots/r6-pw-base.png`, `shots/r6-probe-railonly.png` — the before shots.

### Harnesses added (all `harness/pw*.mjs`, owned by rail)

- `pwshot.mjs` — stands the camera a named distance off a named track at a named
  arc length. The preset cameras never frame the permanent way.
- `pwmap.mjs` — reads a rail material's generated map back and reports the mean
  sRGB of each band. Two rounds shipped charcoal while reasoning that the paint
  "should be mid brown"; this is how that stops.
- `pwstands.mjs` — resolves every circuit's `terminal` to a world point across
  four layouts and counts the distinct stands, plus loop usage and one-wayness.
- `pwjourney.mjs` — parse-to-discharge time and arrivals in 75s, which is the
  number the soak's liveness assertion actually depends on.

### Still weak

1. **The black band across the middle distance is not rail geometry.** Settled
   this round with a direct A/B: rendered identically with and without `gi`
   loaded, and with `gi` absent the cess and batter between two roads are evenly
   lit with no band at all (`shots/r6-probe-nogi.png`). It is lighting. Repro
   already in REQUESTS.md.
2. **The pale ribbon with regular dark dashes in the middle distance is
   buildings.js's dock platform edge**, not track — its hazard marking
   desaturating with distance. Noted in REQUESTS.md; nothing here can fix it.
3. **Junction standage cannot exceed the row pitch** where rows are packed. 90m
   against an 84m consist is six metres, and the only lever left is the floor
   plan or shorter rakes.
4. **The formation still stands up to 3.6m above ground on sparse layouts.**
5. **Two roads 8.4m apart still each carry their own ballast**, so the cess
   between them is drawn twice.
6. **No weeds, no spoil, no fishplates, no rail joints.** The rail texture
   repeats every metre of arc length, so a joint every 18m cannot live in it.
7. **A slab across a turnout is not drawn.** The apron stops well clear of both
   sets of points, because paving a switch is a different piece of engineering
   and drawing one would claim detail the geometry has not got.

---

## Round 8 — the railway comes down off its causeway (2026-08-07)

Ryan: "the amount that the train rails float above the terrain is insane."

### Which of the two it was

The brief offered two diagnoses. It was the first, and the measurement settles
it without argument. `harness/railfloat.mjs` samples every station circuit at 40
points and subtracts `terrain.heightAt` directly beneath the sampled railhead.
The **minimum** over 280 samples was 0.737m — exactly `FORMATION`, the design
offset — while the median was 2.219m. A constant offset cannot produce that
spread. The offset was right; the thing it was offset *from* was wrong.

The permanent way is 687mm and always was: 280mm of ballast under the tie, a
155mm sleeper, a 20mm baseplate, 172mm of rail, 60mm of formation proud. The
missing metre and a half was fill, produced by four upward-only steps in
`Track.build` that were each defensible alone:

1. the ground was sampled as a **maximum across ±2.6m**, which on any cross-fall
   lifts the whole line on to the uphill toe of its own ballast;
2. it was **blurred over ±20m, twice** — so in every hollow the profile flew
   across, the line left the ground and stayed there;
3. every **deficit against that maximum was dilated ±14m, blurred and added
   back**, which is a fill that no longer knows what it was filling;
4. and the result was raised to a **ruling-grade envelope** with a 4.0m cap and
   a 3.6m ceiling. On this terrain — 25m of relief, local slopes of 1 in 3 — the
   smallest 1-in-40 curve above the ground stands 20m clear at p90, so the
   ceiling bound *everywhere* and the whole trunk sat on it.

The last one is the trap worth remembering: **an envelope has no preference for
staying low.** Clip an unsatisfiable envelope to a ceiling and you have not
built a railway to a ruling grade, you have built a causeway at the ceiling
height.

### What replaced it

One idea instead of four corrections. The profile lives in a tube —

    lo = ground + 0.03    never cut, because nothing here can excavate
    hi = ground + 1.50    never a bank taller than a works railway builds

— and inside it a **taut string**: blur, project back into the tube, at falling
wavelengths (16, 10, 6, 3, 2 metres, three rounds each, all O(n)). A taut string
rests on the ground wherever the ground will carry it and lifts only to span
what it cannot follow, which is both the right answer to "minimum earthworks"
and a fair description of how a cheap industrial line is laid. `lo` gets the
last word, so "no hillside through the sleepers" holds exactly.

The lateral sample narrowed to ±1.30m, the sleeper end. Outside it the ballast
batter is already a drape that rises to meet the ground; sampling out to the
shoulder was paying for the same protection twice, in railway height.

The ladder's top wavelength was measured, not chosen: at 60m the median float is
1.19m, at 26m 0.90, at 16m 0.83, at 11m 0.79 — and by 11 the profile rides
ripples a ballasted line would have planed off and the worst vertical curve
tightens from R=200m to R=170m. 16 is where the trade flattens.

### Measured, before and after, same terrain, same four layouts

Railhead above `heightAt`, 280 route samples per layout:

| | median | p90 | max | over 2m | below ground |
|---|---|---|---|---|---|
| before | 0.907–1.203 | 3.64–4.33 | 4.59–5.31 | 41–119 | 1 |
| after | **0.717–0.736** | **0.98–1.15** | **2.26–2.87** | **6–10** | **0** |

Against the terrain as it stood at the start of the round (it was rebuilt
mid-round by its own owner) the same comparison is median **2.219 → 0.826**,
p90 4.459 → 1.007, max 4.614 → 2.233.

Of the 0.73m median, 0.687 is the permanent way. The fill is a median of
**0.04–0.22m per alignment**, and 0–9% of each line stands on anything over 0.9m.

The ride got better at the same time, which was not the goal but is the proof
the string is doing engineering rather than just sitting down:

| track | worst gradient before | after |
|---|---|---|
| main | 1 in 2.2 | 1 in 3.2 |
| branch0 | 1 in 1.9 | 1 in 2.3 |
| branch1 | 1 in 1.9 | 1 in 2.3 |

and the worst vertical curve went from R≈5m (curvature 0.202) to R≈8.5m (0.118).
Both are still bad, and both are the terrain's, not the grading's — see below.

### Verified, not assumed

- `harness/railfloat.mjs` also asks whether the ground now comes up *through*
  the stone, since the profile is floored on a narrower sample than before:
  **0 breaches in 3744 shoulder samples** (ground above the batter drape's
  ceiling at ±1.9m and ±2.4m). Five frames out of ~2500 on layout 1 have ground
  above the ballast toe under the sleeper, which `lo` forbids — those are
  `pinEnd`, which runs after grading and may lower a junction end on to its
  parent's rail level.
- Pictures, A/B at an identical camera and an identical terrain, only rail.js
  differing: `shots/r8-ab-old.png` vs `shots/r8-after-yard2.png`. Old: a
  sand-coloured apron three times the width of the track, running unbroken to
  the horizon. New: a ribbon tucked in tight to the surface.
  `shots/r8-before-yard.png` and `shots/r8-before-street.png` are the same
  defect against the earlier terrain, which is what Ryan was looking at, and
  `shots/r8-after-street-final.png` is the same street camera once terrain had
  stopped rebuilding: a low shoulder, the cess merging into grass, the alignment
  following the land. `shots/r8-film/frame-02.png` and `frame-08.png` are the
  whole scene with a working on the road.
- `soak.mjs --parses 200 --layouts 4`: collision 0, reversal 0, floating 0,
  unreachable 0, relayout 0, console errors 0, arrivals 5–6, 14km run. The
  one-way circuit is untouched — nothing in this round moved a control point,
  only heights. The run reports FAIL on one assertion, `edge` (17–32 faults, a
  26–55m step in ground height at r=1140–1620m), and it is not the railway's:
  terrain.js grew a coastline this hour and the same soak against the PREVIOUS
  rail.js fails with edge 20 and byte-identical fault strings. The site is
  ±450m; the railway never reaches r=1100m. Recorded in REQUESTS.md.

### Still weak

1. **A railway cuts, and this one cannot.** Everything left is that. `heightAt`
   is read-only here and terrain builds first, so the profile is floored on the
   ground and the residual fill, and every gradient steeper than 1 in 40, are
   consequences of never being allowed to excavate. `Rail.formationCorridors()`
   now publishes exactly what a cut would need — centreline, wanted ground
   level, half-width 4.15m, 1-in-1.5 batter — and the ask is written up in
   REQUESTS.md with numbers. With it, railhead-above-ground becomes a flat
   0.687m everywhere and the ruling grade actually holds.
2. **`maxGrade` is now an intention, not a guarantee.** `this.overGrade` reports
   the fraction of each line laid steeper than it was specified to. On branch0/1
   that is real: a locomotive on 1 in 2.3 is a thing a critic names in a glance.
3. **No bridge.** Where a line genuinely wants a viaduct it gets a 1.5m bank and
   then follows the ground down. Honest, but a span with piers would be better
   and is not drawn.
4. Items 1–7 of round 6's "still weak" list stand, except the old item 4 ("the
   formation still stands up to 3.6m above ground"), which is what this round
   was about.

### Harness

- `harness/railfloat.mjs` — the acceptance measurement: railhead vs `heightAt`
  over every circuit, per-track fill/gradient/curvature, shoulder breach count,
  across N relayouts. Run it before believing anything about track height.
- `harness/railgrade.mjs` — decomposition: the natural ground profile under each
  alignment, and the minimum fill a g-Lipschitz railway is forced into at
  several ruling grades, with and without cutting. This is what proved the
  ruling grade was unsatisfiable rather than merely expensive.

---

## Round 9 — the alignment gets rules, and declares what it needs (2026-08-07)

Ryan: *"the erosion wont allow the track to go without earth beneath it, it even
cuts through terrain right now to keep it flat ... it clearly doesnt care about
curves too ... And I want some elevation change, and a bridge over some water
too. but we need some rules before we let it go ham."*

Measured before: minimum radius 29–38m, maximum gradient 35–44%, 132–184 of 400
samples over the ruling grade, **zero cutting declared anywhere, ever**. The
profile was a taut string inside a tube whose floor was the ground, so it could
only fill; where the hillside rose faster than a bank could follow, the line
climbed it.

### What changed

- **Rules, at the top of the file, applied per class of line.** `main` is the
  running line (`R_MIN_RUN` 90m, `GRADE_RULING` 2.5%); branches, loading roads,
  terminal roads and the yard spur are yard connections (`R_MIN_YARD` 55m). A
  spiral into and out of every curve, sized from the radius and recomputed every
  fit pass. A deflection under `CURVE_MIN_TURN` is not a curve and is laid
  straight.
- **The vertical alignment is designed, not draped.** `Track._grade` builds the
  minimum-earthwork g-Lipschitz profile: `dilate(G)` is the all-fill railway,
  `erode(G)` the all-cut one, and the answer is a weighted mean of the two,
  pulled back toward the ground six times with a re-projection after each pull so
  it can never leave the rule. Two box means give every change of grade a
  parabolic vertical curve.
- **`pinEnd` obeys the rule too**, which it did not. The 90m junction blend was
  where every steep chainage on this railway actually lived: a road whose grading
  put its end four metres from its parent's railhead took those four metres out
  over ninety, which is 1 in 12. The blend is now as long as the discrepancy
  needs, and afterwards the profile is alternately re-fitted and clamped into
  cones radiating from each anchor at exactly the ruling gradient — a projection
  onto the intersection of two convex sets, so the pin ends up exact AND the rule
  ends up held.
- **`Rail.earthworks()`** publishes the declaration: `{track, kind, from, to,
  length, maxDepth, half, batter, step, points}` per span, `kind` one of
  cut/fill/tunnel/viaduct/bridge/grade, `points[i*3+1]` the formation level. On
  `ctx.railEarthworks` and emitted as `rail:earthworks`. Shape and rationale in
  REQUESTS.md.
- **Structures.** `_span` builds a trough-girder deck with piers at ~26m and
  wider abutments at the ends, squared to the track; `_portal` builds a horseshoe
  tunnel mouth with wing walls. Nothing is laid inside a bore — it is under the
  hill — and the ballast ribbon stops draping over the side on a deck, which is
  what made the first attempt hang a twenty-metre curtain of stone off each side
  of the bridge.
- **Route choice by earthwork.** `_legCost` scores a corridor by `|fit(G) − G|`
  rather than by relief, which is the right question: 30m of steady climb is free
  and a 6m gully is a bridge. Both ring legs are now chosen that way, and each
  candidate is charged for the branches that have to reach it.
- **Dead track.** `_auditTracks` builds every circuit, compares against
  `this.tracks`, logs anything no working reaches and removes it before the
  meshes are built. Currently zero on the lab's floor.

### Measured after (400 samples per route, three routes)

| | before | after |
|---|---|---|
| maximum gradient | 35–44% | **2.50%** on every route |
| samples over the ruling grade | 132–184 / 400 | **0** |
| minimum radius, as designed | 29–38 | 44.9 `main`, 48.6–52 branches, 130–160 elsewhere |
| cut / fill / bridge / tunnel | 0 / all / 0 / 0 | 1842m cut · 780m fill · 259m viaduct (3) · 150m tunnel (2) · 983m at grade |
| vertical curves | none | every grade change; worst radius 79m, best 26.6km |

Gates: `soak --parses 200 --layouts 4` — collision 0, reversal 0, floating 0,
unreachable 0, relayout 0, console errors 0, deadRailway 0, 13.4km run. The one
FAIL is `edge` (12 terrain height steps at r=380–1100m), byte-identical to the
run against the previous rail.js and not the railway's — the railway never
reaches r=380m.

### Still weak

1. **The 90m running-line rule is broken at `main`'s two terminal corners** and
   at both branch throats, and it is a plan dimension rather than anything in
   this file: the hub sits 124.4m from the nearest row's running line where a
   90m corner plus a 1:6 lead plus a 90m throat needs 201m. Recorded in
   `rail.exceptions`, reported, and asked for in REQUESTS.md.
2. **1842m of declared cutting is invisible** until terrain applies the
   declaration. `FILL_BIAS` is 0.72 rather than 0.5 purely so the railway can be
   seen in the meantime; going back to a balanced profile halves the earthwork
   and is one constant.
3. **No water crossing exists to bridge.** The rule is implemented and fires on
   `ground ≤ waterY + WET_FREEBOARD`, but on the current island the water is all
   ocean and the routable envelope contains none of it. The viaducts are what
   Ryan will see; a bridge appears the moment terrain puts water inside the site.
4. **A hard black square at `cam=top`** — screen-space, rail-dependent, not this
   file's geometry (checked). Written up in REQUESTS.md.
