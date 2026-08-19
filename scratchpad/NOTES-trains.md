# NOTES-trains (2026-08-07) — block signalling

## What was wrong

`soak.mjs --parses 500 --layouts 10` on the code as it stood: **8,910 distinct
collision faults**, `maxConcurrent 5`, reversals 0, and one pre-existing 404
console error. Ryan: "trains run into each other, they go back through the
railway."

The file's own comment admitted it: *"One rail, two directions. There is no
signalling logic here worth the name."* Two consists were kept apart by asking,
in world space, whether the other one looked like it had gone by
(`_clearToStart`, `_clearToReturn`, `_loopHold`). That is timing, not
signalling, and timing fails at exactly the moment two trains meet.

## What replaced it

The OpenTTD model. The railway is divided into blocks; a block is held by at
most one consist; a consist may not move on to a block it does not hold; a
reservation is **refused, never negotiated**. `this.blocks` (`Map<blockId,
consist>`) is the whole of it and is the only authority on occupancy —
`rail.occupy()/release()` is still called every frame, but as a *display* input
so the signals show what the interlocking decided, not as the decision.

Two kinds of block, both derived from the railway rather than declared:

- **`line:<name>`** — one running line, worked as a single-line token. Taken at
  the dock, given up when the train is standing in its loop again. The loading
  loops are the passing places, which is why they exist.
- **`common`** — the stretch two branches share. Found geometrically:
  `_interlock()` hashes every sample of every station's circuit into a 6m grid
  and marks any sample lying within `SHARE_R` of a sample belonging to a
  circuit on a **different** line. That needs nothing from `rail.js` and
  survives it re-laying the permanent way, which it did twice during this
  session (`line0/1` → `branch0/1`, circuit 1,741m → 2,579m).

The interlock between them is Factorio's chain signal: a train does not enter
`common` unless it can hold it outright, and while it waits it stands on its own
line — rail it already holds, that nothing else can want. Whoever holds `common`
needs only its own token to clear it and holds that already, so it always makes
progress. Hold-and-wait cannot form; there is no deadlock to break.

## Reversals

The harness's reversal detector is **correct and the fault was genuinely
absent** — every circuit `rail.js` hands back is closed and `turned`, so `s`
counted up all the way round and nothing wound back. Two ways it could have
fired were still live in the code and are now gone:

- The `turned === false` fallback (a site with no room for a balloon) drove the
  train with `dir = -1`, propelled back up the road it came in on. It now runs
  round instead: `_runRound()` reverses the *array*, not the train
  (`s' = L - s + length`), so the working is pulled home nose-first with its
  distance-run still counting up. State `turn` for the 5.5s it takes.
- `_drive` integrated and *then* snapped back to the goal, winding `s` down by
  up to a frame of line speed on every arrival. It now clamps the step instead.
- The yard shunt ran with a negative velocity for half its cycle. Same fix: the
  loco runs round its cut, the array reverses, `v` never goes below zero.

## Queueing

`c.pending = Math.min(4, c.pending + 1)` was silently dropping the fifth print.
Parses are now booked in `this.backlog` (one integer per instrument, no cap) and
the working takes the whole book when it leaves. At the rate a lab prints that
is one parse, one train. At 500 parses a minute it is one train carrying the
lot, which is what a railway does — nothing is discarded and the map never
claims a working it did not run.

## What this costs

`traffic.mjs` (new, in `harness/`) measures throughput, because the soak cannot
tell a correct interlocking from a deadlocked one — a railway with every train
standing still also has no collisions. Measured on the lab's real layout: two
branches, one `common` section spanning **1,300m of a 2,579m circuit**, so the
site runs about one working per 70s and `maxLive` is 2. That is the railway's
limit, not the code's; see the `REQUESTS.md` note to `rail.js` for the two
pieces of infrastructure (a double-track trunk, standage in the balloon) that
would each roughly double it.

Seven trains still stand at their benches, which is most of what makes the yard
read as busy.

## Budgets

