# gi.js — round 8 (shadows/grade) and round 9 (the no-GI rung)

---

# Round 9 — "the floor tier gets no global illumination at all"

Ryan: "maybe floor can have no lighting at all (like GI, it can still have like
a rudimentary emission system and all that but no shadows or complex lighting)."

engine.js publishes it on the tier and gi.js now reads it off the tier object
rather than off `tier.name`:

    floor: { shadows: false, ao: false, bloom: false,
             lighting: 0.00, gi: false, emissiveOnly: true }

## What "no GI" was made to mean

The brief's own words: *a different code path, not the same one turned down.*
Two helpers at the top of the file decide it — `giOff(tier)` (`gi === false` or
`lighting <= 0`) and `lightingBudget(tier)` — and one flag, `this._flat`, is
read everywhere it matters. When it is set, none of the following is *built*:

| | lit tiers | `_flat` |
|---|---|---|
| probe grid | 1000–2000 L1 probes, 3 half-float 3D textures, per-frame relight slice | **none** — no grid, no trace, no `sampler3D` in any shader |
| coarse cascades | 1–2 × 2048² RGBA + depth, one redraw ≈100 draws/s | **none allocated** |
| screen-space AO | 4 taps + contact bite on direct | **not compiled in** |
| point lights | 3–10 pooled `PointLight`s | **0** — `NUM_POINT_LIGHTS` compiles out |
| environment | PMREM cube + cube-UV fetch per fragment | **ours dropped**; specular from the flat hemisphere |
| shadow ortho | fit + texel-snap every frame | **not fitted** |
| near cull | sweep of every instance matrix, 4× a second | **not run** |
| meter | fullscreen pass + `readRenderTargetPixels` 6×/s | **not run**; analytic exposure only |

`update()` has a separate early branch for it that does two things — the
exposure and the once-a-second material adoption — and returns. What is left in
the shader under `LEM_GI_FLAT` is one `mix()` between a sky colour and a
bounced-ground colour by the normal's `y`, plus emissive.

**The hemisphere split is kept on purpose.** A single constant ambient makes the
north wall of a shed exactly as bright as its roof, which is the most
recognisable tell of a toy renderer and costs nothing to avoid.

**Specular does not disappear with the cube.** `_ensureEnvironment` argues
correctly that a world with no environment map turns every metal surface matte
black, and on this site that is the tank farm, the railheads and the gantry
steel. So `GI_IBL` gains a `LEM_GI_FLAT` branch that evaluates the same
two-colour hemisphere along the reflection vector — one `mix`, no texture — and
`lemFlatSpec` is 0 whenever an environment map is still standing, so the sky is
never counted twice. **In production it is currently 0**, because sky.js owns
`scene.environment` (sky.js:1687) and gi.js does not remove another module's;
filed in `REQUESTS.md`.

**Emissive is the rudimentary emission system.** With no bloom and no point
pool, a lit window is the only thing in the frame that can say "on", and both
of the things that used to sell it — the halation and the pool of light on the
ground — are gone. `lemEmissiveGain` (1.85) multiplies `totalEmissiveRadiance`
in the shader, not `material.emissiveIntensity`, because buildings.js drives
that property every frame from its own night curve.

## `gi: false` cannot leave the world black

`_fitFlatAmbient` replaces `_fitFill` on this path and is a different fit, not
a scaled one. The lit path holds the fill at **0.21** of the sun so a cast
shadow has two and a half stops to fall through. With `shadows: false` there is
no cast shadow to protect and no occlusion to take anything away, so the ambient
*is* the whole picture of every surface the sun does not face:

* `FLAT_FILL_RATIO = 0.78` of the key, and
* `FLAT_FILL_MIN = 0.135` absolute — above `_fitFill`'s own night floor of
  0.055, because that one has a probe-lit world underneath it and this has
  nothing underneath it at all. At 03:00 under a storm this is what is left,
  and an operator still has to read a status board by it.

Entering the tier also **forgets the meter** (`_sceneEV`, `_sceneEVLow`). The
meter stops running, and `_applyGrade` would otherwise go on placing the
exposure and the black point from whatever the frame happened to be at the
instant the tier stepped down, for the rest of the session.

## The other tiers now read `lighting`, and spend it on work

`lighting` is a budget, not a dimmer — nothing multiplies a colour by it,
because a tier step that darkened the world would read as dusk falling rather
than as a setting changing. What it buys:

