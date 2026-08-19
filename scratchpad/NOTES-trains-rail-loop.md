# The railway goes both ways now — `rail.js` + `trains.js`

Ryan, on the running map: *"Theres no infrastrucutre for the trains to go back to
thier stations."*

## What was actually true before I changed anything

Verified from the running world, not from reading:

- `shots/base-yard.png`, 66 draws / 1.13M tris — trains on the road, fine.
- `harness/traincycle.mjs` (new) polled the live consists for 45s. Every working
  ran `run → s = route.len → dwell 1.6s → fade 1.1s → group.visible = false`.
  Route lengths 543–840m, one-way, station → hub. **Nothing came back, and no
  track existed on which anything could have.** The old `_stepRun` fade state is
  the whole of it.

So: a real topology hole, and the fix had to be track, not an animation.

## What was built

**A balloon loop at the LabCore terminal** (`Rail._balloon`). Chosen over a
run-round and over double track for reasons the site forces:

- a run-round needs a headshunt as long as the train at every one of seven
  benches 90m apart;
- double track needs a second formation the whole 900m out plus crossovers to be
  believable — roughly 2x the permanent way;
- a loop needs one corner of ground nobody else wants, and it is what a real
  petroleum unit-train terminal uses: arrive, discharge while running round,
  leave facing the way you came, never uncoupling the locomotive.

Its shape is fixed by two things it must not touch. `buildings.js` lays a
300 × 150 concrete yard slab over everything **north** of the platform road
(`labcoreTerminal`, `dockZ − 66 ± 75`), so the loop can only hang south. And the
nearest row's running line is a **constant 124.4m** south of the platform road —
constant for any fleet, because `hub.z` and `runZ` are both derived from the same
row. That leaves ~100m of depth: a 42m cap radius with the reverse curve that
brings the far leg back on to the line at ~92m. `Track` gained `opts.radii` so
one alignment can be tight where it turns and gentle where it becomes a turnout;
all the sharp radius is inside the loop and both connections are 1:11-ish points
a train takes at speed.

Also new:

- **`terminal.link`** — a connection from the outer platform road down on to the
  inner one, so both rows reach the one loop and the one loading rack. This is
  the crossover the brief asked for on the running line: it is where an inbound
  working off one road and an outbound off the other are on different rails at
  the same moment.
- **`yard.reception`** — the spare road moved *inside* the loop's belly, which
  is where a discharge terminal actually stables a cut of tanks, and which keeps
  the throat clear. The old `_yardSpur` off the platform road is kept as the
  fallback and now collides with nothing because it only runs when there is no
  loop.
- `yardEast` pushed to `hub.x + 152` so the loop's facing points have railway
  beyond them instead of a buffer stop against their heel; `WX` to `minX − 150`
  so the throat has room. ~590m of new track.
- **`rail.cycle(uid)`** → `{route, terminal, loopExit, turned, line}`. The route
  is a genuinely **closed** circuit: dock → running line → the rack under
  `buildings.js`'s gantry → round the loop → back down the same line → into the
  same loading loop, ending on the point it started on with the tangent running
  the same way through it. `route(uid)` is unchanged for anyone still using it.

**Every bench already had a passing loop** — `_siding` has always built a loading
loop with two turnouts at each station — and that is what the trains now use to
cross. A working holds at its own starter, standing in its loop on rail nothing
else needs, while an inbound runs by on the running line beside it.

## trains.js

The closed route is the whole trick: `c.s` **wraps**, so a standing train has its
head at `s = 0` and its rake trailing back through the tail of the same array,
which is the rail it will arrive on. It pulls forward, runs the circuit, and
comes home to where it stood. Nothing fades, nothing is destroyed, nothing is
re-created.

- States: `out → discharge → hold → back → idle`, and **`idle` is visible** —
  every instrument's train stands in its loading loop. That is the other half of
  "the trains go back to their stations", and it is what the old floor could
  never show.
- **Loaded out, empty back.** The tank car bodies ride **55mm** higher on the
  return (about what a set of freight springs gives back), ramping over the
  discharge dwell and settling again over 4.5s at the bench. Bogies do not move.
  Empty stock also runs a little harder (34 vs 30 m/s cap).
