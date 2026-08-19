# Reference material — the bar a build is judged against

34 files, all verified as real raster images (`file` + `magick identify`), none under 300 KB.
Captured / sourced 2026-08-06.

| set | files | format |
|---|---|---|
| After the Flood (PlayCanvas) | 12 | PNG 1920×1080, captured live |
| Transport Fever 2 | 12 | JPEG, 10 × 1920×1080 + 2 × 1602×534 |
| Train sim (Dovetail / Trainz) | 10 | JPEG 1920×1080 |

---

## 1. After the Flood — PlayCanvas WebGL demo

**Live URL:** `https://playcanv.as/p/44MRmJRU/` (title "After the Flood — WebFest", by mf_f).
That page is a wrapper; the app itself is the iframe `https://playcanv.as/apps/yv53Js1j/index.html`,
which is what was loaded directly so the canvas fills a true 1920×1080 with no page chrome.

**Capture method — this is a live render, not a press shot.** Headed Chromium 1.61 via Playwright
(`--use-angle=metal`, real GPU; headless SwiftShader was avoided as instructed), viewport 1920×1080,
`deviceScaleFactor: 1`, loaded with `?quality=high` **and** the in-app ULTRA button clicked, then PLAY.
Quality tier was read back out of the running app to confirm it applied:

```
skyQuality 2 (HIGH)   waterQuality 2 (HIGH)   leavesQuality 2 (HIGH)
fogQuality 2 (HIGH)   shadowQuality 2 (HIGH)  mirrorQuality 1 (MEDIUM)
aa 4 (4×)             blitColor/blitDepth true
```

35 s load + 22 s settle before the first frame; 3.5 s dwell on every camera move so the sky raymarch,
planar reflection and 4× AA resolve. Screenshots are of the `#application-canvas` element only, so each
file is the raw framebuffer (the small "POWERED BY PLAYCANVAS" mark visible bottom-left is a DOM overlay
the app itself draws over the canvas region, not something added in capture).

The demo's own free camera (`flyCamera` script) was used to reach the 12 viewpoints: its character
controller was disabled and the camera positioned directly, including at the author's four hand-placed
`camera-angles` nodes (`path-1`…`path-4`). Nothing about the renderer, materials, or post chain was altered.

| file | camera | what it shows |
|---|---|---|
| `aftertheflood-01.png` | app default spawn, eye height 2.4 m | Hero first-person view. Wet plaza, sunlit zebra crossing, gold maple right of frame, collapsed concrete slabs left, distant fogged skyline, red phone box as the only saturated accent at ~1 km. The reference frame for ground material + contact shadow. |
| `aftertheflood-02.png` | author node `path-2` | Full maple canopy against sky, lit building interiors behind, leaf-litter ground. Best read on foliage card density and emissive window falloff. |
| `aftertheflood-03.png` | author node `path-1` | Two trees + moss-topped boulders over wet paving, tilted glass tower behind. Author-composed angle. |
| `aftertheflood-04.png` | −45, 4.0, 20 | Wide stone stair flight, single gold tree, warm up-lighters washing the concrete. Reference for artificial light on dry stone. |
| `aftertheflood-05.png` | −36, 1.2, 50 | Red telephone box on wet paving beside standing water. **The contact-shadow and colour-accent reference:** hard cast shadow, dappled shadow from off-screen structure, mirror streak in the water at left. |
| `aftertheflood-06.png` | −4, 1.4, −14, pitched −25° | Macro of fallen maple leaves on ribbed concrete slabs. **The texture/alpha reference** — leaf cards, alpha-to-coverage dither, paving normal detail, per-leaf drop shadows. |
| `aftertheflood-07.png` | −18, 1.1, 20, at waterline | Flooded street corridor. **The wet-surface reference:** window lights reflected as long vertical streaks broken into horizontal ripple bands, stepping-stones half-submerged. |
| `aftertheflood-08.png` | −10, 1.1, 30 | Flooded plaza with submerged posts and railings, near-black water in the foreground. |
| `aftertheflood-09.png` | −10, 1.1, 30, yaw 250° | Open floodwater to a fogged horizon, capsized tower slab overhead, distant skyline islands, sun glitter path. **The sky/horizon/water-scale reference.** |
| `aftertheflood-10.png` | −24, 6.0, 14 | Wet plaza terrace looking out over the flood to the skyline through heavy aerial haze. |
| `aftertheflood-11.png` | −20, 22, −6, pitched −24° | Elevated overview of the collapsed building field; reads the whole scene's geometry budget at once. |
| `aftertheflood-12.png` | −20, 3.0, 8, pitched +48° | Bare branch canopy and a lit streetlamp against sky. **The sky-gradient and cloud reference.** |

