# sky.js — round two

Round one lost all four blind critics. Every one of them said something about
this file: "a flat gradient with no cloud, no sun disc, yet p95=227", "the haze
is a uniform global veil, not depth-graded", "vertical column striping and
horizontal banding through the fog band", "sky is a bare gradient with banding".
This is what changed.

## 1. The horizon was khaki because the integral did not converge

The single most important fix, and it is a numerical one. `scatter()` marched
fourteen *even* steps to the top of the atmosphere. A ray three degrees above
the horizon runs three hundred kilometres, so the first sample landed ten
kilometres up — past the 8 km scale height — carrying a twenty-kilometre slab of
optical depth. Blue was over-extinguished, red survived, and a clear midday
horizon came out desert-brown. Everything downstream inherited it: the fog is
the horizon, so the fog was brown too, and `flatten()` and the overcast damping
were all quietly compensating for a broken integral.

Both marches now step on a curve — view parameter cubed, light parameter squared
— at the same step count. Measured on the CPU mirror at 13:00, the anti-solar
horizon went from B−R **−14** to **+9**, and the 0.5° band from **−82** to
**−10**. Nothing else in this file would have worked until that was right.

With the integral converging, three constants moved: `mieBase` 0.35 → 1.0 (a
forested valley floor, not a mountaintop — the grey it scatters is what keeps
the zenith off the black point), `ms` 0.05 → 0.10, and a new `msTintPow` 0.45
weighting the multiple-scatter source by the Rayleigh cross-section, because
bounced light is sky light and sky light is blue.

## 2. The sky is drawn at its own stop; the light is not

`sunI` is chosen so a lit surface lands where the composite wants it, and the
composite has one exposure for a scene running noon to midnight. At that
exposure the sky came out three stops hot — the horizon integrated past 1.0
linear, ACES flattened it toward white, and the fog derived from it became the
white veil four critics saw.

So the dome and `scene.fog` are drawn at `A.skyStop` (0.32, opening to 0.70 as
the sun goes under, because one stop cannot hold both a noon horizon and a
golden-hour zenith). **`sunIntensity`, `sunColour`, `ambientColour` and the
PMREM environment map are untouched at the physical scale** — the env dome is a
second material sharing every uniform object except its own stop — so gi.js and
everyone's materials see exactly what they saw before. A highlight
desaturation, mirrored on the CPU for the fog colour, takes the low-sun horizon
band to cream instead of leaving it a saturated orange stripe to clipping.

Measured, sky alone at cam=low: mean RGB 67/97/111, B−R **+43.4**, sat 41%,
meanL 92, p95 139 — against `aftertheflood-12`'s 65/86/108, +42.2, 42.6, 83,
162. Before: 171/181/163, B−R −7.9, meanL 178.

## 3. Aerial perspective, in everyone's shader

`THREE.ShaderChunk`'s four fog chunks are rewritten once in `build()`, before
any other subsystem exists. `FogExp2` is one global density with no ceiling —
the same air over the ridge as in the valley, running all the way to opaque.
Now: density falls off with height (closed form along the ray, exact for an
exponential profile, two exponentials), and the factor saturates at **86%** so
the far field flattens instead of dissolving. At the camera's altitude with no
height difference it reduces exactly to three's own curve.

Clear air now costs ~8% at 500 m, ~50% at 1500 m, and stops. `shots/v7-all-low-14.png`
is the result: saturated foreground grass, valley mist under a graded treeline,
and three layers of ridge receding into blue behind it — `tf2-12`'s composition,
which is what critic two said we could not do.

## 4. Banding, striping, clouds, sun

- **LUT 256×128 → 512×256**, and the fetch is jittered by up to a texel with
  interleaved gradient noise. Bilinear reconstruction of a steeply curved
  function jumps slope at every texel boundary and the eye reads those jumps as
  Mach bands — vertical down the azimuth texels, horizontal across elevation.
  That is the striping the critic found.
- **The horizontal dashes at the horizon** were the cloud slab seen edge-on:
  within a couple of degrees the ray enters the deck fifty to two hundred km out
  and crosses a sliver of it, sampling features far smaller than a step. Faded
  out past 26 km now. The noise is also sampled against altitude rather than
  planet-centred Y, which was spending the whole float on the earth's radius.
- **Clouds.** The horizon haze started at 0.30 — seventeen degrees — and was
  deleting the entire layer at every camera angle the floor uses; it now lives
  in the last few degrees. Coverage has a floor (a clear afternoon is cumulus,
  not an empty sky), the fair-weather base dropped 1750 m → 900 so they subtend
  something, feature scale roughly halved so several are in frame rather than
  one or none, and bases are lit at a seventh of the sky term and cooled, so
  they have bruised grey bottoms and lit tops. An overcast deck gets a much
  higher floor and a diffuse-transmission floor on its self-shadow, or one cloud
  from horizon to horizon comes out black.
