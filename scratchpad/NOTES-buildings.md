# NOTES — buildings.js

## r8 (2026-08-07) — "the clipping through the ground for the train stations"

### What was actually wrong

Not the building origin. The soak's dock check passes and always did. Terrain
grades **one tilted design plane** across the whole yard (capped at 1.8% per
axis) and cuts a level pad on it at each station; `_padHeight` put the site
origin at one point on that plane and every one of the ~300 parts was then
placed from that single height. So a facility was buried at its uphill corner
and hanging over its downhill one, and the terminal — 300 x 150m, of which
terrain grades 128 x 96 — was mostly standing on raw landscape.

Measured before the change, over each site's true footprint:

| site | terrain vs datum | footprint >0.25m under ground |
|---|---|---|
| stations (worst) | +1.48 / −1.30 m | 39.2 – 39.9 % |
| `__labcore__` | **+3.70 / −24.37 m** | 39.6 % |

The terminal picture is the one to look at: `shots/r8-base/hub.png`. The west
half of the yard — flare stack, fin-fan bank, a whole tank bund — is standing on
grass with no slab anywhere near it.

### What it is now

**`makeEarthworks(ground, y0, ox, oz, {hx, hz, cz, band})`** — one function per
site defining the surface the facility stands on, in local metres:

- ground at or above the design level is **followed** (you cannot wish a level
  pad over a hill, and raising the site to its highest corner puts the platform
  1.5m above the track it serves);
- ground below it is **filled**, but only over the core; the fill dies away over
  `band` metres so the rim of a large yard meets natural ground instead of
  standing on a four-storey retaining wall.

Station core = the whole apron (so a station is still a level platform on fill,
and nothing changes on flat ground). Terminal core = 80 x 56 with a 70m band, so
its outer sixty metres conform.

**`Kit` is founded, not flat.** `kit.stand(x, z, hx, hz)` sets the level every
subsequent part is placed from, to the *highest* earthworks under that footprint;
`kit.foot(key, …)` does the same and casts the plinth down past the *lowest*, so
a 60m bund across a grade meets the ground at both ends. Every archetype calls
one per rigid volume — hall, stack, annex, bund, control room, pumphouse, day
tank, admin block. Small kit finds its own ground: flood masts, pipe-rack bents
(legs of different lengths under one level pipe run), gantry legs, water-tower
legs, fin-fan legs, bullet-tank saddles, each fence bay, each service barrier.

**The podium is an earthworks, not a box.** `slabGrid` / `slabSkirt` build the
apron as a conforming grid with a retaining skirt whose bottom follows the real
terrain point by point — which is the whole reason it is a strip and not a box: a
box has one bottom, and a yard on a grade needs its downhill edge metres deeper
than its uphill one. The terminal also gets a flipped underside grid, because
twelve metres of fill under a one-sided surface is back-face culled from below.

`APRON = 0.35` is the make-up the finished surface stands proud of the
earthworks. Where the rule follows the ground it returns the ground *exactly*,
which is coplanar with the terrain mesh — and coplanar is z-fighting. The
terminal's tarmac came out blotched with hillside showing through it
(`shots/r8-real/L0-hub.png` before, `shots/r8-real2/hub.png` after).

### Measured after

`padUnderGround` = min over the footprint of (earthworks surface − terrain):

| | before | after |
|---|---|---|
| footprint under ground, stations | 39.2 – 39.9 % | **0 %** (min +0.35m) |
| footprint under ground, terminal | 39.6 % | **0 %** (min +0.35m) |
| max fill the skirt must cover | n/a | 1.8m stations, 12.2m terminal — skirt bottom tracks the ground, so it meets it regardless |

Budgets, `mods=terrain,buildings`, ultra: buildings = **98 draw calls, 192k
triangles** (113/436k with terrain, 15/245k terrain alone). The conforming slabs
add roughly 25k triangles across eight sites and **no draw calls at all** — every
one goes into a material group the site already had, via `kit.raw`. Whole scene
at ultra in `film.mjs`: 329 draws / 2.20M tris / 120fps, against 450 / 2.5M.

### Verified

- `soak.mjs --parses 200 --layouts 4`: `floating 0 · collision 0 · reversal 0 ·
  unreachable 0 · consoleErrors 0`. It reports FAIL on `edge: 20` only — 26–41m
  terrain steps at 0.9–1.3km radius, an assertion that reads `terrain.heightAt`
  and nothing else. It passed at ~17:05 today and started failing when
  `terrain.js` replaced FAR_SIZE/BACK_SIZE. Logged in REQUESTS.md.
- `film.mjs --frames 9 --every 1200 --cam yard --time 16`: no console errors,
  120fps every frame, `shots/r8-film/`.
- Read back: three stations on layout 0 (`multitek-ns`, `koehler-cp`,
  `pac-flash-1`) and three on layout 2 (`optimpp-1`, `multitek-s`,
  `pac-flash-2`) at `cam=street`, plus the terminal wide. `shots/r8-real/`,
  `shots/r8-real2/`.
- `shots/r8-acc/` are the same six on a **synthetic** 3%-per-axis grade
  (`bclip.mjs --synth`), taken while `terrain.js` was failing to build. Worth
  keeping: the synthetic grade is harsher than the real design plane, so it is
  the stronger test of the founding rule.

### Still weak

- **`worstBareDrop` over-reports.** The audit flags a founding whose footprint
  spans a grade and which has no `foot()` plinth, but it cannot see the ones that
  fill the gap by hand (gantry legs, bullet saddles, tower legs). The terminal's
  14.4m "bare" drop is a 22m bullet tank whose saddles are drawn to their own
  ground and is fine. Someone reading the number cold will chase a ghost.
- **The terminal fills 12m deep mid-yard.** Correct, closed, and it renders — but
  it is a big embankment and the batter is a vertical concrete face rather than a
  graded slope. A real one would be 1:1.8 rip-rap.
- **The station apron is still a hard rectangle on the landscape.** Nothing hangs
  and nothing is buried, but the edge is a kerb line rather than a batter, and
  from the wide camera seven of them read as seven trays.
- The two blind-critic items — untextured boxes with no contact darkening, and
  the repeated window tile — were not touched this pass. The facade patch
  (`bldField`/`bldMacro`) and the window reveals are from earlier rounds and are
  still in; whether they read is a judging question, not a measured one.
- `harness/bclip.mjs` is new: numeric audit + screenshot from one page load,
  `--layout`, `--at labcore`, `--dx/--dz`, `--synth`.
