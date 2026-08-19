# terrain.js — round 7

Two defects were named, both in `shots/r6-landscape.png`. Both were verified in
the frame before anything was changed, and one of them turned out not to be what
six rounds of critics thought it was.

## 1. "The near ground is a smooth wash with a coarse speckle laid over it"

The speckle was real: a mat of thin dark threads, three to eight pixels across,
over the whole near and middle field (`shots/TA-near.png`, `TB-noveg-near.png`).

**It survived every plausible fix**, each tested and read back: the bump turned
off entirely (`TB-nobump-near.png`), shadows off (`TJ-noshadow-near.png`),
vegetation dropped, gi's screen-space AO zeroed (`TT-cmp.png`), the sun's own
shadow map off (`TU-c.png`), every layer texture halved in contrast, and the
whole set regenerated at twice the resolution (`TH-3-near.png`). It is present
in the raw blended albedo before a single drift term is applied, it is NOT
present in any single layer read at the same scale and the same pixel
(`TL-1112.png`, `TL-1314.png`), and the splat weights that blend them are smooth
(`TN-67.png`).

**It is `DataArrayTexture` anisotropy.** Setting the layer array's `anisotropy`
from 16 to 1 removes it outright — same frame, same pixel, one number:
`shots/TO-aniso.png` (1 above, 4 below). The mid distance is *cleaner* at aniso
1, not blurrier (`TO-midcmp.png`), which is the tell: correct anisotropic
filtering is sharper than none, and this was blurrier and noisier at once. The
threads are dark and the layers adjacent in the array to the two swards are the
forest floor and the mud, the two darkest surfaces in the set. Logged in
REQUESTS.md for anyone else sampling a `sampler2DArray` at a grazing angle.

Everything else the critics described about the near ground was separately true
and separately fixed:

- **`LAYER_TEX` 256 → 512.** At 256 a texel is 3–5 cm of ground and the finest
  feature a tile could carry without aliasing inside itself was ~15 cm — five to
  eight pixels at the forty metres the ground in front of `cam=low` actually
  sits at. The primary grain of every surface was landing as visible blobs.
  Measured cost: 87 ms → **294 ms** to generate the whole seven-layer set.
- **Every layer's frequency ladder rebuilt.** Contrast at a distance is the
  *weight* of whichever frequency survives the mip chain times the range the
  lerp covers, so the two have to move together. Half the sward's weight now
  sits at 7–9 cm; the metre-scale blob fields that dominated (`clod` at 72 cm,
  `damp` at 45 cm, `stem` at 28 cm, ballast stones at 25 cm, asphalt cracks at
  56 cm going to near-black) are all halved in size and in swing.
- **The asphalt crack network was a black web over the whole yard** — 0.104 in
  sRGB is 0.011 linear, a hole, at fourteen pixels across. Now 0.168 at half the
  feature size.
- **A material break at 3–40 m** (`bare`, off a third read of the detail map at
  62 m per tile). Every term between three metres and forty was previously a
  *multiply on one colour*; a value modulation survives one filter and not two.
  A change of material does not smear, because at every mip level it is still
  two surfaces rather than one surface at two brightnesses.
- **The splat is sharpened before it is normalised** (`w^2.5`). A linear splat
  renders every fragment as the *average* of every layer with any weight — and
  an average of three materials is a fourth one, flatter than any of its parts,
  printed over the whole valley. The dirt rules alone laid a mean weight of 0.13
  everywhere: a brown veil no critic could name but every one of them measured.
- **The grass/straw turn is thresholded, not proportional**, and broken at 1.4 m
  and 12 m as well as by the macro map. A constant one-part-straw-to-three blend
  is a wash; a knee is a patchwork.
- **The tone curve ran the wrong way.** `pow(albedo, 1.17)` on a *linear* albedo
  that lives at 0.04–0.12 is a 20% dimmer wearing a contrast lift's clothes. The
  near ground was landing at a mean of 51/255 against 91 on `refs/tf2-12.jpg`
  and 126 on `refs/tf2-05.jpg` — dark ground makes every mark on it a hole.

## 2. "The distant hills have no form"

**Established which side the fault was on first, by switching the fog off.** The
answer was: mostly mine. `shots/TF-fog0-hills.png` is that frame with
`scene.fog.density = 0` — rounded olive dough to the horizon, no ridgeline, no
rock, no forest anywhere on it. Four causes, all in this file:

1. **The canopy was painted BRIGHTER than the pasture it grew out of.**
   `uCanopyCol × canopyV × 2.7` landed at (0.21, 0.31, 0.17) linear against a
   sward at 0.04–0.08 — three to four times its albedo. Dropping that one colour
   put the ridges back on its own (`shots/TG-canopy-hills.png`).
2. **No crest frequency existed.** All the relief came from one fbm at a 900 m
   period; the tallest thing in the landscape varied over ~400 m. A ridged fbm
   at 160 m plus a 60 m spur field, gated to ground that is both far from the
   site and high on the valley side, so nothing about the yard changes.