- **The sun.** Disc and aureole were already right; added a third, much wider
  and fainter glow so the key light has a visible source even when the disc is
  out of frame. `shots/v7-sun19.png` is the disc on the horizon with clouds
  silhouetted in front of it.
- **Night.** At 21:00 every channel measured exactly 0 — the composite's black
  point of 0.035 ate the physical airglow floor whole. `A.nightGlow` is lifted
  about six times, which is a fudge in this file compensating for a number in
  engine.js (logged in REQUESTS.md). Night now reads deep blue with stars and
  silhouetted cloud: meanL 12.7, p95 30.

## Cost

| | draws | triangles |
|---|---|---|
| sky alone, before | 11 | 2218 |
| sky alone, after | 11 | 5050 |

**+0 draw calls, +2832 triangles** (the dome went 48×24 → 72×36 to cut the
interpolation error in the view direction). Whole scene with gi+terrain+vegetation
measures 55-63 draws / 277-394k triangles, well inside 450 / 2.5M. The 512×256
LUT is four times the pixels of the old one but only redraws when the sun or the
weather moves — never per frame. Per frame the dome is one texture fetch, a sun
disc and the cloud march, unchanged in step count.

## Verified

`mods=sky` and `mods=sky,gi,terrain,vegetation`, cam `low` and `wide`, times 7 /
13 / 18.5 / 21, weather clear and overcast. **Zero console errors in every run**
— which matters more than usual here, because the ShaderChunk patch compiles
into terrain's, vegetation's and gi's materials as well as three's own.

## Still weak

- **Overcast at midday is darker than it should be** (meanL 61, p95 113). The
  deck reads as a storm lid rather than the bright grey one an overcast noon
  actually has. The light reaching a deck's underside is transported through a
  kilometre of cloud and this model only fakes it with a constant floor.
- **Golden hour passes through green** on its way from a warm horizon to a blue
  zenith. Real dusk goes through pink, and the authored twilight layer that
  would supply it only switches on below 4.5°, so 18:30 misses it.
- **The fog colour fight is unresolved** — terrain.js and weather.js both still
  overwrite it after me, and they run later. Everything above assumes the sky's
  own value survives. See REQUESTS.md.
- **The below-horizon dome fill** is graded now rather than a flat slab, but in
  a wide shot where terrain runs out it is still a large low-detail area.
- **`A.nightGlow` is a fudge**, and should come back out if the composite ever
  grows a time-of-day exposure.

---

# sky.js — round three

Round two lost 4-0 again, and the critics repeated round one almost verbatim
about this file: uniform haze across depth, far field dissolving to one cream
value, sky "empty and featureless", "clipped to pure white".

**First: what was actually true.** The round-two aerial-perspective fog patch
*was* reaching the frame — `harness/fogprobe.mjs` pulls the compiled fragment
source out of the live renderer and all five fogged programs carry `lemTau`, so
the height-falloff integral and the 86% cap were both live. The defect was not a
patch that failed to apply. It was exposure. Measured at 13:00/`cam=low`:

| band | round two | round three | tf2-12 (ref) |
|---|---|---|---|
| upper sky | L 205, B−R +19 | L 136, **B−R +63** | L 126, B−R +76 |
| low sky | L 217, B−R +10 | L 146, B−R +50 | L 125, B−R +82 |
| far ridge | L 190, B−R +20 | L 109, **B−R +62** | L 152, B−R +52 |
| treeline | L 176, B−R +13 | L 98, B−R +56 | — |
| mid-ground | L 103, B−R −21 | L 77, B−R −8 | L 102, B−R +10 |

Whole frame went 138.0 / σ 63.7 / p1 0 / p95 218 / B−R −4 to **98.0 / σ 46.0 /
p1 10 / p95 179 / B−R +19** — bracketed by tf2-07 (78.9/48.5/15/174) and tf2-03
(108.6/56.6/12/178) on every statistic.

## What changed

1. **`mieBase` 1.0 → 0.42.** Mie scattering is achromatic and forward-peaked, so
   a high aerosol load whitens the *horizon* far faster than the zenith. At 1.0
   the low sky measured B−R +10 — white — and since the fog colour is the
   horizon colour by construction, every distant object faded into white. `ms`
   0.10 → 0.11 and `msTintPow` 0.45 → 0.75 pay back the fill that removing the
   aerosol cost, in the isotropic term where it does not touch the horizon.
