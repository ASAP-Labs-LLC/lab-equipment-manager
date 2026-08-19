# LEM floor → 3D world · where this got to

Snapshot taken 2026-08-06, mid-run, because the network share was being left.
Everything needed to pick this up on another machine is in this zip.

Live progress board (same URL updates in place):
<https://claude.ai/code/artifact/5eb1a6e8-3591-43a0-8bf2-ee80b23d4e94>

---

## 1. What was asked for

Ryan, verbatim:

> for the web server on the MAP. Change it from little buildings and the parsers
> being dots to a full rendered HTML game (that still is based on the machine,
> still says statuses) but instead of little boxes they are procedurally
> generated buildings that populate automatically on a little forrest map, and
> trains periodically go between them. It must look like a AAA game in terms of
> quality, with conservative poly count geometry but optimized using textures to
> maximize detail. It should have real time weather system as well, and global
> illumination. Msut be able to run on any browser, and still show the LAB data
> an UI just as it does now, but the map is reaplced with this real world 3d
> world. Every time a parser parses it should have a train leave the machine
> "station" and go out into the labcore. No 2d mode, jsut 3d.

Chosen bar, and the perf floor picked from the options offered:

- **PlayCanvas "After the Flood"** — lighting, weather response, render quality.
  Same medium as us: WebGL in a browser, no install.
- **Transport Fever 2** + **Train Simulator** — rail infrastructure, locomotives,
  petroleum tank cars. It is a diesel lab, so the freight is tankers and the
  power is a realistic mix of old and new.
- **60 fps at 1080p on Intel Iris Xe class**, under 3s to first interactive
  frame, under 8 MB transferred.

## 2. The prompt this run is executing

```
Replace the machine map in "LEM Web Server" with a real-time 3D world.
Procedurally generated buildings on a forest map, one per machine, populating
automatically from live data and still showing every status the current map
shows. All the existing LAB data and UI stays exactly as it is. Every time a
parser parses, a train leaves that machine's station and hauls out to LabCore.
No 2D fallback.

The bar is three real things, fetched directly, never described: PlayCanvas
"After the Flood" for lighting, weather and render quality; Transport Fever 2
and Train Simulator for rail infrastructure, track, yards and rolling stock.
It's a diesel lab, so trains are petroleum tankers, a realistic mix of old and
new. Conservative poly count, detail carried by textures, real-time weather,
global illumination, runs in any browser. It must hold 60fps at 1080p on Intel
Iris-class integrated graphics, under 3s to first interactive frame, under 8MB
initial payload.

Break this into the smallest pieces that can be judged on their own. For each,
fan out a builder and a separate critic with fresh context. The critic
screenshots our running map and the reference at matched viewport and camera,
judges them blind with labels stripped, measures the frame rate itself, says
which is better, and names the single biggest remaining gap. Then it goes back
to the builder.

The critic should be a harsh critic. Praise is not useful. If ours does not win,
it keeps going.

/loop on each piece until the critic picks ours blind and the perf numbers hold.
Do not stop before that.

Keep a live progress page updating as the work evolves so I can watch it.

Fan out subagents and ultracode.
```

## 3. What is done

### The page seam — complete, tested

`templates/floor.html` went from 2560 to ~2100 lines. About 430 lines of SVG
renderer (the projection, `prism()`, `drawMachine()`, `layout()`, `drawFloor()`,
the blip animation, the drag handlers) were removed and replaced by a bridge.

**`drawFloor()` and `spawnBlip()` deliberately kept their names.** Eighteen call
sites already meant "the floor changed, show it" and "a print was parsed, show
it leaving" — which is still exactly what they mean. Only the other side
changed. That is why the diff is small despite the change being total.

Everything else on the page is untouched: both rails, the tally, the legend, the
QC/PM/CAL dialogs, corrections, lab hours, sign-in, the debug simulator, the max
map view, the 2s poll of `/api/machines` and `/api/events`.