3. **The rings sampled coarser than that field.** 20 m and 100 m cells averaged
   a ridgeline into a dome before it reached a vertex. `MID_SEG` 130 → 182 and
   `FAR_SEG` 72 → 144 (14 m and 50 m cells). Both counts are chosen so the hole
   punched for the previous ring lands on an exact cell boundary.
4. **No rock on the high ground.** Every stone rule keyed off `slope`, and slope
   off a 50 m cell under-reports the real angle by most of an order of magnitude
   — so the rule never fired two kilometres out. Height survives a coarse grid
   where slope does not, so exposed rock is now elevation-driven on the rings,
   and the rock tint LIGHTENS the stone layer instead of darkening it (outcrop
   is the brightest large surface on a hillside; it is what makes every ridge in
   `refs/tf2-12.jpg` legible against its own forest). Layer 3 was also pulled to
   neutral: 8% more red than blue comes back through four kilometres of a haze
   that multiplies blue by 1.42 as **mauve**, which is what turned the skyline
   purple.

The other side is measured and written up in `REQUESTS.md`: at `weather=clear`,
the far range arrives at the fog with σ 40.2 (comparable to the reference's
48.6) and leaves it at **15.7** — a 61% contrast loss at 2–4 km on the mildest
preset there is.

## Measured

| | draws | tris |
|---|---|---|
| terrain alone | 15 | 223,826 (was 166,410) |
| sky + gi + terrain | 15 | 228,242 |
| whole scene, `cam=yard` | 199 | 1,162,874 |
| whole scene, `cam=low` | 174–310 | 1.15–1.85 M (varies with trains on the road) |

Budget 450 / 2.5 M. **No draw call added**; +57 k triangles, all of it the two
LOD rings, which is what bought the ridgelines. 121–133 fps at 1080p on the M5
Max, msP95 10.1. Texture generation 294 ms (layers, now 512²) + 292 (macro) + 46
(detail); payload 957 KB for `mods=terrain`. One extra texture read on the
ground (12 base), and the layer array's anisotropy went 16 → 1.

```
                       meanRGB    B-R   sat%  meanL  sigma   p1   p95
r6-landscape (before) 85/100/100  +14.6  41.1   97.1   45.0   10   163
R7-final-low          81/98/104   +22.7  36.2   95.2   33.4   10   143
refs/tf2-12.jpg       93/112/107  +14.1  39.4  107.4   55.4   11   209
```

Local σ on the open ground (900×300 patch), which is the number the "no texture
detail" complaint actually was: **15.5 → 19.5** at `cam=yard`, **16.8 → 21.6** at
`cam=low`, against 27 on the `tf2-12` hillside and 45 on the `tf2-05` field.

## Screenshots

Before/after and the whole diagnostic chain, all read back and looked at:
`r6-landscape.png` → `R7-final-low.png`, `RA-yard.png`, `R7-scene-low.png`,
`R7-scene-yard.png`, `R7-terr-alone.png`, `RQ2-low.png` / `RQ2-floor.png`
(tiers), `RW-rain/overcast/snow/fog.png`. Diagnosis: `TA-near.png`,
`TB-nobump-near.png`, `TJ-noshadow-near.png`, `TL-1112.png`, `TL-1314.png`,
`TN-67.png`, `TT-cmp.png`, `TO-aniso.png`, `TO-midcmp.png`, `TF-fog0-hills.png`,
`TG-canopy-hills.png`, `TX-fog0.png`, `TEX-albedo.png` / `TEX2-albedo.png`.
No console errors in any run.

## Still weak

- **The frame's global σ went DOWN, 45 → 33.** Most of that is the old frame's
  contrast being the white haze wall against a dark treeline — a defect scoring
  as range. But the ground's own local σ is still 20 against the references'
  27–45, and closing the rest of that gap wants near-field *geometry* (TF2's
  grass at forty metres is instanced blades, not a texture) which is
  vegetation.js's, not mine.
- **The far range still carries a mauve cast in places.** Terrain's own surface
  out there measures a green 78/86/53; the mauve is what the chromatic haze
  makes of the warm end of it. Neutralising the rock layer fixed the worst of
  it; the rest is the fog term.
- **`cam=yard`'s left half is still a pale wash** — that is the graded platform
  under the macro map's site-wear gradient, and it is where the buildings and
  the track stand, so it is mostly hidden in the real floor.
- The `low`/`floor` tier is honest now (asphalt folds onto stone rather than
  dirt, so the aprons are grey instead of purple) but the ground loses its
  straw/sward patchwork entirely, because dry pasture folds onto the meadow.
- The corridors are still legible as a fan from directly overhead.
- The river's outer rim still shows one straight segment near the pool.

---

# terrain.js — round 8 (2026-08-07): the map's lip, and the ground under a building

Ryan: *"Buildings need to have the terrain beneath them generate… The map should
seamlessly blend into the background, right now there is a massive lip."*
The gate this round was measured, not judged: `harness/soak.mjs` walks eight
bearings out from the site on ten machine layouts and calls a 26m rise over a
20m step a fault.

## Baseline

    soak: collision 6736 · reversal 0 · floating 0 · unreachable 0 · edge 24