`shots/trains-block-yard.png`, full stack, `cam=yard time=15`: 287 draws /
1.86M triangles / no console errors. Geometry unchanged by this round.

## 2026-08-07 — contact darkening

Both blind critics, neither told which render was ours, said the same thing
about the rolling stock: it "casts no shadow whatsoever and has no contact
darkening at the wheels, so every vehicle floats on the rail".

Half of that was already wrong and it is worth writing down why the other half
was right. `castShadow` was set — on every vehicle body, on both sideframe
pools, on both wheelset pools — and the shadows are there in
`shots/train-contact.png`, thrown across the ballast. Which is exactly why the
note lands: a cascade fitted to a site several hundred metres across has texels
the size of a sleeper. It can put a tank car's shadow on the ground beside the
track; it cannot resolve the dark few centimetres in the crib directly under the
solebar, and that narrow band is the entire visual cue that an object is
*resting* on something rather than hovering over it.

So it is drawn rather than sampled. `_buildContact` makes one InstancedMesh —
one soft blob per vehicle, 3.15m across and a little longer than the car,
sitting 155mm under the railhead so it is over the sleeper tops and under the
floor. It rides the vehicle's own basis in `_placeConsist`, is collapsed to zero
scale by `_park` with the trucks, and is the first thing `onQuality` sheds: at
`low` the real shadow map is off too, and a contact patch under a vehicle that
throws nothing reads as a stain rather than as light.

Two details that were not free:

- **Multiply, not a black quad.** The crib it darkens is already dark and a
  fixed black reads as a hole. `THREE.MultiplyBlending` needs
  `premultipliedAlpha: true` or three warns once per frame per instance.
- **The map is deliberately NOT flagged sRGB.** It is a multiplier, not a
  colour: it is applied to whatever is already in a linear buffer, so decoding
  it through the sRGB transfer would turn a 0.44 shade into 0.16 and put a hole
  in the crib after all.

It is pinned to the RAIL height rather than to the body, so an empty tank
standing 55mm higher on its springs does not lift its own shadow with it.

One draw call for the whole railroad. Whole scene at `cam=yard`, ultra: 295
draws · 1.94M triangles, budget 450 / 2.5M.

`node soak.mjs --parses 500 --layouts 10` still passes with every counter zero;
none of this touches the interlocking.

## 2026-08-07, round 6 — the stock was excluded from the shadow pass by one word

The claim to check first was "shadows exist in the scene and the locomotive is
simply excluded". It was true, and the mechanism was not `castShadow`.

**What was already right.** `castShadow` was set on every body and both
instanced pools; the vehicles reached three's shadow pass (`harness/tshadow.mjs`
wraps `renderer.shadowMap.render` and tallies what is submitted); they were in
the near map (`harness/tmapdiff.mjs` reads the 3072² map twice, once with the
trains hidden, and paints the texels that changed — the consists are legible in
it, locomotive and all); and `getShadow` on the receiving ground returns an
articulated train (`harness/tterm.mjs`). Hiding the trains at `cam=yard
time=16` lifts the apron beside one by 36 codes out of 255. **Cascade 0 never
failed.** Three rounds of "set castShadow on the trains" would have found
nothing again.

**What was wrong, in one line of `mat()`:**

```js
transparent: true, opacity: 1, depthWrite: true,
```

with a comment explaining that a train fades out when it is absorbed at the
terminal. Nothing has faded since the railway became a ring — the working comes
home instead — and the flag outlived its animation. gi's `_depthFor` refuses any
`transparent` material a coarse depth material, correctly, so **every vehicle in
the world was refused both coarse cascades.** Cascade 0's box is fitted to the
camera and is a couple of hundred metres across; the site is several times that.
Inside it the stock cast. Outside it — every wide, aerial or long-lens frame the
critics judged — it cast nothing at all, standing on ground the mast beside it
was shading, because the mast is a coarse-cascade caster and the tank car was
not. One word, six rounds.

