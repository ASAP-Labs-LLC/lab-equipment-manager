# The LEM 3D floor — subsystem contract

Read this before writing a line. Every builder owns exactly ONE file and must
not edit any other file in the repo. If you believe you need a change in
`engine.js`, `index.js`, `camera.js`, `textures.js` or `floor.html`, do not make
it — write what you need into `scratchpad/REQUESTS.md` (append, never rewrite)
and work around it.

## What is being built

The LEM Web Server's lab floor map (`/floor`) is being replaced. It used to be
an SVG drawing: little isometric boxes for instruments, dots travelling down
pipes when a parser parsed. It becomes a rendered 3D world — a small forested
site where each instrument is a **station building**, and every time a station
module parses a print, a **train** leaves that station and runs the line out to
the **LabCore terminal**.

The lab data UI around the map (rails, tally, dialogs, menus) is unchanged and
is not yours to touch.

## The bar

This is judged blind against real references by a harsh critic:

- **PlayCanvas "After the Flood"** — lighting, weather response, render quality.
- **Transport Fever 2** and **Train Simulator** — rail infrastructure realism,
  locomotives, petroleum tank cars, trackside detail.

Reference screenshots and a technical breakdown live in `scratchpad/refs/`
(read `refs/MANIFEST.md`). If they are not there yet, start anyway; check again
before you finish.

**It is a diesel lab.** Petroleum theming throughout: tank cars, fuel racks,
loading gantries, pipe runs, storage tanks. Locomotives should be a realistic
mix of old (ALCO/EMD F-unit and GP-series era) and modern (SD70/ES44 era).

## Hard constraints

1. **Conservative geometry, detail from textures.** Nothing is sculpted that
   can be painted. Whole-scene budget: **≤450 draw calls, ≤2.5M triangles** at
   the `ultra` tier. Instance anything that repeats.
2. **Nothing is downloaded.** No CDN, no image files, no model files, no new
   npm dependency. A lab bench has no internet. Textures are generated on the
   client from `world/textures.js` (noise, canvas painting, `normalFromHeight`).
   The whole page must stay under 8 MB transferred.
3. **60 fps at 1080p on integrated graphics.** The dev machine is an M5 Max, so
   raw fps here means nothing — hold the budgets instead, and keep your GPU
   frame cost proportionate. Respect `onQuality(tier)`: at `low`/`floor` you
   must shed work, not just look slightly worse.
4. **WebGL2, no addons.** Only `three` (the vendored core build) and the
   `world/*` modules. `three/addons/*` is NOT vendored and must not be imported.
5. **Never throw during `build()`.** A subsystem that fails is skipped and the
   map continues without it — but a subsystem that throws mid-frame kills the
   render loop and blanks the floor. Guard anything uncertain.

## The interface

Your file exports one class. `index.js` constructs it, calls `build()`, and
then calls the lifecycle methods below. Every method except the constructor is
optional — implement what you need.

```js
import * as THREE from 'three';

export class Terrain {                 // the export name is fixed per module
  constructor(ctx) { this.ctx = ctx; }

  async build(plan) {}                 // create meshes, add to ctx.scene
  update(dt, t) {}                     // every frame; dt seconds, t elapsed
  onPlan(plan) {}                      // the fleet's layout changed (rare)
  onMachines(machines, plan) {}        // status changed; every 2s poll
  onSelected(uid) {}                   // an instrument was selected, or null
  onHover(uid) {}                      // pointer entered/left an instrument
  onWeather(weather) {}                // weather state changed
  onTime(hours) {}                     // time of day changed
  onQuality(tier) {}                   // the quality ladder stepped
  dispose() {}
}
```

### `ctx`