`harness/edgeprobe.mjs` (written this round) walks the same layouts on 32
bearings instead of 8 and does not stop at the first fault, so the faults can be
told apart instead of counted:

    L0 compact site     3 bad steps, worst 31.8m at r=1040m   seam continuous
    L3 sparse, 992m     23 bad steps, worst 71.8m             seam -4.2 → -53.4
    L7 sparse, 1263m    18 bad steps, worst 71.0m             seam -4.2 → -68.1

The `seam` line samples 2m either side of the fine field's edge. That split the
report in two, and they had nothing to do with each other.

## Fault 1 — the fine field stopped before the site did

The core was a fixed 800m square. Instruments sit on a 44m bay grid, so a fleet
spread over fourteen bays is a site a KILOMETRE across, and the graded platform
ran off the edge of the core into ground that had never heard of it: `heightAt`
answered from the design plane at x = cx+399 and from raw noise at cx+401. A
49m cliff, in the middle of the yard. The rings had the same problem in
geometry — they were built from `_baseHeight` while the core was built from the
design plane.

Three changes, and the third is the one that makes it structural:

- `_coreExtent()` sizes the fine field to the site plus 150m, quantised to a
  whole even number of MID-ring cells (the hole `_buildRing` punches has to land
  exactly on the mesh it makes room for), with the subdivision dropping 4 → 2 as
  the site grows so the vertex count stays near 300². A compact fleet still gets
  exactly 800m / 224 segments — bit-identical to before.
- `_gradedHeight(x, z)` is the finished surface as an ANALYTIC function.
  `heightAt` uses it outside the core and `_buildRing` builds from it, so the
  two sides of every seam are the same surface sampled at different rates.
- `_ringT(x, z)` replaces the per-ring constant 0 / 0.55 / 1.0. That constant
  fed the canopy lift, which pushes ring vertices up by as much as 15m to break
  a ridgeline against the sky — so it JUMPED at every ring boundary and drew a
  15m ridge, perfectly circular, at exactly 1300m. That ring is visible in
  `shots/tr-base-wide.png` and is the second half of the lip.

## Fault 2 — the "smoothing" pass was a 33% vertical scale

Found by attribution rather than by looking: `harness/attr.mjs` printed the core
grid against `_gradeTo(base, design, footprintDistance)` along one row and they
disagreed by 24m. `_buildField`'s two blur passes computed a nine-tap average
whose weights sum to ONE and then divided it by 0.75. Fifty-five metres of
hillside came out at eighty, twice over. It applied to every vertex more than
~8m outside the earthworks, which is most of the core — it is why the site sat
in a bowl, why the batters read at 1:1 when CUT_SLOPE says 1:1.35, and why the
yard had knolls in it.

## Fault 3 — the hills were steeper than hillsides get

The remaining faults were a kilometre from anything this file grades: open
country at 52°+. `Tex.fbm` is fixed at gain 0.5 against lacunarity 2, the one
combination where every octave contributes the same slope, so six octaves are
six chances to line up into a cliff and there is no knob. `_baseHeight` now uses
this file's own `wfbm` (gain argument, rotated octaves, no 7.2km wrap) at gain
0.40, a 1500m base wavelength, and amplitudes cut by about a third. That last
part is also what the references wanted — `refs/tf2-12.jpg` puts its range in
the bottom twentieth of the frame and ours was filling a third of it.

## Fault 4 — two more map edges that were not in the soak

- The far ring stopped at 3.6km with a skirt, and a skirt is a wall. A
  `BACK_SIZE = 24000` ring at 500m cells adds 7,032 triangles and one draw call
  and the world recedes into haze instead (`shots/tr-horizon-hi.png`, camera
  1100m up: no rim anywhere in frame).
- The river sheet ran 1500m either way and stopped, and from any height its end
  was a straight pale edge across the valley. It runs 9km now on the SAME 200
  rows — `zAt` is linear near the site (18m rows, where the shoreline is read
  close up) and quintic past it (380m rows at the ends).

## The buildings

The soak only tested the dock point, which passes happily while the far side of
a shed hangs over a slope. Widened (and said so, as asked) to sample the ground
on 12m and 24m rings around each station: the pad is a plane and the design
plane's gradient is capped at 1.8% per axis, so nothing on it may deviate more
than ~0.9m. `harness/padshot.mjs` measures the same thing on the worst layout
and gets **±0.68m at r=34m** on every station of layout 7 — which is exactly
34 × 0.02, i.e. the plane's own slope and nothing else. Visually:
`shots/tr-fix-st-multitek-ns.png`, ground meeting the plinth on every side, no
gap and no buried base.

## What is still weak

- **~45% of rail.js's geometry stands on ground this file never graded**
  (`harness/railfit.mjs`), because terrain grades a guess at the alignment
  before rail exists. Harmless on the lab's layout, twenty-metre embankment
  walls on layout 7. Written up in REQUESTS.md with the fix that would work.
- The far range is gentler than it was and reads closer to the reference, but
  the middle distance is still a soft wash — that is aerial perspective, not
  geometry.
- A hard-edged pale wedge appears at high camera angles with sky+gi loaded and
  is absent with `mods=terrain` alone. Not this file; in REQUESTS.md.