**The other half of the exclusion.** gi records a module's intent once, as
`userData.lemCastBase`, the first time it sees an object. The adaptive ladder
probes *upward from `floor`*, and `onQuality` used to turn casting off at `low`
and `floor` — so at the moment gi first swept, every train carried
`castShadow = false`, and `lemCastBase` was recorded `false` for the life of the
page. Measured: `base: false` on all 37 vehicle meshes at ultra. Now only
`floor` sheds, the saving at `low` comes out of the running gear instead
(bogies, four instanced draws, 168 instances — a bogie's shadow is detail inside
the body's own), and `_applyCastFlags` writes `lemCastBase` and `lemKeepShadow`
explicitly on every pass rather than letting gi infer them.

## The drawn contact patch is deleted

Last round's `_buildContact` — one soft multiply blob per vehicle — was the
wrong answer to a claim that was half false, and it was itself the long-standing
fault. A/B at `cam=yard time=9` (`harness/tcontact.mjs`): with it on, the
consist wears an amorphous grey wash that does not follow the sun and does not
lengthen at nine in the morning, and there is a **fuzzy dark patch sitting on
open grass with nothing standing on it** — the yard shunt's patch, left where it
was when `_stepShunt` hid the consist at a low tier without zeroing it. "A
hard-edged pure-black quad" and "orphan dark blobs with no visible caster",
reported in six rounds, were this file's own work. It is gone; nothing replaces
it.

## Shadow freshness

The refresh was keyed off "is there a working near the camera", every other
frame. It is now keyed off **metres actually travelled since the map was last
drawn** (`_shadowStep`, 0.35m at ultra/high, 0.7m below). At line speed that is
about every other frame, which is what it cost before; a train standing at a
signal now costs nothing.

## What it measures

Pinned judging shot (`shots/r6-trains-pinned.png`, `cam=street at=multitek-ns
time=16`, ultra): **122 draws · 1.19M triangles · 131 fps · no console errors**,
against 450 / 2.5M. `cam=yard` 184–235 draws, 1.3–1.7M; `cam=wide` 215 / 1.44M;
`weather=overcast` is the worst measured at 345 / 1.98M. Page payload 2.03 MB
of the 8 MB allowance. Gates: `soak --parses 500 --layouts 10` **passes** —
every safety counter 0, arrivals 12, 37,508 m run; `film.mjs --frames 12 --every
1100 --cam yard --time 16` runs clean, 12 frames, no console errors. One InstancedMesh and
its material are gone with the contact patch, and the vehicles have moved out of
the transparent queue into the opaque one.

Acceptance images, all read back: `shots/r6-only9-trains-only.png` and
`r6-only16-trains-only.png` — every caster in the world silenced except the
rolling stock, so every dark mark on the ground was thrown by a train. At 09:00
the consist throws a continuous articulated band along the whole rake with
distinct darker patches under each underframe and a taller shape at the
locomotive. At 16:00 the yard preset looks nearly along the light, so the same
shadow is foreshortened into a narrow band hugging the far side of the consist —
correct, and worth knowing before anyone judges a 16:00 yard frame again.

## Still weak

- **Beyond cascade 0 the stock still casts nothing**, and now for a different
  reason: it enrols in the coarse cascades and is trimmed straight back out,
  because `CSM_MAX_CASTERS[0].ultra` is 104 and the site already fills it
  exactly. See the note appended to `REQUESTS.md`. That trim also re-dirties
  both coarse maps once a second, which is a cost my change introduced.
- `film.mjs` does not pin the quality tier, so a 13-second capture is shot
  mostly at `floor`, where the shadow map is 512 texels over the whole site and
  none of this is visible. `harness/tfilm.mjs` is the same tool pinned to ultra
  and is what the sheets above were read from.
- The tank cars' running gear is visibly wrong at street distance (`/tmp/s1`
  crop of `shots/r6-base-street.png`): the sideframes read as floating silver
  boxes and the wheels are hard to find. That is geometry, not shadow, and it is
  the next thing a close critic will say.

## A concurrent edit to this file landed mid-round