The 2D toggle is **gone**, not hidden — button, CSS and camera code all removed,
with a test asserting it stays gone.

### The engine — mine, not a framework's

`static/world/engine.js`. three.js is vendored into `static/vendor/` because a
lab bench has no internet; a CDN import is a blank floor. On top of it:

- HDR render target with a depth texture
- half-res horizon-based AO, normals reconstructed from depth (so no normal
  buffer and no extra geometry pass), then a depth-aware blur in x and y
- bloom: threshold at half res, blur chain at quarter
- ACES composite that owns tone mapping, grade, vignette and grain
- FXAA (MSAA on a multi-pass HDR pipeline costs bandwidth integrated parts do
  not have)
- a five-tier quality ladder (`ultra`→`floor`) that steps itself down on a p80
  frame-time window, promptly down and reluctantly up, so a wall display never
  oscillates between tiers
- the shadow map redraws only when something asks for it
  (`engine.shadowNeedsUpdate`), not every frame

`static/world/textures.js` generates every surface on the client from wrapping
value-noise and Worley cells — nothing is downloaded, which is how "detail from
textures" stays affordable inside an 8 MB page.

`static/world/index.js` is the whole seam. Subsystems are loaded **dynamically
and independently**: one that fails is logged and skipped, and the map carries
on without it. The floor is a status display before it is a rendering.

### The import map

`worldmap()` in `web_app.py` emits an import map with a per-file hash on every
module URL. A static `import` cannot carry a version of its own, so this is the
only place a fingerprint can go — without it a screen holding last week's
`terrain.js` runs it against this week's renderer, which is the exact stale-static
failure `static_version()` was added for.

### Subsystems — all 13 files landed, none critiqued yet

| file | what it owns |
|---|---|
| `sky.js` | scattering sky, sun/moon, cloud cover, the environment map everything is lit by |
| `gi.js` | fitted sun shadows, irradiance probe grid with ground bounce, light registry |
| `terrain.js` | forested valley, cut pads, graded rail formation, splatted ground, water, puddles |
| `buildings.js` | one procedural petroleum facility per instrument + the LabCore terminal |
| `rail.js` | track, ballast, sleepers, turnouts, signals, yard |
| `trains.js` | one parse → one train; tank cars behind old or modern diesel power |
| `vegetation.js` | instanced forest, translucent foliage, wind gusts, undergrowth |
| `weather.js` | the state machine: rain, storm, snow, valley fog, lightning, slow drying |
| `labels.js` | name plates, lit status band, QC/PM/CAL pills, hazard stripes, beacons |

### Tests

**1031 server tests passing**, plus 8 new ones for the import map and the
3D-only floor.

Tests that asserted on SVG internals were rewritten to assert the same
*behaviour* in the new medium, not deleted:

- the order-independent bay layout now runs against the shipped `claimBays()`,
  which was made a pure exported function in `index.js` precisely so
  `tests/js/layout.mjs` could keep executing the real code
- drag is still local-until-dropped (the world commits on pointerup, never on
  pointermove — one POST per pixel otherwise)
- clicking bare ground still deselects; finishing a drag still does not

**Two tests are deliberately red** and are the next thing to satisfy:
`TestStatusColours::test_dead_line_still_reads_as_a_barrier` and
`test_the_palette_survived_the_move_to_3d`, both asserting against
`static/world/labels.js`.

Unrelated pre-existing failure: `tests/test_tray.py::TestPortHandover` fails on
socket binds in this sandbox. Nothing to do with this work.

## 4. What is NOT done

**The critic rounds have not run.** Every subsystem is first-draft, self-verified
by its own builder. Nothing has yet been compared blind against the bar, which
is the entire point of the exercise. Known already:

- **The grade is badly off.** Measured: our render sits at **B−R −70.9** and 68%
  saturation. After the Flood runs **+16 to +42** at 43%. Ours is an orange
  image where the bar is a cool one.