## Budgets

    terrain meshes  244k triangles / 5 draw calls (was ~222k / 4)
    whole scene     yard 289 draws / 1.89M tris · wide 209 / 1.37M
                    (budget 450 draws / 2.5M triangles)
    re-grade        462ms for a compact fleet, 470ms at the 2000m maximum core

---

# terrain.js — round 9 (2026-08-07): the complaint was about the wrong surface

Four critic findings came in, all of them naming the water or the embankment.
Before changing a line, each was located in the frame. **Two of the four are not
this file's**, and one of those is the largest single defect in the judged shot —
which is why they are written up in `REQUESTS.md` with the scripts that prove it
rather than worked around here.

## What was verified first

`harness/whit.mjs` (new) raycasts a grid of screen points through the judged
camera and names the mesh that answers. `harness/wshadow.mjs` (new) then casts
from each ground hit along the sun vector and names the caster.
`harness/wslope.mjs` (new) walks the core heightfield and reports the slope
histogram, the share of vertices steep enough to fire the triplanar branch, and
the dihedral break between adjacent cells.

- **"A's water is a featureless dark plane… no reflection of the quay wall two
  metres away."** There is no water in that frame. Every pixel below the quay is
  `terrain-core` at 8–17m, `waterY` is −31.3m, and hiding the water mesh
  outright changes nothing (`shots/W2-nowater.png`). What they were describing
  is ground in the building's shadow at RGB 24/34/46 against 154/143/123 with
  the shadow map off — a 1:35 sun-to-sky ratio where daylight is 1:6. Terrain's
  own sky occlusion is not it: forcing the whole `aSky` attribute to 1.0 leaves
  the frame bit-identical. → REQUESTS.md §1.
- **The actual river was then looked at**, from a camera placed on the bank
  (`shots/W8-river.png`). It has a glitter lane off the real sun, an environment
  reflection, depth-graded colour and a shoreline. The `MeshStandardMaterial`
  rewrite from the previous round reaches the frame — `harness/wprobe.mjs` (new)
  confirms all five `onBeforeCompile` splices landed, no `#include` left
  unconsumed, `uEnvAmt` = 1 with sky.js's PMREM published — and raising
  `envMapIntensity` from the 0.23 gi.js sets it to, all the way to 4.0, changes
  the frame by nothing visible (`shots/W1-env1.png`). **Nothing was changed in
  the water.** It was not the defect.
- **"A's embankment is a faceted low-poly prism… a hard visible crease."** The
  embankment in that frame is `rail`, at 7–10m. → REQUESTS.md §3.
- **"Projection stretch on slopes."** The triplanar branch added last round does
  compile and does run, but `wslope.mjs` says it fires on **1.0% of the core**
  and the steepest ground anywhere on it is 30°. On terrain, this is close to a
  non-issue; the stretch the critics see is on the rail prism.

## What did change

**1. The earthworks are no longer a prism (`_gradeTo`, `GRADE_ROUND`, `smin`).**
This is the one critic finding that was real *and* mine, and it is geometry.
Clamping a design plane against natural ground gives a surface that is
continuous and a slope that is not — a corner at the crest where the platform
turns into the batter, and another at the toe where the batter daylights. On a
3.57m grid that is a facet edge. Measured:

    worst slope break between adjacent cells   37.9°  →  12.6°
    cells breaking by more than 8°             0.86%  →  0.22%
    steepest ground on the core                29.9°  →  26.8°

`f²/(f + r)` at the crest is zero with zero derivative at `f = 0`, so the
platform edge is still **exactly** the design plane — which is what the pads and
the soak's conforming-ground check stand on — and the turn is spread over about
two and a half cells. The toe is a rounded minimum whose radius ramps from zero
over the same distance, for the same reason: at full radius it would pull the
platform edge itself down by up to `k/4`.

It lives in `_gradeTo` and not in another blur pass in `_buildField` on purpose.
`_gradedHeight` answers for the rings, for `heightAt` past the core, and for
every seam round 8 spent its time on; anything that softens the earthworks has
to be in the one function both sides call, or the two descriptions of the same
ground drift apart again.

**2. A pace-scale material break in the near ground (`scuff`, `soilShare`).**
Everything this shader did below four metres was a multiply on one colour. The
`bare` break that fixed the middle distance last round is built from the 12m and
62m detail reads, so the finest thing it can do is turn the ground over about
four metres — invisible at the eight metres the street camera stands at.
`dNear.a` (46cm) and `dMid.b` (35cm) are already fetched, so a scuff of soil and
stone through the sward costs no texture read.

The first cut of it **measured worse than the wash it replaced** and the reason
is worth keeping: the coarse break sends two thirds of what it takes to stone,
and stone is the *lighter* layer, so scuffing at pace scale as well just added a
third material at a third brightness everywhere. Mean went up, local sigma went
down (9.97 → 9.17). It now swings both ways — a 3m field picks soil in one patch
and stone in the next — so `soilShare` is a variable and `wDirt`, `wStone` and
`rockRatio` all read it.

