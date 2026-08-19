/* weather.js — the clock the rest of the world runs on.
 *
 * Everything else in the lab world *reacts* to weather: the terrain darkens
 * when it is wet, the trees bend when it blows, the sky takes its cloud cover
 * from here, the buildings turn their lights on early under a storm. This file
 * is the only thing that decides what the weather actually is, and it decides
 * it by evolving — clear becomes fair becomes overcast becomes drizzle becomes
 * rain becomes storm becomes clearing, on plausible dwell times, with every
 * parameter interpolated across the change rather than snapped.
 *
 * That matters because of what this screen is. The floor is a wall display. It
 * is on all day in a room where nobody is looking at it most of the time, and
 * the thing that makes a person glance back at a wall display is that it is
 * never quite the same twice. A fixed "rain" preset is wallpaper by lunchtime.
 * A full cycle here runs 20–40 minutes and never repeats its timings, so the
 * site looks different at 09:15 than it did at 08:40 without anybody touching
 * it.
 *
 * Three rules shaped the implementation:
 *
 *   1. **The setter is the notification.** `ctx.weather` is a plain object that
 *      every subsystem holds a reference to, but writing into it silently is
 *      how you get a terrain that is dry in a downpour: `LEMWorld.setWeather`
 *      is what calls `onWeather` on everyone and marks the shadow map dirty.
 *      So state goes out through `ctx.world.setWeather()`, and — because that
 *      call rebuilds the shadow map, which is the most expensive thing in the
 *      frame — it goes out *throttled*, when a parameter has actually moved far
 *      enough to see. Interpolation happens at full frame rate in here; the
 *      published copy steps at about 0.01.
 *
 *   2. **Precipitation is one draw call each.** Rain and snow are instanced
 *      quads whose position is computed entirely in the vertex shader from a
 *      per-instance seed and the clock, wrapped into a box centred on the
 *      camera. No CPU particle loop, no per-frame buffer upload, and the volume
 *      follows the viewer for free. Density is `instanceCount`, which costs
 *      nothing to change, so shedding work at a lower quality tier is one
 *      assignment.
 *
 *   3. **Rain is lit by being blended, not shaded.** The beauty pass renders
 *      into a linear HDR target, so a screen blend (`src·(1−dst)`) brightens a
 *      streak against a dark forest and *darkens* it against a blown-out sky,
 *      because `1−dst` goes negative where the sky is above 1.0. That is the
 *      real behaviour of a water streak — bright against dark, dark against
 *      bright — and it falls out of the blend function for one draw call rather
 *      than a framebuffer read we cannot afford.
 *
 * Nothing here is allowed to throw. A weather module that dies mid-frame takes
 * the render loop with it, and the floor is a status display before it is a
 * rendering — so every section of `build()` is guarded independently and a
 * section that fails simply leaves that effect missing.
 */
import * as THREE from 'three';

/* The states, and what each one *is* as a set of parameters. `precip` is total
 * precipitation before temperature decides whether it falls as rain or as snow;
 * `dwell` is the range, in seconds, that the state holds before it moves on.
 *
 * The dwell numbers are the whole feel of the system. They sum, around a
 * typical loop, to roughly 24 minutes of dwell plus seven transitions of ~45s,
 * which lands inside the 20–40 minute cycle the brief asks for. Shorten them
 * and the sky becomes a slideshow; lengthen them and the display is wallpaper
 * again. */
const PRESETS = {
  clear:    {cloud: 0.06, fog: 0.05, precip: 0.00, wind: 0.20, dwell: [210, 400]},
  fair:     {cloud: 0.34, fog: 0.11, precip: 0.00, wind: 0.30, dwell: [170, 320]},
  overcast: {cloud: 0.86, fog: 0.26, precip: 0.00, wind: 0.42, dwell: [150, 300]},
  drizzle:  {cloud: 0.90, fog: 0.42, precip: 0.22, wind: 0.38, dwell: [110, 220]},
  rain:     {cloud: 0.96, fog: 0.52, precip: 0.78, wind: 0.58, dwell: [120, 250]},
  storm:    {cloud: 1.00, fog: 0.64, precip: 1.00, wind: 0.95, dwell: [90, 190]},
  clearing: {cloud: 0.46, fog: 0.20, precip: 0.00, wind: 0.34, dwell: [110, 210]},
  fog:      {cloud: 0.55, fog: 0.94, precip: 0.00, wind: 0.06, dwell: [150, 290]},
  snow:     {cloud: 0.90, fog: 0.50, precip: 0.72, wind: 0.40, dwell: [190, 350]},
};

/* Where each state can go next, and how likely. The graph is deliberately not
 * a ring: a storm can dump straight back into rain and go round again, fog can
 * appear out of a clear morning, and clearing can fall back to overcast. What
 * it must never do is offer a jump that reads as a glitch — clear to storm — so
 * there is no edge for one. */
const CHAIN = {
  clear:    [['fair', 0.66], ['fog', 0.17], ['overcast', 0.17]],
  fair:     [['overcast', 0.52], ['clear', 0.31], ['fog', 0.17]],
  overcast: [['drizzle', 0.40], ['rain', 0.24], ['clearing', 0.21], ['fog', 0.15]],
  drizzle:  [['rain', 0.48], ['overcast', 0.26], ['clearing', 0.26]],
  rain:     [['storm', 0.34], ['drizzle', 0.35], ['clearing', 0.31]],
  storm:    [['rain', 0.58], ['clearing', 0.42]],
  clearing: [['fair', 0.58], ['clear', 0.31], ['overcast', 0.11]],
  fog:      [['overcast', 0.40], ['clearing', 0.34], ['fair', 0.26]],
  snow:     [['overcast', 0.44], ['clearing', 0.31], ['fog', 0.25]],
};

/* The states that are "weather happening" — the ones a temperature drop can
 * turn into snow instead. */
const WET_STATES = new Set(['drizzle', 'rain', 'storm']);

/* How far a parameter has to move before the rest of the world is told. These
 * are not a micro-optimisation: `setWeather` marks the shadow map dirty, and
 * the shadow map is the most expensive single thing in the frame, so a value
 * that wanders continuously (wind angle, gust, temperature) would otherwise
 * hold the renderer at a rebuild every frame for a change nobody can see. Each
 * threshold is roughly "the smallest step that shows on screen". */
const PUBLISH_EPS = {
  rain: 0.010, snow: 0.010, wetness: 0.012, fog: 0.010, cloud: 0.010,
  wind: 0.012, windGust: 0.055, windAngle: 0.05, temperature: 0.35,
  snowCover: 0.012, snowfall: 0.010, visibility: 0.015,
};
const PUBLISH_KEYS = Object.keys(PUBLISH_EPS);

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = t => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
/* smoothstep with an inverted edge pair, i.e. 1 below `a` and 0 above `b`. */
const fade = (a, b, x) => smooth(clamp((x - a) / (b - a || 1e-6), 0, 1));

/** Deterministic PRNG. The whole system is reproducible from one integer, so a
 *  screenshot taken today can be taken again next week. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* Rain. Position, motion blur and billboarding are all one vertex shader: the
 * instance's seed places it in a box, the clock moves it along the wind-tilted
 * fall vector, and a modulo wraps it around the camera so the volume travels
 * with the viewer without the drops travelling with it. */
const RAIN_VS = /* glsl */`
  in vec3 aSeed;      // 0..1 position in the box
  in vec3 aRand;      // width, length and brightness jitter
  uniform float uTime;
  uniform vec3 uCam, uExtent, uVel;
  uniform float uWidth, uLen, uNear;
  out vec2 vUv;
  out float vFade;

  void main() {
    vec3 free = (aSeed * 2.0 - 1.0) * uExtent + uVel * uTime;
    /* Wrap into a box centred on the camera. The drop keeps falling in world
     * space — it is only its *tile* that follows the viewer — so panning the
     * camera does not drag the rain along with it. */
    vec3 rel = mod(free - uCam + uExtent, uExtent * 2.0) - uExtent;
    vec3 p = uCam + rel;

    vec3 dir = normalize(uVel);
    vec3 toCam = uCam - p;
    float dist = length(toCam);
    vec3 side = normalize(cross(dir, toCam / max(dist, 0.001)));

    /* The streak IS the motion blur: its length is the distance the drop covers
     * in one notional exposure, so heavier (faster) rain draws longer. */
    vec3 world = p
      + side * (uv.x - 0.5) * uWidth * aRand.x
      + dir  * (uv.y - 0.5) * uLen  * aRand.y;

    vUv = uv;
    /* Kill drops in the camera's lap — at half a metre one drop is a bar across
     * the frame — and taper the tile edge so the box has no visible wall. */
    vFade = smoothstep(uNear, uNear * 5.0, dist) *
            (1.0 - smoothstep(uExtent.x * 0.55, uExtent.x * 0.99, length(rel.xz))) *
            aRand.z;
    gl_Position = projectionMatrix * viewMatrix * vec4(world, 1.0);
  }`;

const RAIN_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  in float vFade;
  uniform vec3 uColor;
  uniform float uIntensity;
  layout(location = 0) out vec4 outColor;
  void main() {
    float across = 1.0 - abs(vUv.x - 0.5) * 2.0;
    across = across * across;
    float along = smoothstep(0.0, 0.22, vUv.y) * smoothstep(1.0, 0.72, vUv.y);
    float a = across * along * vFade * uIntensity;
    if (a < 0.002) discard;
    outColor = vec4(uColor * a, a);
  }`;

/* The dark half of a rain streak. Screen blending alone only darkens a drop
 * where the background is above 1.0 in HDR, which is the sky and nothing else —
 * so a wider, softer multiply pass underneath gives the streak the refractive
 * body it has against a *lit* wall or a pale gravel yard, and the screen pass
 * on top becomes its specular core. Two draw calls for the whole storm. */
const RAIN_DARK_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  in float vFade;
  uniform float uIntensity;
  layout(location = 0) out vec4 outColor;
  void main() {
    float across = 1.0 - abs(vUv.x - 0.5) * 2.0;
    float along = smoothstep(0.0, 0.30, vUv.y) * smoothstep(1.0, 0.68, vUv.y);
    float a = across * along * vFade * uIntensity;
    if (a < 0.002) discard;
    outColor = vec4(vec3(1.0 - a), 1.0);
  }`;