---

## 2. Transport Fever 2 (Urban Games, 2019)

Sourced from the Steam storefront API (`appdetails`, `path_full` 1920×1080 assets) for the base game
(app 1066780) and two DLC (2195610, 2195611), plus two wide banner crops from the official site.

| file | source URL | what it shows |
|---|---|---|
| `tf2-01.jpg` | `shared.akamai.steamstatic.com/store_item_assets/steam/apps/1066780/ss_a6bb849ecc478467f2db7cf635c298dbc4fc686e.1920x1080.jpg` | Modern curved-roof station exterior, glass facade with large analog clock, city towers behind. |
| `tf2-02.jpg` | `…/apps/1066780/ss_b1fd04a855f5cb2a35d7d816b54ea81635a9ee9f.1920x1080.jpg` | Platform level: two bullet-nose high-speed trains nose to nose, overhead catenary, platform clocks, passenger. |
| `tf2-03.jpg` | `…/apps/1066780/ss_4d989afce867ca774adca249e783195109f62efc.1920x1080.jpg` | **Rail/ballast/bogie close-up.** Yellow-blue diesel switcher #6731 coupled to a "Virginia & Truckee" boxcar; sleepers, ballast, trucks, handrails all legible. |
| `tf2-04.jpg` | `…/apps/1066780/ss_16addbc44d2cc1ffd18159bd751fd89717481aa6.1920x1080.jpg` | Steam passenger train on a stone multi-arch viaduct through forested hills beside a lake. |
| `tf2-05.jpg` | `…/apps/1066780/ss_f9bce48456f3466e0f00d11d26cff248c2456694.1920x1080.jpg` | Red farmhouse across a golden wheat field, treeline and forested hills behind. |
| `tf2-06.jpg` | `…/apps/1066780/ss_931ee3a5c366bd6c3b330f16a6aa3d1a2ee88582.1920x1080.jpg` | Aerial of a multi-track city station: trains at platforms under catenary, passenger crowds, surrounding towers. |
| `tf2-07.jpg` | `…/apps/2195610/ss_8feef427e688317c7cb945dde7834d3604c6aee2.1920x1080.jpg` | **Forested cutting.** Blue/yellow InterCity diesel curving through ballasted double track, wolf on a grassy rise. |
| `tf2-08.jpg` | `…/apps/2195610/ss_eabb94c2313ee582d87f05706715b9ab0ca5811c.1920x1080.jpg` | Aerial of a red-roofed village in a forested river valley with a stone railway viaduct. |
| `tf2-09.jpg` | `…/apps/2195611/ss_61b0ac50bf787ad2ed02349c6a070090648bb4a7.1920x1080.jpg` | **Catenary reference.** TGV-style set on ballasted track under a run of masts, cantilevers, contact and messenger wire. |
| `tf2-10.jpg` | `https://www.transportfever2.com/wp-content/uploads/2019/04/main_cargo_yard_07.jpg` | **Tank-car freight + depot.** Rail yard: switchers and freight cars, rows of white cylindrical petroleum tankers behind, brick sheds, oil drums, factory skyline. 1602×534 banner crop — the only asset carrying this content. |
| `tf2-11.jpg` | `https://www.transportfever2.com/wp-content/uploads/2019/04/main_old_times_06.jpg` | Steam loco at a desert station platform, passengers, luggage carts, red rock behind. 1602×534 banner crop. |
| `tf2-12.jpg` | `…/apps/1066780/ss_2daa5380ff04a3eaa7c835260e295b2ade6b50b0.1920x1080.jpg` | Open hillside with boulders, pine/deciduous forest edge, layered mountain ridges. Terrain + forest reference. |