It was rendered on its own channel to check that it reaches the frame at the
right scale before it was believed: `shots/WC-debug.png`, red = `scuff`,
green = the coarse break, blue = the distance fade.

**3. The 46cm value rung widened from ±28% to ±36%.** A material break between
three layers that have already been desaturated to 0.72 and put through a chain
of multiplies carries less contrast than the one value term read at the scale a
critic is standing at.

## Measured

| | draws | tris |
|---|---|---|
| terrain alone (`mods=terrain`) | 16 | 244,906 |
| sky + gi + terrain | 16 | 249,322 |
| whole scene, `cam=street` | 220 | 1,854,534 |
| whole scene, `cam=wide` | 212 | 1,577,044 |
| whole scene, `cam=yard` | 249 | 1,864,980 |

Budget 450 / 2.5M. **No triangle and no draw call added** — `_gradeTo` moves
vertex heights and creates no vertices, and the shader changes are arithmetic on
reads that were already being made. No new texture, no new uniform.

```
                       meanRGB    B-R   sat%  meanL  sigma   p1   p95
WH-final-wide          58/83/81  +23.0  55.0   78.0   55.6   10   168
references                                            47-57  11-17 170-178
```

The wide camera now sits inside the reference band on all three tone numbers.

Gate: `soak.mjs --parses 120 --layouts 3` → collision 0, reversal 0, floating 0,
unreachable 0, **edge 0**, consoleErrors 0. PASS, before and after.

## Screenshots

`W0-base-street.png` (baseline) → `WH-final-street.png`, `WH-final-wide.png`,
`WD-yard.png`, `WH-rain.png`, `WI-floor.png`. Diagnosis, all read back:
`W1-env1.png`, `W2-nowater.png`, `W3-noshadow.png`, `W4-sky1.png`,
`W5-yard.png`, `W8-river.png` (the actual river), `WC-debug.png`,
`WG-round0.png`. No console errors in any run.

## Still weak

- **Local sigma on the open ground did not move** (9.97 → 9.25 on a 9×9 window,
  yard camera). The scuff is demonstrably in the frame and at the right scale,
  and the value rung is wider, and neither shows up in that statistic — because
  the three layers it switches between are, after the end-of-shader
  desaturation and gain, close in value. Closing the rest of that gap means
  either pulling the layers further apart in hue and value (which risks the
  "coarse dark mottle" every previous round fought) or near-field geometry,
  which is vegetation.js's.
- **The frame is blown at the top end**: p95 224 on the street camera against
  the references' 170–178. That is new since the ground got brighter and it is
  the next thing to chase.
- The triplanar branch is 1% of the core and could arguably be spent elsewhere;
  it is kept because a sparse layout puts real batters in the near field.
- A backtick inside a GLSL comment closes the template literal and fails as a
  JavaScript syntax error four hundred lines further down, with the whole
  subsystem skipped and the map drawn without terrain. It cost a build this
  round; there is now a note in the comment that did it.

---

# terrain.js — round 10 (2026-08-07): it is an island

Ryan: *"You know what would save a ton of render distance and draws? Making it
into an island instead of a patch of land. Please add far geometry for like the
mainland that is far away (could even be in the skybox)… It can be a sizable
island, that expands dynamically with each equipment added."*

## The saving, which is the whole point

|  | before | after |
|---|---|---|
| land area drawn | **568 km²** (a 24km square) | **5.8 km²** |
| heightfield extent | ±12,000 m | land stops at **1,591 m**; the last land mesh at 2,088 m |
| terrain triangles | 301,600 | **244,500** |
| terrain meshes / draws | 5 | **4** |
| land meshes | core + 3 LOD rings | core + **one** ring |
| `mods=sky,gi,terrain` | 16 draws / 306,050 tris | **15 / 249,550** |
| whole scene, `cam=wide` | — | 361 draws / 2.39 M tris, 69–76 fps |
| whole scene, `cam=low` | — | 157 / 1.41 M, 122 fps |
| re-grade (compact fleet) | ~470 ms | ~430 ms (erosion 95 ms, on a smaller span) |

**99.0% of the land is gone and 19% of the triangles with it.** The two numbers
differ by that much because the triangles were never spread evenly over the
area — the 24km backdrop was 500m cells. What the area buys is everything
*else*: shadow-map fitting, vegetation's LOD budget, the fog's job, and the
render distance Ryan actually asked about.

Two things did it. The land is finite (`_islandSD`), and **the ring throws away
every quad more than 14 m under water** (`DROWN_DEPTH`) — the sea is opaque by
six metres of depth, so a seabed past that is a surface under a lid. That
second one takes the ring from 114k triangles to 40k, which is what paid for a
17 m cell on the coast instead of a 21 m one.

The camera's far plane is **not** the saving and does not have to move; it is
still 6800 m and the ocean and the painted mainland are both sized against it
(`OCEAN_R = far × 0.92`, `HORIZON_R = far × 0.74/0.87`). It could come in to
about 7000 m but no further — see REQUESTS.md.

## What is there now