/* Snow. Same wrap, different physics: slow, swaying, tumbling, and billboarded
 * to the camera rather than to the fall vector, because a snowflake has no
 * streak to align to. */
const SNOW_VS = /* glsl */`
  in vec3 aSeed;
  in vec3 aRand;      // size, spin rate, sway phase
  uniform float uTime;
  uniform vec3 uCam, uExtent, uVel;
  uniform float uSway, uNear;
  out vec2 vUv;
  out float vFade;

  void main() {
    float ph = aRand.z * 6.2831853;
    vec3 sway = vec3(sin(uTime * 0.55 + ph), 0.0, cos(uTime * 0.41 + ph * 1.7)) * uSway;
    vec3 free = (aSeed * 2.0 - 1.0) * uExtent + uVel * uTime + sway;
    vec3 rel = mod(free - uCam + uExtent, uExtent * 2.0) - uExtent;
    vec3 p = uCam + rel;

    /* Camera-facing, tumbling about the view axis. */
    vec3 f = normalize(uCam - p);
    vec3 r = normalize(cross(vec3(0.0, 1.0, 0.0), f));
    vec3 u = cross(f, r);
    float a = uTime * aRand.y * 2.4 + ph;
    vec2 q = (uv - 0.5) * aRand.x;
    vec2 rot = vec2(q.x * cos(a) - q.y * sin(a), q.x * sin(a) + q.y * cos(a));
    vec3 world = p + r * rot.x + u * rot.y;

    vUv = uv;
    float dist = length(uCam - p);
    vFade = smoothstep(uNear, uNear * 5.0, dist) *
            (1.0 - smoothstep(uExtent.x * 0.55, uExtent.x * 0.99, length(rel.xz)));
    gl_Position = projectionMatrix * viewMatrix * vec4(world, 1.0);
  }`;

const SNOW_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  in float vFade;
  uniform vec3 uColor;
  uniform float uIntensity;
  layout(location = 0) out vec4 outColor;
  void main() {
    float d = length(vUv - 0.5) * 2.0;
    /* A wide, soft falloff rather than a hard disc: a flake twenty metres out
     * covers about a pixel, and a hard-edged disc at that size aliases into a
     * little square that reads as dirt on the lens. */
    float a = pow(max(0.0, 1.0 - d), 1.6) * vFade * uIntensity;
    if (a < 0.003) discard;
    outColor = vec4(uColor * a, a);
  }`;

/* Splash rings. Each instance carries where it landed and when; the shader
 * derives the whole life of the ring from the clock, so the CPU only writes a
 * slot when it respawns. */
const SPLASH_VS = /* glsl */`
  in vec4 aPos;       // xyz world, w max radius
  in vec2 aLife;      // spawn time, duration
  uniform float uTime;
  out vec2 vUv;
  out float vPhase;
  void main() {
    float ph = clamp((uTime - aLife.x) / max(aLife.y, 0.001), 0.0, 1.0);
    /* sqrt: a ripple sprints outward and then loiters, which is what makes it
     * read as water rather than as a growing circle. */
    float r = mix(0.05, aPos.w, sqrt(ph));
    vec3 world = aPos.xyz + vec3(position.x, 0.0, position.y) * r * 2.0;
    vUv = uv; vPhase = ph;
    gl_Position = projectionMatrix * viewMatrix * vec4(world, 1.0);
  }`;

const SPLASH_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  in float vPhase;
  uniform vec3 uColor;
  uniform float uIntensity;
  layout(location = 0) out vec4 outColor;
  void main() {
    float d = length(vUv - 0.5) * 2.0;
    float ring = smoothstep(0.52, 0.90, d) * smoothstep(1.02, 0.88, d);
    float crown = (1.0 - smoothstep(0.0, 0.45, d)) * (1.0 - smoothstep(0.0, 0.30, vPhase));
    float a = (ring * (1.0 - vPhase) + crown * 0.6) * uIntensity;
    if (a < 0.003) discard;
    outColor = vec4(uColor * a, a);
  }`;

/* The stacked haze layers: ground fog and low scudding cloud are the same
 * shader with different numbers. Each vertex carries its layer index so all the
 * sheets ride in one draw call, and the terrain's own depth clips the sheet —
 * which is why fog pools in the valleys without anything here knowing where the
 * valleys are. */
const SHEET_VS = /* glsl */`
  in float aLayer;
  uniform sampler2D uMap;
  uniform float uSpread, uRise, uTime, uDrift, uWave;
  uniform vec2 uWind;
  out vec2 vUv;
  out float vLayer;
  out vec3 vWorld;
  void main() {
    vec2 suv = uv * uSpread + uWind * uTime * uDrift * (0.55 + aLayer * 0.35)
               + vec2(aLayer * 0.37, aLayer * 0.61);
    vec3 p = position;
    p.y += aLayer * uRise;
    /* Ripple the sheet with the same noise that colours it. A dead-flat plane
     * cutting a hillside draws a razor-straight line across the slope, and a
     * straight line is the one thing fog never has — this turns the same
     * intersection into a ragged, drifting bank edge for the cost of a vertex
     * texture fetch on a grid of a few hundred points. */
    p.y += (texture(uMap, suv * 0.5).a - 0.45) * uWave;
    vec4 world = modelMatrix * vec4(p, 1.0);
    vUv = suv;
    vLayer = aLayer;
    /* The world position, and NOTHING derived from it. A sheet is two triangles
     * covering a kilometre, so anything computed per-vertex — distance, view
     * angle — is a linear blend of four corner values across the whole thing,
     * and the middle of the quad gets a distance that belongs to its edge. The
     * position interpolates correctly; the derived quantities do not. */
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }`;

const SHEET_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  in float vLayer;
  in vec3 vWorld;
  uniform sampler2D uMap;
  uniform vec3 uColor;
  uniform float uOpacity, uLayers, uNearFade, uFarFade, uGraze;
  layout(location = 0) out vec4 outColor;
  void main() {
    vec3 toEye = vWorld - cameraPosition;
    float dist = length(toEye);
    /* How square-on the sheet is seen. A horizontal plane viewed from its own
     * height collapses to a line, and a line of noise on the horizon is the
     * single most obvious tell that the "cloud" is a quad. */
    float graze = abs(toEye.y) / max(dist, 0.001);

    /* Blur the sample towards a coarser mip as the sheet turns edge-on. At a
     * degree or two of grazing one texel covers a whole screen row and the
     * noise resolves into a comb of pickets along the horizon — anisotropic
     * filtering cannot save a plane seen from inside its own thickness. */
    float bias = (1.0 - smoothstep(0.0, 0.30, graze)) * 4.0;
    float n = texture(uMap, vUv, bias).a;
    float m = texture(uMap, vUv * 0.43 + vec2(0.19, -0.31), bias).a;
    float a = n * (0.45 + 0.75 * m);
    /* Thin the stack towards the top so the bank has a soft ceiling rather than
     * a lid, and fade both ends of the distance range so no sheet ever shows an
     * edge. */
    a *= (1.0 - vLayer / max(uLayers, 1.0) * 0.82);
    a *= smoothstep(0.0, uNearFade, dist) * (1.0 - smoothstep(uFarFade * 0.55, uFarFade, dist));
    a *= smoothstep(0.0, uGraze, graze);
    a *= uOpacity;
    if (a < 0.004) discard;
    outColor = vec4(uColor * a, a);
  }`;

/* Light shafts, done as one camera-locked quad rather than a volumetric march:
 * a radial burst at the sun's screen position, chopped by angular noise so it
 * reads as shafts broken by trees instead of a lens flare. It is a cheat — it
 * has no occlusion — which is why it only ever appears when the sun is low and
 * the air is thick, where a real shaft would be washing over everything anyway. */
const SHAFT_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform vec2 uSun;
  uniform float uAspect, uTime, uStrength;
  uniform vec3 uColor;
  layout(location = 0) out vec4 outColor;
  void main() {
    vec2 d = (vUv - uSun) * vec2(uAspect, 1.0);
    float r = length(d);
    float ang = atan(d.y, d.x);
    float rays = (0.5 + 0.5 * sin(ang * 21.0 + uTime * 0.21)) *
                 (0.55 + 0.45 * sin(ang * 9.0 - uTime * 0.13 + 1.7)) *
                 (0.6 + 0.4 * sin(ang * 43.0 + uTime * 0.07));
    float body = exp(-r * 2.6) * rays * smoothstep(0.02, 0.16, r);
    float core = exp(-r * r * 55.0);
    float a = (body * 0.55 + core * 0.85) * uStrength;
    if (a < 0.002) discard;
    outColor = vec4(uColor * a, a);
  }`;

const SHAFT_VS = /* glsl */`
  out vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`;