2. **`skyStop` 0.32 → 0.088.** The sky was sitting on the shoulder of the ACES
   curve, where a two-to-one radiance ratio survives as four bytes. That is why
   nothing in it had a ladder: cloud tops were ten bytes off the sky, the ridge
   twenty-seven off the horizon. `duskStop` 2.2 → 4.8 and `nightGlow` ×1.5 keep
   twilight and the night floor where they were.
3. **Chromatic fog.** `fogFactor` is now a `vec3` — Rayleigh cross-sections
   normalised and pulled 55% toward grey, squared by the exp2 form to ~2:1
   blue-over-red. Value alone could not carry distance through that tone curve;
   hue can. This is what turns the far ridge blue instead of pale.
4. **Cloud multiple scattering**, three octaves (decaying contribution, decaying
   extinction, phase walked toward isotropic). Costs three exponentials per
   marched sample and *saves* two `pow` calls, because `mu` is constant along a
   ray so the phase values are now computed once per pixel. Density +75%,
   coverage floor 0.28 → 0.10 so a fair day is broken cumulus with blue gaps
   rather than a lid over a hard-shadowed ground.
5. **Sky shoulder** (`uSkyCeil`), applied to the visible dome and to the fog
   colour, switched off for the environment dome. It is inert at every daytime
   sun elevation — measured identical from 0 to 3.5 at 14:00 — and exists only
   to stop a low sun blowing fifteen degrees of horizon to the clip.
6. **Sun disc** 0.0047 → 0.0058 rad, pulled toward neutral, and held out of the
   sky path so the shoulder does not flatten it. Its gain is now a uniform: 130
   on the camera's dome, **55 on the environment dome**, because gi.js grades the
   world against that map (see the trap below).
7. **Dither**: the cloud-march entry jitter moved from a degenerate `hash13` to
   interleaved gradient noise, and its amplitude now falls with the step count —
   at the `floor` tier's two steps a full-step white-noise offset printed a
   lattice across the whole deck.

## Cost

Sky alone: **2 draw calls, 5 040 triangles** (the unit dome; unchanged). Plus one
512×256 LUT pass and one PMREM, neither per-frame — only when the sun or the
weather moves. Cloud march 12 → 14 steps at `ultra` (the loop bound was already
14, so no extra registers). Whole scene at `ultra`, 1920×1080: **334 draw calls,
1.85 M triangles, msP50 8.3 / msP95 9.1** — vsync-bound at 120 fps on this M5
Max, i.e. no measurable regression against the 8.3/9.3 measured before the
change. Ladder verified: `low` 4 steps + detail off, `floor` 2 steps, no errors.

## The trap worth knowing

**gi.js grades the whole world against the environment map, and its adaptation is
temporal.** Raising the sun disc's gain from 55 to 130 in the env dome moved the
*whole frame's* mean luminance by 23 and its first percentile by 29. Worse, the
adaptation takes about ten seconds to converge, so `shot.mjs --seconds 5`
measures a transient: two runs of identical code five seconds apart differed by
30 luminance points, and a parameter sweep at 5 s showed differences that a sweep
at 12 s showed to be nil. **Every grade number in this file was taken at
`--seconds 13`.** Anything shorter is measuring gi's auto-exposure mid-flight.

## Still weak

- The `low`/`floor` cloud deck still shows some edge stipple. Two march steps
  cannot resolve a dense cumulus; the honest fix is a cheaper cloud model down
  there rather than a shorter march of the same one.
- The sun disc is only ever in shot within a few degrees of setting (the rig
  cannot pitch above the horizon), and at that elevation it sits inside genuine
  glare. It now clips the blue channel where its surround does not, which is a
  25-byte edge — real, but subtle.
- Clouds are one deck at one altitude. No high cirrus above the cumulus, so the
  sky has one layer of depth where a real one has two or three.
- The ambient this file publishes is 0.8 stop dimmer and much bluer than round
  two's (removing the aerosol). Shadows are deeper and cooler, which matches the
  reference, but gi.js and buildings.js were tuned against the old value and the
  darkest building faces are close to crushed.

## Aerial perspective, round three (2026-08-07) — the vec3 was inside the square

Another agent's ablation on distant canopy put the blue-white far tier entirely
on this file: fog on 41/81/104 (B−R +62), fog off 29/48/19 (B−R −9). Confirmed
here on a foliage-masked band at 780m, reproducing the old shader exactly through
the new override (`--fog '{"k":[0.64,1.0,2.0164],"p":2}' --pin 0.000995`):
+63.9 fogged against −19.0 unfogged. The material path was never at fault.