- **A coast, not a boundary.** `_islandSD` warps the plane by up to a third of
  the island's radius on a 720 m field *before* taking the radius, then
  crenulates at 165 m. That gives bays that cut in, headlands that stand past
  the mean radius and an inlet or two — none of which a function of bearing can
  make. The warp is gated to zero over the site, and its amplitude is capped
  against the margin, so the deepest possible bay stays 38% of `COAST_MARGIN`
  clear of the last thing the lab owns whatever the fleet does. Three sea
  stacks are picked per layout from candidate bearings that land 55–330 m
  offshore.
- **Beaches and cliffs fall out of one rule.** How wide the land takes to fall
  to the water, and the exponent of the fall, are both taken from how high the
  ground *behind* stands: high ground meets the sea over 80 m with an exponent
  under one (a cut cliff), low ground over 320 m with an exponent well over one
  (a strand). Nothing chooses; the headland that stands proud in plan is also
  the one standing tallest, so it is also the one that gets the cliff.
- **`waterY` is the sea and it is one scalar.** Planar, no tide, no vertex
  displacement, so `waterY`, `waterLevel` and `waterAt(x,z)` are all the same
  number and are safe to compare a tree's foot against. It moved from −8 to −30
  in the unshifted frame (33 m under the finished yard, was 11) because at
  eleven metres every cliff on the island would have been shorter than the
  buildings standing back from it.
- **The ocean is one polar disc**, 25k triangles, rows packed through the band
  the coastline can wander over and running away geometrically to 6.2 km.
  Depth-graded colour over a 14 m ramp, four drifting ripple octaves, sky
  reflection off the PMREM, and surf that shoals on DEPTH — so the breaking
  bands follow the shoreline whatever shape it is, and nothing in the shader
  knows where the coast is. Quads entirely on land are never emitted.
- **A mainland on the horizon**: two bands of ridge at 5.2 and 6.1 km, 896
  triangles, one draw. Unfogged and hazed by *height* rather than uniformly
  (more air at the foot than at the crest), colour derived from `scene.fog.color`
  so it converges to whatever the rest of the distance converges to, base 300 m
  under the waterline so the sea covers the join, `depthTest: false` at
  renderOrder −0.5 so it behaves as part of the sky.
- **Seasons on the ground**, off `ctx.season` / `world.autumnality` /
  `world.winterliness` and never off the thermometer. Summer moves the
  grass→straw *knee* rather than adding a tint, and biases it by the
  heightfield's own crest mask, so straw spreads out of the patches that were
  driest anyway. Spring greens the sward and muds the traffic routes. Autumn is
  a multiply toward warm on sward, straw and painted canopy — never an added
  pigment, because this albedo is linear and lives at 0.04–0.12. Winter snow
  lies deep on shallow and shaded ground and thin on steep sun-facing faces
  (the +Z half of the sky), with ice at the water's edge off `winterliness`
  alone. `onSeason` sets uniforms only — no re-grade.
- **Streams drain to the sea**, which is the thing a patch of land could never
  do. The erosion grid is now sized to the island rather than to the core plus
  a guess, so it contains the whole catchment and every drop is followed until
  it leaves.

## Four bugs found, three of them mine

1. **`_splat` was called with three arguments missing.** `flow`, `moist` and
   `sun` were added to the signature by the previous run and never wired to
   either call site, so every ground fragment in the map was splatted from
   `undefined`: `smoothstep` of NaN is NaN, and the normalisation's
   `!(sum > 1e-4)` guard set `out[0] = 1` while leaving the other six weights
   NaN. The whole landscape rendered as a sheet of blue
   (`shots/isl-base-wide.png`). It parsed, it threw nothing, and no test could
   see it.
2. **The ocean was wound backwards.** On a polar grid the winding that faces a
   rectangular grid upward faces a polar one down, so every triangle was
   back-facing and culled. What looked like a sea in `isl-air3.png` was the
   ring's drowned seabed with the sky dome behind it, and it cost an hour of
   hunting for a shader fault in a mesh that was never drawn — the vertex
   normals said (0,1,0) while the rasteriser said "away".
3. **Subaerial erosion was carving the seabed.** Forty thousand droplets ran
   off the island and cut up to 39 m out of the shelf, leaving thirteen metres
   of water immediately off the beach: no shoaling, no surf zone, no strand,
   and open-ocean colour at the waterline. The residual is now tapered to
   nothing at the waterline as well as at the grid's rim. Every one of those
   symptoms was a shader complaint and none of them was a shader.
4. **The ring's canopy lift was applied twice** — once inside `_baseHeight`
   (where the previous round moved it) and again to the ring vertices, so the
   ring was drawn up to fifteen metres above the surface `heightAt` reports.
   That is Ryan's *"grass won't stick to the floor"* from the other side: cover
   conforms perfectly to `ctx.ground()` and then the ring is drawn somewhere
   else. The old copy is deleted; the silhouette is unchanged.

## The gate

`soak.mjs --parses 200 --layouts 4`: collision 0, reversal 0, floating 0,
unreachable 0, relayout 0, consoleErrors 0, deadRailway 0 — and **edge 29, all
of them the coastline**. Every fault is a downward step at r = 1180–1480 m,
which is the waterline crossing or the face of a sea cliff. The check is right
and the ground is right; they simply disagree about whether a coast is an edge.
Not weakened — reported. See REQUESTS.md for what it would take to teach it.