- **The sky's horizon is an olive band** where the bar has a smooth 110-value
  gradient running cool at zenith to warm-pale at the horizon.
- **`terrain.js` had a live shader compile error** at snapshot time —
  `'tBumpH' : redefinition`, `'tPuddleAmt' : redefinition` — a uniform declared
  twice. Its builder may have fixed it after this snapshot; check first.
- Buildings, rail, trains and vegetation have never been seen by anyone but
  their own builder.

**Files may be mid-write.** The builder agents were still running when this was
taken. Check each module parses before trusting it.

## 5. How to start it up again

```bash
cd "<wherever you put>/LEM Web Server"
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m pytest tests/ -q            # expect 1031 + 8 passing, 2 red

# the demo lab: 7 instruments, every status, a parse every 4 seconds
python3 ../scratchpad/devworld.py --port 5599
# → http://127.0.0.1:5599/floor
```

The screenshot + measurement harness (needs `npm i playwright` once, and
`npx playwright install chromium`):

```bash
cd scratchpad/harness
node shot.mjs \
  --url "http://127.0.0.1:5599/static/world/dev/solo.html?mods=rail,trains&cam=street&weather=rain&time=17" \
  --out ../shots/rail-street.png --seconds 4
```

It writes the PNG plus a sidecar JSON with fps, draw calls, triangles, quality
tier, transferred bytes and console errors. **Headless chromium here still used
the real GPU** (`channel: 'chromium'` + `--use-angle=metal`), which is worth
knowing — the numbers are real, they are just real for the wrong GPU (see §6).

`solo.html` parameters: `mods` (comma-separated subsystem ids, empty = all),
`cam` (`wide` `low` `street` `yard` `top`), `at` (a machine uid), `time` (hours),
`weather` (`clear` `overcast` `rain` `storm` `snow` `fog`).

Judging tools, both dependency-free:

```bash
python3 harness/grade.py ours.png refs/aftertheflood-03.png   # colour grade
python3 harness/blind.py pair round2-sky ours.png refs/x.png  # blind A/B
python3 harness/blind.py reveal round2-sky A                  # the answer key
```

## 6. The one honest caveat

This was built on an M5 Max. Raw fps here flatters the result by roughly five to
eight times against the Intel Iris Xe target, so **no fps number from this
machine should be quoted as meeting the bar**. What is being held to instead:

- ≤450 draw calls, ≤2.5M triangles at the `ultra` tier
- frame cost as a share of the 16.6 ms budget
- ≤8 MB transferred (currently ~1 MB with four subsystems loaded)
- <3 s to first interactive frame (currently 240 ms)

Real confirmation needs a bench PC. That is a genuine open item, not a formality.

## 7. What is in this zip

```
LEM Web Server/            only the files this work touched
  static/world/*.js          the 13 subsystem modules + engine/camera/textures
  static/world/dev/          the solo harness page
  static/vendor/             three.js, vendored
  templates/floor.html       the rewritten page
  templates/floor.html.svg-backup   the SVG version, if a diff is wanted
  web_app.py                 adds worldmap()
  tests/                     the new and rewritten tests
scratchpad/
  CONTRACT.md                what every builder was held to — read this first
  REQUESTS.md                cross-module requests the builders raised
  refs/                      34 verified reference images + MANIFEST.md
  harness/                   shot.mjs, grade.py, blind.py, dbg.mjs
  shots/                     build screenshots (JPEG) + their measurement JSON
  devworld.py                the offline 7-instrument demo lab
  progress.html              the live progress board
  RESUME.md                  this file
```

`node_modules` and `.venv` are excluded — both reinstall in one command.

## 8. The next move

Run the blind critic rounds. One critic per subsystem, fresh context, given the
pair directory and the checklist in `refs/MANIFEST.md` — never told which image
is ours. Binary verdict, one biggest gap, back to the builder. Start with the
grade and the sky, because they are measurably wrong and everything else is
photographed through them.