| field | what it is |
|---|---|
| `ctx.scene` | the `THREE.Scene`. Add your meshes here. |
| `ctx.camera`, `ctx.renderer`, `ctx.engine`, `ctx.rig` | the engine's own objects |
| `ctx.Tex` | `world/textures.js` — `fbm`, `cells`, `paint`, `packORM`, `normalFromHeight`, `makeTexture`, `material(uid, build)` |
| `ctx.plan` | the site layout (below) |
| `ctx.weather` | `{preset, rain, snow, wetness, fog, wind, windAngle, cloud, temperature}` — read it, don't write it |
| `ctx.quality` | the current tier: `{name, scale, shadow, ao, bloom, trees, particles, reflections}` |
| `ctx.ground(x, z)` | terrain height in metres. Returns 0 if terrain is not loaded. |
| `ctx.station(uid)` | one station entry, or null |
| `ctx.seededRandom(key)` | a deterministic `() => 0..1` for that key |
| `ctx.METRES_PER_BAY` | 44 — one unit of the lab's saved grid |
| `ctx.on(name, fn)` / `ctx.emit(name, payload)` | world events |
| `ctx.world` | the `LEMWorld` — `timeOfDay`, `selected`, `machines` |

### `plan`

```js
{
  stations: [{uid, title, machine, index, gx, gy, x, z, rng}],  // x/z in metres
  byUid: Map<uid, station>,
  hub: {uid: '__labcore__', title: 'LABCORE', x, z},
  bounds: {minX, maxX, minZ, maxZ},
}
```

`station.machine` is the live `/api/machines` row and carries what the floor
must keep showing:

- `status` — `GREEN` `YELLOW` `RED` `SERVICE` `DEAD-LINE` `UNKNOWN`
- `sub_statuses` — `{qc, pm, calibration}`, each one of the same six
- `module_running` (bool), `module_state` (`running`/`stopped`/`closed`/`unknown`)
- `title`, `reason`, `maintenance_due`, `effective_specs`, `last_activity`

Status colours, unchanged from the old floor and not open for reinterpretation:

```
GREEN #21c071 · YELLOW #f5c542 · RED #f85b5b
SERVICE #a855f7 · DEAD-LINE #e2483d (hazard stripes) · UNKNOWN #6b7280
```

### Events

- `ctx.on('parse', ({uid, labId}) => …)` — a station module parsed a print.
  **This is what dispatches a train.** One parse, one train.
- `ctx.on('ready', …)` — every subsystem has built.

### Picking

Anything clickable goes in `ctx.world.pickables` and carries the instrument on
itself: `object.userData.machineUid = uid`. Clicking opens that instrument's
record in the left rail; right-click opens its action menu; dragging it moves
the instrument on the floor and is written back to the server. Buildings own
this — nothing else should add pickables.

### Anchors

`ctx.world.anchors` is a `Map<uid, {top: number}>` — the height in metres of
the top of that instrument's building, so labels and beacons know where to sit.
`buildings.js` writes it. Read it with a fallback: `?.top ?? 18`.

## Verifying your work

A dev server with the demo fleet is already running on **port 5601**.

Screenshot your own module in isolation (no other subsystem loaded):

```bash
cd /Users/rynatical/LAB-lem/scratchpad/harness
node shot.mjs \
  --url "http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain&cam=low&time=16.5&weather=clear" \
  --out ../shots/terrain-low.png --seconds 4
```

The project moved off the lab network share on 2026-08-06; everything now
lives under `/Users/rynatical/LAB-lem`. The dev server is on 5601 — **do not
start another one**, and never bind 5599, which `tests/test_tray.py` uses.

`solo.html` query parameters:

- `mods` — comma-separated subsystem ids to load. **Load yours plus only what
  you genuinely need** (e.g. `sky,gi,terrain`). Empty means all.
- `cam` — `wide` `low` `street` `yard` `top`
- `at` — a machine uid to centre on
- `time` — hours, e.g. `7.5`, `13`, `18.75`, `21`
- `weather` — `clear` `overcast` `rain` `storm` `snow` `fog`

The harness writes a sidecar `.json` with fps, draw calls, triangles, quality
tier, payload bytes and **console errors**. A run with errors is a failed run —
fix them. **Read your own PNG back with the Read tool and look at it.** You are
building something visual; shipping unlooked-at is not acceptable.

Iterate until it holds up next to the references. Then write a short section in
`scratchpad/NOTES-<yourmodule>.md`: what you built, the budgets you measured,
the screenshots you took, and what you know is still weak.

## House style

This codebase comments the *why*, in prose, at the point where the reasoning
would otherwise be lost — read `engine.js` or `index.js` for the register. Match
it. No decorative comments, no restating the code. Long files are fine; the
project already has 2500-line ones.