export class Weather {
  constructor(ctx) {
    this.ctx = ctx;
    this.enabled = true;
    this.group = null;

    const q = (() => {
      try { return new URLSearchParams(location.search); } catch { return null; }
    })();
    this.seed = Math.max(1, parseInt(q?.get('seed') || '', 10) || 20260806);
    /* Three streams, deliberately separate. `rng` draws the chain and nothing
     * else, so the sequence of skies a display walks through is the same on
     * every run whatever the particle counts or the frame rate did; `rngFx`
     * feeds the things that draw per frame (splash placement, strike timing),
     * which would otherwise make the forecast depend on how fast the GPU is. */
    this.rng = mulberry32(this.seed);
    this.rngFx = mulberry32(this.seed ^ 0x9e3779b9);
    this.rngBuild = mulberry32(this.seed ^ 0x85ebca6b);

    /* A forced preset from the harness (or from anyone calling setWeather with
     * a preset we did not publish) stops the machine dead. A screenshot that
     * says `weather=storm` has to be a storm four seconds later and still a
     * storm forty seconds later, or comparing two runs means nothing. */
    this.forced = q?.get('weather') || null;
    this.locked = !!this.forced;

    this.state = this.forced && PRESETS[this.forced] ? this.forced : 'fair';
    this.from = this.state;
    this.phase = 'dwell';
    this.phaseT = 0;
    this.dwellFor = this._dwell(this.state);
    this.transFor = 45;
    this.queue = [];
    this._fillQueue();

    /* Live parameters. `p` is the truth, updated every frame; `published` is
     * the last copy the rest of the world was told about. */
    const base = PRESETS[this.state];
    const cold = this.state === 'snow';
    this.p = {
      preset: this.state, cloud: base.cloud, fog: base.fog, precip: base.precip,
      wind: base.wind, windAngle: 0.6,
      rain: cold ? 0 : base.precip, snowfall: cold ? base.precip : 0,
      snow: cold ? 0.9 : 0, snowCover: cold ? 0.9 : 0,
      wetness: 0, temperature: cold ? -3 : 13,
      windGust: 0, lightning: 0, visibility: 1,
    };
    this.published = {};
    this._selfPublish = false;
    this._sincePublish = 0;

    this.tempPhase = this.rng() * 100;
    this.windPhase = this.rng() * 100;
    this.hours = ctx.world?.timeOfDay ?? 12;

    this.tier = ctx.quality || {particles: 1, name: 'ultra'};
    this.time = 0;
    this.strikeIn = 4 + this.rng() * 6;
    this.flash = 0;
    this._flashSeq = null;
    this._sunLight = null;
    this._sunScan = 0;
    this._sunDir = new THREE.Vector3(0.4, 0.7, 0.5);
    this._fogColor = new THREE.Color(0.55, 0.62, 0.72);
    this._ownsFog = false;
    this._ownsBackground = false;
    this._tmp = {v: new THREE.Vector3(), v2: new THREE.Vector3()};
    this._disposables = [];
  }

  /* ---- construction ------------------------------------------------------ */

  async build() {
    this._hasTerrain = !!this.ctx.world?.subsystems?.has?.('terrain');
    this._white = new THREE.Color(1, 1, 1);
    const parts = [
      ['group', () => {
        this.group = new THREE.Group();
        this.group.name = 'weather';
        this.group.matrixAutoUpdate = false;
        this.ctx.scene.add(this.group);
      }],
      ['fog', () => this._buildFog()],
      ['devGround', () => this._buildDevGround()],
      ['sheets', () => this._buildSheets()],
      ['rain', () => this._buildRain()],
      ['snow', () => this._buildSnow()],
      ['splash', () => this._buildSplashes()],
      ['storm', () => this._buildStorm()],
      ['shafts', () => this._buildShafts()],
    ];
    for (const [name, fn] of parts) {
      try { fn(); }
      catch (err) {
        /* One broken effect is a missing effect. It is never a broken floor. */
        console.warn(`[weather] "${name}" did not build — continuing without it.`, err);
      }
    }
    try { this._applyTier(); this._recompute(0); this._publish(true); }
    catch (err) { console.warn('[weather] first tick failed', err); }
  }

  /** Ground height, from whoever actually knows it. Guarded because a splash
   *  landing on a terrain that is still building must not take the frame down
   *  with it. */
  _ground(x, z) {
    try {
      if (this._hasTerrain) return this.ctx.ground(x, z) || 0;
      return this._devHeight ? this._devHeight(x, z) : 0;
    } catch { return 0; }
  }

  /** Two tileable alpha noises: one soft and billowy for fog banks, one with
   *  more contrast for cloud. Generated once, shared by every sheet. */
  _noise(size, scale, contrast, seed) {
    const T = this.ctx.Tex?.Tex || this.ctx.Tex || {};
    const fbm = T.fbm;
    const paint = T.paint;
    if (!fbm || !paint) return null;
    const cv = paint(size, (x, y, u, v) => {
      const n = fbm(u * scale, v * scale, {octaves: 4, period: scale, seed});
      const m = fbm(u * scale * 2.7 + 4, v * scale * 2.7, {octaves: 3, period: scale * 2.7, seed: seed + 41});
      let a = (n * 0.72 + m * 0.28 - 0.34) * contrast;
      a = clamp(a, 0, 1);
      return [1, 1, 1, a * a * (3 - 2 * a)];
    });
    const tex = new THREE.CanvasTexture(cv);
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.colorSpace = THREE.NoColorSpace;
    tex.anisotropy = 16;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    this._disposables.push(tex);
    return tex;
  }

  _buildFog() {
    const scene = this.ctx.scene;
    if (!scene.fog) {
      scene.fog = new THREE.FogExp2(this._fogColor.getHex(), 0.0006);
      this._ownsFog = true;
    }
    /* If nothing has drawn a sky by the time we run, the horizon *is* the fog,
     * and a black void behind a rain curtain tells nobody anything. We take the
     * background only while it is unclaimed and hand it straight back the
     * moment a real sky appears. */
    if (scene.background === null && !this.ctx.world?.subsystems?.has?.('sky')) {
      this._bg = new THREE.Color(this._fogColor);
      scene.background = this._bg;
      this._ownsBackground = true;
    }
  }

  /* The solo harness can load this module on its own, and rain with no ground
   * under it has no splashes, no wet sheen and nothing for the fog to pool in —
   * which makes the one screenshot that is supposed to prove the module works
   * prove nothing. This is a stand-in floor, and only in the dev harness with
   * no real terrain loaded. It has relief on purpose: a fog bank over a perfect
   * plane is a flat veil, and "it pools in the valley" is a claim that can only
   * be checked against a valley. */
  _buildDevGround() {
    let dev = false;
    try { dev = location.pathname.includes('/world/dev/'); } catch { dev = false; }
    if (!dev || this.ctx.world?.subsystems?.has('terrain')) return;

    const T = this.ctx.Tex?.Tex || this.ctx.Tex || {};
    const fbm = T.fbm;
    const height = fbm
      ? (x, z) => (fbm(x / 900 + 8, z / 900 + 3, {octaves: 4, period: 6, seed: 5}) - 0.52) * 62 +
                  (fbm(x / 240, z / 240, {octaves: 3, period: 12, seed: 91}) - 0.5) * 7
      : () => 0;

    /* Zeroed at the origin, because the harness orbits (0,0,0) and a camera
     * buried thirty metres inside the hill it is meant to be looking at makes
     * every screenshot a lie. And bowled at the rim, so the far edge of the
     * quad falls below the horizon instead of drawing a dashed line across it —
     * which is precisely the artefact this scaffold was accused of two rounds
     * ago, and it was the scaffold. */
    const SIZE = 7000, SEG = 88, HALF = SIZE / 2;
    const base = height(0, 0);
    const relief = (x, z) => {
      const r = Math.min(1, Math.hypot(x, z) / HALF);
      return height(x, z) - base - Math.pow(r, 3) * 620;
    };
    this._devHeight = relief;

    const geo = new THREE.PlaneGeometry(SIZE, SIZE, SEG, SEG);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.getAttribute('position');
    for (let i = 0; i < pos.count; i++) {
      pos.setY(i, relief(pos.getX(i), pos.getZ(i)));
    }
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({color: 0x6b6f5e, roughness: 0.94});
    if (T.paint) {
      const cv = T.paint(256, (x, y, u, v) => {
        const n = T.fbm(u * 6, v * 6, {octaves: 5, period: 6, seed: 3});
        const g = 0.26 + n * 0.22;
        return [g * 0.94, g, g * 0.90];
      });
      const tex = new THREE.CanvasTexture(cv);
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.repeat.set(70, 70);
      tex.anisotropy = 8;
      mat.map = tex;
      mat.color.setHex(0xffffff);
      this._disposables.push(tex);
    }
    const mesh = new THREE.Mesh(geo, mat);
    mesh.receiveShadow = true;
    mesh.name = 'weather:dev-ground';
    this.group.add(mesh);
    this._disposables.push(geo, mat);
    /* And a light, so the stand-in is visible at all when `gi` is not loaded.
     * The intensities look high because three's lighting is in physical units:
     * a Lambert surface divides by π, so "sunlight" is around 3–4, not 1. */
    let lit = false;
    this.ctx.scene.traverse(o => { if (o.isLight) lit = true; });
    if (!lit) {
      const hemi = new THREE.HemisphereLight(0x9ab4d4, 0x38402c, 2.4);
      const dir = new THREE.DirectionalLight(0xffe9c8, 4.2);
      dir.position.set(180, 220, 140);
      this.group.add(hemi, dir);
      this._devLights = [hemi, dir];
    }
  }

  _buildSheets() {
    this._fogMap = this._noise(256, 5, 2.2, 11);
    this._cloudMap = this._noise(256, 4, 2.9, 29);
    if (this._fogMap) this.groundFog = this._sheet(this._fogMap, 6, 1300, 0x000000, 700, 24);
    if (this._cloudMap) this.scud = this._sheet(this._cloudMap, 4, 7000, 0x000000, 690, 14);
    /* The cloud deck is the one sheet a viewer is usually *underneath*, so it
     * has to survive a much shallower look-up angle than the fog banks do. */
    /* Only the last couple of degrees are cut. A deck at 200m seen from a
     * street-level camera never rises more than about twelve degrees above the
     * eye, so a grazing fade generous enough to hide the horizon comb also
     * hides the entire cloud — the mip bias in the fragment shader is what
     * deals with the comb, and this is just the final degree it cannot save. */
    if (this.scud) {
      this.scud.mat.uniforms.uGraze.value = 0.035;
      this.scud.mat.uniforms.uNearFade.value = 60;
    }
    if (this.groundFog) this.groundFog.mesh.name = 'weather:ground-fog';
    if (this.scud) this.scud.mesh.name = 'weather:scud';
  }