- Single-line working: `_clearToStart` / `_clearToReturn` / `_loopHold`, asked of
  the whole railway rather than per line, because both roads feed one loop and
  one rack. `rail.occupy()`/`release()` are called every frame from
  `_placeConsist`, so aspects are red because there is metal in front of them.
  Signals gained a **caution ring** (red < 70m, yellow < 190m) and station
  starters are now driven by `rail.starter(uid, running)` — green only while that
  bench's own working is out.
- Nothing vanishes. A consist with no instrument is collapsed to zero scale; a
  running train keeps the route it is running until it gets home even across a
  replan.

## Measured

Whole scene, `sky,gi,terrain,buildings,rail,trains`, 1920 × 1080, ultra:

| view | draws | tris |
|---|---|---|
| `cam=street` t11 | 109 | 892k |
| `cam=street` t16 | 113 | 898k |
| `cam=yard` t11 | 118 | 905k |
| `cam=yard` t16 | 124 | 907k |
| oblique over the loop | 108 | 884k |
| `cam=yard`, seven trains on the road | 85 | 1.36M |
| nose-in at the rack, 90m out | **275** | **1.68M** |

Budget 450 / 2.5M. Worst seen **275 draws / 1.68M tris**, at a camera 90m from
the terminal with the whole tank farm and four trains in frame — the same order
as the 244 / 1.8M the close track view already cost. The loop adds ~590m of
track (~65k tris) and standing stock adds ~20 draws.
First interactive frame 1.43s.

Quality ladder driven through every tier at runtime plus a replan
(`harness/rail-stress.mjs`): `floor 43/637k · low 46/642k · medium 61/707k ·
high 62/784k · ultra 62/784k`, 12 tracks, 19 signals, yard route present, **no
console errors** on any run above.

**Round trip, measured** (`harness/cyclewatch.mjs`, new — logs every state change
with a clock, 240s run, seven benches, a parse every 2s):

| circuit | out to the rack | full round trip |
|---|---|---|
| 1503m | 31.2s | **79.3s** |
| 1863m | 37.4s | 90.6s |
| 2202m | 41.4s | 101.0s |
| 2054m | 40.3s | 118.0s (held longer at the loop) |

So **80–120s**, the spread being how long each waited its turn at the loop's
starter. In the same run every working got home, went straight back out, and
`STANDING` at the end was four out and three in their loops — no starvation and
no deadlock. (An earlier cut *did* deadlock: three trains holding on the same
forty metres, each the reason the other two could not go. That is what
`_loopHold`'s queue is for, and it is why the measurement is in this file.)

**When parses arrive faster than trains can run** — the harness fires one every
2s against ~85s round trips — each bench books up to 4 and drops the rest; the
starter stays at danger and the train goes when the road is clear. `pending`
saturates at 4 within the first minute, which is the honest picture of a queue
and is exactly what the old fade-out was hiding.

## Screenshots

- `shots/loop-turn.png` — the whole answer in one frame: LABCORE, the balloon,
  the reception road in its belly, the throat, a train on the loop.
- `shots/loop-top.png` — plan view of the new topology.
- `shots/loop-yard11.png`, `loop-yard16.png`, `loop-street11.png`,
  `loop-street16.png` — the mandated four.
- `shots/base-yard.png` — before.

## Still weak

1. **The loop stands on a tall embankment.** The corner south of the terminal is
   falling ground and rail's fill logic builds a bank there — correct behaviour,
   prominent result. Raised in `REQUESTS.md`: widening terrain's graded corridor
   ~100m south would put the loop on the shelf.
2. **Ballast still reads as pale dirt at grazing angles** (rail's known weakness
   #1/#2/#4 — sleeper value against crib depth, the green cast from gi's grass
   bounce, the 1.15m tile). Untouched this round.
3. **No discharge connection is modelled on the car.** `buildings.js`'s gantry
   already stands over the rack road with eleven drop arms, so the train stops
   under a real connection, but nothing reaches down to a manway. Vapour off the
   vents during the dwell is standing in for it.
4. **Trains do not detour *into* a loop to cross** — an outbound waits at the
   bench it is already standing in rather than being routed into a mid-section
   refuge. Correct for this layout (every bench is a loop) and it would need
   traffic-dependent routing to do otherwise.
5. **The set-back fallback is untested on a real site.** If the site is too short
   for a balloon, `turned` is false and the train propels home. It runs, but no
   fixture in this repo is small enough to exercise it.