Coverage check: track/ballast `03`, `07`, `09`; stations/depots `01`, `02`, `06`, `11`; tank cars `10`;
forested terrain with buildings `04`, `05`, `07`, `08`, `12`; signals/catenary/trackside clutter `02`, `06`, `09`, `10`.

---

## 3. Train simulators — Dovetail Games + Trainz

All ten from the Steam storefront API. Note these split into **two different engine generations**, and
the difference matters when using them as a bar — see the breakdown below.

| file | source URL | title | what it shows |
|---|---|---|---|
| `trainsim-01.jpg` | `…/store_item_assets/steam/apps/2584080/ss_6e33ebbc80fdac9efcbf9b5881f5233f360fbfa9.1920x1080.jpg` | TSW4: Cargo Line Vol. 1 – Petroleum | **Best tank-car reference.** Four VTG tank cars (red / dark grey / blue / green) in a yard: end domes, side ladders, walkways, handrails, hazard placards, bogies; foreground track thrown out of focus. |
| `trainsim-02.jpg` | `…/apps/2584080/ss_80e98535946f9dd7449a016d8716f12b0a2866b7.1920x1080.jpg` | TSW4 Petroleum | EWS Class 66 (66074) hauling grey VTG tank cars past a station building. |
| `trainsim-03.jpg` | `…/apps/2584080/ss_9f7897dd40312c05506b25cbfdfa5aee3e628431.1920x1080.jpg` | TSW4 Petroleum | Class 66 (66094) with dark tank cars passing an HST in a city rail corridor. |
| `trainsim-04.jpg` | `…/apps/376953/ss_dae94eb1edd9277e288b75e1d59c90ecf015cc92.1920x1080.jpg` | TSC: Western Pacific FP7 "California Zephyr" | **Older diesel.** Head-on close-up of an orange/silver EMD FP7 cab unit (805-A). |
| `trainsim-05.jpg` | `…/apps/376953/ss_34d0a95c872e0880f4646fb75d7a0f2a3ebddfbb.1920x1080.jpg` | TSC: WP FP7 | The same F-unit consist crossing a steel truss bridge over a reservoir. |
| `trainsim-06.jpg` | `…/apps/258666/ss_97ccb7063e298bef4a0920ef8a252912028864cd.1920x1080.jpg` | TSC: NS GP38-2 High Hood | **Older diesel, GP-series.** Three-quarter of NS 5189 on a wooded siding, exhaust plume, ballast and cutting. |
| `trainsim-07.jpg` | `…/apps/258666/ss_31b280dd2d1fe61ff945bd3b436dad5232b03690.1920x1080.jpg` | TSC: NS GP38-2 | NS 5195 pulling red gondolas past an industrial loading facility. |
| `trainsim-08.jpg` | `…/apps/208362/ss_181962239dc941a4eebb4cf82c345572fdd4174a.1920x1080.jpg` | TSC: UP SD70ACe | **Modern power.** Low three-quarter of UP 1989 in Rio Grande heritage paint: snowplow pilot, ditch lights, walkways, MU hoses, trucks. |
| `trainsim-09.jpg` | `…/apps/222596/ss_9460e7af454d77794ddbe870e60dd93168d5dab9.1920x1080.jpg` | TSC: UP Heritage SD70ACes | **Modern power.** UP 1996 in SP heritage orange/black leading a long boxcar string under an overpass. |
| `trainsim-10.jpg` | `…/apps/1784570/ss_d18d6d1601fefc21b87f1c48fd75ceea416eca34.1920x1080.jpg` | Trainz Railroad Simulator 2022 | UP GP-series pair (902) with hoppers and a gondola on a mountain curve at a grade crossing. |