  /** `layers` stacked quads in ONE geometry, each tagged with its index so the
   *  vertex shader can lift and offset it. Six sheets for six draw calls would
   *  be six times the state changes for the same twelve triangles. */
  _sheet(map, layers, size, color, order, seg = 20) {
    const pos = [], uvs = [], idx = [], lay = [];
    const h = size / 2, n = seg + 1;
    for (let l = 0; l < layers; l++) {
      const base = l * n * n;
      for (let j = 0; j < n; j++) {
        for (let i = 0; i < n; i++) {
          const u = i / seg, v = j / seg;
          pos.push(-h + u * size, 0, -h + v * size);
          uvs.push(u, v);
          lay.push(l);
        }
      }
      for (let j = 0; j < seg; j++) {
        for (let i = 0; i < seg; i++) {
          const a = base + j * n + i;
          idx.push(a, a + n, a + 1, a + 1, a + n, a + n + 1);
        }
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geo.setAttribute('aLayer', new THREE.Float32BufferAttribute(lay, 1));
    geo.setIndex(idx);
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), size);

    const mat = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: SHEET_VS, fragmentShader: SHEET_FS,
      uniforms: {
        uMap: {value: map}, uColor: {value: new THREE.Color(color)},
        uOpacity: {value: 0}, uLayers: {value: layers}, uRise: {value: 1.8},
        uSpread: {value: 3.0}, uTime: {value: 0}, uDrift: {value: 0.004},
        uWave: {value: 3.0},
        uWind: {value: new THREE.Vector2(1, 0)},
        /* The distance fade has to finish INSIDE the quad. Set it past the
         * half-width and the sheet still has alpha where its geometry stops,
         * which draws a dead-straight dotted line across the horizon — the one
         * artefact that instantly says "that cloud is a rectangle". */
        uNearFade: {value: 14}, uFarFade: {value: size * 0.45},
        /* Barely any grazing fade by default: a fog bank is *most* visible when
         * you look along it, so the only thing worth killing is the last degree
         * where one texel covers a screen row and the noise turns into a comb. */
        uGraze: {value: 0.045},
      },
      transparent: true, depthWrite: false, depthTest: true,
      side: THREE.DoubleSide, blending: THREE.NormalBlending,
      /* Every fragment shader here writes `colour × alpha`. Without this flag
       * three picks the straight-alpha blend factors and multiplies by alpha a
       * second time, so an effect asking for 0.3 coverage renders at 0.09 —
       * which is exactly why the snow looked like specks of dust and the fog
       * needed an implausible opacity to show up at all. */
      premultipliedAlpha: true,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.renderOrder = order;
    mesh.frustumCulled = false;
    mesh.visible = false;
    this.group.add(mesh);
    this._disposables.push(geo, mat);
    return {mesh, mat, layers, size};
  }