At 14:48 local, while this round was running, `onQuality` changed under me:
`maxActive = {ultra: 6, …}` became `_tierCap = {ultra: 8, …}` plus a new
`_setActive()` that sets `maxActive = clamp(2, roads + 1, cap)`. It is not my
change and I have left it alone.

Three `soak --parses 500 --layouts 10` runs, all on this file, and the middle
one is the interesting one:

| when | collisions | arrivals | verdict |
|---|---|---|---|
| 14:36, before the edit landed | 0 | 8 | fail on `deadRailway`, arrivals alone |
| 14:51, just after it landed    | **20** | 7 | fail |
| 15:0x, same bytes as the 14:51 run | 0 | 12 | **pass** |

So the twenty collisions are **not** reproducible and the 14:51 run should not
be read as a verdict on the new `_setActive`. Two machines' worth of harness
runs were going at once on this box while it ran, and this soak samples in real
time; a starved sampler and a railway that is genuinely running more workings at
once are easy to confuse. It is worth one deliberate re-run on a quiet machine
before anyone concludes either way, because letting `maxActive` reach 8 does
raise the number of consists that can be on the ring together and that is
exactly what the interlocking is for.

Nothing in this round touches traffic in any case: the only state added to
`_step` is `this._shadowLag` and a write to `engine.shadowNeedsUpdate`, both
read-only with respect to the interlocking.

## 2026-08-07 — what a one-way ring changed here

rail.js's network is a ring: no piece of track carries traffic in both
directions, so `rail.runFor()` answers null for every block and the single-line
TOKEN machinery in `_authority` is correct and **inert**. It stays, because the
degenerate circuit a bench with no branch gets is still worked out and back, and
because a token is the right answer whenever single line exists — but on the
network the soak actually drives, nothing takes one. What is left doing the work
is plain block reservation plus the chain rule at junctions. A head-on is not
prevented in this file any more. It is prevented by the shape of the track.

### Three faults the ring's extra traffic exposed

Raising the traffic (below) made the soak report **20 collisions**, all of them
one pair of consists on one loading road closing to 3.9m. It was right to, and
none of the three causes were the interlocking being wrong.

**`CLEAR` was doing a job nobody had named.** It is where a train stands when it
is refused — short of the block joint, not on it — and it was 3.0m, sized to be
"more than one frame of line speed". But the train in front holds the block
ahead and its tail can be anywhere inside it, *including standing exactly on the
joint*, so CLEAR **is** the minimum buffer-to-buffer gap this railway can
produce. 6.5m now, which is an overlap beyond a stop signal rather than a
rounding allowance.

**Blocks are the unit of exclusion, not the unit of distance.** The block on a
loading road is cut 3m ahead of each stand; a locomotive and four tank cars is
84m and the stands are a bay — 90m — apart. So two neighbours at their own
stands stand 3m apart and no block granularity fixes it. `_berth(c)` is the
driver's own rule on top: never close up to within `BERTH` (9m) of the tail of
the train in front **on the same circuit**, measured round the lap. It is only
meaningful between consists sharing a circuit, because that is the only case
where their arc lengths are the same coordinate.

**"Same circuit" is the route object, not the cycle.** Every bench on a road
gets its own cycle record — it carries that bench's own `dockS` — while they all
share one sampled route. The first cut of `_berth` compared cycles, which are
never equal, and the rule silently did nothing: the measured worst gap stayed at
3.04m to the centimetre. Comparing `c.route` took it to **9.50m**
(`harness/gap.mjs`, new — it reports the closest approach on the whole railway
and dumps both consists).

**And nobody is SEATED foul of anybody.** `_seat` puts each working on its own
bench's stand, which is the right answer one train at a time and the wrong one
for a road: it is where the trains were *put*, not a move, so no interlocking
can save it. `_stand` now lays each road out from the exit end backwards and a
train that does not fit at its own stand stands short of it, which is what a
shunter would do with it.

### The traffic cap is the railway's number, not the GPU's