Coverage check: tank cars `01`, `02`, `03`; older diesel (F-unit / GP) `04`–`07`, `10`; modern (SD70ACe) `08`, `09`.
**Gap:** no confirmed exterior GE ES44AC/Dash-9 shot — the BNSF ES44AC pack's screenshots turned out to show
only the SD40-2 and cab interiors, so it was excluded rather than mislabelled. SD70ACe carries "modern power".

---

# What the bar actually looks like

Measured values below are computed over the actual files (ImageMagick: channel means, standard deviation,
and luminance percentiles from the 8-bit grey histogram). `B−R` is mean blue minus mean red in 0–255 units —
a direct proxy for colour temperature. `sat%` is mean HSL saturation.

| set | R | G | B | B−R | mean L | σ | p1 | p5 | p50 | p95 | p99 | sat% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| After the Flood (12) | 64.2 | 76.8 | 89.8 | **+25.6** | 76.9 | 37.3 | 21 | 28 | 70 | 140 | 170 | 25.0 |
| Transport Fever 2 (12) | 109.5 | 115.4 | 106.8 | −2.8 | 110.6 | **63.7** | 17 | 28 | 110 | 213 | 236 | 27.0 |
| Train sims (10) | 122.4 | 125.5 | 122.9 | +0.5 | 123.6 | **71.3** | 4 | 20 | 122 | 227 | 240 | 28.9 |

The single most transferable number: **After the Flood is a full stop darker and decisively cooler than
either train sim, and it uses roughly half their contrast range.** Its p95 sits at 140/255 where the train
sims sit at 213–227. It is a low-key, blue-biased image that reserves its top 40% of range for a handful of
small highlights. If a render is judged against it and comes back with a mean near 110 and a p95 near 220,
it is not in the same grade at all.

## 3.1 After the Flood — geometry

- **Density is spent on silhouettes, not surfaces.** Buildings are large planar concrete slabs with almost no
  surface modelling: no window reveals, no mullion depth, no panel joints as geometry. What sells them is that
  there are ~20 of them at wildly non-orthogonal angles (`leveldesigned/buildings`, 20 entities), so the
  silhouette against sky is complex even though each mesh is cheap. **Lesson: buy silhouette complexity, not
  polygon-per-square-metre.**
- **Trees are the exception and get real geometry.** In `aftertheflood-12` the bare canopy resolves several
  hundred individually modelled branches down to twig scale, rendered as near-black tapering tubes — not cards,
  not a billboard. They stay solid and unaliased against a bright sky at 1080p. In `aftertheflood-01`/`-02` the
  leafed maples carry many hundreds of leaf cards clustered on those branches.
- **Leaves are alpha-tested cards, and the demo does not hide it.** `aftertheflood-06` at macro range shows a
  single flat quad per leaf with a maple cutout, and the alpha edge is resolved with a visible
  **alpha-to-coverage stipple** (a regular dither lattice on every leaf boundary, ~1px cell). The `bush_fall_anim`
  entities carry an explicit `alphaTocoverage` script. This is the correct trade for a WebGL budget and it reads
  as film grain at normal distance — but a critic *will* see the dither if the camera gets close.
- **Ground is flat slabs plus a normal map.** The paving in `-06` has no geometric relief at all: the ribbed
  strip and the cracks are entirely texture and normal map. Its credibility comes from the grazing sun raking
  across the ribs, not from displacement.
- **Scatter is the load-bearing detail.** Dozens of individually placed loose leaves on the ground, bollards,
  benches (`benches_batch`), boulders with moss caps, a phone box, streetlamps, overhead cables. Every wide shot
  has 3–5 pieces of small human-scale clutter in the near field. Remove them and the images collapse into grey boxes.

## 3.2 After the Flood — materials and wet surfaces

This is what the reference is actually for.

- **Two distinct wet treatments, not one.** (a) A global **water plane at Y = 0** spanning 8000×8000 units — the
  flood itself. (b) Separately, *damp paving* above the waterline with a darkened albedo and broad low-gloss
  reflection (`aftertheflood-01`, `-05` foreground). Confusing these is the classic failure: the flood mirrors,
  the damp stone only smears.