| | ultra 1.00 | high 0.90 | medium 0.70 | low 0.45 |
|---|---|---|---|---|
| coarse cascade refresh | 0.90 s | 1.00 s | 1.29 s | 2.00 s |
| meter readback interval | 0.16 s | 0.18 s | 0.23 s | 0.36 s |
| probes relit per frame (trace / relight) | 220 / 900 | 198 / 810 | 154 / 630 | 99 / 405 |
| AO drive `1.15·(0.55+0.45L)` | 1.15 | 1.13 | 1.06 | 0.98 |

Each of those costs *latency in the light's response to a change*, which is
invisible on a floor display, rather than costing the picture. The meter one is
the most valuable: the readback is the only thing in this file that makes the
CPU wait for the GPU, and on a part with no async readback path it is a full
pipeline flush.

## A real bug found on the way: the ladder climbs OUT of the floor tier

`_adoptShadow` decides an object's `castShadow` exactly once in its life — which
is right for a flag another module owns — and the adaptive ladder now *starts*
at the floor tier and climbs. So every object the world contained at boot was
being permanently marked "never casts", at every tier, for the rest of the
session, on any machine that booted at the bottom. Silent, permanent, and
attributable to nothing.

Objects suppressed that way now go on `_flatAdopted` with the wanted answer on
`userData.lemCastWanted`, and `_restoreShadowFlags` replays them on the way up —
including `lemCastBase`, which is what the near cull and both coarse cascades
read instead of the live flag, and without which the restore would be half done
(casting into three's map for ever, enrolled in neither cascade).
`giaccept.mjs` steps ultra → floor → ultra in one session and counts casters
afterwards, so a regression here shows up as a number.

## New harnesses

* `harness/giaccept.mjs` — both tiers on **one** world: load, shoot `ultra`,
  step the engine to `floor` in place, shoot, step back to `ultra`, shoot,
  count casters. Two separate page loads are not comparable while three other
  modules are being rewritten in the same hour.
* `harness/giflat.mjs` — the A/B this round needed and no sidecar can give:
  hold one page at `floor`, sample it, then turn `_flat` back off in place and
  sample again. Nothing removed this round was a draw call, so draws and
  triangles barely move; what moved is per-frame CPU and one pipeline stall.
* `harness/settle.sh` — shoot and grade until the site actually built.

## Measured

`harness/gitier.mjs`, one page, stepping ultra → high → medium → low → floor →
ultra. `cam=yard`, `time=16`, clear, 1920×1080, zero page errors:

| tier | lighting | probes | cascades | point lights | AO drive | shadow map | fill (`lemGIStrength`) | draws | triangles |
|---|---|---|---|---|---|---|---|---|---|
| ultra | 1.00 | 3380 | 2 | 10 | 1.150 | on | 0.152 | 330 | 1.65 M |
| high | 0.90 | 2420 | 2 | 8 | 1.098 | on | 0.152 | 338 | 1.66 M |
| medium | 0.70 | 1024 | 1 | 6 | 0.995 | on | 0.152 | 332 | 1.57 M |
| low | 0.45 | 363 | 0 | 3 | 0 | on | 0.152 | 302 | 1.29 M |
| **floor** | **0.00** | **0** | **0** | **0** | **0** | **off** | **0.388** | **155** | **0.94 M** |
| ultra again | 1.00 | 3380 | 2 | 10 | 1.150 | on | 0.152 | 330 | 1.65 M |

Against the 450 draw / 2.5 M budget: **ultra 330 / 1.65 M, floor 155 / 0.94 M**
— the bottom rung is 53% of the draw calls and 57% of the triangles.
`LEM_GI_FLAT` is stamped on all 109 registered materials at the floor tier and
on none at any other. `emissiveGain` 1.85 at the floor tier, 1 elsewhere.
Exposure 4.00 → 1.63 (the flat fill is 2.55× the lit path's, and the analytic
model stops down for it rather than the frame going up a stop).

**`castersAfterClimb: 177`** — the same count as before the descent, i.e. the
shadow-flag restore works. Without it that number is 140 for ever.

`harness/giwarn.mjs` steps ultra → floor → medium → floor → high and collects
every `[gi]` console message and page error: **empty**. That is the check that
matters most for the new `#include <emissivemap_fragment>` anchor — a splice
that misses compiles cleanly and silently applies nothing.

Grade, `cam=yard t16` clear, on a correctly-built site:

| frame | meanL | σ | p1 | p50 | p95 |
|---|---|---|---|---|---|
| ultra | 116.2 | 55.7 | 22 | 117 | 218 |
| floor, before this round | 115.6 | 52.7 | 19 | 114 | 216 |
| floor, this round | 123.7 | 40.2 | **27** | 118 | 201 |

A tenth of a stop brighter overall, the shadow end lifted from 19 to 27 (the
"no black holes" requirement), the highlight end pulled in from 216 to 201, and
σ down from 53 to 40 — which is the whole point: **flatter, and legible**.

## Acceptance — `shots/r8-accept/`, one page, ultra → floor → ultra

| | draws | triangles | fps | p95 ms | meanL | σ | p1 | p95 |
|---|---|---|---|---|---|---|---|---|
| `ultra.png` | 311 | 2.09 M | 120 | 9.3 | 119.6 | 64.2 | 18 | 227 |
| `floor.png` | **142** | **1.22 M** | 120 | 9.3 | 99.4 | 50.2 | **17** | 201 |
| `ultra-again.png` | 303 | 2.08 M | 120 | 9.5 | 119.5 | 64.4 | 18 | 227 |

Zero console errors. Against 450 draws / 2.5 M: the floor tier is **46% of the
draw calls and 58% of the triangles** of ultra.

**What I can see in the floor frame.** The forest is intact — the same
foreground conifer and birch, the same stand across the right of the frame, the
same treeline on the ridge; `trees` only falls 1.00 → 0.90 and it shows as
nothing. Every instrument is identifiable: the brick block with its window
grid, the stack behind it, the second shed and its gantry, the shed at frame
left, the platform canopies, the tank cars and the green locomotive on the
running line, the ballast shoulder and both tracks. The ground keeps its
texture — grass tufts, the dry patch, the embankment. **Nothing in the frame is
black**: p1 is 17, against 18 at ultra.

**What is gone, and is supposed to be.** Every cast shadow. In `ultra.png` the
stand on the right throws a long articulated shadow across the field and the
foreground trunk lays one over the grass; in `floor.png` that ground is evenly
lit and the trees sit on nothing. Contact darkening at the rail feet and under
the platform edge is gone with the AO. The frame is 0.27 stop darker and σ
falls from 64 to 50 — flatter, exactly as asked, and still readable at every
point a status board needs to be.

`ultra-again.png` is the same image as `ultra.png` to within a code
(119.5/64.4/18/128/227 against 119.6/64.2/18/128/227), tree shadows and all —
the descent to the flat rung is fully reversible.

## What the flat path actually saves, A/B on one page

`harness/giflat.mjs`, `cam=yard t16`, 500 frames each half, `_flat` turned back
off in place between them so the world, the camera and the driver state are
identical:

| | `gi: false` honoured | same tier, machinery left on |
|---|---|---|
| `gi.update` self-time | **0.0122 ms/frame** | 0.0494 ms/frame |
| draw calls | 154 | 154 |
| triangles | 1.675 M | 1.675 M |
| frame p95 | 9.2 ms | 9.4 ms |

**Four times less main-thread time in this module**, and the draw and triangle
counts are byte-identical — which is the point worth remembering: *nothing this
round removed was a draw call*. It was the shadow ortho fit and texel snap every
frame, the quarter-second sweep over every instance matrix on the site, the
probe relight slice, and the meter's `readRenderTargetPixels`. On an M5 Max that
is 0.037 ms; the two costs that do not appear in this number at all are the
readback's pipeline fence (a GPU stall, not CPU time) and the PMREM, and both
are worst on exactly the integrated part this rung exists for.

This A/B does **not** include the coarse cascades, because `CSM_BY_TIER.floor`
was already empty before this round — the floor tier never rendered one. The
cascade saving is real but it is older than this change.

## The gates

* `film.mjs --frames 9 --every 1200 --cam yard --time 16` (ultra) — **PASS**,
  9 frames, 3 workings running, 72–102 fps, zero errors, `r8-film-sheet.png`
  shows a coherent site with the forest standing and tree shadows on the field.
* `soak.mjs --parses 200 --layouts 4` — **PASS at 16:52** (collision 0,
  reversal 0, floating 0, unreachable 0, edge 0, consoleErrors 0). Re-run at
  17:15 it **FAILS on 17 `edge` faults and nothing else**, consoleErrors 0: the
  terrain rewrite that landed in between put 26–41 m steps in `heightAt` at
  r = 880–1740 m. `soak` walks the height function, which gi.js does not touch.
  Filed in `REQUESTS.md`; every agent's gate is currently failing on it.

## Still weak

* **The flat specular path is not exercised in production.** sky.js owns
  `scene.environment`, so `lemFlatSpec` is 0 and the floor tier still pays for
  the PMREM and the cube-UV fetch. Filed; one line in sky.js recovers it.
* **The sun is kept at the flat tier.** `lighting: 0.00` and `emissiveOnly` can
  be read as "no directional light either", and that was tried on paper and
  rejected: with no key at all a tank car, a storage tank and a shed wall are
  one flat silhouette each, and the acceptance asks for every instrument to be
  identifiable. What is off is everything the sun *costs* — its shadow map, its
  ortho fit, its cull. This is the one judgement call in the round and it is the
  one to revisit first if Ryan meant it literally.
* Round 8's open items are unchanged: no true black in the composite (the lift
  is the floor, at 18/255), the key-to-fill is still steep on the lit path, no
  cascade past ~1.6 km, and terrain does not cast into three's near map.