## Screenshots (all read back)

`isl-base-wide.png` (the NaN baseline) → `isl-air6.png` (the island from the
air), `isl-coast12.png` (a bay, a beach, shoaling water and the mainland),
`isl-summer/autumn/winter.png` (whole scene, `cam=wide`), `isl-low.png`,
`isl-top.png`. Diagnosis: `isl-air2/3/4/5.png`, `isl-coast1/6/8/10/11.png`,
`isl-ab-nohorizon/noocean/noring/nofog/none.png`, `isl-ocean-magenta.png`.
New harness: `islcam.mjs` (arbitrary rig pose + island metrics),
`islcoast.mjs` (stands the camera on a coastline), `islab.mjs`, `islhit.mjs`.

## Still weak

- **The sea's own edge is visible from a camera more than ~600 m up.** The
  ocean stops at the far plane, and from 1800 m the disc's rim is 16° below the
  horizon. Nothing in the floor's camera set goes there (`top` is 420 m), but a
  future free camera would find it.
- **The near ocean is flat.** No swell displacement, no wake, and the ripple
  field is the river's. It reads at 200 m and it is thin at 40 m.
- **Aerial perspective eats the coast.** The waterline is 1.3–1.6 km from the
  site and `scene.fog` runs at 0.00094, so the coast arrives at about 30% of
  its own contrast. That is the same 61% loss logged in round 7 and it is
  gi.js's number, not mine — but an island puts the most interesting thing in
  the frame exactly where it bites.
- The interior still reads as a terrace with hills round it, because the graded
  platform is 700 m across on this fleet and the island is 2.7 km.
- Sand only appears on ground that is both low and nearly level; the foot of a
  cliff is bare, which is right, but it means whole stretches of coast have no
  strand at all.

---

# Round 12 (2026-08-07) — the island reads as an island, and the default camera cannot see a horizon

Ryan, on `shots/island-wide.png`: *"make the island smaller, like much smaller,
from the default camera angle it does not look like an island yet."* The
acceptance criterion the integrator wrote from it is a composition, not a
radius: sea past the land on more than one side, a coastline the eye can
follow, the far mainland across open water behind it.

## First, stop judging it by eye

Round 11 shrank the disc from 1347 m to 484 m and the frame still read as a
coast. Nothing in the loop could say why, because a PNG cannot separate "sea"
from "pale haze on distant ground" — which is the exact failure the round is
about. So `harness/islframe.mjs`: it unprojects a grid of pixels, marches each
ray against `terrain.heightAt`, and classifies what it meets as land, sea,
mainland or sky. No shading, no fog, no vegetation.

The first thing it measured decided the whole round:

    cam=wide  camera 305 m from the plan's middle, 209 m above the sea,
              vertical fov 42°, pitch 26.36°
              => the frame's TOP EDGE is a ray 5.36° BELOW the horizontal
              => it meets the water at 2231 m
              => skyPct 0. There is no horizon in the default view.

The true horizon projects to ndcY 1.29 — **14.5% above the top of the frame**.
So the painted ranges at 5.2 and 6.1 km were never in the default picture and no
amount of shrinking would have put them there. That is why the sea ended in a
white glare band with nothing beyond it.

## What changed

**The island's base radius is per bearing.** `_coastFloor` already computed a
per-bearing keep-out for the bays; `_coastLobes` now takes that array, adds the
margin, smooths it 9× and renormalises it to hold its own peak, and `_islandSD`
measures against it on the bearing of the *warped* point. The coast is the shape
of the lab rather than a circle around it.

    before (round 11)   uniform 484 m
    after               284 m … 479 m, mean 386, land 0.558 km²

Mean 386 is **below** the site's own radial reach of 395 m, which is only
possible because that reach is one arm of the rail ring rather than a circle the
lab occupies. `COAST_MARGIN` 90 → 42, so open ground from the last rail to the
waterline is 80 m. The warp amplitudes halved (0.22/0.10 → 0.115/0.055) because
the base is no longer a disc that needed the warp to carry all of the shape.

**A near mainland**, `_buildMainland`: real geometry (2,464 tris, 1 draw call),
an arc across the bearing the map is looked at from, tapering to nothing either
side so every other view still opens onto open sea. Shoreline at
`islandR + wobble + 560·√(R/480)` = 1,142 m on the demo fleet. It shares the
horizon's shader (`_rangeMaterial`) so it costs no extra program.

Frame, measured (`islframe --cam wide`):

                     round 11    round 12
    our island        76.9%       68.6%
    open sea          23.1%       16.3%
    land across it     0%         15.2%
    waterline, left edge   17%      39%
                     centre       19%      30%
                     right edge   32%      32%

The left edge and the centre now cross the waterline 9 points apart where they
used to be 2 — the coast is a curve in frame instead of a rule, which is the
thing a shoreline cannot do.

## Three bugs, all of them found by measuring rather than by looking