- **The flood is a planar mirror, ripple-distorted, Fresnel-weighted.** In `aftertheflood-07`:
  - Reflections are **geometrically correct vertical mirror images** — a lit window at height *h* reflects to
    depth *h* below the waterline, and the lamp post's reflection stays perfectly plumb.
  - The mirror image is broken into **horizontal ripple bands**, not blurred. Amplitude grows toward the camera:
    a reflected vertical edge wanders roughly ±4 px near the horizon and ±25–30 px in the bottom third of frame.
    The bands are wide and slow — this is a low-frequency normal map, not noise.
  - **Fresnel is respected and it is the single most important behaviour to copy.** The near-field water
    (steep view angle) is close to black — mean luminance in the bottom quarter of `-07`/`-08` runs 25–45/255,
    barely above the darkest pixels in frame — while the far field toward the horizon brightens to 100–140 and
    picks up the sky. A render whose water is uniformly bright across its depth is instantly wrong.
  - **Reflections are dimmer than their source.** The warm window in `-07` reads ~215/255 direct; its reflection
    peaks around 150–170. Roughly a 25–30% energy loss, plus the ripple spreads it.
  - There is **no refraction, no depth-tinted volume, no caustics**. The water is treated as an opaque dark
    mirror. It still convinces, because the reflection and the Fresnel are right.
- **Sun glitter is a directional path, not scattered sparkles** (`aftertheflood-09`): a coherent brightening lane
  running from the sun's azimuth to the camera, widening toward the viewer.
- **Concrete is one desaturated grey-beige family, differentiated only by lighting.** Nearly every hard surface
  shares a palette; separation comes from which face catches the low sun. Glass towers are dark, near-mirror
  panels with faint warm interior lights punched through.
- **One saturated accent per frame, and only one.** The red telephone box (`-05`) is the sole object above ~60%
  saturation in an otherwise 25%-saturation image; in `-01` it is a 6-pixel red dot a kilometre out and still
  reads. The discipline is the point.

## 3.3 After the Flood — lighting and shadow

- **Single low warm sun + large cool sky ambient.** The lighting rig is ~5 lights (`light_shadow_important_E1/E2`,
  `light_shadowed_tree`, `light_shadow_important_low`, `light_final`) plus a fill group — a small, deliberate set.
- **Sunlit stone reads 190–230/255 and warm; shadowed stone reads 55–85/255 and blue.** That is a ~3-stop key-to-fill
  ratio with a hue shift across it. The crossing stripes in `aftertheflood-01` are the clean test case: the lit
  bands are warm off-white, the paving between them cool grey.
- **Shadows are shadow-mapped and keep a constant, slightly soft edge over their whole length** — see the phone
  box in `-05`. There is **no contact-hardening / PCSS penumbra growth**: the edge at the base of the box is the
  same width as 4 m away. Shadow density is deep but not crushed; occluded paving still holds texture.
- **Contact grounding is done with shadow, not SSAO.** Every bollard, leaf, bench and boulder sits on a distinct
  cast shadow. In `-06` each fallen leaf has its own offset soft drop shadow — that single detail is what stops
  the leaves reading as decals painted on the ground. There is no visible ambient-occlusion darkening in crevices
  beyond what is baked into the albedo.
- **Volumetric fog is an explicit, authored layer.** Dozens of `fogQuad`/`fogQuadGlobal` soft-particle cards plus
  `RayIslandL` light-shaft meshes. Aerial perspective is strong and non-linear: objects ~100 m out lose roughly
  half their contrast, and the distant skyline in `-09`/`-10` collapses to a flat blue-grey silhouette at
  ~20% contrast. Fog is cooler and lighter than the scene, so distance = brighter and bluer.
- **Practical lights are small, warm, and never bloom out.** Streetlamps and window interiors sit around
  200–230/255 with a tight, restrained halo. The luminance p99.9 hits 255 in all twelve frames, but the p99 is
  only ~170 — highlights are *tiny*. Big soft bloom would immediately look wrong here.

## 3.4 After the Flood — sky and grade