`maxActive` was `{ultra: 4 … floor: 1}`. Both ends of that were wrong. It costs
**nothing in draw calls** — every consist is on the map whether it is moving or
standing at its bench, so a running train is a train that was already being
drawn; a moving one costs a few matrices a frame. What the old number really
stood in for was the single-line trunk: with one road to the terminal worked in
both directions the round trip WAS the headway.

`_setActive()` now takes it from the railway — one working per loading road,
plus one — capped by tier at `{ultra: 8, high: 8, medium: 6, low: 4, floor: 3}`
and never below two. One train on a seven-instrument lab is the map
under-reporting the lab.

That is also what moved the soak's throughput assertion from failing to passing,
and it is worth being honest about the order: the assertion failed first
(`arrivals 7 < layouts 10`), the cap was the thing that was wrong, and shortening
the outbound run by pulling `WX` in 65m (rail.js) was the other half. Journeys
on the eight sane layouts are now 33–54s against the soak's ~58s window; the two
fourteen-bay scatters are 45–105s and still contribute nothing, which is honest
— a site with instruments 1.2km apart has a railway that takes two minutes.

### Measured

`node soak.mjs --parses 500 --layouts 10` → **PASS**: collision 0, reversal 0,
floating 0, unreachable 0, edge 0, consoleErrors 0, **arrivals 12**, 37.5km run,
`maxConcurrent 8`.

`node film.mjs --frames 12 --every 1100 --cam yard --time 16` → 110–132fps,
three to four workings on the road throughout, `shots/r4-film-sheet.png`.
Distance-along-route counts up monotonically in every frame of
`shots/r4-ring/track.json` — a working goes round, it does not come back.

Whole scene at `cam=yard`, ultra: **344 draws · 1.98M triangles**, budget
450 / 2.5M. Geometry untouched by any of this.

### Still weak

1. **`_berth` is O(consists²) per `_permit` call.** Eight consists, so 64
   comparisons a frame per train — nothing at this size, and wrong if the fleet
   ever grows past a few dozen.
2. **A working refused at the berth still holds an active slot** while it
   stands, unless it was refused at `_tryStart`. Correct, but it means a road
   with a slow train on it uses capacity it is not moving anything with.
3. **Nothing routes over the second platform road**, so the terminal is still
   one road deep for arrivals. rail.js's note has the same item.
4. **The run-round path (`turned === false`) is still untested on a real site.**
   No layout in this repo produces a bench without a branch.

## 2026-08-07, round 7 — a consist that flipped, a bench that starved, and lamps

Three operator notes. Two were faults in this file, one was a stale design
decision. The instrument used for each is named with its number.

### 0. The first instrument that lied was `grep`

Before anything else, because it shaped the brief. The round was dispatched with
"trains.js contains NO lighting code at all — zero matches for light, lamp, beam
or emissive". That is false: `command grep -c light trains.js` is **25**. The
shell's `grep` here is a shim that runs `ugrep --ignore-files`, which honours
ignore rules and returned nothing — silently, with exit 1, indistinguishable
from "no matches". Every search in this tree has to go through `command grep`.
This file already had `_devLights`, `_stepLights`, `_buildGlow` and a
`THREE.SpotLight`. Sixth instrument to give a confident wrong answer.

### 1. The green train by LABCORE — it was the yard shunt, and it is deleted

Found before it was touched (`harness/zz-shunt.mjs`, new — polls every consist
at 10Hz and reports arc-length span, world-heading reversals between samples,
and distance to the hub):

| | before | after |
|---|---|---|
| consists | 9 | 8 |
| slot 8 route / consist length | 149.1m / 48.9m | — |
| slot 8 arc span visited | 88.2m of 149m, for ever | — |
| slot 8 heading flips in 45s | **1** | — |
| slot 8 arc jumps / max jump | **1 / 88.2m** | — |
| slot 8 distance to LabCore hub | 39.6m | — |
| heading flips, ALL consists | 1 | **0** |
| arc jumps, ALL consists | 1 | **0** |