1. **The mainland was wound backwards and invisible.** Round 10's ocean bug,
   verbatim: on a polar grid `cross(u_radial, u_tangential)` points DOWN, so
   `(a0, b0, a1)` back-faces every triangle. Built, in the frustum, in the draw
   call, not on the screen. The A/B that caught it is `_hideshot.mjs` —
   `isl5-nomain-top.png` versus the same frame with the mesh hidden.
2. **Then it rendered as sky.** Hazed like the 5.2 km ranges (66% air at the
   shore) it was within a couple of per cent of the sea it stood behind. It is
   keyed to DISTANCE now (`1 - exp(-d·0.00033)`), which is also what makes it
   read at `cam=low`, where it is a proper blue-green range across the water
   instead of a flat poster (`isl9-low.png`).
3. **Its colour ramp played out off-screen.** `aUp` was normalised over the full
   300 m amplitude; the wide camera only sees the bottom 90 m. Over 130 m now.

A fourth was caught by `node --check`: a backtick inside a shader comment, which
is the trap `LEM Web Server/CLAUDE.md` records twice. Third time.

## "Grass won't stick to the floor" — not this file, measured

`harness/_groundfit.mjs` compares every DRAWN ground vertex against
`heightAt(x, z)` — which is what `ctx.ground()` returns and what every blade is
placed at. Orphan vertices matter here: a LOD ring keeps its full V×V array and
punches the hole in the INDEX, so half of it sits unused under the core and
measuring those reports a disagreement that is never rendered.

    terrain-core   21,275 drawn dry vertices   worst 0.000 m
    terrain-ring    1,860 drawn dry vertices   worst 0.029 m

The round-10 double canopy lift is gone and nothing has replaced it. If cover
still floats, it is not being placed at `ctx.ground()`.

(`heightAt` and `_gradedHeight` differ by up to 6.1 m deep inside the core,
where `heightAt` reads the fine field — but 0.58 m within 60 m of the core's
edge, so the two surfaces join smoothly and nothing outside reads the other.)

## The gates

`soak.mjs --parses 200 --layouts 4`: collision 0, reversal 0, floating 0,
unreachable 0, relayout 0, consoleErrors 0, deadRailway 0, **edge 12** (was 32).

All twelve are the coast and none of them is what the check was built for.
Reproduced on the demo layout with `_edgewalk.mjs`, which runs the identical
walk and prints the sea alongside:

    bearing 3  r=380m  -1.9 → -39.6   local coast radius 402m   kind rock
    bearing 4  r=440m  12.2 → -29.9   local coast radius 460m   kind rock
    bearing 5  r=420m  -6.7 → -40.4   local coast radius 382m   kind rock

Not weakened. Note that neither fix I proposed in round 10 would catch these:
the lower sample is still 11–21 m ABOVE the waterline, so they are the cliff
FACE, not the crossing. See REQUESTS.md for the one-line test that does.

Budgets, `mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather`:

    cam=wide   353 draws  2.43 M tris   first frame 2.74 s
    cam=top    197 draws  1.42 M tris   first frame 2.74 s
    cam=low    152 draws  1.36 M tris   first frame 2.70 s
    quality=floor, wide  171 draws  1.03 M tris  first frame 2.78 s

First frame is under the 3 s budget and about 0.6 s better than the 3.37 s this
round started from — the ring is 118 cells square instead of 130 (−18% of its
vertices) because a smaller island needs less shelf around it. The mainland
costs one draw call and 2,464 triangles and is NOT shed at `floor`: it is what
makes the composition, and it is a thousandth of the budget. The draw/triangle
counts at `wide` are not mine — `vegetation.js` is being worked on in parallel
and moved by 150 draws and 1.2 M triangles between two of my own runs.

## Screenshots (all read back)

`isl2-wide.png` `isl2-top.png` `isl2-low.png` `isl2-floor-wide.png` — the gate
set. Diagnosis: `isl3-wide` (lobes, no mainland), `isl4-solo`/`isl4-wide` (the
mainland wound backwards), `isl5-nomain-top` vs `isl5-tg-top` (the A/B that
proved it was drawing and looked like sky), `isl6/isl7-tg-top` (haze), `isl9-low`
(distance haze at street level), `isl10-top` (the far shore, cropped).

## Still weak

- **`cam=top` cannot show the island and never could.** Pitch 68.75° at 420 m
  puts its top edge 47.75° below the horizontal, which meets the ground 408 m
  from the camera: it frames the yard and a corner of beach. `islframe --cam
  top` measures 4.3% sea. Judging the island there is judging the wrong camera.
- **An uninformed viewer would probably say "spit" or "point" before "island".**
  The near and side coasts are behind the camera — it stands 305 m from the
  plan's middle and the earthworks reach 429 m, so it is standing ON the island
  and cannot see it end. This is now floored by the site, not by taste: the
  coast is already within 80 m of the last rail on every bearing.
- The strand on the far mainland is a uniform pale line and can be misread as
  surf.
- The sun glitter on the open water is very bright at time=16 and flattens the
  middle distance; that is the water shader's, and it is the next thing I would
  look at.