---

# Round 8 — "a train drags an amorphous dark blob instead of a train-shaped shadow"

It does, and the cause is not in the shadow map. Two things, both measured.

New harness: `harness/giblob.mjs` (freeze a moving consist, then re-shoot the
same frame with one lighting contributor removed at a time), `harness/gishade.mjs`
(raycast a pixel and read back every term that decides its colour), and
`harness/gicost.mjs` (draws/triangles sampled over a working railway instead of
one end-of-run snapshot, which swings by a factor of two on this scene).

## What the blob actually is, proved one contributor at a time

Filmed the yard at `time=16` and froze the consist, then A/B'd at pixel level
(`shots/giblob*`, luminance sampled at the same points in every variant):

| variant | shaded ground A | shaded ground B |
|---|---|---|
| base | **10** | **12** |
| coarse cascades off (`lemCsmReady`=0) | 10 | 12 |
| AO buffer off (`lemAOStrength`/`lemAOContact`=0) | 10 | 11 |
| every caster in the scene `castShadow=false` | 32 | 95 |
| three's own map off (`sun.castShadow=false`) | 34 | 96 |

So it *is* a cast shadow, out of three's near map — not the coarse cascades, not
AO, not a blob decal (there is none anywhere in `world/`). Attributing it by
subsystem gave: point A is **rail's** shade, point B is **the train's own**.