- **The sky is raymarched volumetric cloud, not a cubemap.** The `sky` script compiles a shader using a 16×16×16
  volume LUT, curl and noise textures, blue-noise jitter and a temporal accumulation buffer (`skyRt`, `skyBlend`),
  with an explicit sun direction of roughly `(-0.045, 0.021, 0.999)` — i.e. very low on the horizon. Console
  logs "Compiling sky" on load.
- **Gradient, measured on `aftertheflood-12`:** deep desaturated blue at zenith (~RGB 60/90/125), lightening and
  warming continuously to a pale grey-blue near the horizon (~145/165/185), with the horizon band in `-09`
  carrying a faint warm cream where the sun sits. **The gradient is smooth over a ~110-value luminance range with
  no banding** at 8-bit — worth noting, because a naive two-colour vertical lerp bands visibly over that range.
- **Clouds have internal shading, not flat alpha.** Wispy cumulus with a lit edge facing the sun and a bruised
  blue-grey core, soft-edged and non-repeating, and they drift.
- **Grade: cool, low-key, low-contrast, and deliberately narrow.** Mean B−R of **+25.6** across all twelve frames
  (up to +41.8 on the sky-heavy `-12`) — every frame is measurably blue-biased except the leaf macro `-06`
  (−8.9, where warm foliage fills the frame). Shadows fall to 21–28/255 but almost never to 0; midtone sits at
  70/255; p95 at 140. **Nothing in the image is allowed to be bright except light sources and directly sunlit
  stone.** Mean saturation 25%. The look is dusk-after-a-storm, and it holds across every camera position.

## 3.5 Transport Fever 2 — what it actually does

TF2 is a *strategy-scale* renderer that survives close inspection. That combination is the useful lesson.

- **Geometry: modest, spent entirely on read-at-distance shapes.** Locomotive bodies are chamfered boxes; the
  detail budget goes into **separate, real geometry for the things that break silhouette** — handrails as thin
  round tubes (`tf2-03`, clearly 6–8-sided extrusions, not alpha cards), steps, grab irons, walkway grating,
  MU hoses, horn clusters. Bogies are genuinely modelled: separate sideframes, journal boxes, visible springs
  and brake cylinders. Wheels are cylinders, not textures.
- **Track is the standout.** In `tf2-03`: individual sleepers as separate quads with per-sleeper wood-grain
  variation and slight rotational jitter, ballast as a tiling normal-mapped crushed-stone material that reads as
  distinct stones near the camera, rails with a **bright polished specular line on the railhead and a rust-brown
  web** — that two-tone rail is the single detail that most says "real track", and it is nearly free.
- **Catenary is fully modelled** (`tf2-09`): masts, cantilever arms, registration arms, contact wire *and*
  messenger wire *and* the droppers between them. The wires are thin dark geometry against sky and do alias
  slightly — TF2 accepts that rather than dropping them.
- **Materials are diffuse-dominant with baked grunge.** Paint is not metallic and barely reflects; credibility
  comes from a blotchy value-variation overlay on the albedo (fade, dirt streaks, panel discolouration on the
  yellow hood in `-03`). Ambient occlusion under running boards and around fittings is **painted into the
  texture**, not computed. Lettering is crisp decal work.
- **Lighting: one hard directional sun, sharp shadows, bright open sky.** No fog in the near field. Mean
  luminance 110.6, σ 63.7, p95 213 — a bright, high-contrast, near-neutral image (B−R −2.8). Nothing is crushed
  and nothing is precious about highlights.
- **Distance falls off a cliff, and that is the honest weakness.** In `tf2-03` the background ridge is a low-res
  terrain band with a repeating tree-impostor texture; sky is a simple gradient with sparse billboard clouds.
  TF2 knows the camera is normally 30 m up and pays only for what is near.
- **Marketing shots grade differently — do not mistake them for gameplay** (`tf2-10`, `tf2-11`): warm orange key
  against blue fill, heavy atmospheric haze, bloom, vignette and a tilt-shift diorama falloff. `tf2-10` is where
  the tank cars live, so it is worth having, but its grade is not the engine's.