Three changes to `patchFogChunks` and the density that drives it.

**1. `FOG_K` was inside the exp2 square.** `1 - exp(-(tau*K)^2)` squares the
chromatic weights along with the distance, so `[0.80, 1.00, 1.42]` was not the
2:1 blue-to-red the comment beside it claimed — it was 1.42²/0.80² = **3.16:1**,
and blue reached any given opacity at 0.56 of the range red did. The comment and
the code had disagreed since the vec3 went in. The weight now multiplies the
optical depth, never sits inside the power with it.

**2. The exponent is 1.5, not 2.** `FogExp2`'s square is not physical —
Beer-Lambert is `exp(-tau)` — and it is the wrong shape for a ladder of ridges:
calibrated so 700m matches the reference, an exponent of 2 overshoots 1.5km by
half again and an exponent of 1 undershoots 3km. One `pow` in a branch the
fragment already took, guarded with `max(tau, 1e-5)` because pow of zero is
undefined in GLSL and depth is exactly zero at the near plane.

**3. The density was re-derived, and its weather exponent went 1.7 → 2.6.** A
density means something else under a different curve. The weather exponent moved
because at 1.7 the fixture's ordinary `fog: 0.10` was contributing two thirds of
the total density: "clear air" was mostly the weather term and the base could not
be tuned without the slider fighting it. Two anchors now, both measured — 700m
foliage at the reference's B−R, and `fog: 0.9` still closing the view at about
a hundred metres (`weather=fog` still renders a silhouetted treeline in a
white-out; screenshot `shots/fog-heavy.png`).

### The ladder, measured

Blue-minus-red on **foliage only**, at `cam=wide time=16 quality=ultra`. The
mask comes from a fog-off frame of the same scene, so terrain and sky inside the
band cannot contaminate it; ranges come from projecting every scene instance to
the screen (`harness/skyfog.mjs --map`, `harness/fogbands.py`). 1.5km and 3km are
read on the 1360m band with the density scaled by 1500/1360 and 3000/1360 — the
fog factor depends only on the product, and the site has no foliage past ~1.5km.

| range | unfogged | before | after | tf2-12 |
|---|---|---|---|---|
| 250m | −3.0 | +11.2 | +1.9 | — |
| 560m | −15.0 | +50.1 | +11.1 | — |
| 700m | ≈−17.5 | ≈+57 | **+13.2** | +11 |
| 1130m | −25.8 | +83.2 | +26.4 | — |
| 1500m | ≈−24 | — | **+36.0** | +23 |
| 3000m | ≈−24 | — | **+54.3** | +41 |

`cam=low` (camera at 24m, so deeper in the haze layer by design): crowns at 130m
−21.9 against −26.3 unfogged, treeline at 430m +15.7, green still the largest
channel. Gates: 121 fps / 55 draws / 804k tris wide, 121 / 58 / 685k low, zero
console errors.

### The tier changes the answer by about a factor of two

`shot.mjs` pins `quality=ultra`; `solo.html` with no `quality` parameter settles
on **floor**. The identical fog measures +14.9 B−R at 780m on floor and +30.1 on
ultra, and the old fog measured +34 against +64. Two numbers from different tiers
are not comparable, and a target quoted without one is unusable — the +57 figure
this round was calibrated against is an ultra number, which is why the shipped
density is 0.00042 and not the 0.00065 that hit the same target on floor.

### `scene.fog.density` is now settable

`sky.setFogDensity(v)` pins it and survives a recompute; `null` hands it back to
the weather. `_recompute` used to rewrite it unconditionally, which is why two
attempts to ablate the haze silently measured a fogged frame. The compiled-in
constants (`k`, `max`, `h`, `p`) can only be overridden before the world builds,
via `globalThis.__lemFog` — they are baked into every material's fog chunk.

## Still weak (this round)

- 1.5km and 3km sit about +13 above the reference in absolute B−R. Our unfogged
  canopy is 15–20 units *greener* than tf2-12's, so the same haze lands higher;
  measured as a rise above each render's own foliage the two ladders are close
  (ours 1 : 2.0 : 2.6, the reference roughly 1 : 1.75 : 2.9). Fixing the absolute
  number means a greyer fog colour, and the fog colour is the horizon.
- The honest trade: at 0.00065 the far field layers visibly better to the eye
  (`shots/u-on.png` beside `shots/fog-wide.png`) and measures twice the
  reference. Calibration won, per the brief. If a critic calls the far field flat
  rather than veiled, that is the knob and this is the direction it moved.