Every other consist scored 0 flips and 0 jumps in the same run, which is what
makes the identification conclusive rather than circumstantial.

The flip was a real state-machine fault and not merely an animation nobody
liked. `_stepShunt` turned the trip at the far end with the same route-reversal
`_runRound` uses — correct arithmetic — but `_runRound` spends `TURN` seconds in
a `turn` state doing it and this did it **in one frame**: the head teleported
88.2m back down the array and every vehicle's basis was rebuilt facing the other
way, so a 49m consist swapped end for end between two frames.

It is deleted rather than fixed, on grounds a longer road does not touch: it was
scenery (the only consist on the railway not produced by a parse, against this
file's own promise that "nothing here invents a train that no parser sent"), and
it was **invisible to the interlocking** — `_stepShunt` never called `_permit`
or `_signal`, and `_placeConsist` called `rail.release` for it every frame, so
it held no block and appeared in no lookahead while moving on rail near the
terminal throat.

**It was worth 5 of the soak's 9 `backwardsFrames`.** That is the number the
brief flagged as "a real defect wearing a clean scoreboard", and it was half
right: 9 → 4 on removal. The remaining 4 are in the real workings and are NOT
explained. See "Still weak".

One thing not measured, and it should have been: the shunt's livery was dealt by
`seededRandom('consist/8')` between the two `gp` liveries — ASAP green and LBX
maroon — and the probe recorded the colour but did not print it. So "green" is
consistent with the code and was not confirmed by instrument. The other three
identifiers (never leaves, flips, 40m from LABCORE) were.

### 2. "No way for a train to get out" — dispatch had half of it wrong

`harness/zz-queue.mjs` (new). Fires parses at ONE bench — the one deepest in its
road's queue — and watches 200s. The measured layout put `koehler-cp` 268.8m
behind the exit-end stand with four trains in front of it.

| | before | after |
|---|---|---|
| koehler-cp backlog over 200s | 10 → **95, never drained once** | 23→5 and 40→1, **drained twice** |
| its peak backlog | 95 | 40 |
| its own train departed? | yes, t=155s — **carrying another bench's traffic** | yes, carrying its own |
| consists that departed at all | 6 of 7 | 6 of 7 |
| active workings | 3/3 at every sample | 3/3 at every sample |

The cause was `_wantFor` preferring the **nearest** booked bench. A train may
only ever leave from the exit end of the road — `_stepIdle` closes the queue up
that way and the interlocking will not let anything else out — so by the time a
train is *allowed* to move it is by construction standing a long way from its
own bench. "Nearest booked bench" therefore resolved to "whichever bench is near
the exit", and the bench with the most work waiting was the least likely to be
served. The deeper its queue, the worse its odds.

It is longest-book-first now, with distance demoted to a tiebreak between
benches holding the same number. That has the property proximity has not: a
starved bench's book only grows, so it becomes the largest, so it is served —
there is no ordering of prints that starves a bench. The ordinary case (one
print, one road, one train) is unchanged, because then only one bench has a book
and both rules pick it. It touches no movement and no reservation; it only picks
which uid a departure is booked to.

**What this does NOT do, said plainly.** It does not clear the queue, it holds
it. On a one-way single line with N stands in series, if B is behind A then no
pathing, scheduling or reservation gets B out before A — only metal does. The
traffic moves; the vehicle does not, and `active 3/3` at every sample of both
runs is the ceiling that produces. The geometry that would actually clear it —
intermediate trailing turnouts from the loading road back on to the row's
branch, mid-rank — is written up in `REQUESTS.md` with the two constraints
rail.js could not be expected to know: variants must be identical over the
loading road (or `_berth` compares arc lengths on different curves and silently
does nothing, the bug that produced 3.04m gaps), and each variant needs its own
`line` name (or `soak.mjs`'s one-line interval test compares incomparable arc
lengths and invents collisions on a sound railway). No speculative consumer code
was written against an interface that does not exist yet.

### 3. Headlights, through gi's pool

Before: **one** `THREE.SpotLight`, created once and ridden by whichever working
was nearest the camera. The operator's "only one train's light can render at a
time" was literally true and it was a choice — the reasoning above `_buildGlow`
(adding a light mid-run recompiles every material in the scene) is still sound
and is exactly the problem gi's fixed-size pool solves.

Every locomotive now asks `gi.requestLight` for a lamp, positioned out along its
own beam so it pools on the railhead rather than on the nose it is bolted to.
`harness/zz-lamps.mjs` (new), time=21, tier pinned per page load:

| tier | gi pool | train lamps live | refused lamps fall back to |
|---|---|---|---|
| ultra | 10 | **7** | — |
| low | 3 | **3** (both moving workings + 1) | lens at 0.405 (up from 0.30) |
| floor | **0** | **0** | working locos at 1.0, stabled at 0.405 |

So: 1 → 7 lamps lighting the world at once, and at the floor tier `.active` is
honestly false for all eight and the additive lens carries every one of them —
which is what the API is for. `alwaysOn` is left false so gi's `artificialFactor`
does the day/night gate and a lamp asked for at noon costs no slot at all.
Priority is 3 for a working under power, 1 for one standing at a signal, 0 for
one stabled at its bench, so at `low` the two moving workings took the top two
slots — measured, not asserted. Handles are released in `dispose()`; a request
outlives the module that made it and would otherwise burn a pool slot for ever
at the last place a train stood.

The one SpotLight stays, on the nearest working, for the directional throw a
point light cannot give.

### Gates

| instrument | before | after |
|---|---|---|
| `soak --parses 500 --layouts 6` | **PASS**, all counters 0 | **PASS**, all counters 0 (run twice) |
| soak backwardsFrames | 9 | **4** |
| soak maxConcurrent / arrivals | 8 / 9 | 7 / 9 |
| `pytest tests/ -q` | 1043 passed, 7 skipped | **1043 passed, 7 skipped** |
| `film.mjs --out /tmp/tr --frames 12` | — | 12 frames, 120–122fps, `errors: []`, arc length monotonic in every frame for both workings, no `shunt` state |
| night shot, `cam=yard time=21 ultra` | — | 230 draws / 1.44M tris, `errors: []`, `settled: true` |
| night shot, `cam=street time=23 ultra` | — | 188 draws / 1.40M tris, `errors: []`, `settled: true` |

Budget is 450 draws / 2.5M triangles. Removing the shunt takes three meshes, six
sideframes and twelve wheelsets out of the scene.

### Still weak

1. **`buildStable` is `false` on every shot taken this round.** Three other
   rounds were writing `rail.js`, `terrain.js`, `vegetation.js` and `engine.js`
   throughout; four capture attempts, all unstable. The frames are honest
   individually (`settled: true`, `errors: []`) but **must not be compared
   against any other frame**, and the same caveat applies to the soaks, which
   load the page six times over ten minutes. The direction of every number
   matches what the change predicts, which is why the attribution stands.
2. **4 `backwardsFrames` are unexplained.** They are in the real workings, not
   the shunt, and they do not trip the `reversal` counter (which needs 20
   consecutive). Roughly one per relayout, which points at `_tryStart` →
   `_seat` re-seating a consist to `parkS` in the same frame `_dispatch` sets
   its state to `out`, so the soak's next sample sees a non-idle train whose `s`
   went down. Hypothesis, not measurement — I did not instrument it.
3. **The traffic cap was not touched.** `maxActive = roads + 1` = 3 on this
   site, and it was saturated at every sample of both queue runs, so it — not
   the interlocking — is what decides how many trains an operator sees moving.
   Raising it is the change these notes already record as coinciding with 20
   collisions, and it needs its own soak rather than being bundled with three
   other changes into one.
4. **Parked locomotives now show their lamps at night** (glow opacity 0.30
   against a working train's 0.80). Judged by eye from two frames, not measured;
   a yard with seven stabled trains has seven faint beam cones in it and nobody
   has looked at that case.