**Two traps that made three earlier rounds of this measurement wrong.** First,
`_nearCull` rewrites `castShadow` on everything in the box every quarter second,
so any experiment that clears the flag is silently undone before the screenshot
— every per-subsystem A/B reads as null until it is stubbed out. Second,
`scene.traverse(o => o.castShadow = false)` also hits the DirectionalLight, so
"all casters off" quietly becomes "no shadow map", which is a different test.

**The mechanism.** The composite does `c = max((c - uBlackPoint)/(…), 0)` — a
hard clip. Anything below the black point is not darkened, it is deleted, and
`uLift` then paints the whole deleted set one flat colour. `_applyGrade` was
placing the black point at 0.80 of the tone-mapped fifth-percentile meter tile,
which on the yard frame landed at 0.0146, **above the shaded ground**. Measured
on the frame the brief complained about: **8.7% of it sat inside a six-code band
and p0.5 = p1 = p2 = 10, exactly `uLift`.** A locomotive's shadow, the 22 cm gap
between two wagons, the shade on an embankment and a cast shadow on grass were
four different scene luminances all encoding to the same grey. The shadow map
resolves 5 cm; the grade was throwing the picture of it away. That is, I think,
the same fault behind "a broad soft dark region with no caster to explain it"
and "caster-less soft dark blobs" in five earlier rounds. There was a caster.

The second half is resolution. `_fitShadow` set the sharp box to `dist * 0.80`,
which at `cam=yard` is a radius-80 box at a 5.2 cm texel — and handed everything
past 84 m to cascade 0 at **28 cm**. The consists run at 78–95 m, i.e. straight
through the handover band, so half a train was shadowed at 5 cm and half at 28.