## 3.6 Train sims — and the important split

**These ten files are not one bar. They are two, and only one of them is high.**

**Train Sim World 4 (`trainsim-01`, `-02`, `-03`) — Unreal-based, modern PBR. This is the real bar for rolling stock.**

- `trainsim-01` is the reference frame for petroleum tank cars. Per car: a barrel with **visible weld seams as
  geometry**, a raised end dome with its own guard rail, a full-length side ladder, a walkway with grating,
  handrails, a hazard placard board, underframe brake gear, and modelled bogies with springs and journal boxes.
  Nothing that breaks silhouette is a texture.
- **True PBR metal.** The tank barrels show a wide, soft anisotropic vertical specular sweep down each cylinder
  with a sky-coloured top rolloff and a bounce-warmed underside — you can read the curvature purely from the
  shading. Colour varies car to car (red / grey / blue / green) but the *response* is identical, which is what
  makes them look like the same object in different paint.
- **Weathering is directional and physical:** grime accumulates in the lower third and around fittings, streaks
  run downward from the walkway edges.
- **Depth of field is used as a compositional tool** — the foreground track in `-01` is thrown well out of focus,
  the subject is sharp, and the background trees soften. Ballast at the focal plane resolves individual stones.
- Grade: bright natural daylight, mean 117.5, blue-ish sky bias (+9.8), σ 70.6 — full range used, p1 of 1/255
  (true blacks present) and p95 of 209.

**Train Simulator Classic (`trainsim-04`–`-09`) and Trainz (`-10`) — previous-generation. Use for shape reference, not fidelity.**

- Loco geometry is still good and worth studying for **proportion and fitting placement** — `trainsim-08` (SD70ACe)
  has correctly modelled handrails, ditch lights, snowplow pilot, MU hoses and radiator grilles; `trainsim-06`
  (GP38) has the right hood-unit proportions and yellow safety rails.
- But the **shading is flat and largely baked**: black paint on the GP38 reads as a nearly uniform value with no
  environment response; the SD70ACe's dynamic-brake housing has almost no form shading. Textures are visibly
  lower resolution — ballast in `-06`/`-08` is a mushy tiling wash with no individual stones, nothing like `-01`.
- **Shadows are weak or absent**: the SD70ACe in `-08` has no readable ground shadow at all, so it floats.
  In `-06` the loco sits on the ballast with only a vague dark smear.
- Vegetation is card-based with obvious flat silhouettes and a hard alpha edge; smoke is a soft billboard blob
  (very visible in `-06`); skies are photographic domes that blow to 253–255.
- Several frames carry a **heavy vignette baked into the screenshot** (`-06`, `-07`), which inflates their σ.

### The transferable checklist

If a render is going to be held against this material, these are the specific things a critic will look for:

1. **Fresnel on every wet surface** — near field dark, far field bright. Non-negotiable; it is the first thing
   that reads as fake.
2. **Reflections geometrically plumb and dimmer than source**, broken by low-frequency ripple bands whose
   amplitude grows toward the camera — not a uniform blur.
3. **Damp ≠ flooded.** Two materials, two behaviours.
4. **Every object sits on its own cast shadow**, down to individual leaves. Constant-width shadow edges are
   acceptable; missing shadows are not (see `trainsim-08` for the failure).
5. **A smooth 110-value sky gradient with no banding**, cool at zenith, warm-pale at horizon, with shaded clouds.
6. **Aerial perspective that halves contrast by ~100 m** and flattens the far field to ~20%.
7. **Silhouette-breaking detail as real geometry** — handrails, ladders, branches, catenary wire — and everything
   else as normal-mapped flat.
8. **Two-tone rails** (polished head, rust web) and per-sleeper variation on any track in frame.
9. **One saturated accent per frame**, in an image sitting near 25% mean saturation.
10. **Hold the grade:** for a moody piece, mean L ≈ 77, p95 ≈ 140, B−R ≈ +25. Drifting to mean 110 / p95 215 is
    a different and less controlled image.