  /** A plane's triangles, plus per-instance attributes, as one instanced
   *  geometry. Allocated once at the ultra count; the quality ladder only ever
   *  moves `instanceCount`, which is free. */
  _instancedQuad(count, attrs) {
    const geo = new THREE.InstancedBufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(
      [-0.5, 0.5, 0, 0.5, 0.5, 0, -0.5, -0.5, 0, 0.5, -0.5, 0], 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(
      [0, 1, 1, 1, 0, 0, 1, 0], 2));
    geo.setIndex([0, 2, 1, 2, 3, 1]);
    for (const [name, size, fill] of attrs) {
      const arr = new Float32Array(count * size);
      for (let i = 0; i < count; i++) fill(arr, i * size, i);
      geo.setAttribute(name, new THREE.InstancedBufferAttribute(arr, size));
    }
    geo.instanceCount = count;
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);
    this._disposables.push(geo);
    return geo;
  }

  _buildRain() {
    const N = 11000;
    const r = this.rngBuild;
    const geo = this._instancedQuad(N, [
      ['aSeed', 3, (a, o) => { a[o] = r(); a[o + 1] = r(); a[o + 2] = r(); }],
      ['aRand', 3, (a, o) => {
        a[o] = 0.6 + r() * 0.9;          // width jitter
        a[o + 1] = 0.5 + r() * 1.1;      // length jitter
        a[o + 2] = 0.45 + r() * 0.55;    // brightness jitter
      }],
    ]);
    const mat = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: RAIN_VS, fragmentShader: RAIN_FS,
      uniforms: {
        uTime: {value: 0}, uCam: {value: new THREE.Vector3()},
        uExtent: {value: new THREE.Vector3(62, 34, 62)},
        uVel: {value: new THREE.Vector3(0, -18, 0)},
        /* `uNear` fades drops out from here to five times it. It is generous on
         * purpose: a two-metre streak two metres from the lens is a white bar
         * across a third of the frame, which reads as a rendering fault rather
         * than as rain. */
        uWidth: {value: 0.035}, uLen: {value: 1.5}, uNear: {value: 2.6},
        uColor: {value: new THREE.Color(0.78, 0.86, 1.0)},
        uIntensity: {value: 0},
      },
      /* Screen blend in a linear HDR target: `src·(1−dst)` lifts a streak out
       * of a dark forest and pushes it *below* a blown-out sky, which is the
       * "bright against dark, dark against bright" the brief asks for, for the
       * cost of a blend mode instead of a framebuffer read. */
      transparent: true, depthWrite: false, depthTest: true,
      blending: THREE.CustomBlending,
      blendSrc: THREE.OneMinusDstColorFactor, blendDst: THREE.OneFactor,
      blendEquation: THREE.AddEquation,
      side: THREE.DoubleSide,
    });
    this.rain = new THREE.Mesh(geo, mat);
    this.rain.name = 'weather:rain';
    this.rain.renderOrder = 900;
    this.rain.frustumCulled = false;
    this.rain.visible = false;
    this.rainMax = N;
    this.group.add(this.rain);
    this._disposables.push(mat);

    const darkMat = mat.clone();
    darkMat.fragmentShader = RAIN_DARK_FS;
    darkMat.uniforms = THREE.UniformsUtils.clone(mat.uniforms);
    darkMat.blending = THREE.CustomBlending;
    darkMat.blendSrc = THREE.DstColorFactor;
    darkMat.blendDst = THREE.ZeroFactor;
    this.rainDark = new THREE.Mesh(geo, darkMat);
    this.rainDark.name = 'weather:rain-body';
    this.rainDark.renderOrder = 898;      // under the bright core, never over it
    this.rainDark.frustumCulled = false;
    this.rainDark.visible = false;
    this.group.add(this.rainDark);
    this._disposables.push(darkMat);
  }

  _buildSnow() {
    const N = 9000;
    const r = this.rngBuild;
    const geo = this._instancedQuad(N, [
      ['aSeed', 3, (a, o) => { a[o] = r(); a[o + 1] = r(); a[o + 2] = r(); }],
      ['aRand', 3, (a, o) => {
        /* Squared, so most flakes are small and a few are the fat near ones
         * that sell the depth of the fall — but the fat ones stay under about a
         * hand's width. Past that they stop being snow and become bokeh. */
        const s = r();
        a[o] = 0.035 + s * s * 0.085;    // flake size
        a[o + 1] = 0.3 + r() * 1.4;      // spin
        a[o + 2] = r();                  // sway phase
      }],
    ]);
    const mat = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: SNOW_VS, fragmentShader: SNOW_FS,
      uniforms: {
        uTime: {value: 0}, uCam: {value: new THREE.Vector3()},
        uExtent: {value: new THREE.Vector3(34, 24, 34)},
        uVel: {value: new THREE.Vector3(0, -1.2, 0)},
        uSway: {value: 1.4}, uNear: {value: 0.9},
        uColor: {value: new THREE.Color(0.94, 0.96, 1.0)},
        uIntensity: {value: 0},
      },
      transparent: true, depthWrite: false, depthTest: true,
      blending: THREE.NormalBlending, side: THREE.DoubleSide,
      premultipliedAlpha: true,
    });
    this.snow = new THREE.Mesh(geo, mat);
    this.snow.name = 'weather:snow';
    this.snow.renderOrder = 905;
    this.snow.frustumCulled = false;
    this.snow.visible = false;
    this.snowMax = N;
    this.group.add(this.snow);
    this._disposables.push(mat);
  }

  _buildSplashes() {
    const N = 420;
    const geo = this._instancedQuad(N, [
      /* Spawned off-world and already expired, so nothing shows until the first
       * real respawn puts a ring somewhere the rain is actually falling. */
      ['aPos', 4, (a, o) => { a[o] = 0; a[o + 1] = -9999; a[o + 2] = 0; a[o + 3] = 0.4; }],
      ['aLife', 2, (a, o) => { a[o] = -100; a[o + 1] = 0.6; }],
    ]);
    const mat = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: SPLASH_VS, fragmentShader: SPLASH_FS,
      uniforms: {
        uTime: {value: 0}, uColor: {value: new THREE.Color(0.8, 0.88, 1.0)},
        uIntensity: {value: 0},
      },
      transparent: true, depthWrite: false, depthTest: true,
      blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      premultipliedAlpha: true,
    });
    this.splash = new THREE.Mesh(geo, mat);
    this.splash.name = 'weather:splashes';
    this.splash.renderOrder = 880;
    this.splash.frustumCulled = false;
    this.splash.visible = false;
    this.splashMax = N;
    this.splashPos = geo.getAttribute('aPos');
    this.splashLife = geo.getAttribute('aLife');
    this.group.add(this.splash);
    this._disposables.push(mat);
  }

  _buildStorm() {
    /* Two lights we own outright. Flashing the sun and the ambient the sky
     * module is already writing every frame is a fight nobody wins; a dedicated
     * pair that sits at zero intensity between strikes costs nothing and cannot
     * be overwritten. Neither casts a shadow — a shadow map rebuild per flicker
     * frame is the most expensive thing in the frame, spent on two frames of
     * light nobody can resolve. */
    this.flashSun = new THREE.DirectionalLight(0xdae8ff, 0);
    this.flashSun.position.set(400, 500, 300);
    this.flashAmb = new THREE.AmbientLight(0xa8c2e8, 0);
    this.group.add(this.flashSun, this.flashAmb);

    const geo = new THREE.PlaneGeometry(2, 2);
    const mat = new THREE.MeshBasicMaterial({
      color: 0xcfe0ff, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthTest: false, depthWrite: false,
      side: THREE.DoubleSide, fog: false,
    });
    this.flashPlane = new THREE.Mesh(geo, mat);
    this.flashPlane.renderOrder = 960;
    this.flashPlane.frustumCulled = false;
    this.flashPlane.visible = false;
    this.group.add(this.flashPlane);
    this._disposables.push(geo, mat);

    const boltMat = new THREE.LineBasicMaterial({
      color: 0xeaf2ff, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false,
      fog: false,
    });
    this.boltMat = boltMat;
    this.bolt = new THREE.LineSegments(new THREE.BufferGeometry(), boltMat);
    this.bolt.frustumCulled = false;
    this.bolt.renderOrder = 940;
    this.bolt.visible = false;
    this.group.add(this.bolt);
    this._disposables.push(boltMat);
  }

  _buildShafts() {
    const geo = new THREE.PlaneGeometry(1, 1);
    const mat = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      vertexShader: SHAFT_VS, fragmentShader: SHAFT_FS,
      uniforms: {
        uSun: {value: new THREE.Vector2(0.5, 0.6)}, uAspect: {value: 1.78},
        uTime: {value: 0}, uStrength: {value: 0},
        uColor: {value: new THREE.Color(1.0, 0.86, 0.62)},
      },
      transparent: true, depthTest: false, depthWrite: false,
      blending: THREE.AdditiveBlending, side: THREE.DoubleSide, fog: false,
      premultipliedAlpha: true,
    });
    this.shafts = new THREE.Mesh(geo, mat);
    this.shafts.name = 'weather:shafts';
    this.shafts.renderOrder = 950;
    this.shafts.frustumCulled = false;
    this.shafts.visible = false;
    this.group.add(this.shafts);
    this._disposables.push(geo, mat);
  }

  /* ---- the state machine -------------------------------------------------- */

  _dwell(name) {
    const d = PRESETS[name]?.dwell || [150, 250];
    return d[0] + this.rng() * (d[1] - d[0]);
  }

  _next(from) {
    const edges = CHAIN[from] || CHAIN.fair;
    let total = 0;
    for (const [, w] of edges) total += w;
    let r = this.rng() * total;
    for (const [name, w] of edges) { r -= w; if (r <= 0) return name; }
    return edges[edges.length - 1][0];
  }

  _fillQueue() {
    let last = this.queue.length ? this.queue[this.queue.length - 1] : this.state;
    while (this.queue.length < 4) { last = this._next(last); this.queue.push(last); }
  }

  /** Temperature: a slow seeded drift with a diurnal swing on top. It is not a
   *  climate model — it exists so that a cold spell turns the rain to snow now
   *  and then without anybody choosing it, which is the only way snow ever
   *  appears on a display nobody touches. */
  _temperature(t) {
    const a = Math.sin(t / 830 + this.tempPhase) * 8.5;
    const b = Math.sin(t / 317 + this.tempPhase * 2.1) * 4.0;
    const diurnal = Math.sin((this.hours - 9.5) / 24 * Math.PI * 2) * 4.5;
    return 6.5 + a + b + diurnal - this.p.cloud * 2.0;
  }

  _advance(dt) {
    if (this.locked) return;
    this.phaseT += dt;
    if (this.phase === 'dwell') {
      if (this.phaseT >= this.dwellFor) {
        this.from = this.state;
        let to = this.queue.shift() || this._next(this.state);
        this._fillQueue();
        /* A cold snap redirects the wet states rather than adding an edge for
         * snow everywhere: the chain decides *whether* something falls, the
         * thermometer decides what it is. */
        if (this.p.temperature < 0.6 && WET_STATES.has(to)) to = 'snow';
        if (this.p.temperature > 4.5 && to === 'snow') to = 'rain';
        this.state = to;
        this.phase = 'trans';
        this.phaseT = 0;
        const delta = Math.abs((PRESETS[to]?.cloud ?? 0) - (PRESETS[this.from]?.cloud ?? 0)) +
                      Math.abs((PRESETS[to]?.precip ?? 0) - (PRESETS[this.from]?.precip ?? 0));
        this.transFor = 26 + delta * 42 + this.rng() * 18;
      }
    } else if (this.phaseT >= this.transFor) {
      this.from = this.state;
      this.phase = 'dwell';
      this.phaseT = 0;
      this.dwellFor = this._dwell(this.state);
    }
  }

  /** Everything published, recomputed from the machine's position. */
  _recompute(dt) {
    const A = PRESETS[this.from] || PRESETS.fair;
    const B = PRESETS[this.state] || PRESETS.fair;
    const u = this.phase === 'trans' ? smooth(clamp(this.phaseT / this.transFor, 0, 1)) : 1;
    const p = this.p;

    if (!this.locked) {
      p.cloud = lerp(A.cloud, B.cloud, u);
      p.fog = lerp(A.fog, B.fog, u);
      p.precip = lerp(A.precip, B.precip, u);
      /* The wind veers, slowly. Wrapped, because an angle that only ever grows
       * eventually loses its low bits on a display that has been on for days. */
      p.wind = lerp(A.wind, B.wind, u);
      p.windAngle = (p.windAngle + dt * (0.004 + p.wind * 0.010)) % (Math.PI * 2);
      p.temperature = lerp(p.temperature, this._temperature(this.time), 1 - Math.exp(-dt / 6));

      /* One precipitation figure, split by temperature. The crossover is wide
       * on purpose: sleet — some of each — is what actually happens at 1°C. */
      const snowFrac = this.state === 'snow' && this.phase === 'dwell'
        ? 1 : fade(2.8, -0.4, p.temperature);
      p.rain = p.precip * (1 - snowFrac);
      p.snowfall = p.precip * snowFrac;
      p.preset = this._label(u);
    }

    /* Gusting is published separately from the wind it rides on, and it is
     * computed even when the machine is locked: a forced screenshot should
     * still have the trees moving. Two frequencies, because one is a metronome.
     * It is its own field so that a value which wanders every second cannot
     * drag `wind` — and with it the shadow map — along behind it. */
    const g = Math.sin(this.time * 0.37 + this.windPhase) * 0.5 +
              Math.sin(this.time * 0.131 + this.windPhase * 3.3) * 0.5;
    p.windGust = clamp(0.5 + 0.5 * g, 0, 1) * p.wind * 0.45;

    /* Wetness: soaks in fast, dries out very slowly. The slow half is the whole
     * point — a yard that is still dark twenty minutes after the rain stopped
     * is what makes the site read as a place with a history rather than a
     * switch with two positions. */
    const wetTarget = clamp(p.rain * 1.25 + p.snowCover * 0.25, 0, 1);
    const tau = wetTarget > p.wetness ? 16 : 250;
    p.wetness += (wetTarget - p.wetness) * (1 - Math.exp(-dt / tau));

    /* Snow lies, then melts at a rate set by how far above freezing it is.
     *
     * `snow` published to the rest of the world is this ACCUMULATION, not the
     * fall — terrain.js and vegetation.js feed `weather.snow` straight into how
     * white the ground and the canopy are, and a bench that goes bare the
     * instant the last flake lands is the one thing snow never does. The fall
     * itself is `snowfall`, which is what the particle density reads. */
    const coverTarget = clamp(p.snowfall * 1.5, 0, 1);
    if (coverTarget > p.snowCover) {
      p.snowCover += (coverTarget - p.snowCover) * (1 - Math.exp(-dt / 70));
    } else {
      p.snowCover = Math.max(0, p.snowCover - dt * (0.0012 + Math.max(0, p.temperature) * 0.0016));
    }
    p.snow = p.snowCover;

    /* Visibility, for anyone who wants one number instead of three. */
    p.visibility = clamp(1 - p.fog * 0.75 - p.rain * 0.2 - p.snowfall * 0.25, 0.05, 1);
    p.lightning = this.flash;
  }

  /** The name the rest of the world sees. It flips at the halfway point of a
   *  transition, so "storm" arrives when it looks like a storm and not when the
   *  machine decided one was coming. */
  _label(u) {
    const name = u < 0.5 ? this.from : this.state;
    if (this.p.snowfall > 0.12 && this.p.snowfall > this.p.rain) return 'snow';
    if (this.p.fog > 0.7 && this.p.precip < 0.1) return 'fog';
    return name;
  }

  /** Push to `ctx.world.setWeather`, which is what tells everybody else.
   *
   *  Throttled deliberately: `setWeather` marks the shadow map dirty, and a
   *  shadow map rebuild is the single most expensive thing in this frame. A
   *  parameter that has moved less than 0.01 is a parameter nobody can see
   *  move, so it waits. In a steady state this publishes nothing at all. */
  _publish(force = false) {
    const world = this.ctx.world;
    if (!world?.setWeather) return;
    let moved = false;
    for (const k of PUBLISH_KEYS) {
      if (Math.abs((this.p[k] ?? 0) - (this.published[k] ?? -99)) >= PUBLISH_EPS[k]) {
        moved = true; break;
      }
    }
    const presetChanged = this.p.preset !== this.published.preset;
    if (!force && !presetChanged && (!moved || this._sincePublish < 0.5)) return;

    const patch = {preset: this.p.preset};
    for (const k of PUBLISH_KEYS) patch[k] = this.p[k];
    patch.lightning = this.p.lightning;
    Object.assign(this.published, patch);
    this._sincePublish = 0;
    this._selfPublish = true;
    try { world.setWeather(patch); }
    catch (err) { console.warn('[weather] publish failed', err); }
    finally { this._selfPublish = false; }
  }

  /* ---- lighting the world ------------------------------------------------- */

  /** Where the sun is. The sky module owns the real light; we look for it and
   *  use it if it is there, and fall back to an arc derived from the clock so
   *  the shafts and the fog colour still make sense with nothing else loaded. */
  _sun(dt) {
    this._sunScan -= dt;
    if (this._sunScan <= 0) {
      this._sunScan = 1.0;
      let best = null;
      this.ctx.scene.traverse(o => {
        if (o.isDirectionalLight && o !== this.flashSun &&
            (!best || o.intensity > best.intensity)) best = o;
      });
      this._sunLight = best;
    }
    if (this._sunLight && this._sunLight.intensity > 0.02) {
      this._sunDir.copy(this._sunLight.position).normalize();
    } else {
      const th = (this.hours - 6) / 12 * Math.PI;
      const elev = Math.sin(th) * 1.18;
      const az = th - Math.PI * 0.5;
      this._sunDir.set(Math.cos(elev) * Math.sin(az), Math.sin(elev),
                       Math.cos(elev) * Math.cos(az));
    }
    return this._sunDir.y;
  }

  /** The colour the horizon has right now, from the sun's height and the cloud
   *  cover. If a sky module has already set a fog colour we defer to it — one
   *  argument about what grey the distance is, and the sky wins it. */
  _horizon(elev) {
    const day = fade(-0.12, 0.16, elev);
    const dusk = fade(0.30, 0.0, elev) * fade(-0.22, 0.02, elev);
    const c = this._tmpColor || (this._tmpColor = new THREE.Color());

    const clearDay = [0.50, 0.63, 0.80];
    const dullDay = [0.60, 0.63, 0.67];
    const night = [0.030, 0.040, 0.062];
    const cloud = clamp(this.p.cloud, 0, 1);
    const storm = clamp((this.p.precip - 0.4) / 0.6, 0, 1);

    let r = lerp(clearDay[0], dullDay[0], cloud);
    let g = lerp(clearDay[1], dullDay[1], cloud);
    let b = lerp(clearDay[2], dullDay[2], cloud);
    /* A storm sky is not simply a grey day — it is much darker and it loses its
     * blue, which is what makes lightning read at all. */
    const dark = lerp(1, 0.42, storm);
    r *= dark; g *= dark * 0.99; b *= dark * 1.04;

    r = lerp(night[0], r, day);
    g = lerp(night[1], g, day);
    b = lerp(night[2], b, day);

    /* Sunrise and sunset push warm, and cloud eats most of it. */
    const warm = dusk * (1 - cloud * 0.72);
    r += warm * 0.30; g += warm * 0.13; b -= warm * 0.05;

    c.setRGB(Math.max(0, r), Math.max(0, g), Math.max(0, b));
    return c;
  }

  /* ---- the frame ---------------------------------------------------------- */

  update(dt, t) {
    if (!this.enabled || !this.group) return;
    try { this._tick(clamp(dt, 0, 0.12), t); }
    catch (err) {
      /* A throw here kills the render loop for the whole floor. It gets exactly
       * one chance to be a fluke, and then this module turns itself off. */
      this._faults = (this._faults || 0) + 1;
      console.error('[weather] update failed', err);
      if (this._faults > 3) {
        this.enabled = false;
        this.group.visible = false;
        console.error('[weather] disabled after repeated failures — ' +
                      'the map continues without weather.');
      }
    }
  }

  _tick(dt, t) {
    this.time += dt;
    this._sincePublish += dt;
    this.hours = this.ctx.world?.timeOfDay ?? this.hours;

    this._advance(dt);
    this._storm(dt);
    this._recompute(dt);

    const cam = this.ctx.camera;
    const elev = this._sun(dt);
    const p = this.p;
    /* One clock for every shader, wrapped so float32 does not lose the low bits
     * of a display that has been on since Monday. */
    const clock = this.time % 600;

    const wx = Math.cos(p.windAngle), wz = Math.sin(p.windAngle);
    /* The gust rides on the wind for anything visual — the rain streaks tilting
     * in and out is most of what "it is blowing" looks like — but it stays out
     * of the published `wind`, which subsystems fold into slower things. */
    const windSpeed = (p.wind + p.windGust * 0.7) * 21;

    this._updateFog(elev);
    this._updateRain(dt, cam, clock, wx, wz, windSpeed, elev);
    this._updateSnow(dt, cam, clock, wx, wz, windSpeed, elev);
    this._updateSplashes(dt, cam, clock);
    this._updateSheets(dt, cam, clock, wx, wz, elev);
    this._updateShafts(cam, elev);
    this._updateFlashPlane(cam);

    this._publish();
  }

  _updateFog(elev) {
    const scene = this.ctx.scene;
    const p = this.p;
    const horizon = this._horizon(elev);

    /* Density is exponential-squared, so these numbers are small and the useful
     * range is narrow: 0.0004 is a clear day with real aerial perspective at a
     * kilometre, 0.0045 is a fog bank you cannot see the next station through. */
    const density = 0.00034 + p.fog * 0.0040 + p.rain * 0.0011 + p.snowfall * 0.0016;

    if (scene.fog) {
      if (scene.fog.isFogExp2) {
        /* If we did not create the fog, another module owns its density curve
         * as well as its colour, and fighting it every frame is how you get a
         * flickering horizon. We only take the density we were given. */
        if (this._ownsFog) scene.fog.density = density;
      } else if (scene.fog.isFog && this._ownsFog) {
        scene.fog.near = 40;
        scene.fog.far = clamp(1 / Math.max(density, 1e-5), 220, 3600);
      }
      if (this._ownsFog) {
        scene.fog.color.copy(horizon);
        this._fogColor.copy(horizon);
      } else {
        /* Somebody else's fog colour is the authority on what the distance
         * looks like, so the ground fog and the rain take their cue from it. */
        this._fogColor.copy(scene.fog.color);
      }
    }
    if (this._ownsBackground) {
      if (scene.background === this._bg) this._bg.copy(this._fogColor);
      else this._ownsBackground = false;   // a real sky turned up; hand it back
    }
  }

  _updateRain(dt, cam, clock, wx, wz, windSpeed, elev) {
    const mesh = this.rain;
    if (!mesh) return;
    const amount = this.p.rain;
    if (amount < 0.008) {
      mesh.visible = false;
      if (this.rainDark) this.rainDark.visible = false;
      return;
    }
    mesh.visible = true;

    const u = mesh.material.uniforms;
    const fall = 9 + amount * 13;
    /* A drop does not reach wind speed, but it gets most of the way there — and
     * the tilt of the streaks is the only thing on screen that says how hard it
     * is blowing. */
    u.uVel.value.set(wx * windSpeed * 0.62, -fall, wz * windSpeed * 0.62);
    u.uTime.value = clock;
    u.uCam.value.copy(cam.position);
    const reach = lerp(46, 76, amount);
    u.uExtent.value.set(reach, 30 + amount * 12, reach);
    /* Longer streaks in heavier rain: this is the exposure smear, and it is
     * what separates a downpour from a shower more than the count does. */
    u.uLen.value = 0.055 * Math.hypot(fall, windSpeed * 0.62) + 0.45;
    u.uWidth.value = 0.026 + amount * 0.016;

    /* Rain is only as bright as the world it is falling through. At night the
     * streaks all but vanish except where a lamp catches them, which is exactly
     * right and costs nothing. */
    const light = clamp(fade(-0.14, 0.18, elev) * (1 - this.p.cloud * 0.35) + 0.08, 0.08, 1);
    const flashLift = this.flash * 0.9;
    u.uColor.value.setRGB(0.72 + flashLift, 0.80 + flashLift, 0.96 + flashLift)
      .multiplyScalar(0.35 + light * 0.85);
    u.uIntensity.value = (0.34 + amount * 0.42) * this._particleScale;
    mesh.geometry.instanceCount = Math.max(
      1, Math.floor(this.rainMax * this._particleScale * clamp(amount * 1.1, 0, 1)));

    if (this.rainDark) {
      this.rainDark.visible = this._rainBodyOn;
      if (this._rainBodyOn) {
        const d = this.rainDark.material.uniforms;
        d.uTime.value = u.uTime.value;
        d.uCam.value.copy(u.uCam.value);
        d.uExtent.value.copy(u.uExtent.value);
        d.uVel.value.copy(u.uVel.value);
        d.uLen.value = u.uLen.value * 0.94;
        d.uWidth.value = u.uWidth.value * 2.3;    // the body is wider than the core
        d.uNear.value = u.uNear.value;
        /* Weak on purpose. This pass exists to give the streak an edge against
         * a pale surface, not to draw grey bars over the yard. */
        d.uIntensity.value = (0.10 + amount * 0.16) * this._particleScale;
      }
    }
  }

  _updateSnow(dt, cam, clock, wx, wz, windSpeed, elev) {
    const mesh = this.snow;
    if (!mesh) return;
    const amount = this.p.snowfall;
    if (amount < 0.008) { mesh.visible = false; return; }
    mesh.visible = true;

    const u = mesh.material.uniforms;
    /* Snow falls at walking pace and blows sideways almost as fast as the wind,
     * which is the whole difference in silhouette between snow and rain. */
    const fall = 0.75 + amount * 0.9;
    u.uVel.value.set(wx * windSpeed * 0.85, -fall, wz * windSpeed * 0.85);
    u.uTime.value = clock;
    u.uCam.value.copy(cam.position);
    /* A much tighter volume than the rain uses. Snow is slow, so what makes it
     * read is flakes per cubic metre, not reach — a wide sparse box looks like
     * dust in a sunbeam. */
    const reach = lerp(22, 32, amount);
    u.uExtent.value.set(reach, 18 + amount * 6, reach);
    u.uSway.value = 0.9 + (1 - this.p.wind) * 1.9;
    const light = clamp(fade(-0.16, 0.16, elev) + 0.16, 0.16, 1);
    u.uColor.value.setRGB(0.93, 0.95, 1.0).multiplyScalar(0.4 + light * 0.75);
    u.uIntensity.value = (0.30 + amount * 0.22) * this._particleScale;
    mesh.geometry.instanceCount = Math.max(
      1, Math.floor(this.snowMax * this._particleScale * clamp(amount * 1.2, 0, 1)));
  }

  /** Splashes are the one part with a CPU loop, because a ring has to land on
   *  the ground and only `ctx.ground` knows where that is. It stays cheap by
   *  costing a ground sample per *respawn* rather than per frame: the shader
   *  runs the whole life of the ring from its spawn stamp. */
  _updateSplashes(dt, cam, clock) {
    const mesh = this.splash;
    if (!mesh) return;
    const amount = this.p.rain;
    const active = Math.floor(this.splashMax * this._particleScale * clamp(amount * 1.3, 0, 1));
    if (active < 1 || this._particleScale < 0.25) { mesh.visible = false; return; }
    mesh.visible = true;
    mesh.material.uniforms.uTime.value = clock;
    mesh.material.uniforms.uIntensity.value = 0.26 + amount * 0.30;
    mesh.geometry.instanceCount = active;

    const pos = this.splashPos.array, life = this.splashLife.array;
    let dirty = false;
    /* Only a slice of the pool is examined per frame — a full sweep of 420
     * ground samples every frame is a terrain query the terrain does not
     * deserve — but the slice has to be CONTIGUOUS. Walking it in strides of
     * seven and then advancing the cursor by a multiple of seven only ever
     * visits one seventh of the pool: the other six sevenths sat at their
     * off-world spawn point forever, which is why heavy rain was landing about
     * sixty splashes instead of four hundred. */
    const step = Math.max(1, Math.ceil(active / 25));
    const r = this.rngFx;
    const eye = cam.position.y;
    this._splashCursor = (this._splashCursor || 0) % Math.max(active, 1);
    for (let n = 0; n < step; n++) {
      const i = (this._splashCursor + n) % active;
      const lo = i * 2;
      if (clock - life[lo] < life[lo + 1] && life[lo] <= clock) continue;
      const a = r() * Math.PI * 2;
      const rad = 3 + Math.sqrt(r()) * 44;
      const x = cam.position.x + Math.cos(a) * rad;
      const z = cam.position.z + Math.sin(a) * rad;
      const y = this._ground(x, z);
      /* A ring is a flat disc: seen from below it is a hoop hanging in the sky,
       * which is exactly what a splash on the slope above you renders as. Leave
       * the slot expired rather than place one there. */
      if (y > eye - 0.6) continue;
      /* Anything at or below sea level is standing water, and a ripple on water
       * is wider and lasts far longer than a splash on gravel. Only where there
       * is a terrain to ask: with no terrain loaded every sample answers zero,
       * and a whole dev floor of pond ripples proves nothing. */
      const water = this._hasTerrain && y <= 0.12;
      const po = i * 4;
      pos[po] = x; pos[po + 1] = y + 0.045; pos[po + 2] = z;
      pos[po + 3] = water ? 0.7 + r() * 1.2 : 0.22 + r() * 0.3;
      life[lo] = clock;
      life[lo + 1] = water ? 1.1 + r() * 1.1 : 0.35 + r() * 0.35;
      dirty = true;
    }
    this._splashCursor += step * 7;
    if (dirty) { this.splashPos.needsUpdate = true; this.splashLife.needsUpdate = true; }
  }

  _updateSheets(dt, cam, clock, wx, wz, elev) {
    /* Ground fog. It pools at dawn, it pools when the preset is fog, and it
     * lingers a little after rain — and it pools *in the valley* for free,
     * because the sheets sit at a fixed height and the terrain's own depth
     * buffer clips whatever is standing above them. */
    if (this.groundFog && this._sheetsOn) {
      const dawn = fade(3.4, 5.6, this.hours) * fade(10.2, 7.6, this.hours);
      const dusk = fade(18.4, 20.0, this.hours) * fade(23.0, 21.0, this.hours) * 0.45;
      const damp = this.p.wetness * fade(0.10, 0.0, this.p.rain) * 0.35;
      const still = fade(0.55, 0.12, this.p.wind);
      let s = clamp((dawn + dusk) * (0.35 + this.p.fog * 1.5) + damp * still, 0, 1);
      s = Math.max(s, fade(0.55, 0.95, this.p.fog));
      s *= still * 0.7 + 0.3;

      const m = this.groundFog;
      m.mesh.visible = s > 0.01;
      if (m.mesh.visible) {
        /* The bank follows the viewer, snapped to a coarse grid so the noise
         * does not crawl with the camera. Its floor is the lowest ground
         * nearby, which is what "valley" means here. */
        const gx = Math.round(cam.position.x / 40) * 40;
        const gz = Math.round(cam.position.z / 40) * 40;
        const g = (x, z) => this._ground(x, z);
        const low = Math.min(g(gx, gz), g(gx + 110, gz), g(gx - 110, gz),
                             g(gx, gz + 110), g(gx, gz - 110),
                             g(gx + 78, gz + 78), g(gx - 78, gz - 78));
        m.mesh.position.set(gx, low + 0.9, gz);
        const u = m.mat.uniforms;
        u.uOpacity.value = s * 0.16;
        u.uColor.value.copy(this._fogColor).lerp(this._white, 0.30);
        u.uTime.value = clock;
        u.uWind.value.set(wx, wz);
        u.uDrift.value = 0.0018 + this.p.wind * 0.004;
        u.uRise.value = 1.5;
        u.uSpread.value = 2.6;
        u.uWave.value = 3.4;
      }
    } else if (this.groundFog) {
      this.groundFog.mesh.visible = false;
    }

    /* Low scud. Only under a properly closed sky — a broken cloud deck drawn as
     * a flat sheet reads as a smear, so below 0.72 cover there is none. */
    if (this.scud && this._sheetsOn) {
      const s = fade(0.72, 0.99, this.p.cloud);
      const m = this.scud;
      m.mesh.visible = s > 0.01;
      if (m.mesh.visible) {
        const gx = Math.round(cam.position.x / 400) * 400;
        const gz = Math.round(cam.position.z / 400) * 400;
        m.mesh.position.set(gx, 210, gz);
        const u = m.mat.uniforms;
        u.uOpacity.value = s * (0.13 + this.p.precip * 0.20);
        const dim = lerp(0.62, 0.30, clamp(this.p.precip, 0, 1));
        u.uColor.value.copy(this._fogColor).multiplyScalar(dim)
          .addScalar(this.flash * 0.55);
        u.uTime.value = clock;
        u.uWind.value.set(wx, wz);
        u.uDrift.value = 0.0016 + this.p.wind * 0.0075;
        u.uRise.value = 42;
        u.uSpread.value = 3.6;
        u.uWave.value = 34;
      }
    } else if (this.scud) {
      this.scud.mesh.visible = false;
    }
  }

  /** A camera-locked quad. Parenting to `ctx.camera` would be simpler, but the
   *  engine never adds the camera to the scene, so a child of it is never
   *  traversed and never drawn. Placing it by hand costs one quaternion copy. */
  _faceCamera(mesh, cam, dist, pad = 1.06) {
    const h = 2 * dist * Math.tan((cam.fov || 42) * Math.PI / 360) * pad;
    const w = h * (cam.aspect || 1.78);
    const fwd = this._tmp.v.set(0, 0, -1).applyQuaternion(cam.quaternion);
    mesh.position.copy(cam.position).addScaledVector(fwd, dist);
    mesh.quaternion.copy(cam.quaternion);
    mesh.scale.set(w, h, 1);
  }

  _updateShafts(cam, elev) {
    const mesh = this.shafts;
    if (!mesh || !this._shaftsOn) { if (mesh) mesh.visible = false; return; }
    /* Shafts want a low sun and thick air, and nothing else. High sun gives you
     * a lens flare, thin air gives you a smear over a clear sky, and neither is
     * what standing in a forest at seven in the morning looks like. */
    const low = fade(-0.03, 0.10, elev) * fade(0.46, 0.14, elev);
    const thick = clamp(this.p.fog * 1.5 + this.p.wetness * 0.25, 0, 1);
    const open = fade(0.85, 0.35, this.p.cloud);
    const strength = low * thick * open * (1 - clamp(this.p.precip, 0, 1) * 0.55);
    if (strength < 0.004) { mesh.visible = false; return; }

    const sun = this._tmp.v2.copy(this._sunDir).multiplyScalar(2400).add(cam.position);
    const fwd = this._tmp.v.set(0, 0, -1).applyQuaternion(cam.quaternion);
    if (fwd.dot(this._sunDir) < 0.05) { mesh.visible = false; return; }
    sun.project(cam);
    if (sun.z > 1) { mesh.visible = false; return; }

    mesh.visible = true;
    this._faceCamera(mesh, cam, 3);
    const u = mesh.material.uniforms;
    u.uSun.value.set(sun.x * 0.5 + 0.5, sun.y * 0.5 + 0.5);
    u.uAspect.value = cam.aspect || 1.78;
    u.uTime.value = this.time;
    u.uStrength.value = strength * 0.30;
    /* Low sun through water vapour is orange; through a cold clear morning it
     * is nearly white. */
    const warmth = fade(0.22, 0.0, elev);
    u.uColor.value.setRGB(1.0, lerp(0.94, 0.74, warmth), lerp(0.86, 0.46, warmth));
  }

  _updateFlashPlane(cam) {
    if (!this.flashPlane) return;
    if (this.flash < 0.004) { this.flashPlane.visible = false; return; }
    this.flashPlane.visible = true;
    this._faceCamera(this.flashPlane, cam, 2.4);
    this.flashPlane.material.opacity = clamp(this.flash * 0.10, 0, 0.35);
  }

  /* ---- lightning ---------------------------------------------------------- */

  _storm(dt) {
    const stormy = this.p.precip > 0.72 && this.p.wind > 0.6 &&
                   this.p.snowfall < this.p.rain;
    if (!stormy) {
      this.flash = Math.max(0, this.flash - dt * 4);
      this._flashSeq = null;
      this.strikeIn = 1.2 + this.rngFx() * 2.5;
    } else if (this._flashSeq) {
      this._flashT += dt;
      /* A real strike is several returns down the same channel over a third of
       * a second, not one square pulse. Two or three sub-flashes with a decay
       * between them is the difference between lightning and a light switch. */
      let v = 0;
      for (const [at, amp] of this._flashSeq) {
        const d = this._flashT - at;
        if (d >= 0) v = Math.max(v, amp * Math.exp(-d * 13));
      }
      this.flash = v;
      if (this._flashT > 0.9) {
        this._flashSeq = null;
        this.flash = 0;
        /* A working storm flashes every few seconds. Space them out to the
         * once-a-minute of a distant front and the wall display spends most of
         * its storm looking like a badly exposed overcast. */
        this.strikeIn = 1.4 + this.rngFx() * 6.5 * (1.35 - this.p.precip);
        this._publishLightning(0);
      }
    } else {
      this.strikeIn -= dt;
      if (this.strikeIn <= 0) this._strike();
    }

    if (this.flashSun) {
      this.flashSun.intensity = this.flash * 5.5;
      this.flashAmb.intensity = this.flash * 1.4;
    }
    if (this.bolt) {
      this.bolt.visible = this.flash > 0.02 && !!this._boltLive;
      if (this.boltMat) this.boltMat.opacity = clamp(this.flash * 1.5, 0, 1);
    }
  }

  _strike() {
    const r = this.rngFx;
    const near = r() < 0.42;
    const peak = near ? 0.75 + r() * 0.45 : 0.25 + r() * 0.3;
    const seq = [[0, peak]];
    const returns = 1 + Math.floor(r() * 3);
    for (let i = 0; i < returns; i++) {
      seq.push([0.06 + r() * 0.34, peak * (0.35 + r() * 0.55)]);
    }
    this._flashSeq = seq;
    this._flashT = 0;
    this.flash = peak;

    /* Put the flash light where the strike is, so the site is lit from the side
     * the bolt came down rather than from wherever the sun happens to be. */
    const az = r() * Math.PI * 2;
    if (this.flashSun) {
      this.flashSun.position.set(Math.cos(az) * 700, 380 + r() * 250, Math.sin(az) * 700);
    }
    this._boltLive = near && this._makeBolt(az, r);
    this._publishLightning(peak);
  }

  /** A visible channel for the near strikes. Cheap — one LineSegments of a few
   *  dozen segments, rebuilt per strike — and it is the thing that makes a still
   *  frame read as a storm rather than as a badly exposed overcast. */
  _makeBolt(az, r) {
    if (!this.bolt) return false;
    const dist = 420 + r() * 700;
    const bx = Math.cos(az) * dist, bz = Math.sin(az) * dist;
    const top = 260 + r() * 160;
    const pts = [];
    const walk = (x, y, z, steps, spread, depth) => {
      let cx = x, cy = y, cz = z;
      for (let i = 0; i < steps; i++) {
        const nx = cx + (r() - 0.5) * spread;
        const nz = cz + (r() - 0.5) * spread;
        const ny = cy - (y / steps) * (0.6 + r() * 0.9);
        pts.push(cx, cy, cz, nx, ny, nz);
        if (depth > 0 && r() < 0.22) walk(nx, ny, nz, 3 + (r() * 3) | 0, spread * 0.8, depth - 1);
        cx = nx; cy = Math.max(0, ny); cz = nz;
        if (cy <= 0.5) break;
      }
    };
    walk(bx, top, bz, 16, 26, 1);
    if (!pts.length) return false;
    this.bolt.geometry?.dispose?.();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(bx, top / 2, bz), top);
    this.bolt.geometry = geo;
    return true;
  }

  /** Lightning goes out through the setter, but only at the start and the end
   *  of a strike. Publishing the flicker itself would rebuild the shadow map
   *  three times inside a third of a second for a light nobody can resolve. */
  _publishLightning(value) {
    this.p.lightning = value;
    this.published.lightning = value;
    if (!this.ctx.world?.setWeather) return;
    this._selfPublish = true;
    try { this.ctx.world.setWeather({lightning: value}); }
    catch { /* the flash still happens; only the notification is lost */ }
    finally { this._selfPublish = false; }
  }

  /* ---- lifecycle ---------------------------------------------------------- */

  /** Somebody outside set the weather. Unless it was us, that is an operator or
   *  a harness *choosing* a sky, and the machine stops evolving — a forced
   *  screenshot that drifts back to fair after ninety seconds is not a
   *  screenshot of anything. */
  onWeather(w) {
    if (this._selfPublish || !w) return;
    const preset = w.preset;
    const known = preset && PRESETS[preset];
    if (!known && preset === this.p.preset) return;
    this.locked = true;
    if (known) {
      const base = PRESETS[preset];
      this.state = this.from = preset;
      this.phase = 'dwell';
      this.phaseT = 0;
      Object.assign(this.p, {
        preset, cloud: base.cloud, fog: base.fog, precip: base.precip,
        wind: base.wind,
      });
      /* Whatever the caller actually named wins over the preset's defaults —
       * they may want an overcast with the wind of a gale. */
      for (const k of ['wetness', 'fog', 'wind', 'windAngle', 'cloud',
                       'temperature', 'snowCover']) {
        if (typeof w[k] === 'number') this.p[k] = w[k];
      }
      if (preset === 'snow' && typeof w.temperature !== 'number') this.p.temperature = -3;
      /* Rain and snow are set as a *pair*, never one at a time. Naming only the
       * snow and letting the previous rain stand is how you get a forced snow
       * screenshot with rain still falling through it. */
      const hasRain = typeof w.rain === 'number';
      const hasSnow = typeof w.snow === 'number';
      if (hasRain || hasSnow) {
        this.p.rain = hasRain ? w.rain : 0;
        this.p.snowfall = hasSnow ? w.snow : 0;
      } else {
        const snowy = preset === 'snow' || this.p.temperature < 0.5;
        this.p.rain = snowy ? 0 : base.precip;
        this.p.snowfall = snowy ? base.precip : 0;
      }
      this.p.precip = this.p.rain + this.p.snowfall;
      /* A forced wet preset should look as though it has been raining for a
       * while, not as though it started on the frame the screenshot was taken. */
      if (typeof w.wetness !== 'number') this.p.wetness = clamp(this.p.rain * 1.15, 0, 1);
      if (typeof w.snowCover !== 'number') this.p.snowCover = clamp(this.p.snowfall * 1.4, 0, 1);
      this.p.snow = this.p.snowCover;
      this.published = {};
      this._publish(true);
    }
  }

  onTime(hours) { if (typeof hours === 'number') this.hours = hours; }

  onQuality(tier) { this.tier = tier || this.tier; this._applyTier(); }

  _applyTier() {
    const t = this.tier || {};
    this._particleScale = clamp(typeof t.particles === 'number' ? t.particles : 1, 0.1, 1);
    /* At the bottom of the ladder the big transparent sheets and the shaft quad
     * go first: they are all fill rate, which is exactly what the machine that
     * dropped to `floor` has run out of. */
    const name = t.name || 'ultra';
    this._sheetsOn = name !== 'floor';
    this._shaftsOn = name === 'ultra' || name === 'high' || name === 'medium';
    this._rainBodyOn = name === 'ultra' || name === 'high';
    if (!this._rainBodyOn && this.rainDark) this.rainDark.visible = false;
    if (!this._sheetsOn) {
      if (this.groundFog) this.groundFog.mesh.visible = false;
      if (this.scud) this.scud.mesh.visible = false;
    }
    if (!this._shaftsOn && this.shafts) this.shafts.visible = false;
  }

  /** What it is doing and what it is about to do. Handy in the console, and the
   *  contract the floor's HUD would read if anyone wires one up. */
  forecast() {
    const remaining = this.phase === 'dwell'
      ? Math.max(0, this.dwellFor - this.phaseT)
      : Math.max(0, this.transFor - this.phaseT);
    let at = remaining;
    const upcoming = this.queue.slice(0, 3).map(name => {
      const entry = {preset: name, inSeconds: Math.round(at)};
      const d = PRESETS[name]?.dwell || [150, 250];
      at += (d[0] + d[1]) / 2 + 45;
      return entry;
    });
    return {
      seed: this.seed,
      locked: this.locked,
      preset: this.p.preset,
      state: this.state,
      phase: this.phase,
      changingIn: Math.round(remaining),
      next: this.phase === 'trans'
        ? {preset: this.state, inSeconds: Math.round(remaining)}
        : (upcoming[0] || null),
      upcoming,
      params: {
        rain: +this.p.rain.toFixed(3), snowfall: +this.p.snowfall.toFixed(3),
        snow: +this.p.snow.toFixed(3),
        wetness: +this.p.wetness.toFixed(3), snowCover: +this.p.snowCover.toFixed(3),
        cloud: +this.p.cloud.toFixed(3), fog: +this.p.fog.toFixed(3),
        wind: +this.p.wind.toFixed(3), windAngle: +this.p.windAngle.toFixed(3),
        temperature: +this.p.temperature.toFixed(1),
        visibility: +this.p.visibility.toFixed(3),
      },
    };
  }

  dispose() {
    this.enabled = false;
    try {
      const scene = this.ctx.scene;
      if (this._ownsFog && scene.fog) scene.fog = null;
      if (this._ownsBackground && scene.background === this._bg) scene.background = null;
      this.bolt?.geometry?.dispose?.();
      for (const d of this._disposables) { try { d.dispose?.(); } catch { /* gone already */ } }
      this._disposables.length = 0;
      if (this.group) scene.remove(this.group);
    } catch (err) {
      console.warn('[weather] dispose', err);
    }
  }
}

export default Weather;