## What changed in gi.js

1. **`uBlackPoint` moves under the shadow tone instead of onto it** — 0.25 of
   the measured fifth percentile, clamped 0.001–0.020 (was 0.80, clamped
   0.002–0.055).
2. **`uLift` 0.0035 → 0.0060.** The lift is now what sets the floor. 0.0035
   encoded to 10/255; the references hold p1 at 17 (Transport Fever 2) and 21
   (After the Flood) *with texture in it*, and 0.0060 encodes to 18.
3. **The sharp shadow box fills the cap it was sized for** — `far = clamp(dist *
   1.45, 60, 168)`. At `cam=yard` that is a radius-144 box at 9.4 cm instead of
   radius-80 at 5.2, and the mid-ground stops being a 28 cm cascade.

## Measured

| | before | after |
|---|---|---|
| yard t16 p0.5 / p1 / p5 | 10 / 10 / 12 | **18 / 21 / 32** |
| % of frame in codes 8–13 | **8.7%** | **0.0%** |
| draws mean / worst (yard, parses running) | 254 / 304 | **262 / 355** |
| triangles mean / worst | 1.69 M / 1.94 M | 1.62 M / **2.01 M** |
| fps mean / min | 121 / 115 | 120 / 109 |

Against 450 draws / 2.5 M. Across `wide 16`, `street 11`, `low 17.5`,
`yard 12.5`, `yard 21`, `yard 16 overcast`: worst 361 draws / 2.02 M, fps
117–120, **zero console errors** in every run. p1 lands 19–25 and p5 26–36 by
hour, against the references' 17–21 / 28. `soak.mjs --parses 120 --layouts 3`
**PASS**, 0 console errors.

## Acceptance — what I can see in the sheets

`shots/gi-film16b-sheet.png` (yard, t=16, twelve frames while the railway works)
against the original `shots/film-yard-sheet.png`. In `frame-09` at 3× the
consist's shadow on the grass to its lower right is **articulated**: the
locomotive's shadow is a long low hood with a raised cab block and the stack
standing off it, plainly a different profile from the two rounded barrel forms
behind it; the shadow narrows twice at the coupled gaps; each vehicle's trucks
read as small dark blocks along the shadow's lower edge; the leading tank's head
tapers. In `frame-01` the same shadow falls down the embankment face and the
tank car's ladder is legible in it. The embankment ballast under it is now a
readable dark grey stone band rather than a void.

**At `time=9` there is nothing to see, and it is not the renderer.** Measured,
sun direction against the yard camera's own view vector: at 9 the sun is at
azimuth +124.5° and the camera looks at −126.1°, i.e. the sun is **112° from the
view direction — behind the camera's shoulder**. Every shadow in that frame
falls away from the viewer and hides behind its own caster, and no lighting
change can put one there. `shots/gi-film09-sheet.png` shows exactly that: a
bright, near-shadowless frame. The side-lit hour at this camera is **11:00**
(85° from view). Filed in `REQUESTS.md`; it is the same trap CLAUDE.md already
records for `time=13`.

## Still weak

- **The frame lost its true black.** With the black point at a quarter of the
  shadow tone nothing is clipped, but nothing reaches 0 either — the floor is
  the lift, at 18/255, everywhere. The right fix is a soft shoulder in the
  composite instead of `max(…, 0)`; filed in `REQUESTS.md` with the one-line
  change. Until then this is a workaround with the correct histogram.
- **The key-to-fill is untouched and still steep.** `_fitFill` delivers indirect
  0.18 against a sun of 2.19 at 16:00 (12:1 in irradiance). It reads acceptably
  now only because the toe is no longer eating it. Worth re-fitting against the
  references once the grade settles — one variable at a time, and this round's
  was the grade.
- **The far field past ~1.6 km still has no cascade**, and terrain still does
  not cast into three's near map. Both unchanged from round 7.
- **The world moved under the last hour of this work.** `rail.js`,
  `vegetation.js`, `index.js` and `engine.js` were all rewritten by other agents
  between 13:40 and 13:55; the site currently has no branch line past the yard
  camera and the treeline renders with blue speckle. Every number and every
  frame quoted above was taken before that, on one consistent world, with both
  of this round's changes in. The A/B mechanism does not depend on the layout,
  but the acceptance films should be retaken once the site settles.
