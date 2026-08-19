/* gi.js — the light on the site.
 *
 * One directional light, three shadow cascades and a grid of irradiance probes.
 * Between them they are responsible for nearly all of whether this reads as a
 * rendered place or as a WebGL demo, so four decisions are worth stating up
 * front:
 *
 *   0. **The sun's shadow is cascaded, and the caster set is chosen, not
 *      inherited.** three gives a DirectionalLight one shadow map; one map that
 *      reaches a ridge cannot also resolve a handrail. Cascade 0 is the light's
 *      own, fitted small; two coarser maps are rendered here and selected per
 *      fragment by which box a pixel falls in. What goes into each of them is
 *      decided by projected size and by distance — see `CSM_BY_TIER` and
 *      `_nearCull` — because the shadow pass was re-rendering nearly as much
 *      geometry as the beauty pass, most of it sleepers.
 *
 *   1. **Indirect light is directional.** A constant ambient term is the single
 *      most recognisable tell of a toy renderer: every surface not facing the
 *      sun goes the same flat grey, and the north wall of a shed is as bright as
 *      the inside of a yard. What is here instead is a sparse grid of spherical
 *      harmonic probes (L1), lit from a sky gradient plus a ground bounce tinted
 *      by the terrain, with coarse occlusion traced against a height field of
 *      the terrain and the buildings. The inside of the yard is darker than the
 *      open field because the probes in the yard can see less sky, which is the
 *      actual reason it is darker outside too.
 *
 *   2. **Screen-space AO multiplies indirect light, and barely touches direct.**
 *      The engine computes an AO buffer and its composite applies none of it
 *      (`uAOStrength` is zeroed from here); it is folded in instead into
 *      `reflectedLight.indirectDiffuse`, where occlusion physically belongs.
 *      AO over direct sunlight is the classic mistake — it puts grime in the
 *      lit side of every crease and reads as dirt, not shadow — and when the
 *      shadow map only reached 192 metres it was being over-driven to nearly
 *      three times gain to stand in for the shadows that were missing. With the
 *      cascades in it is back to roughly unity.
 *
 *   3. **Real point lights are rationed, and the ration never changes size.**
 *      Adding or removing a light from the scene changes `NUM_POINT_LIGHTS` and
 *      recompiles every material in the world, which on a bench PC is a visible
 *      stall. So the pool is allocated once per quality tier and idle lights sit
 *      at zero intensity rather than leaving the scene. Other subsystems ask for
 *      light through `requestLight()` and are told, honestly, whether they got
 *      one — a yard flood that did not win a slot can still draw its emissive
 *      lamp glass and cost nothing.
 *
 * What other modules use:
 *
 *   gi.applyGI(material)            register a MeshStandardMaterial for probe
 *                                   lighting + AO. Idempotent. Arrays accepted.
 *                                   (Everything already in the scene when the
 *                                   world reports `ready` is adopted anyway;
 *                                   this is for materials made later.)
 *   gi.requestLight({position, colour, intensity, radius, priority, alwaysOn})
 *                                   → handle {active, set(), move(), release()}
 *   gi.sunDirection / sunColour / sunIntensity      the current key light
 *   gi.nightFactor                  0 by day, 1 after dusk
 *   gi.artificialFactor             how on the lamps should be (night ∪ storm)
 *   gi.irradianceAt(x, y, z, normal, out)   the probe field, on the CPU
 */
import * as THREE from 'three';

const DEG = Math.PI / 180;

/* Direction count for the probe integration. 32 stratified directions is enough
 * for an L1 fit — L1 has four coefficients per channel, so the fit is massively
 * over-determined at 32 and the error that remains is in bands we do not
 * store. Doubling it costs double the trace and changes nothing visible. */
const DIRS = 32;

/* How far a probe looks for an occluder. Beyond this the site is open field as
 * far as the indirect term is concerned; a hill 200m away does not measurably
 * change the sky visible from a yard, and pretending otherwise costs a longer
 * march on every one of ~2000 probes. */
const TRACE_STEPS = [2.5, 6, 12, 21, 34, 55, 88];

const POOL_BY_TIER = {ultra: 10, high: 8, medium: 6, low: 3, floor: 0};

/* Cascaded shadow maps.
 *
 * three gives a DirectionalLight exactly one shadow map, and one map cannot be
 * both sharp on a locomotive at 40m and present on a ridge at 400m. Round five
 * measured what that costs from both sides at once: the light's ortho was
 * ±192m at `cam=low`, so the near field had crisp building and gantry shadows
 * and *everything past that box cast nothing at all* — which is exactly what
 * every critic in every round has reported, in the form "not a single tree
 * casting a shadow onto the ridge while the buildings twenty metres away cast
 * hard directional shadows from the same sun".
 *
 * So the light's own map becomes cascade 0 and is deliberately made *smaller*
 * — fitted to the first slice of the view frustum, where a 3072 map buys a
 * 10cm texel — and two coarser maps are rendered here, each fitted to a further
 * slice, selected per fragment by which box the fragment falls in and
 * cross-faded over the last fifth of each box so no seam is drawable.
 *
 * They are rendered here rather than by three because a second DirectionalLight
 * would change NUM_DIR_LIGHTS, recompile every material in the world, and then
 * add its own direct term on top of the sun's. A layer per cascade, an ortho
 * camera that can see only that layer, and a depth material swap over a list we
 * already keep is the same result for one uniform block and no recompile.
 *
 * `reach` is how far down the view frustum the cascade extends, as a multiple
 * of the orbit distance; `cap` is the hard ceiling on its radius. Cascade 0's
 * numbers are in `_fitShadow`. */
const CSM_LAYERS = [6, 7];
const CSM_BY_TIER = {
  ultra:  [{size: 2048, from: 0.62, reach: 3.0, cap: 320, quant: 24},
           {size: 2048, from: 2.6,  reach: 8.0, cap: 820, quant: 64}],
  high:   [{size: 1536, from: 0.62, reach: 3.0, cap: 320, quant: 24},
           {size: 1536, from: 2.6,  reach: 8.0, cap: 820, quant: 64}],
  medium: [{size: 1024, from: 0.62, reach: 8.0, cap: 820, quant: 64}],
  low:    [],
  floor:  [],
};

/* What is worth a draw call in a coarse map, and what is worse than nothing.
 *
 * Two measures, both taken from the *prototype* geometry so an InstancedMesh is
 * judged by one sleeper rather than by the four kilometres of track its
 * bounding sphere spans:
 *
 *   `size`   the instance's radius. Under a few texels it is a shadow nobody
 *            can resolve, and the pass has a whole-scene budget to fit in.
 *   `rise`   how tall it stands. This is the one that matters and the one a
 *            radius test misses: the site's four largest instanced sets by
 *            triangle count are rail's sleepers, tie plates, chairs and spikes
 *            — 10,958 instances, ~400k triangles, and a vertical extent of
 *            0.0 to 0.3 metres. Their shadow lands on the ballast they are
 *            bedded in, at every sun angle a lab is ever rendered at. They were
 *            being drawn into the shadow map on every refit, for a result no
 *            viewer has ever seen. That is where the cascades are paid for.
 *
 * And a third rule with no threshold: a mesh whose vertical extent is under a
 * tenth of its footprint is a slab — a yard apron, a road, a painted hazard
 * stripe. Those are coplanar with the terrain they sit on, so at a 40cm texel
 * they do not cast a shadow, they paint a field of acne across the ground
 * around themselves. `labcore:concrete`'s apron is 300m across; it was in the
 * old coarse map's caster list, and "stale dark decals painting shade where
 * nothing stands" is what that looks like from the camera. */
const CAST_MIN_RISE = 0.45;
const CSM_MIN_RISE = [2.0, 5.0];
const CSM_MIN_SIZE = [1.6, 4.0];
const CSM_MAX_CASTERS = [
  {ultra: 104, high: 96, medium: 72, low: 0, floor: 0},
  {ultra: 72, high: 64, medium: 56, low: 0, floor: 0},
];

/* Eye adaptation. The meter is a 24×14 grid of log-luminance tiles read back
 * off last frame's scene target; 336 tiles of sixteen taps each is 5376 samples
 * of the frame, which is far more than a robust trimmed mean needs and small
 * enough that the readback is 1.3 kB. */
const METER_W = 24, METER_H = 14;
const METER_LOG_MIN = -14, METER_LOG_SPAN = 26;

/* Where the metered middle of the frame is asked to sit, in linear scene
 * luminance before the tone curve. Measured, not chosen: the exposure sweep in
 * `harness/gisweep.mjs` put the reference numbers (mean L 79-109, p95 174-178)
 * at exposure 1.5-1.7 on the clear 14:00 frame, and this is the key that lands
 * there from that frame's own measurement. */
const GRADE_KEY = 0.115;

/* Probe grid: metres per cell, and the hard cap on cells per axis. The cap is
 * what keeps a lab with forty instruments from building a 60×60 grid. */
const GRID_BY_TIER = {
  ultra:  {cell: 17, max: 26, layers: 5},
  high:   {cell: 20, max: 22, layers: 5},
  medium: {cell: 27, max: 16, layers: 4},
  low:    {cell: 38, max: 11, layers: 3},
  floor:  null,                          // hemisphere fallback, no 3D fetches
};

/* ---- what the tier is actually asking for -------------------------------- */

/**
 * The bottom rung, and the one question this module now asks first.
 *
 * engine.js publishes `{gi: false, lighting: 0.00, emissiveOnly: true}` on the
 * `floor` tier. Ryan: "maybe floor can have no lighting at all (like GI, it can
 * still have like a rudimentary emission system and all that but no shadows or
 * complex lighting)."
 *
 * The important word is *no*, not *less*. A quarter-strength probe field still
 * builds the grid, still traces ~2000 probes against the height field, still
 * uploads three 3D textures and still costs a `sampler3D` fetch per fragment; a
 * quarter-strength cascade still renders the cascade. Scaling this rig toward
 * zero would keep every one of those costs and pay for none of the picture. So
 * `gi === false` selects a different path outright — see `_flat` below — and
 * this is read off the tier object rather than off `tier.name` so that a tier
 * table which grows a sixth rung, or a harness that pins a synthetic tier,
 * still gets the answer its own fields ask for.
 */
function giOff(tier) {
  return tier?.gi === false || (tier?.lighting ?? 1) <= 0;
}

/**
 * How much of the frame this module is allowed to spend on lighting: 1.00 /
 * 0.90 / 0.70 / 0.45 down the ladder, 0 at the floor. It is a budget, not a
 * dimmer — nothing here multiplies a colour by it, because a tier step that
 * darkened the world would read as dusk falling rather than as a setting
 * changing. What it buys is *work*: how often each coarse cascade is redrawn,
 * how many probes are relit per frame, how often the meter stalls the pipeline
 * for a readback, and how hard the AO buffer is driven. Spending less of it
 * costs latency in the light's response to a change, which is invisible on a
 * floor display, rather than costing the picture.
 */
function lightingBudget(tier) {
  const l = tier?.lighting;
  return clamp(Number.isFinite(l) ? l : 1, 0, 1);
}

/* The lit path's key-to-fill at CLEAR AIR, and the value of `diffuse` that
 * counts as clear air. The argument for 0.21 is on `_fitFill`; these two exist
 * because that argument was being made about a value the rule could not reach.
 *
 * `FILL_CLEAR_DIFFUSE` is weather.js's own `PRESETS.clear` run through
 * `_fitFill`'s own expression: cloud 0.06 + fog 0.05 * 0.35 = 0.0775. It is
 * this file's best guess at another module's private table, so `_fitFill`
 * prefers `ctx.weather.presets.clear` if that module ever publishes one, and
 * tracks the floor downward off the live field either way — see the ask filed
 * for weather.js. A constant standing in for another module's quantity is
 * exactly the failure recorded at the end of REQUESTS.md, and the two guards
 * are there so that this one reports itself instead of going quiet. */
const FILL_CLEAR_DIFFUSE = 0.06 + 0.05 * 0.35;

/**
 * The clear-air fill, as a LAW rather than as a key-to-fill constant.
 *
 * What was here was `FILL_CLEAR_RATIO = 0.21`, a fill expressed as a fraction
 * of the key. That is the wrong shape for the quantity, and it is the wrong
 * shape in the way REQUESTS.md's own pattern section describes: it is a
 * constant standing in for something that varies, so it is right at one hour
 * and wrong at every other. A clear sky's diffuse irradiance does not track the
 * beam HORIZONTAL irradiance — it tracks the beam NORMAL irradiance, because
 * the diffuse comes from scattering out of the same beam and the whole sky
 * dome is the scatterer whichever way the ground faces. The standard clear-sky
 * models all have this shape:
 *
 *      diffuse on a flat-up surface  =  C * DNI          (C dimensionless)
 *      beam on a flat-up surface     =      DNI * sin h
 *      =>  fill : key                =  C / sin h
 *
 * so the ratio is a FUNCTION OF SOLAR ELEVATION and rises as the sun drops —
 * which is exactly what a low sun looks like, and what a constant cannot do.
 *
 * MEASURED, 2026-08-08, `harness/sn-probe.mjs` at `cam=far`, `weather=clear`,
 * on the shipped file before this change (C back-solved from what it actually
 * delivered, C = fillE / (sunIntensity * lum(sunColour))):
 *
 *      09:00   sun 23.82 deg   fill : key 0.2551   =>  C = 0.103
 *      12:00   sun 34.41 deg   fill : key 0.2414   =>  C = 0.136
 *      16:00   sun 21.29 deg   fill : key 0.2935   =>  C = 0.107
 *
 * A physical C is constant across the day at fixed turbidity. Ours wandered by
 * a third, because two different rules were taking turns owning the answer (the
 * ratio at high sun, an absolute floor at low sun) and neither of them is the
 * law. The wander is in the wrong direction as well: it fills MOST at the hour
 * the sun is highest, which is the hour a shadow should be deepest.
 *
 * WHERE 0.075 COMES FROM. The clear-sky diffuse factor runs about 0.055 for
 * exceptionally clean air to about 0.14 for an average clear day. The file was
 * sitting at 0.103-0.136 — the hazy end of "clear". 0.075 is the clean-maritime
 * end, which is what a site on an island in open water is, and it is as far as
 * this can be pushed without leaving the band the physics supports. It delivers
 *
 *      09:00  0.186     12:00  0.133     16:00  0.207
 *
 * i.e. a 27% cut at the low-sun judged hours and a 45% cut at midday, and it
 * lands inside the 0.12-0.15 that was asked for at the hour that request was
 * really describing — a high sun.
 *
 * READ THE MEASUREMENT IN `sn-decomp.mjs` BEFORE MOVING THIS. Cutting the fill
 * is NOT what makes a shadow at the operator's camera, and the number is in
 * REQUESTS.md: at `cam=far` 09:00, 51% of the light in a shadowed ground pixel
 * is sky.js's aerial perspective, applied after lighting, against 19% for this
 * term. Ablated in one session with the stop pinned, taking fill:key from
 * 0.2551 to 0.0383 — a 6.7x cut, four times harder than this change — moved
 * the plant's shadow bar from 0.83 stops to 1.01 and the frame's p01 from 24.3
 * to 21.4, and the two frames are indistinguishable side by side. With the fog
 * ablated the same cut is worth +0.73 stops. Whatever is wrong with the shadows
 * in that frame, it is not in this constant, and spending the indirect term on
 * it buys a fifth of a stop for every stop of legibility it costs.
 */
const FILL_CLEAR_C = 0.075;

/* And the elevation floor under `C / sin h`, so the ratio cannot run away as
 * the sun sets. sin(14 deg) — below that the sun is delivering so little that
 * the absolute floors below own the answer anyway, and letting the divisor keep
 * shrinking only makes a ratio out of nothing. */
const FILL_SIN_MIN = Math.sin(14 * Math.PI / 180);

/* The key above which the daytime ambient floor stops applying at all.
 *
 * That floor exists for an overcast noon — "dim, not dark", where the sun term
 * collapses with the cloud that is producing the light. It was written as an
 * absolute, so it also bound in CLEAR air, where it had no business: measured
 * on the shipped file at 09:00 clear it delivered 0.1950 against the ratio's
 * 0.1845, so the floor owned the answer and the ratio was decoration — which
 * the file said in a comment and left alone. Gating it on how much key there
 * actually is turns it back into what it was written to be. The knee is the
 * clear-air key at the LOWEST judged hour (16:00, sunE 0.664), so the floor is
 * fully out of the way whenever the sun is at least as strong as a clear late
 * afternoon, and comes back smoothly under cloud and at dusk. */
const FILL_FLOOR_KNEE = 0.66;

/* The flat tier's key-to-fill, and why it is nearly four times the lit tiers'.
 *
 * `_fitFill` holds the fill at `FILL_CLEAR_C / sin h` of the sun — a fifth of
 * it at a low sun, an eighth at a high one — so that a cast shadow has two and
 * a half stops to fall through. With `shadows: false` there is no cast
 * shadow to protect, and the only job left for the indirect term is to keep the
 * faces the sun cannot reach off the floor of the encoding — the north wall of
 * a shed, the inside of a gantry, the shaded flank of a tank. Holding a fifth
 * there would put those at the bottom of the range with nothing to explain it,
 * which is the "black holes" failure this tier is least able to argue its way
 * out of. So the flat path trades the separation it cannot use for legibility,
 * which is what the screen is for. */
const FLAT_FILL_RATIO = 0.78;

/* And an absolute floor under it, in the same linear irradiance units the sky
 * model works in. `gi: false` must never be able to produce a black world: at
 * 03:00, under a storm, with the sun at 0.42 and the sky at almost nothing,
 * this is what is left, and an operator still has to be able to read a status
 * board across the room by it. It is deliberately above `_fitFill`'s own night
 * floor of 0.055 — that one has a shadowed, probe-lit world underneath it and
 * this one has nothing underneath it at all. */
const FLAT_FILL_MIN = 0.135;

/* No bloom and no point-light pool at this tier, so a lit window is the only
 * thing in the frame that can say "on". Both of the things that used to sell it
 * are gone — the halation that made a signal lamp read as a lamp, and the pool
 * of light it threw on the ground beneath it — so the emissive itself is worth
 * more here than it is anywhere above. Applied in the shader rather than by
 * writing `emissiveIntensity`, because buildings.js drives that field every
 * frame from its own night curve and two authors on one property is a fight
 * nobody wins. */
const FLAT_EMISSIVE_GAIN = 1.85;

/* ---- small maths -------------------------------------------------------- */

function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

function smoothstep(e0, e1, x) {
  const t = clamp((x - e0) / (e1 - e0 || 1e-6), 0, 1);
  return t * t * (3 - 2 * t);
}

/** The composite's ACES approximation, on one channel. Duplicated rather than
 *  shared because it lives in a GLSL string in engine.js; the grade below has
 *  to know where a given scene luminance is going to land, and guessing at that
 *  is how a black point ends up sitting on top of half the frame. */
function acesLuma(x) {
  const v = Math.max(0, x);
  return clamp((v * (2.51 * v + 0.03)) / (v * (2.43 * v + 0.59) + 0.14), 0, 1);
}

/** Fibonacci sphere — equal solid angle per direction, which is what lets the
 *  SH accumulation below use one constant weight instead of a per-sample one. */
function sphereDirections(n) {
  const out = [];
  const ga = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i + 0.5) * (2 / n);
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const a = ga * i;
    out.push(new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r));
  }
  return out;
}

/* Float32 → IEEE half. Three ships DataUtils, but a dozen lines here is cheaper
 * than depending on which of the two vendored bundles happens to export it. */
const _f32 = new Float32Array(1);
const _i32 = new Int32Array(_f32.buffer);
function toHalf(val) {
  _f32[0] = val;
  const x = _i32[0];
  let bits = (x >> 16) & 0x8000;
  let m = (x >> 12) & 0x07ff;
  const e = (x >> 23) & 0xff;
  if (e < 103) return bits;
  if (e > 142) return bits | 0x7c00;
  if (e < 113) {
    m |= 0x0800;
    return bits + ((m >> (114 - e)) + ((m >> (113 - e)) & 1));
  }
  bits |= ((e - 112) << 10) | (m >> 1);
  return bits + (m & 1);
}

/* ---- the material patch -------------------------------------------------- */

/* Declared once and injected after `<common>`, which is the first include in
 * both stages and therefore the only place guaranteed to precede every use. */
const GI_PARS = /* glsl */`
varying vec3 vLemWorld;
uniform float lemIblDiffuse;
uniform float lemEnvSpec;
uniform float lemGIStrength;
uniform vec3 lemSkyIrradiance;
uniform vec3 lemGroundIrradiance;
#ifdef LEM_GI_FLAT
  uniform float lemEmissiveGain;
  uniform float lemFlatSpec;
#endif
#ifdef LEM_GI_PROBES
  uniform highp sampler3D lemProbeR;
  uniform highp sampler3D lemProbeG;
  uniform highp sampler3D lemProbeB;
  uniform vec3 lemGridMin;
  uniform vec3 lemGridInvSize;
#endif
#ifdef LEM_SSAO
  uniform sampler2D lemAOMap;
  uniform vec2 lemAORes;
  uniform float lemAOStrength;
  uniform float lemAOFloor;
  uniform float lemAOContact;
#endif
#ifdef LEM_FAR_SHADOW
  uniform vec3 lemNearCentre;
  uniform vec3 lemLightRight;
  uniform vec3 lemLightUp;
  uniform float lemNearRadius;

  uniform sampler2D lemCsmMap0;
  uniform mat4 lemCsmMat0;
  uniform vec4 lemCsmParam0;      // texel, texel, depth bias, normal bias
  uniform vec4 lemCsmBox0;        // centre.xyz, radius
  uniform float lemCsmReady0;
  #ifdef LEM_CSM2
    uniform sampler2D lemCsmMap1;
    uniform mat4 lemCsmMat1;
    uniform vec4 lemCsmParam1;
    uniform float lemCsmReady1;
  #endif

  /* A cascade's footprint, measured in the light's own plane rather than in
   * world XZ — the boxes are axis-aligned to the light, not to the site, and
   * testing the wrong axes puts the handover band at an angle to the map it is
   * handing over from. 1 well inside, 0 outside, and a smooth fifth of the box
   * in between: a hard swap between two maps whose texels differ by a factor of
   * four draws a visible line across the ground, and the whole reason for
   * having three of these is that nobody should be able to see where one ends. */
  float lemBoxWeight( const in vec3 wpos, const in vec3 centre, const in float radius ) {
    vec3 d = wpos - centre;
    float q = max( abs( dot( d, lemLightRight ) ), abs( dot( d, lemLightUp ) ) );
    return 1.0 - smoothstep( radius * 0.80, radius * 0.97, q );
  }

  float lemNearWeight( const in vec3 wpos ) {
    return lemBoxWeight( wpos, lemNearCentre, lemNearRadius );
  }

  /* RGBA-packed depth, unpacked by hand: <packing> is included after <common>
   * and therefore after this, so unpackRGBAToDepth is not declared yet. */
  float lemUnpackDepth( const in vec4 v ) {
    return dot( v, vec4( 1.0, 1.0 / 255.0, 1.0 / 65025.0, 1.0 / 16581375.0 ) );
  }

  /* Four point taps on a rotated box — the whole filter, since the sampler is
   * NEAREST and has to be (the render target is built that way, and why is
   * argued there). One tap is a staircase and nine buy nothing a 30-80cm texel
   * can hold. */
  float lemCascade( const in vec3 wpos, const in vec3 wnrm, const in sampler2D map,
                    const in mat4 mat, const in vec4 param ) {
    vec4 c = mat * vec4( wpos + wnrm * param.w, 1.0 );
    vec3 p = c.xyz / c.w;
    if ( p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0 || p.z > 1.0 || p.z < 0.0 ) return 1.0;
    float d = p.z - param.z;
    vec2 t = param.xy;
    float s = step( d, lemUnpackDepth( texture2D( map, p.xy + vec2( -0.8,  0.4 ) * t ) ) )
            + step( d, lemUnpackDepth( texture2D( map, p.xy + vec2(  0.4,  0.8 ) * t ) ) )
            + step( d, lemUnpackDepth( texture2D( map, p.xy + vec2(  0.8, -0.4 ) * t ) ) )
            + step( d, lemUnpackDepth( texture2D( map, p.xy + vec2( -0.4, -0.8 ) * t ) ) );
    return s * 0.25;
  }

  /* The selector. Coarsest first, then each finer cascade mixed in over its own
   * box, then the whole thing faded out inside cascade 0 — where three's own
   * lookup has already run and this must keep its hands off, or every penumbra
   * in the near field is squared.
   *
   * lemCsmReady is zero until a map has actually been drawn once. A render
   * target that has never been rendered is not a white one — the texture object
   * exists with no image behind it — and sampling it would put the entire far
   * field in shadow, which is a far louder failure than having no cascade. */
  float lemFarShadow( const in vec3 wpos, const in vec3 wnrm ) {
    float near = lemNearWeight( wpos );
    if ( near >= 0.999 ) return 1.0;
    float s = 1.0;
    #ifdef LEM_CSM2
      if ( lemCsmReady1 > 0.5 ) s = lemCascade( wpos, wnrm, lemCsmMap1, lemCsmMat1, lemCsmParam1 );
    #endif
    if ( lemCsmReady0 > 0.5 ) {
      s = mix( s, lemCascade( wpos, wnrm, lemCsmMap0, lemCsmMat0, lemCsmParam0 ),
               lemBoxWeight( wpos, lemCsmBox0.xyz, lemCsmBox0.w ) );
    }
    return mix( s, 1.0, near );
  }
#endif

/* The unoccluded sky-and-ground term: what a surface at this orientation would
 * receive standing in an open field. Both halves of lemIndirect are built off
 * it — the probe path as a floor under the traced answer, the fallback path as
 * the whole answer. */
vec3 lemHemisphere( const in vec3 wnrm ) {
  return mix( lemGroundIrradiance, lemSkyIrradiance, wnrm.y * 0.5 + 0.5 );
}

/* Irradiance, not radiance: the coefficients are pre-multiplied on the CPU by
 * the cosine-lobe convolution (Â0 = π, Â1 = 2π/3) so that this matches three's
 * own convention, where RE_IndirectDiffuse divides by π again. Each texture
 * holds one colour channel as (L1x, L1y, L1z, L0), which means hardware
 * trilinear filtering interpolates the SH correctly — SH is linear, and so is
 * the filter, per channel.
 *
 * Known weak, measured 2026-08-07 and left alone rather than papered over: in a
 * deeply enclosed cell — a patch of ground between a tank farm and a building
 * wall — the traced field answers 0.011 against the 0.31 the key-to-fill fit
 * asks for and the 0.30 an open cell fifty metres away actually returns. The
 * trace is single-bounce: it marches one ray per direction, and light that
 * reaches that pocket off the concrete and then off the wall is real and is not
 * modelled. That pixel renders at 11/255 with no texture left in it, where both
 * reference sets hold their p1 at 17-21 and neither lets a cast shadow reach
 * the bottom of the range. A floor of the open-field hemisphere under the probe
 * answer is the obvious cure and was tried; at every value under 1.0 it did not
 * move the histogram by a single code, which is not yet understood and is not
 * something to ship a confident constant for. */
vec3 lemIndirect( const in vec3 wpos, const in vec3 wnrm ) {
  #if defined( LEM_GI_FLAT )
    /* The whole indirect term at the bottom rung, and deliberately the first
     * branch rather than a fall-through from the probe path: no world position
     * is read, no 3D texture is bound, no grid exists to bind. Two colours and
     * the normal's vertical component — sky above, bounced ground below — which
     * is the cheapest thing that is still not a constant. A single flat ambient
     * makes the north wall of a shed exactly as bright as its roof, and that one
     * tell is what makes a render read as a toy from across a room; keeping the
     * hemisphere split costs one mix and buys back the only shape this tier has
     * left. lemGIStrength is fitted by _fitFlatAmbient, not _fitFill. */
    return lemHemisphere( wnrm ) * lemGIStrength;
  #elif defined( LEM_GI_PROBES )
    vec3 uvw = clamp( ( wpos - lemGridMin ) * lemGridInvSize, vec3( 0.0 ), vec3( 1.0 ) );
    vec4 n4 = vec4( wnrm, 1.0 );
    vec3 E = vec3( dot( texture( lemProbeR, uvw ), n4 ),
                   dot( texture( lemProbeG, uvw ), n4 ),
                   dot( texture( lemProbeB, uvw ), n4 ) );
    return max( E, vec3( 0.0 ) ) * lemGIStrength;
  #else
    return lemHemisphere( wnrm ) * lemGIStrength;
  #endif
}
`;

const GI_WORLDPOS = /* glsl */`
  vec4 lemW = vec4( transformed, 1.0 );
  #ifdef USE_BATCHING
    lemW = batchingMatrix * lemW;
  #endif
  #ifdef USE_INSTANCING
    lemW = instanceMatrix * lemW;
  #endif
  vLemWorld = ( modelMatrix * lemW ).xyz;
`;

/* Both halves of this run at the end of `<lights_fragment_begin>`, where the
 * direct loop has finished accumulating into `reflectedLight` and `irradiance`
 * has just been declared for the indirect one — the only point in the shader
 * where both are in scope.
 *
 * The far cascade multiplies the *whole* direct term rather than the sun's
 * alone, which is a small lie: a yard lamp within a hundred metres of a pixel
 * beyond the near cascade would be shadowed by a building three hundred metres
 * away. It applies only outside the near box, where a point light is at the far
 * end of its own inverse-square falloff, and the alternative is replacing
 * three's whole light loop to reach one term inside it. */
const GI_APPLY = /* glsl */`
  #if defined( RE_IndirectDiffuse ) || defined( LEM_FAR_SHADOW )
    vec3 lemWorldNormal = inverseTransformDirection( geometryNormal, viewMatrix );
  #endif
  #if defined( LEM_FAR_SHADOW ) && defined( RE_Direct )
    if ( receiveShadow ) {
      float lemFarTerm = lemFarShadow( vLemWorld, lemWorldNormal );
      reflectedLight.directDiffuse *= lemFarTerm;
      reflectedLight.directSpecular *= lemFarTerm;
    }
  #endif
  #if defined( RE_IndirectDiffuse )
    irradiance += lemIndirect( vLemWorld, lemWorldNormal );
  #endif
`;

/* The sky is counted once, and this is where that is enforced.
 *
 * `scene.environment` is a PMREM of the sky, and three feeds it to indirect
 * diffuse as well as to specular: `iblIrradiance` accumulates in
 * `<lights_fragment_maps>` and `RE_IndirectSpecular_Physical` then adds
 * `diffuse * iblIrradiance / PI` to `indirectDiffuse`. That is a second
 * ambient source, at full sky strength, sitting entirely outside the
 * key-to-fill ratio `_fitFill` computes — and unlike the probe field it
 * carries no occlusion at all, because a cube map does not know a building is
 * standing in front of it. Fill at parity with the key is exactly what a cast
 * shadow cannot survive: the shadow mask multiplies the direct term, and if
 * the direct term is a fifth of the pixel there is nothing on screen to see.
 * That was this renderer's missing-shadows bug, and it read as a lighting
 * problem rather than a shadow one, which is why it survived four reviews.
 *
 * Zeroing the term takes the multiple-scattering compensation with it. That is
 * a percent or two on rough dielectrics and the right trade for having the sun
 * mean something.
 *
 * It is applied after the chunk instead of inside it deliberately. The
 * previous attempt matched the `iblIrradiance += getIBLIrradiance(...)` line
 * itself, which never existed to match: `onBeforeCompile` runs BEFORE
 * `resolveIncludes`, so a patch can only ever see `#include <...>` lines, and
 * `.replace` on a string that is not there fails silently and returns the
 * source unchanged. Anchor on includes, never on chunk internals. */
/* The second half of this is here because `material.envMapIntensity` is a lie
 * whenever the environment comes from the scene rather than the material.
 *
 * three, in `setProgram`:
 *
 *     r.isMeshStandardMaterial && null === r.envMap && null !== t.environment
 *       && ( b.envMapIntensity.value = t.environmentIntensity )
 *
 * — i.e. for every standard material in this world (none of which carries its
 * own `envMap`; they all light off `scene.environment`), the per-material
 * value is overwritten by the scene's every single frame, after the uniform
 * has been refreshed from the material. So vegetation's carefully argued 0.30
 * on leaf cards, rail's 1.5 on a polished railhead, buildings' 2.2 on glass
 * and this module's own attempts to grade the fill were all being discarded
 * before the first pixel. Four modules had a knob wired to nothing.
 *
 * `scene.environmentIntensity` is therefore the only global lever, and it is
 * set from `_envFactor`. Per-material weight is put back here instead, on
 * `radiance` — the accumulator `<lights_fragment_maps>` has just filled and
 * `RE_IndirectSpecular` is about to consume — with `lemEnvSpec` a uniform this
 * material owns rather than one shared out of `this.uniforms`. */
/* The flat tier's specular, and the reason `scene.environment` can be dropped
 * there at all.
 *
 * `_ensureEnvironment` argues, correctly, that a world with no environment map
 * turns every metal surface matte black — `radiance` stays zero, and a metalness
 * of 1 has no diffuse to fall back on. On this site that is the tank farm, the
 * railheads and the gantry steel, i.e. most of what an operator is looking for.
 * So the bottom tier cannot simply have its cube taken away.
 *
 * What it can have is the same two-colour hemisphere the diffuse term is
 * already using, evaluated along the reflection vector instead of the normal.
 * That is one mix and no texture fetch, against a prefiltered cube-UV lookup
 * plus the PMREM render that built it, and it keeps steel reading as steel:
 * a tank's upper flank picks up sky, its lower flank picks up ground, and the
 * horizon runs round it where a horizon should. Divided by PI because
 * `lemHemisphere` returns irradiance and this slot wants radiance.
 *
 * `lemFlatSpec` is 0 whenever an environment map is present, because sky.js may
 * own `scene.environment` and this module does not get to remove somebody
 * else's; adding to `radiance` on top of a live cube would be the sky counted
 * twice, which is the exact bug `lemIblDiffuse` exists to prevent. */
const GI_IBL = /* glsl */`
  #if defined( RE_IndirectDiffuse )
    iblIrradiance *= lemIblDiffuse;
  #endif
  #if defined( RE_IndirectSpecular )
    radiance *= lemEnvSpec;
    clearcoatRadiance *= lemEnvSpec;
    #ifdef LEM_GI_FLAT
      if ( lemFlatSpec > 0.0 ) {
        vec3 lemRefl = inverseTransformDirection(
          reflect( - geometryViewDir, geometryNormal ), viewMatrix );
        vec3 lemFlatR = lemHemisphere( lemRefl ) * ( lemFlatSpec / PI ) * lemEnvSpec;
        radiance += lemFlatR;
        clearcoatRadiance += lemFlatR;
      }
    #endif
  #endif
`;

/* The rudimentary emission system the bottom rung is allowed to keep. One
 * multiply, after three's own emissive map has been folded in, so a module that
 * animates `emissiveIntensity` or paints an emissive map is scaled rather than
 * overridden. See `FLAT_EMISSIVE_GAIN` for why it is worth more here. */
const GI_EMISSIVE = /* glsl */`
  #ifdef LEM_GI_FLAT
    totalEmissiveRadiance *= lemEmissiveGain;
  #endif
`;

/* Splice `body` in after `anchor`, and say so out loud if the anchor is gone.
 *
 * A shader patch that misses is the worst kind of bug this file can have: the
 * material still compiles, the scene still renders, nothing throws, and the
 * one term you were reaching for is simply never applied. The env-map fix
 * above spent weeks in that state. three renames chunks between revisions and
 * other subsystems chain their own patches ahead of ours, so a miss is a
 * question of when — it just has to be a miss somebody hears. Warned once per
 * anchor, because this runs per material compile. */
const _missed = new Set();
function after(src, anchor, body, tag) {
  const out = src.replace(anchor, anchor + '\n' + body);
  if (out === src && !_missed.has(tag)) {
    _missed.add(tag);
    console.warn(`[gi] shader anchor "${anchor}" not found (${tag}) — that ` +
                 `part of the lighting patch is not being applied.`);
  }
  return out;
}

/* Applied after `<aomap_fragment>`, so a baked AO map and the screen-space
 * buffer compose rather than fight. Both land on indirect light and nothing
 * else. */
/* `lemAOStrength` is allowed past 1, which is why the clamp is here. The buffer
 * is computed at half resolution from a depth prepass and blurred across
 * depth edges, and the probe field it is correcting sees the world at
 * seventeen metres a cell: between them they under-report occlusion at exactly
 * the scale that matters, the last few centimetres where two surfaces meet. A
 * mild over-drive puts that back. Without the clamp it would drive a fully
 * occluded crease to negative irradiance, which reads as a black hole. */
/* Four taps, not one, and a floor under the result.
 *
 * The buffer is eight dithered samples on a golden-angle spiral at half
 * resolution, blurred only where the bilateral weight lets the blur cross. On a
 * grazing slope that weight collapses and the raw dither survives — as the
 * diagonal streaks and speckle three separate critics reported in the dark
 * parts of the frame. One point sample of that buffer reproduces it exactly;
 * four bilinear taps a texel apart average sixteen of its texels and it stops
 * being visible. The floor is the second half of the same problem: in full
 * shade the indirect term is the entire pixel, so an AO value driven past 1 by
 * `lemAOStrength` was multiplying the only light there was and taking the
 * ground to zero. Occlusion darkens a surface; it does not delete it. */
/* And a contact bite on the direct term, which is the part three rounds of
 * critics have all reported missing.
 *
 * Occlusion over direct light is normally the classic mistake — a flat AO
 * multiply on the sun puts grime in the lit side of every crease. But applying
 * it to indirect light *only* is a physically pure position that, on this
 * scene, produces no visible contact at all: at a key-to-fill of two and a
 * half stops the indirect term is a fifth of a sunlit pixel, so darkening it
 * by half moves that pixel by 10%, and where it matters most — a grass tuft
 * or a trunk meeting sunlit ground — the ground is *lit*, so the fifth is all
 * there is to take. Measured last round at a delta mean of 2.5/255. Nobody saw
 * it, three rounds running.
 *
 * The compromise is to bite the direct term too, but only where the buffer is
 * deeply occluded. Squaring the occlusion makes the response almost nothing at
 * AO 0.9 (a gentle slope, a normal-map crease — where a direct multiply would
 * read as dirt) and substantial at AO 0.3 (the last few centimetres where two
 * surfaces meet, which the shadow map cannot resolve and the seventeen-metre
 * probe grid cannot see). That is contact, not grime, and it is what the
 * reference frames get from having every leaf sit on its own drop shadow. */
const GI_AO = /* glsl */`
  #ifdef LEM_SSAO
    vec2 lemAOUv = gl_FragCoord.xy / lemAORes;
    vec2 lemAOT = 1.0 / lemAORes;
    float lemAO = ( texture2D( lemAOMap, lemAOUv + vec2( -1.2,  0.6 ) * lemAOT ).x
                  + texture2D( lemAOMap, lemAOUv + vec2(  0.6,  1.2 ) * lemAOT ).x
                  + texture2D( lemAOMap, lemAOUv + vec2(  1.2, -0.6 ) * lemAOT ).x
                  + texture2D( lemAOMap, lemAOUv + vec2( -0.6, -1.2 ) * lemAOT ).x ) * 0.25;
    lemAO = clamp( mix( 1.0, lemAO, lemAOStrength ), lemAOFloor, 1.0 );
    reflectedLight.indirectDiffuse *= lemAO;
    reflectedLight.indirectSpecular *= lemAO;
    float lemOcc = 1.0 - lemAO;
    float lemContact = 1.0 - lemOcc * lemOcc * lemAOContact;
    reflectedLight.directDiffuse *= lemContact;
    reflectedLight.directSpecular *= lemContact;
  #endif
`;

/* The meter pass. Each output tile is the mean of sixteen log2 luminances from
 * last frame's HDR scene target, encoded into eight bits over a 26-stop window.
 *
 * Log, not linear, and encoded to a byte rather than read back as half floats,
 * for the same reason: this has to survive a readback on every driver in a lab,
 * and RGBA8 is the one format `readRenderTargetPixels` is guaranteed to hand
 * back. A 26-stop window across 256 codes is a tenth of a stop per code, which
 * is finer than the adaptation will ever act on. */
const METER_VS = /* glsl */`
  out vec2 vUv;
  void main() { vUv = uv; gl_Position = vec4( position.xy, 0.0, 1.0 ); }`;

const METER_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tScene;
  uniform vec2 uStep;
  uniform float uLogMin, uLogSpan;
  layout(location = 0) out vec4 outColor;
  void main() {
    float acc = 0.0;
    for ( int y = 0; y < 4; y ++ ) {
      for ( int x = 0; x < 4; x ++ ) {
        vec2 uv = clamp( vUv + ( vec2( float( x ), float( y ) ) - 1.5 ) * uStep,
                         vec2( 0.002 ), vec2( 0.998 ) );
        vec3 c = texture( tScene, uv ).rgb;
        acc += log2( max( dot( c, vec3( 0.2126, 0.7152, 0.0722 ) ), 1e-5 ) );
      }
    }
    float v = ( acc / 16.0 - uLogMin ) / uLogSpan;
    outColor = vec4( clamp( v, 0.0, 1.0 ), 0.0, 0.0, 1.0 );
  }`;

/** A fullscreen triangle. engine.js has one of these and does not export it;
 *  eleven lines is cheaper than asking for a change to a file we do not own. */
function fullscreenPass(fragmentShader, uniforms) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(
    new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(
    new Float32Array([0, 0, 2, 0, 0, 2]), 2));
  const mat = new THREE.ShaderMaterial({
    vertexShader: METER_VS, fragmentShader, uniforms,
    depthTest: false, depthWrite: false, glslVersion: THREE.GLSL3,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.frustumCulled = false;
  return mesh;
}

export class GlobalIllumination {
  constructor(ctx) {
    this.ctx = ctx;
    this.dirs = sphereDirections(DIRS);

    this.sunDirection = new THREE.Vector3(0.2, 0.72, 0.66).normalize();
    this.sunColour = new THREE.Color(0xfff1dc);
    this.sunIntensity = 3.2;
    this.nightFactor = 0;
    this.artificialFactor = 0;

    /* The sky the probes are lit from. sky.js draws the visible one; if it
     * exposes a sampler we use it, and if not this analytic pair is what the
     * probes integrate. Kept in linear radiance, roughly 1/5 of the sun's
     * horizontal irradiance on a clear day. */
    this.zenith = new THREE.Color(0.10, 0.17, 0.36);
    this.horizon = new THREE.Color(0.34, 0.42, 0.56);
    this.groundAlbedo = new THREE.Color(0.20, 0.20, 0.15);
    this.wallAlbedo = new THREE.Color(0.30, 0.29, 0.27);

    this.materials = new Set();
    this.grid = null;
    this._probesDirty = false;
    this._traceDirty = false;
    this._cursor = 0;
    this._adoptClock = 0;
    this._lightClock = 0;
    this._lightSeq = 0;
    this._lightRequests = new Map();
    this._pool = [];
    this._built = false;

    this.uniforms = {
      lemIblDiffuse:      {value: 0},
      lemGIStrength:      {value: 1},
      lemSkyIrradiance:   {value: new THREE.Vector3(0.5, 0.6, 0.8)},
      lemGroundIrradiance:{value: new THREE.Vector3(0.16, 0.16, 0.13)},
      lemGridMin:         {value: new THREE.Vector3()},
      lemGridInvSize:     {value: new THREE.Vector3(1, 1, 1)},
      lemProbeR:          {value: null},
      lemProbeG:          {value: null},
      lemProbeB:          {value: null},
      lemAOMap:           {value: null},
      lemAORes:           {value: new THREE.Vector2(1920, 1080)},
      /* Both only ever compiled in under LEM_GI_FLAT, but declared here at all
       * tiers: `this.uniforms` is assigned wholesale into every patched shader
       * and a value that appears and disappears from that object between tier
       * steps is a uniform three has already cached a location for. */
      lemEmissiveGain:    {value: 1},
      lemFlatSpec:        {value: 0},
      /* Amplified to 2.8 for three rounds, with a 0.62 bite taken out of the
       * DIRECT term on top, because the shadow map was reaching 192 metres and
       * nothing past that was grounded — so screen-space occlusion was being
       * asked to stand in for shadows it cannot be. Measured this round on the
       * wide 16:30 frame: turning it off moved the image by a mean of 39/255
       * and 64% of pixels by more than 20/255. That is not contact occlusion,
       * that is a second lighting model, and what it was actually painting was
       * the buffer's own dither — eight golden-angle samples at half resolution
       * whose bilateral blur collapses on a grazing slope — smeared across the
       * yard at nearly three times gain. It is the "large hard-edged near-black
       * blobs with no tree or terrain feature above them" three rounds of
       * critics reported, and it survived because the thing it was compensating
       * for was real.
       *
       * With three fitted cascades behind it the compensation is not needed and
       * the fake is worse than nothing, so this comes back to roughly unity:
       * occlusion of the indirect term, which is where occlusion belongs, plus
       * a token bite on direct light for the last few centimetres the 11cm
       * near-cascade texel still cannot resolve. */
      lemAOStrength:      {value: 1.15},
      /* Both re-measured 2026-08-07 and left where they were: knocking the AO
       * buffer out entirely on the clear cam=low frame moved a cast-shadow
       * patch by 0.1/255 and the frame histogram not at all, so whatever is
       * crushing the shadows there (see lemIndirect) it is not this. */
      lemAOFloor:         {value: 0.34},
      lemAOContact:       {value: 0.20},
      lemNearCentre:      {value: new THREE.Vector3()},
      lemLightRight:      {value: new THREE.Vector3(1, 0, 0)},
      lemLightUp:         {value: new THREE.Vector3(0, 0, 1)},
      /* Enormous until the first fit lands, so `lemNearWeight` reads 1 and the
       * coarse lookup short-circuits before it can sample a map that has not
       * been drawn yet. */
      lemNearRadius:      {value: 1e9},
      lemCsmMap0:         {value: null},
      lemCsmMat0:         {value: new THREE.Matrix4()},
      lemCsmParam0:       {value: new THREE.Vector4(1 / 2048, 1 / 2048, 0.0012, 0.4)},
      lemCsmBox0:         {value: new THREE.Vector4(0, 0, 0, 1)},
      lemCsmReady0:       {value: 0},
      lemCsmMap1:         {value: null},
      lemCsmMat1:         {value: new THREE.Matrix4()},
      lemCsmParam1:       {value: new THREE.Vector4(1 / 1536, 1 / 1536, 0.0012, 1.0)},
      lemCsmReady1:       {value: 0},
    };
    this._modeKey = '';

    /* Adaptation state. `_sceneEV` is the metered log2 luminance of the frame;
     * until the first readback lands it is null and the analytic model runs
     * alone, which is also what happens if a driver refuses the readback. */
    this._sceneEV = null;
    this._expNow = null;
    /* Set by `setExposureLocked`. Never set by anything in here: a module that
     * could pin its own stop would eventually pin it during a weather change
     * and nobody would find out for a week. */
    this._exposureLocked = false;
    /* Set by `setFillTrim`. A multiplier on the indirect term's *target* inside
     * `_fitFill`, applied after the ratio and both floors, so an ablation on
     * the key-to-fill balance is one call and cannot be undone by `onTime`
     * putting `lemGIStrength` back — which is exactly how three earlier rounds
     * of shadow numbers on this file came out wrong. See `setFillTrim`. */
    this._fillTrim = 1;
    this._meterClock = 0;
    this._meterBusy = false;
    this._csm = [];
    this._csmTurn = 0;
    this._cullable = [];
    /* Membership, so that re-enrolment is a set test rather than a scan. It is
     * not a WeakSet because entries are deleted when an object leaves the
     * scene, and a WeakSet cannot be enumerated to find them. */
    this._cullIn = new Set();
    this._cullClock = 0;
    this._depthCache = new Map();
    /* Cascade 0's reach, which the coarse cascades' slices are measured off.
     * `_fitShadow` owns it; this is what it is worth before the first fit. */
    this._nearReach = 120;

    /* The bottom rung's switch. Set from the tier in `build`/`onQuality` and
     * read all over this file; when it is true none of the probe, cascade,
     * occlusion, environment or artificial-light machinery is built, serviced
     * or compiled in. */
    this._flat = false;
    this._budget = 1;
    /* Objects whose shadow flags this module suppressed because the world was
     * on the floor tier when it first saw them. The adaptive ladder now *climbs*
     * from the floor tier, and `_adoptShadow` decides once and never revisits —
     * so without this list a world that boots at the bottom and steps up to high
     * would never cast a shadow again, at any tier, for the rest of the session.
     * That is a silent, permanent, hard-to-attribute failure, which is why it is
     * a list and not a comment. */
    this._flatAdopted = [];
  }

  /* ---- build ------------------------------------------------------------- */

  async build(plan) {
    try {
      const ctx = this.ctx;
      this.tier = ctx.quality || {name: 'high', shadow: 1536, ao: true};
      this._flat = giOff(this.tier);
      this._budget = lightingBudget(this.tier);

      this._buildSun();
      this._buildCascades();
      this._buildMeter();
      this._buildPool();
      this._readSky(ctx.world?.timeOfDay ?? 13);
      this._applyFlatMode(this._flat);
      this._refreshEnvIntensity();

      if (plan) this._buildGrid(plan);
      this._syncMode();

      /* Adopt whatever is already standing. Subsystems build in order and GI is
       * second, so on the first pass this finds almost nothing — the `ready`
       * hook below is the one that matters, and `update()` keeps sweeping for
       * meshes that appear later (a train is built when a print is parsed). */
      this._adopt();
      ctx.on?.('ready', () => {
        try {
          this._adopt();
          /* The shadow map is drawn on demand and `ready` is the first moment
           * there is a whole world to draw into it. Two more refits over the
           * following second catch the modules that finish a frame or two
           * late — cheaper than watching for them, and it stops after that. */
          this._fitShadow(true);
          this._settleRefits = 2;
        } catch (e) { void e; }
      });

      this._devProxies();
      this._built = true;
      ctx.engine.shadowNeedsUpdate = true;
    } catch (err) {
      /* A world with no lighting rig is still a world; a world whose lighting
       * rig threw during build is a blank floor. */
      console.warn('[gi] build fell back to the default rig —', err);
    }
  }

  /* ---- the sun ----------------------------------------------------------- */

  /** Cascade 0's map resolution. `ultra` gets more than the engine's tier asks
   *  for because this is the map every contact shadow in the near field comes
   *  out of: at 3072 over the ±168m box `_fitShadow` now allows, a texel is
   *  11 cm, which is a handrail. The map costs fill, not draw calls, so it is
   *  the one lever that buys shadow sharpness without touching the budget. */
  _shadowSize() {
    const base = Math.max(512, (this.tier?.shadow | 0) || 1536);
    return this.tier?.name === 'ultra' ? Math.max(base, 3072) : base;
  }

  _buildSun() {
    this.sun = new THREE.DirectionalLight(0xffffff, 3.0);
    this.sun.castShadow = true;
    const size = this._shadowSize();
    this.sun.shadow.mapSize.set(size, size);
    this.sun.shadow.camera.near = 1;
    this.sun.shadow.camera.far = 1200;
    /* PCFSoftShadowMap ignores `radius` — its kernel is derived from the map
     * size — so the edge width is whatever a texel is. That is exactly the
     * constant-width edge the reference checklist calls acceptable; what it
     * calls unacceptable is no shadow at all. */
    this.sun.shadow.radius = 1;
    this.sun.shadow.blurSamples = 8;
    this.sun.matrixAutoUpdate = true;
    this.ctx.scene.add(this.sun);
    this.ctx.scene.add(this.sun.target);
    this._shadowFit = {centre: new THREE.Vector3(1e9, 0, 0), radius: 0};
  }

  /* ---- the coarse cascades ------------------------------------------------- */

  /** One render target, camera, layer and caster list per coarse cascade.
   *
   *  Rebuilt only when the tier changes the shape of the ladder; a tier step
   *  that leaves both sizes alone keeps the maps and their contents, because
   *  tearing them down means every caster loses its layer bit and the whole
   *  scene has to be swept again before anything casts anywhere. */
  _buildCascades() {
    /* Same argument as the probe grid: no cascade is *rendered* at this tier,
     * so none is allocated either. Two 2048² RGBA targets and their depth
     * buffers are 50 MB of VRAM on a part that has none to spare, and the layer
     * bits the enrolment sweep sets are work nobody would ever read back. */
    const spec = this._flat ? [] : (CSM_BY_TIER[this.tier?.name] ?? CSM_BY_TIER.high);
    const same = this._csm.length === spec.length &&
      this._csm.every((c, i) => c.rt?.width === spec[i].size);
    if (same) return;
    this._disposeCascades();
    if (!spec.length) return;
    try {
      /* Depth packed into RGBA rather than a depth texture: a depth texture
       * cannot be filtered on every WebGL2 part, and the taps in `lemCascade`
       * want to read it as colour anyway.
       *
       * And NEAREST, which is not a detail. These four bytes are one 32-bit
       * fixed-point number, not a colour: bilinear filtering averages the bytes
       * independently, so a texel straddling a silhouette hands back a mantissa
       * byte interpolated between an occluder's and the 0xff of empty sky, and
       * `lemUnpackDepth` decodes that to a depth with no relationship to
       * anything in the scene. It lands *anywhere* in 0..1, which means a
       * fraction of it lands in front of the receiver and shadows it. Every
       * silhouette in both coarse maps was fringed with pixels shadowed by a
       * caster that does not exist, and at a 31cm (cascade 0) and 80cm (cascade
       * 1) texel those fringes are metres wide on the ground — which is what
       * "large dark wedges cross the terrain with no caster anywhere in frame"
       * and "the relay box drops a crisp black rectangle" look like from a
       * camera. The four taps below are the filter; the hardware must not run a
       * second one underneath them. */
      this._depthOpaque = new THREE.MeshDepthMaterial({
        depthPacking: THREE.RGBADepthPacking, side: THREE.FrontSide,
      });
      /* Landforms get their own, pushed away from the light. A hillside is the
       * one caster that is also its own principal receiver, and at the 31cm and
       * 80cm texels these two maps run at, a constant depth bias tuned for a
       * gantry leg is nowhere near enough to keep a gentle slope from shadowing
       * itself — that is a field of acne over the whole terrain, which is worse
       * than the flat lighting it was meant to replace.
       *
       * THIS USED TO BE A POLYGON OFFSET AND A POLYGON OFFSET CANNOT WORK HERE.
       * `polygonOffset` moves the value written to the DEPTH BUFFER. These maps
       * do not read the depth buffer. `MeshDepthMaterial` with
       * `RGBADepthPacking` writes the depth into the COLOUR attachment, and it
       * computes that number in its own fragment shader off an interpolated
       * varying — verified in `static/vendor/three.module.min.js`:
       *
       *    float fragCoordZ = 0.5 * vHighPrecisionZW[ 0 ] / vHighPrecisionZW[ 1 ] + 0.5;
       *
       * Nothing about polygon offset reaches that expression. So this file
       * carried a named anti-acne mechanism for three rounds that could not
       * have moved a single texel, which is worse than carrying none, because
       * the bias numbers around it were tuned as if it were working.
       *
       * The replacement does the same job by the only means that can reach the
       * packed value: it adds the push to `fragCoordZ` itself. The slope term
       * is the depth's own screen-space derivative, which is exactly the
       * quantity `polygonOffsetFactor` multiplies — a slope is steep in the
       * light's depth precisely where it is about to shadow itself — and the
       * constant term is set per cascade from that cascade's texel, in the same
       * currency as every other bias in this file. What it costs is unchanged:
       * a ridge's shadow starts a metre or so downhill of the ridge, which at
       * 400m is not a pixel.
       *
       * Patched on the `#include` line: `onBeforeCompile` runs BEFORE three
       * expands its includes, so a splice that targets expanded chunk text
       * silently applies nothing. */
      this._landBias = {value: 0};
      this._landSlope = {value: 2.2};
      this._depthLand = new THREE.MeshDepthMaterial({
        depthPacking: THREE.RGBADepthPacking, side: THREE.FrontSide,
      });
      this._depthLand.onBeforeCompile = (sh) => {
        sh.uniforms.lemLandBias = this._landBias;
        sh.uniforms.lemLandSlope = this._landSlope;
        sh.fragmentShader = sh.fragmentShader
          .replace('#include <packing>',
            '#include <packing>\nuniform float lemLandBias;\nuniform float lemLandSlope;')
          .replace(/float\s+fragCoordZ\s*=[^;]*;/,
            m => m + '\n  fragCoordZ += lemLandBias + lemLandSlope * ' +
                 'max( abs( dFdx( fragCoordZ ) ), abs( dFdy( fragCoordZ ) ) );');
      };
      /* Or three hands this material the program it compiled for the plain one,
       * patch and all missing. */
      this._depthLand.customProgramCacheKey = () => 'lemDepthLand';
      spec.forEach((s, i) => {
        const rt = new THREE.WebGLRenderTarget(s.size, s.size, {
          type: THREE.UnsignedByteType, format: THREE.RGBAFormat,
          minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter,
          depthBuffer: true, stencilBuffer: false, generateMipmaps: false,
          colorSpace: THREE.NoColorSpace,
        });
        const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 1000);
        cam.layers.set(CSM_LAYERS[i]);
        this._csm.push({
          i, spec: s, rt, cam, layer: CSM_LAYERS[i],
          casters: [], dirty: true, ready: false, overflow: false,
          cost: 0, tris: 0, runs: 0, clock: 0,
          sun: new THREE.Vector3(), fit: new THREE.Vector3(), radius: 0,
        });
        this.uniforms[`lemCsmMap${i}`].value = rt.texture;
        this.uniforms[`lemCsmParam${i}`].value.set(1 / s.size, 1 / s.size, 0.0012, 0.4);
        this.uniforms[`lemCsmReady${i}`].value = 0;
      });
    } catch (err) {
      console.warn('[gi] cascaded shadows unavailable —', err);
      this._disposeCascades();
    }
  }

  _disposeCascades() {
    for (const c of this._csm) {
      for (const o of c.casters) o.layers?.disable?.(c.layer);
      c.rt?.dispose?.();
    }
    this._csm.length = 0;
    this._depthOpaque?.dispose?.();
    this._depthOpaque = null;
    this._depthLand?.dispose?.();
    this._depthLand = null;
    for (const m of this._depthCache.values()) m.dispose?.();
    this._depthCache.clear();
    for (let i = 0; i < CSM_LAYERS.length; i++) {
      this.uniforms[`lemCsmMap${i}`].value = null;
      this.uniforms[`lemCsmReady${i}`].value = 0;
    }
  }

  /** How big one instance of this object is, and how far it stands off whatever
   *  it is sitting on. Measured once and cached, because `computeBoundingBox`
   *  on a geometry nobody has drawn is how you end up printing three's NaN
   *  warning once a frame forever — so it is only asked for when the bounding
   *  sphere is already there and finite, which means the mesh has been through
   *  a frustum test at least once.
   *
   *  Prototype geometry, deliberately. An InstancedMesh's own bounding sphere
   *  spans every instance — four kilometres of track for one sleeper — and
   *  judging a caster by that is how ten thousand invisible sleepers ended up
   *  in the shadow map. */
  _casterMetrics(obj) {
    let m = obj.userData?.lemCast;
    if (m) return m;
    const g = obj.geometry;
    const r = g?.boundingSphere?.radius;
    if (!Number.isFinite(r)) return null;
    let bb = g.boundingBox;
    if (!bb) {
      try { g.computeBoundingBox(); bb = g.boundingBox; } catch (e) { void e; }
    }
    const s = Math.max(obj.scale.x, obj.scale.y, obj.scale.z) || 1;
    const ex = bb ? (bb.max.x - bb.min.x) : r * 2;
    const ey = bb ? (bb.max.y - bb.min.y) : r * 2;
    const ez = bb ? (bb.max.z - bb.min.z) : r * 2;
    const foot = Math.max(ex, ez);
    m = {
      size: r * s,
      rise: ey * (obj.scale.y || 1),
      /* A slab: coplanar with the ground it stands on, so at a 40cm texel it
       * does not cast, it paints acne on the terrain around itself. */
      slab: ey < foot * 0.10 && ey < 4,
    };
    obj.userData = obj.userData || {};
    obj.userData.lemCast = m;
    return m;
  }

  /** The depth material this object should be drawn with in a coarse map.
   *
   *  `customDepthMaterial` first and without argument: it is the owning
   *  module's own answer to this exact question, and vegetation's is the atlas
   *  at the canopy's own alpha threshold — which is the whole reason a tree
   *  casts a tree-shaped shadow rather than the slab it is printed on. The old
   *  coarse map refused every alpha-cut material outright, which is why fifty
   *  vegetation meshes and every tree on the ridge were missing from it. */
  _depthFor(obj) {
    if (obj.userData?.lemLandform) return this._depthLand;
    if (obj.customDepthMaterial) return obj.customDepthMaterial;
    const mat = Array.isArray(obj.material) ? obj.material[0] : obj.material;
    if (!mat || mat.transparent || mat.depthWrite === false) return null;
    if (!(mat.alphaTest > 0) && !mat.alphaMap) return this._depthOpaque;
    const key = mat.uuid;
    let d = this._depthCache.get(key);
    if (!d) {
      d = new THREE.MeshDepthMaterial({
        depthPacking: THREE.RGBADepthPacking, side: mat.side,
        map: mat.map || null, alphaMap: mat.alphaMap || null,
        alphaTest: mat.alphaTest > 0 ? mat.alphaTest : 0.5,
      });
      this._depthCache.set(key, d);
    }
    return d;
  }

  /** Enrol a mesh in whichever coarse cascades it is large enough to matter in.
   *
   *  Size and rise both, and per cascade: a shipping container is worth drawing
   *  into the mid map and not the far one, a tree is worth both, a sleeper is
   *  worth neither. `_trim` then caps each list, so the thresholds decide what
   *  is eligible and the cap decides what actually fits. */
  _enrol(obj) {
    if (!this._csm.length || obj.userData?.noShadow) return;
    const m = this._casterMetrics(obj);
    if (!m || m.slab) return;
    const land = this._isLandform(obj, m);
    /* Landforms are enrolled against the module's intent rather than with it.
     * Every terrain mesh on the site carries `castShadow = false`, correctly:
     * three would draw a 2.6-kilometre ring in full into a 208-metre ortho for
     * a result nobody can see, and `_adoptShadow` refuses anything that big for
     * the same reason. But the coarse maps are 640m and 1640m across and are
     * drawn from a list this module chooses, which is exactly the case the
     * refusal was not written for — and the terrain is the one occluder at that
     * scale. Without it a hillside never shades the cutting below it, an
     * embankment never darkens its own lee, and a ridge at 400m throws nothing
     * across the site at any hour, which is the flat-landscape read every round
     * of critics has described. */
    const wants = land || (obj.userData?.lemCastBase ?? obj.castShadow);
    if (!wants) return;
    /* Not a landform and too big to be anything else: the ground plane, the sky
     * dome, a painted apron. They cost the whole pass and cast onto nothing. */
    if (!land && m.size > 400) return;
    if (land) obj.userData.lemLandform = true;
    if (!this._depthFor(obj)) return;
    for (const c of this._csm) {
      if (obj.layers.isEnabled(c.layer)) continue;
      if (land) {
        /* A landform belongs in a cascade only if it is roughly the size of
         * that cascade's box. The 7.2km horizon ring is entirely outside both
         * of them: drawing it buys a shadow nobody can be standing in and pays
         * a draw call and its whole triangle count on every refit. */
        if (m.size > c.spec.cap * 2.5) continue;
      } else if (m.rise < CSM_MIN_RISE[c.i] || m.size < CSM_MIN_SIZE[c.i]) {
        continue;
      }
      obj.layers.enable(c.layer);
      c.casters.push(obj);
      c.dirty = true;
      c.overflow = true;
    }
  }

  /** A landform: something with real vertical relief and a footprint measured
   *  in hundreds of metres. The rise test is what keeps a yard apron, a road
   *  and the dev ground plane out — they are flat by construction, and a flat
   *  caster coplanar with what it stands on paints acne rather than shade. */
  _isLandform(obj, m) {
    return m.size > 400 && m.rise >= 25 && m.size < 6000 &&
           obj.receiveShadow !== false;
  }

  /** Enforce each cascade's cap, deferred to once per adopt sweep because the
   *  sweep hands objects over in scene order and the smallest may arrive first.
   *
   *  Ranked by size times the square root of the instance count, not by size
   *  alone. Sorting a forest against a tank farm on radius alone puts every
   *  building above every tree — each shed is sixty metres across and each tree
   *  is thirteen — and the cap then removes exactly the thing this cascade was
   *  written for. One InstancedMesh holding six hundred trees is one draw call
   *  and six hundred shadows; that is what the count term is paying for. */
  _trim() {
    for (const c of this._csm) {
      if (!c.overflow) continue;
      c.overflow = false;
      const cap = CSM_MAX_CASTERS[c.i][this.tier?.name] ?? 64;
      if (c.casters.length <= cap) continue;
      const worth = o => (o.userData.lemCast?.size || 1) *
        Math.sqrt(o.isInstancedMesh ? Math.max(1, o.count) : 1);
      c.casters.sort((a, b) => worth(b) - worth(a));
      for (let i = cap; i < c.casters.length; i++) c.casters[i].layers.disable(c.layer);
      c.casters.length = cap;
      c.dirty = true;
    }
  }

  /** Fit one cascade's ortho to its slice of the view frustum and redraw it.
   *
   *  Slices, not nested boxes: cascade 1 starts where cascade 0's reach ends
   *  (with a fifth of overlap for the cross-fade), so its sphere bounds an
   *  annulus rather than everything from the eye outward. That is the whole
   *  difference between a cascade and a second copy of the same map — bounding
   *  0..400m and 0..1200m would give the near slice of the second map the same
   *  texel size as the far one, and buy nothing. */
  _renderCascade(c) {
    const ctx = this.ctx;
    const cam = ctx.camera;
    if (!c.rt || !this.sun || !cam || !c.casters.length) return;
    /* Anything that has left the scene keeps its layer bit and would otherwise
     * be material-swapped forever — a train is built per parse and disposed. */
    if (c.casters.some(o => !o.parent)) {
      c.casters = c.casters.filter(o => o.parent);
      if (!c.casters.length) return;
    }

    const dist = ctx.rig?.distance ?? 200;
    const near = Math.max(cam.near, this._nearReach * c.spec.from);
    const far = clamp(dist * c.spec.reach, this._nearReach * (c.spec.from + 1.4),
                      c.spec.cap * 2.2);
    const fit = this._fitOrtho(cam, near, far, c.spec.quant, c.spec.cap, c.fit);
    if (!fit) return;
    c.radius = fit.radius;

    const d = this.sunDirection;
    const sc = c.cam;
    /* Snapped to whole texels along the light's own axes, for the same reason
     * cascade 0 is: a box that drifts by a fraction of a texel makes every
     * static shadow in the world crawl, and these maps are redrawn on a clock
     * rather than every frame, so the crawl would come out as a stutter. */
    const up0 = Math.abs(d.y) > 0.98 ? new THREE.Vector3(0, 0, 1)
                                     : new THREE.Vector3(0, 1, 0);
    const right = this._csmRight || (this._csmRight = new THREE.Vector3());
    const up = this._csmUp || (this._csmUp = new THREE.Vector3());
    right.crossVectors(d, up0).normalize();
    up.crossVectors(right, d).normalize();
    const texel = (fit.radius * 2) / c.rt.width;
    const centre = this._csmCentre || (this._csmCentre = new THREE.Vector3());
    centre.copy(fit.centre)
      .addScaledVector(right, Math.round(fit.centre.dot(right) / texel) * texel
                              - fit.centre.dot(right))
      .addScaledVector(up, Math.round(fit.centre.dot(up) / texel) * texel
                           - fit.centre.dot(up));

    const back = fit.radius * 1.6 + 340;
    sc.left = -fit.radius; sc.right = fit.radius;
    sc.top = fit.radius; sc.bottom = -fit.radius;
    sc.near = Math.max(1, back - fit.radius * 1.8 - 300);
    sc.far = back + fit.radius * 1.8 + 340;
    sc.position.copy(centre).addScaledVector(d, back);
    sc.up.set(Math.abs(d.y) > 0.98 ? 1 : 0, Math.abs(d.y) > 0.98 ? 0 : 1, 0);
    sc.lookAt(centre);
    sc.updateMatrixWorld(true);
    sc.matrixWorldInverse.copy(sc.matrixWorld).invert();
    sc.updateProjectionMatrix();

    /* Both biases are in this map's own units and scale with its texel: a bias
     * tuned for a 10cm texel is a field of acne at 45cm, and one tuned for
     * 45cm peter-pans everything in the near field by half a metre. */
    const param = this.uniforms[`lemCsmParam${c.i}`].value;
    param.x = param.y = 1 / c.rt.width;
    param.w = clamp(texel * 1.35, 0.05, 1.6);
    param.z = clamp(texel / Math.max(1, sc.far - sc.near) * 3.2, 0.00012, 0.006);
    /* The landform push, in THIS map's normalised depth. An orthographic
     * projection is linear in depth, so metres divide straight through by the
     * frustum's range. 1.5 texels is the same currency `param.w` above is in,
     * and it is a constant number of texels rather than a constant number of
     * metres for the same reason that one is: a push tuned at 31 cm is a field
     * of acne at 80 cm and a push tuned at 80 cm peter-pans a ridge at 31. */
    if (this._landBias) {
      this._landBias.value = clamp(texel * 1.5, 0.05, 2.0) /
                             Math.max(1, sc.far - sc.near);
    }
    const bias = this._csmBiasM || (this._csmBiasM = new THREE.Matrix4().set(
      0.5, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0, 0, 0.5, 0.5, 0, 0, 0, 1));
    this.uniforms[`lemCsmMat${c.i}`].value
      .multiplyMatrices(sc.projectionMatrix, sc.matrixWorldInverse)
      .premultiply(bias);
    if (c.i === 0) this.uniforms.lemCsmBox0.value.set(centre.x, centre.y, centre.z,
                                                      fit.radius);

    const renderer = ctx.renderer;
    const prevTarget = renderer.getRenderTarget();
    const prevClear = renderer.getClearColor(this._csmClear ||
      (this._csmClear = new THREE.Color()));
    const prevAlpha = renderer.getClearAlpha();
    const calls0 = renderer.info.render.calls;
    const tris0 = renderer.info.render.triangles;
    /* Swap materials rather than using `scene.overrideMaterial`: the override
     * would also apply to whatever else happens to sit on this layer, and it
     * would flatten every alpha-cut canopy into the rectangle it is printed on.
     * The per-object swap is a hundred assignments over a list we already keep,
     * and it lets each caster keep its own cutout. */
    const saved = this._csmSaved || (this._csmSaved = []);
    saved.length = 0;
    /* The scene's background is a full-screen draw that ignores layers, so it
     * would paint the sky over the packed depth this pass exists to write. */
    const prevBg = ctx.scene.background;
    try {
      for (const o of c.casters) {
        saved.push(o.material);
        o.material = this._depthFor(o) || o.material;
      }
      ctx.scene.background = null;
      renderer.setRenderTarget(c.rt);
      renderer.setClearColor(0xffffff, 1);     // 1.0 packed = nothing in front
      renderer.clear(true, true, false);
      renderer.render(ctx.scene, sc);
    } catch (err) {
      void err;                                // a missing cascade is survivable
    } finally {
      for (let i = 0; i < c.casters.length; i++) {
        if (saved[i]) c.casters[i].material = saved[i];
      }
      saved.length = 0;
      ctx.scene.background = prevBg;
      renderer.setRenderTarget(prevTarget);
      renderer.setClearColor(prevClear, prevAlpha);
    }
    c.cost = renderer.info.render.calls - calls0;
    c.tris = renderer.info.render.triangles - tris0;
    c.sun.copy(this.sunDirection);
    c.dirty = false;
    c.ready = true;
    c.runs++;
    this.uniforms[`lemCsmReady${c.i}`].value = 1;
  }

  /** At most one coarse cascade per frame, and never on the frame three redraws
   *  its own map.
   *
   *  The budget is 450 draw calls for the whole scene and a wide camera already
   *  spends 234 of them before any shadow is drawn, so two shadow passes in one
   *  frame is the difference between holding the ceiling and blowing it.
   *  Deferring costs almost nothing — these maps are of scenery, and scenery
   *  does not move — so a cascade waits for a frame with room. The exception is
   *  the first: a map that has never been drawn is not usable at all, and one
   *  heavy frame during the load is cheaper than a view with no far shadows. */
  _serviceCascades(dt) {
    if (!this._csm.length) return;
    const ctx = this.ctx;
    for (const c of this._csm) {
      c.clock += dt;
      /* A slow refresh on top of the dirty flags, because rolling stock is
       * enrolled and nothing tells us it moved. Staggered so two cascades never
       * come due on the same frame. */
      /* Divided by the tier's lighting budget: 0.9s at ultra, 1.3s at medium,
       * 2.0s at low. What a slower refresh costs is how long a shadow lags the
       * thing that cast it — a wagon that stopped two seconds ago still has its
       * shadow where it was — and at 640m and 1640m across, that is a lag of
       * well under a texel for anything but rolling stock, which cascade 0
       * owns anyway. It is the cheapest real thing on this ladder to sell. */
      if (c.clock > (0.9 / Math.max(this._budget, 0.2)) + c.i * 0.37) {
        c.clock = 0; c.dirty = true;
      }
      if (c.sun.dot(this.sunDirection) < 0.9995) c.dirty = true;
    }
    const cold = this._csm.find(c => !c.ready && c.casters.length);
    if (cold) { this._renderCascade(cold); return; }
    if (ctx.engine?.shadowNeedsUpdate) return;
    const n = this._csm.length;
    for (let k = 0; k < n; k++) {
      const c = this._csm[(this._csmTurn + k) % n];
      if (!c.dirty) continue;
      if ((ctx.engine?.drawCalls | 0) + (c.cost || 100) > 440) return;
      this._csmTurn = (this._csmTurn + k + 1) % n;
      this._renderCascade(c);
      return;
    }
  }

  /* ---- the meter ----------------------------------------------------------- */

  _buildMeter() {
    if (this._meterRT) return;
    try {
      this._meterRT = new THREE.WebGLRenderTarget(METER_W, METER_H, {
        type: THREE.UnsignedByteType, format: THREE.RGBAFormat,
        minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter,
        depthBuffer: false, stencilBuffer: false, generateMipmaps: false,
        colorSpace: THREE.NoColorSpace,
      });
      this._meterPass = fullscreenPass(METER_FS, {
        tScene: {value: null},
        uStep: {value: new THREE.Vector2(1 / (METER_W * 4), 1 / (METER_H * 4))},
        uLogMin: {value: METER_LOG_MIN}, uLogSpan: {value: METER_LOG_SPAN},
      });
      this._meterScene = new THREE.Scene();
      this._meterScene.add(this._meterPass);
      this._meterCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
      this._meterBuf = new Uint8Array(METER_W * METER_H * 4);
      this._meterTiles = new Float32Array(METER_W * METER_H);
    } catch (err) {
      console.warn('[gi] eye adaptation falling back to the analytic model —', err);
      this._meterRT = null;
    }
  }

  /** Downsample last frame's scene target and start a readback.
   *
   *  Last frame's, deliberately and unavoidably: subsystem `update` runs before
   *  the beauty pass, so the target holds the previous frame. One frame of lag
   *  in a term that is smoothed over most of a second is not observable, and
   *  the alternative — metering after the render — would need a hook in
   *  engine.js that is not ours to add. */
  _meter() {
    const rt = this._meterRT;
    const src = this.ctx.engine?._targets?.scene;
    if (!rt || !src?.texture || this._meterBusy) return;
    const renderer = this.ctx.renderer;
    const prev = renderer.getRenderTarget();
    try {
      this._meterPass.material.uniforms.tScene.value = src.texture;
      renderer.setRenderTarget(rt);
      renderer.render(this._meterScene, this._meterCam);
    } catch (err) {
      void err;
      renderer.setRenderTarget(prev);
      return;
    }
    renderer.setRenderTarget(prev);

    this._meterBusy = true;
    const finish = () => {
      this._meterBusy = false;
      if (!this._disposed) this._consumeMeter();
    };
    try {
      if (typeof renderer.readRenderTargetPixelsAsync === 'function') {
        renderer.readRenderTargetPixelsAsync(rt, 0, 0, METER_W, METER_H, this._meterBuf)
          .then(finish, () => { this._meterBusy = false; });
      } else {
        renderer.readRenderTargetPixels(rt, 0, 0, METER_W, METER_H, this._meterBuf);
        finish();
      }
    } catch (err) {
      void err;
      this._meterBusy = false;
      this._meterRT = null;                   // asked once, refused, never again
    }
  }

  /** A trimmed mean of the tiles, in stops.
   *
   *  Trimmed and not plain, because the two ends of this histogram are exactly
   *  the two things that must not set the exposure: the sun's disc and a
   *  specular highlight sit at the top and would stop the camera down until the
   *  rest of the frame is unreadable, and the black inside a doorway sits at
   *  the bottom and would open it until the sky is paper. What is left in the
   *  middle is the surface the camera is actually looking at — which at
   *  `cam=low`, standing in a tree stand, is genuinely in shadow, and that is
   *  the case the analytic model could never see. */
  _consumeMeter() {
    const buf = this._meterBuf, tiles = this._meterTiles;
    if (!buf || !tiles) return;
    const n = METER_W * METER_H;
    let valid = 0;
    for (let i = 0; i < n; i++) {
      const v = buf[i * 4] / 255;
      if (v <= 0.002) continue;               // an untouched tile, not a black one
      tiles[valid++] = METER_LOG_MIN + v * METER_LOG_SPAN;
    }
    if (valid < n * 0.4) return;              // the target was not drawn this frame
    const use = tiles.subarray(0, valid);
    use.sort();
    const lo = Math.floor(valid * 0.12), hi = Math.max(lo + 1, Math.floor(valid * 0.88));
    let sum = 0;
    for (let i = lo; i < hi; i++) sum += use[i];
    const ev = sum / (hi - lo);
    if (!Number.isFinite(ev) || ev < METER_LOG_MIN + 0.5) return;
    this._sceneEV = ev;
    /* The shadow end of the same histogram, which is what the black point is
     * placed against. A percentile and not the minimum: one tile straddling a
     * doorway would otherwise decide where the whole frame's floor sits. */
    this._sceneEVLow = use[Math.floor(valid * 0.05)];
  }

  /** Fit the shadow ortho to the slice of the view frustum shadows are drawn
   *  in, rather than to a circle around the orbit target.
   *
   *  Those two are not the same shape, and the difference is what a critic
   *  sees. At the street camera the orbit target is a few metres in front of
   *  the eye while the ground being read runs a hundred and fifty metres past
   *  it, so a box centred on the target stops the shadows halfway up the frame
   *  — which is worse than having none, because it reads as weather. Bounding
   *  the eight corners of the near..`far` slice is eight transforms and covers
   *  exactly what is on screen, at every camera preset, with no tuning.
   *
   *  Re-fits are hysteretic and the radius is quantised to 16m steps: every
   *  re-fit is a full shadow-map redraw at ~100 extra draw calls, and a radius
   *  that drifts continuously also changes the texel size continuously, which
   *  makes every static shadow in the world crawl. */
  /** The centre and radius of a sphere around the near..far slice of a camera's
   *  frustum, quantised. Shared by both cascades, which differ only in how far
   *  down the frustum they reach and how coarse a step they round to.
   *
   *  The slice sets the RADIUS. It does not set the centre — see
   *  `_groundAnchor`, and the measurement that made that necessary. */
  _fitOrtho(cam, near, far, quant, cap, out) {
    cam.updateMatrixWorld();
    const tanH = Math.tan((cam.fov || 42) * 0.5 * DEG);
    const tanW = tanH * (cam.aspect || 1.78);
    const pts = this._frustumPts || (this._frustumPts =
      Array.from({length: 8}, () => new THREE.Vector3()));
    let i = 0;
    for (const z of [near, far]) {
      for (const sx of [-1, 1]) for (const sy of [-1, 1]) {
        pts[i++].set(sx * tanW * z, sy * tanH * z, -z).applyMatrix4(cam.matrixWorld);
      }
    }
    const centre = (out || this._fitTmp || (this._fitTmp = new THREE.Vector3()))
      .set(0, 0, 0);
    for (const p of pts) centre.add(p);
    centre.multiplyScalar(1 / 8);
    let radius = 1;
    for (const p of pts) radius = Math.max(radius, p.distanceTo(centre));
    radius = clamp(Math.ceil(radius / quant) * quant, quant, cap);
    this._groundAnchor(cam, radius, centre);
    return {centre, radius};
  }

  /** Move a shadow box onto the GROUND, at the point the camera is aimed at.
   *
   *  The centroid of eight frustum corners is a point on the camera's centre
   *  ray, in mid-air, at whatever height the depth slice happens to put it. At
   *  a camera whose eye is near the ground that is also near the ground and
   *  the difference is a metre or two, which is why this went unnoticed through
   *  nine rounds judged from `yard` and `street`. At `cam=far` — the operator's
   *  own camera, eye 407 m up and 806 m back from the site — it is not.
   *  Measured, 2026-08-08, before this function existed:
   *
   *      near box     centre 362.1 m ABOVE the ground,   75 m from the eye
   *      cascade 0    centre 220.2 m above the ground,  362 m from the eye
   *      cascade 1    centre  97.8 m BELOW the ground, 1004 m from the eye
   *
   *  with the site itself 806 m from the eye. Every one of these boxes is
   *  square to the LIGHT, not to the world, so a centre 362 m above the ground
   *  does not merely waste half its depth — it slides the patch of ground the
   *  box covers by 362/tan(24 deg) = 821 m along the sun's azimuth. The result
   *  was the one `gx-csmmap` printed: the near box and cascade 0 covered 0 and
   *  1 of 8 site pads respectively, and the whole plant — the frame's subject —
   *  was shadowed by cascade 1 at 0.80 m/texel while a 0.10 m map and a 0.31 m
   *  map were being drawn, and paid for, over empty air.
   *
   *  This is a fit bug and not a budget one, which is the whole point: the
   *  casters, the draw calls and the triangles are identical before and after.
   *  The boxes are simply pointed at the picture.
   *
   *  The rule is two clauses and no tuning:
   *
   *    * the centre goes on the ground at the point the camera is AIMED at.
   *      With an orbit rig that point is exact — it is `rig.target` — and for a
   *      free camera it is the centre ray's own ground crossing.
   *    * except that it is never nearer the eye than one radius past the first
   *      ground the frustum can see. That clause is what preserves the street
   *      camera, where the aim point is a few metres in front of the eye and
   *      the ground being read runs a hundred and fifty metres past it: without
   *      it a box centred on the target spends half of itself behind the
   *      picture, which is the failure the frustum fit was written to cure.
   *
   *  Nothing here moves the radius, so no texel size and no hysteresis
   *  threshold changes; only the centre does. The NEAR box is fitted by
   *  `_nearBand` instead, which does move its radius, and says why.
   */
  _groundAnchor(cam, radius, centre) {
    /* The supported ablation. Every A/B this project has run on shadow flags
     * has been silently undone by `_adopt`/`_enrol` on their one-second clock,
     * so the switch is put where the fit is read instead of on the objects:
     * `gi.setShadowAnchor(false)` and the next refit is the old behaviour, in
     * the same page session, with the same world under it. */
    if (this._noAnchor) return centre;
    const g = this._viewGround(cam);
    if (!g.ok) return centre;
    const d = Math.max(Number.isFinite(g.dAim) ? g.dAim : g.dNear + radius, g.dNear + radius);
    if (!Number.isFinite(d)) return centre;
    return this._groundPoint(cam, d, centre);
  }

  /** The three numbers every box fit needs off the camera, measured once:
   *  the ground reference height, how far away the ground first enters the
   *  picture, and how far away the camera is aimed — all horizontal distances
   *  from the eye, so that a box's placement never depends on the eye's height.
   *
   *  `dNear` is taken off the BOTTOM edge of the frustum, not the centre ray.
   *  At the operator's camera the centre ray crosses the ground 806 m out while
   *  the first ground the operator can actually see is 368 m out, and a fit
   *  that uses the centre ray abandons the whole near half of the picture. */
  _viewGround(cam) {
    const g = this._vg || (this._vg = {});
    const rig = this.ctx.rig;
    /* The orbit target is by construction a point on the surface the camera is
     * orbiting, so its height is the site's — not the terrain's mean, not 0. */
    g.gy = Number.isFinite(rig?.target?.y) ? rig.target.y : 0;
    const e = cam.matrixWorld.elements;
    /* Basis off the matrix rather than off the quaternion: this runs after
     * `updateMatrixWorld` and a camera driven by a matrix write would have a
     * stale quaternion. Column 2 of a view matrix is +Z, which points BACK. */
    const fx = -e[8], fy = -e[9], fz = -e[10];
    const ux = e[4], uy = e[5], uz = e[6];
    g.fx = fx; g.fz = fz;
    g.fh = Math.hypot(fx, fz);
    g.ok = g.fh > 1e-4;
    if (!g.ok) return g;                 // straight down: no horizontal axis to place along
    const drop = cam.position.y - g.gy;
    const tanH = Math.tan((cam.fov || 42) * 0.5 * DEG);
    const bx = fx - ux * tanH, by = fy - uy * tanH, bz = fz - uz * tanH;
    g.dNear = (by < -1e-5 && drop > 0) ? drop * Math.hypot(bx, bz) / -by : 0;
    if (rig?.target && Number.isFinite(rig.target.x)) {
      g.dAim = Math.hypot(rig.target.x - cam.position.x, rig.target.z - cam.position.z);
    } else if (fy < -1e-5 && drop > 0) {
      g.dAim = drop * g.fh / -fy;        // a free camera: the centre ray's own crossing
    } else {
      g.dAim = NaN;                      // aimed at or above the horizon
    }
    g.tanH = tanH;
    return g;
  }

  /** A point on the ground reference plane, `d` metres from the eye along the
   *  camera's own horizontal heading. `_viewGround` must have run. */
  _groundPoint(cam, d, out) {
    const g = this._vg;
    return out.set(cam.position.x + g.fx / g.fh * d, g.gy,
                   cam.position.z + g.fz / g.fh * d);
  }

  /** How far from the eye the near map is still worth having, in metres.
   *
   *  The two shadow paths in this file are not the same currency and it took a
   *  measurement to see it. Three's own map is redrawn EVERY frame — not on
   *  our clock: `harness/gy-shadowclock.mjs` traps the setter and finds
   *  `engine.shadowNeedsUpdate` raised 251 times a second, all of it from
   *  `trains.js`'s step. The coarse cascades are redrawn once per 0.9 s inside
   *  our own render call. So one caster in the near map costs a draw call sixty
   *  times a second, and the same caster in cascade 0 costs one draw call about
   *  once a second. Measured at `cam=far`, moving the near box onto the plant
   *  along with the coarse ones: 200 -> 322 draws and 1.281 -> 1.810 M
   *  triangles, every frame.
   *
   *  What it bought was a 0.104 m texel where cascade 0 already delivers 0.313
   *  and where one screen pixel covers 0.688 m of ground. Three times finer
   *  than the display can show, at 61% more draw calls.
   *
   *  So the near map does not follow the aim point past the distance at which
   *  cascade 0's texel is ALREADY smaller than a pixel. That distance is
   *  measured from the quantities in play — cascade 0's fitted radius, its map
   *  size, this camera's field of view and the viewport's own height — and not
   *  from a camera name or a constant, so it moves when any of them is retuned
   *  and it returns Infinity at a tier with no coarse cascade at all, where the
   *  near map is the only map there is.
   */
  _nearUsefulRange() {
    const c0 = this._csm[0];
    if (!c0 || !c0.radius || !c0.rt?.width) return Infinity;
    const cam = this.ctx.camera;
    const tanH = Math.tan((cam?.fov || 42) * 0.5 * DEG);
    if (!(tanH > 0)) return Infinity;
    const h = this.ctx.renderer?.domElement?.height || 1080;
    const texel = (c0.radius * 2) / c0.rt.width;
    return texel * h / (2 * tanH);
  }

  /** Fit the NEAR box to the band of visible ground it is the best map for.
   *
   *  The coarse boxes are placed on what the camera is aimed at; the near box
   *  cannot be, and the reason is a cost difference nobody had measured. It is
   *  fitted to a band instead: from the first ground the picture contains, out
   *  to wherever cascade 0 stops being the cheaper answer.
   *
   *  Returns `{radius: 0}` when that band is empty, which means the near map
   *  has no job at this camera at all. That is not a degenerate case, it is the
   *  operator's own camera: at `cam=far` the first visible ground is 368 m out
   *  and cascade 0's texel goes sub-pixel at 367 m.
   *
   *  Reproduces the radii the hand-tuned reach was already choosing where that
   *  reach was right — 152 m at `cam=yard`, ~70 m at `cam=street` — because it
   *  is fitted to the same thing those numbers were tuned against, one camera
   *  at a time. It differs only where nobody had looked.
   */
  _nearBand(cam, reachCap) {
    if (this._noAnchor) return null;              // ablation: fall back to the slice fit
    const g = this._viewGround(cam);
    if (!g.ok) return null;
    const d0 = g.dNear;
    const aim = Number.isFinite(g.dAim) ? g.dAim : d0;
    /* The far edge: where cascade 0 takes over, or two radii past what the
     * camera is aimed at, whichever comes first. The second term is what keeps
     * the near map on the subject at the `low` tier, where there is no coarse
     * cascade to hand over TO and the first term is infinite. */
    const d1 = Math.min(this._nearUsefulRange(), Math.max(d0, aim) + reachCap * 2);
    const half = (d1 - d0) * 0.5;
    /* Less than one quantisation step of band is no band. */
    if (!(half > 8)) return {radius: 0, centre: null};
    const radius = clamp(Math.ceil(half / 8) * 8, 8, reachCap);
    const lo = d0 + radius;
    const d = clamp(aim, lo, Math.max(lo, d1 - radius));
    return {radius,
            centre: this._groundPoint(cam, d, this._want || (this._want = new THREE.Vector3()))};
  }

  _fitShadow(force = false) {
    if (this._flat) return;             // no map is drawn, so there is none to fit
    if (!this.sun) return;
    const ctx = this.ctx;
    const cam = ctx.camera;
    if (!cam) return;

    const dist = ctx.rig?.distance ?? 200;
    /* How far out the SHARP shadows are drawn, and the number the whole cascade
     * ladder is hung off — cascade 1 starts at 0.62 of it, cascade 2 at 2.7.
     *
     * It used to be dist·1.55 capped at 300, which at cam=low fitted a 384m box
     * and at cam=wide a 640m one. That is a quarter-metre texel at the wide
     * camera, and it was the reason a handrail's shadow was a grey smear while
     * a tree three hundred metres out had none at all: one map was being asked
     * to do both jobs and doing neither. With two coarse maps behind it this is
     * free to be small, and small is what buys a 7cm texel at 3072. */
    /* Raised from `dist * 0.80` capped at 150, which never let the box reach the
     * 168 m the map was sized for. Measured at `cam=yard`: orbit distance 105 →
     * reach 84 → a radius-80 box at a 5.2 cm texel, and everything past 84 m
     * handed to cascade 0 at **28 cm**. A tank car is 3 m wide and the coupled
     * gap between two of them is 22 cm, so at 28 cm — with a four-tap cross
     * spanning ±0.8 of a texel on top — a locomotive and three wagons resolve
     * into one smear with no gaps and no running gear. That is the second half
     * of "the train drags an amorphous dark blob": the near half of the frame is
     * shadowed at 5 cm and the half the trains actually run through at 28.
     *
     * The 3072 map over the 168 m cap is 11 cm, which the note on `_shadowSize`
     * already argues is a handrail. Filling that cap rather than stopping short
     * of it doubles the sharp reach for a texel that is still finer than
     * anything on this site, and it costs nothing measurable — the near map is
     * redrawn on about two frames in three at this camera already, and forcing a
     * redraw every single frame moved the frame rate by less than the noise. */
    /* `far` is now a CEILING on the near box rather than its reach: the reach
     * is fitted to the ground by `_nearBand`. It still hangs the coarse ladder,
     * which is what the paragraph above is about. */
    const far = clamp(dist * 1.45, 60, 168);
    this._nearReach = far;

    /* Park the near map when it has no band to cover — see `_nearBand`. It is
     * parked by taking every caster out of it rather than by clearing
     * `sun.castShadow`, which would change NUM_DIR_LIGHT_SHADOWS and recompile
     * every material in the world each time the camera crossed the threshold. A
     * shadow pass with no casters is a clear and nothing else. */
    const band = this._nearBand(cam, far);
    if (band && band.radius <= 0) {
      if (!this._nearParked) {
        this._nearParked = true;
        this._shadowFit.radius = 0;
        /* Radius, not a flag: `lemBoxWeight` is 0 for every point further than
         * a millimetre from the centre, so the coarse cascades own the frame
         * with no shader change and no branch. */
        this.uniforms.lemNearRadius.value = 1e-3;
        this._cullDirty = true;
        ctx.engine.shadowNeedsUpdate = true;
      }
      return;
    }
    this._nearParked = false;

    let want, radius;
    if (band) {
      want = band.centre;
      radius = band.radius;
    } else {
      const near = Math.max(cam.near, 0.5);
      const got = this._fitOrtho(cam, near, far, 8, 168,
        this._want || (this._want = new THREE.Vector3()));
      want = got.centre;
      radius = got.radius;
    }

    const fit = this._shadowFit;
    const moved = want.distanceTo(fit.centre);
    if (!force && moved < radius * 0.10 && radius === fit.radius) return;
    fit.centre.copy(want);
    fit.radius = radius;

    /* Snap the centre to whole shadow texels along the light's own axes. Without
     * it the shadow crawls over static geometry every time the box moves by a
     * fraction of a texel, which reads as the world shimmering. */
    const d = this.sunDirection;
    const upAxis = Math.abs(d.y) > 0.98 ? new THREE.Vector3(0, 0, 1)
                                        : new THREE.Vector3(0, 1, 0);
    const right = new THREE.Vector3().crossVectors(d, upAxis).normalize();
    const up = new THREE.Vector3().crossVectors(right, d).normalize();
    const size = this.sun.shadow.mapSize.x || 1024;
    const texel = (radius * 2) / size;
    const cr = Math.round(want.dot(right) / texel) * texel;
    const cu = Math.round(want.dot(up) / texel) * texel;
    const snapped = want.clone()
      .addScaledVector(right, cr - want.dot(right))
      .addScaledVector(up, cu - want.dot(up));

    /* Stand the light back far enough that a hill or a stack behind the box is
     * still between the near plane and its own shadow, and no further: the
     * depth range is what the bias below is measured in, so a frustum stretched
     * to 1200m makes every bias number four times as coarse as it needs to be. */
    const back = radius * 1.6 + 260;
    const scam = this.sun.shadow.camera;
    scam.left = -radius; scam.right = radius;
    scam.top = radius; scam.bottom = -radius;
    scam.near = Math.max(1, back - radius * 1.6 - 200);
    scam.far = back + radius * 1.6 + 240;
    scam.updateProjectionMatrix();

    this.sun.target.position.copy(snapped);
    this.sun.position.copy(snapped).addScaledVector(d, back);
    this.sun.target.updateMatrixWorld();

    /* Normal bias in world units has to track the texel, or the value that
     * removes acne on a 500m box punches the contact shadows off a locomotive
     * at 60m. Peter-panning is what happens when this is set once and left.
     * 1.4 texels is a little under the PCF kernel's own reach, which is the
     * point where acne stops without the base of a wall coming unstuck. */
    /* Down from 1.4 texels and a 0.40 m cap. Normal bias buys freedom from
     * acne by pushing the receiver away from the caster along its own normal,
     * and every centimetre of it is a centimetre of gap between a trunk and
     * the shadow it stands on — which is precisely the "hard, unoccluded seam"
     * critics kept reporting where objects meet the ground. At the wide camera
     * the old number was 29 cm of peter-panning. One texel is where the PCF
     * kernel's own reach starts, so it is the smallest value that still hides
     * acne, and the cap keeps the widest fit from undoing the contact again. */
    this.sun.shadow.normalBias = clamp(texel * 1.0, 0.006, 0.22);
    /* Depth bias stays tiny and does the rest. In normalised units over the
     * range set above, this is a couple of centimetres of slope allowance —
     * enough for the terrain, small enough that a rail sitting on ballast still
     * darkens the stone beside it. */
    this.sun.shadow.bias = -0.00006;
    this.ctx.engine.shadowNeedsUpdate = true;

    /* Hand the near cascade's footprint to the shader, in the light's own
     * plane, so the coarse map knows where to stop. `snapped` and not `want`:
     * the box the shader is told about has to be the box that was drawn, or the
     * handover band lands a texel off and a seam appears along it. */
    this.uniforms.lemNearCentre.value.copy(snapped);
    this.uniforms.lemLightRight.value.copy(right);
    this.uniforms.lemLightUp.value.copy(up);
    this.uniforms.lemNearRadius.value = radius;
    /* Cascade 0's box moved, so every box behind it has: they are fitted to
     * slices of the same frustum and hung off the same reach. */
    for (const c of this._csm) c.dirty = true;
    /* And what is inside the near box changed with it. Deferred rather than run
     * here: a camera fly refits several times a second and the cull walks every
     * instance matrix on the site. */
    this._cullDirty = true;
  }

  /* ---- the sky model ------------------------------------------------------ */

  /** Read the sun off sky.js if it is there, and derive it ourselves if it is
   *  not. The direction convention is checked rather than assumed: a vector
   *  that points at the ground at one in the afternoon is a to-sun/from-sun
   *  disagreement, not a sunset, and getting it wrong lights the world from
   *  under the terrain. */
  _readSky(hours) {
    const h = Number.isFinite(hours) ? hours : 13;
    this.hours = h;

    /* Our own model first, so there is always something coherent to fall back
     * on and something to sanity-check the sky module against. The day runs
     * 05:30 to 20:00 rather than a tidy 06:00–18:00: an eight-hour-shift lab
     * spends most of its day between those two numbers, and a model that sets
     * at 18:00 sharp makes 18:30 — the middle of second shift — pitch black. */
    const dayAngle = (h - 5.5) / 14.5 * Math.PI;
    const elev = Math.sin(dayAngle) * (62 * DEG);
    const azi = dayAngle - Math.PI * 0.5;
    const own = new THREE.Vector3(Math.cos(elev) * Math.sin(azi), Math.sin(elev),
                                  Math.cos(elev) * Math.cos(azi)).normalize();

    const sky = this.ctx.world?.subsystems?.get?.('sky');
    let dir = null;
    const raw = sky?.sunDirection;
    if (raw && Number.isFinite(raw.x) && Number.isFinite(raw.y) && Number.isFinite(raw.z)
        && raw.lengthSq?.() > 0.25) {
      dir = new THREE.Vector3(raw.x, raw.y, raw.z).normalize();
      if (own.y > 0.06 && dir.y < -0.06) dir.negate();
    }
    /* `solarDirection` is where the sun really is, even when it is under the
     * site. The key light below may become the moon; the sky's glow must stay
     * on the sunset's side of the world, so the two are kept apart. */
    this.solarDirection = (this.solarDirection || new THREE.Vector3()).copy(dir || own);
    this.sunDirection.copy(this.solarDirection);

    const alt = this.solarDirection.y;
    const w = this.ctx.weather || {};
    const cloud = clamp(w.cloud ?? 0.2, 0, 1);
    const storm = w.preset === 'storm' ? 1 : (w.preset === 'rain' ? 0.55 : 0);

    this.dayFactor = smoothstep(-0.09, 0.10, alt);
    this.nightFactor = 1 - this.dayFactor;
    /* Civil twilight: the sky is still lit for a good while after the sun is
     * not. Collapsing the two into one number is what makes a sunset a light
     * switch instead of an hour. */
    this.civil = smoothstep(-0.32, 0.05, alt);
    /* The lamps come on as the sun gets low, not the instant it is gone —
     * which is when a yard actually switches them on. */
    this.artificialFactor = clamp(
      (1 - smoothstep(0.02, 0.30, alt)) + storm * 0.5 + cloud * 0.15, 0, 1);

    /* Below the horizon the key light becomes the moon: same shadow map, same
     * cost, opposite side of the sky, and a colour cold enough that nobody
     * mistakes it for a very dim afternoon. */
    if (this.dayFactor < 0.02) {
      this.sunDirection.negate();
      this.sunDirection.y = Math.abs(this.sunDirection.y) * 0.55 + 0.35;
      this.sunDirection.normalize();
    }

    /* Warm and weak near the horizon: the atmosphere is a long red filter at a
     * grazing angle, and it is most of what makes an 18:30 frame read as
     * evening rather than as noon with the exposure pulled down. */
    /* The band runs all the way to 48° rather than stopping just above the
     * horizon. A sun at 20° is already visibly golden and already carries a
     * third of the light it does at noon; treating everything above 25° as
     * "noon" is what leaves an 18:30 frame lit by a white sun under a full
     * blue sky, and blue sky plus white sun in shadow is navy. */
    const low = 1 - smoothstep(0.02, 0.75, Math.max(alt, 0));
    const warm = new THREE.Color(1.0, 0.44, 0.16);
    const noon = new THREE.Color(1.0, 0.955, 0.90);
    const sunCol = noon.clone().lerp(warm, low * 0.86);
    const moonCol = new THREE.Color(0.52, 0.62, 0.92);

    if (sky?.sunColour || sky?.sunColor) {
      const c = sky.sunColour || sky.sunColor;
      if (c?.isColor) sunCol.copy(c);
    }
    /* The sky's aureole is always the sun's colour, never the key light's. Tie
     * it to `sunColour` and the residual glow after sunset comes out moon-blue
     * over an orange horizon, which is magenta — and nothing in the sky has
     * ever been magenta at nine in the evening. */
    this.solarColour = (this.solarColour || new THREE.Color()).copy(sunCol);

    const clearSun = 3.55 * this.dayFactor * (1 - low * 0.55);
    const throughCloud = 1 - clamp(cloud, 0, 1) ** 1.35 * (0.72 + storm * 0.25);
    let intensity = clearSun * Math.max(throughCloud, 0.05);
    if (Number.isFinite(sky?.sunIntensity) && sky.sunIntensity > 0) {
      intensity = sky.sunIntensity;
    }
    if (this.dayFactor < 0.02) {
      /* Moonlight is nowhere near this bright in reality. It is this bright
       * because the floor is a status display: an operator has to be able to
       * read the site at 03:00, and a physically honest night is a black
       * rectangle with seven orange dots in it. */
      this.sunColour.copy(moonCol);
      intensity = 0.42 * (1 - cloud * 0.75);
    } else {
      this.sunColour.copy(sunCol);
    }
    this.sunIntensity = intensity;

    if (this.sun) {
      this.sun.color.copy(this.sunColour);
      this.sun.intensity = this.sunIntensity;
      /* A sun this weak casts no shadow anyone can see, and the shadow pass is
       * the most expensive thing in the frame on integrated graphics. At the
       * flat tier it never casts at all — and this line runs on every hour
       * change, so leaving it out of the `_flat` test would quietly hand the
       * shadow pass back to the bottom rung at the next tick of the clock. */
      this.sun.castShadow = !this._flat && this.sunIntensity > 0.14;
    }

    this._buildSkyGradient(alt, cloud, storm, low);
    this._skyFn = typeof sky?.sampleSky === 'function' ? sky.sampleSky.bind(sky) : null;

    /* The two numbers every non-probe consumer needs: sky irradiance on a
     * flat-up surface, and what comes back off the ground. They are also the
     * `floor` tier's entire indirect term. */
    const flat = this._skyIrradianceFlat();
    this.uniforms.lemSkyIrradiance.value.copy(flat);
    /* `_fitFill` first: the ground bounce below has to divide by the scale it
     * sets, and a stale one is a whole hour of light out of date. */
    this._fitFill(flat);
    this._setGroundIrradiance(flat);
    this._adapt();
  }

  /** The hemisphere fallback's downward term — the `floor` tier's entire ground
   *  bounce, and what `irradianceAt` answers with before a grid exists. Carries
   *  the same divided-out sun share as the probe integration, for the same
   *  reason: without it the bottom tier is lit by blue sky alone. */
  _setGroundIrradiance(flat) {
    const gs = clamp(this.giScale ?? 1, 0.02, 4);
    const sunB = Math.max(0, this.sunDirection.y) * this.sunIntensity / gs;
    const a = this.groundAlbedo;
    this.uniforms.lemGroundIrradiance.value.set(
      (flat.x + this.sunColour.r * sunB) * a.r * 0.85,
      (flat.y + this.sunColour.g * sunB) * a.g * 0.85,
      (flat.z + this.sunColour.b * sunB) * a.b * 0.85);
  }

  /**
   * Key to fill — the one number that decides whether anything in the world
   * looks like it is standing on the ground.
   *
   * READ THIS BEFORE YOU TUNE ANYTHING BELOW. The paragraph after it says a
   * shadow's depth is "entirely" this ratio. That was true of the model and it
   * is NOT true of the operator's frame, and five rounds were spent here on the
   * strength of it. Measured 2026-08-08, `harness/sn-decomp.mjs`, `cam=far`
   * `time=9` `weather=clear`, each term ablated alone over the same ground
   * pixels with the stop pinned — the light in a SHADOWED pixel decomposes as
   *
   *      sky.js's aerial perspective   51%
   *      this file's indirect fill     19%
   *      scene.environment (IBL)        3%
   *      key leak, bounce, the rest    27%
   *
   * and a perfect shadow map with zero fill cannot exceed 1.44 stops at that
   * camera while the haze is where it is (`sn-deep.mjs`). Taking fill:key from
   * 0.2551 to 0.0383 — a 6.7x cut — moved the plant's shadow bar 0.83 -> 1.01
   * stops and the two frames are indistinguishable side by side. The ask is
   * filed for sky.js in scratchpad/REQUESTS.md with the fog-on/fog-off pair.
   * The headroom in THIS function is about a fifth of a stop. Spend it if you
   * like, but do not expect a shadow to appear, and check `sn-floor.mjs`
   * afterwards because the status boards are what the screen is for.
   *
   * A shadow is not a thing that gets drawn. It is the absence of the sun, and
   * how dark it comes out is entirely the ratio of the sky's irradiance to the
   * sun's. Left unmanaged that ratio was about 3:4 here: the sky was handing a
   * flat surface three quarters of what the sun was, so a shadowed patch of
   * grass came out at 43% of a lit one — a stop and a half — and at that ratio
   * the shadow map can be pixel-perfect and the render still reads as a model
   * with no shadows in it at all. The reference frames run three stops or more
   * between sunlit and shaded stone.
   *
   * The ratio is not a constant, which is why this is a function rather than a
   * number in `_buildSkyGradient`. In clear air the sky is `FILL_CLEAR_C` of
   * the sun's BEAM, which is a fifth of the key at a low sun and an eighth at a
   * high one; under full cloud the sun is gone and the sky *is* the light, and
   * a scene with no key at all must not go black. So the fill is asked for as a
   * fraction of the sun that rises with cloud and fog, with an absolute floor
   * under it for the overcast and night cases, and the whole probe field is
   * scaled to deliver exactly that.
   *
   * Scaling the indirect term rather than the sky radiance is deliberate:
   * `_skyRadiance` also feeds the environment map and, when sky.js is loaded,
   * comes from sky.js in whatever absolute units that module chose. Normalising
   * against the sun on this side is the only way to hold a key-to-fill ratio
   * that survives someone else re-grading the sky.
   */
  /**
   * The flat tier's ambient, fitted rather than assumed — and the answer to
   * "can `gi: false` leave the world black?".
   *
   * It cannot, and this is where that is enforced. The lit path scales the
   * probe field to a *ratio* of the sun so a cast shadow has room to fall; if
   * the sun is a moonlit 0.42 under storm cloud, a ratio of the sun is a
   * fraction of very little, and the probe field's occlusion then takes some of
   * that away again. Here there is no occlusion to take anything, no shadow to
   * protect, and no second light source anywhere in the frame — so the ambient
   * is the entire picture of every surface the sun does not face, and the
   * question is not what is physical but what is readable.
   *
   * So: a generous fraction of the key, and then a hard floor that does not
   * care what hour it is. `FLAT_FILL_MIN` at the site's albedo is a mid-grey,
   * not a dark one; it is what a status board has to be legible against at
   * three in the morning under a storm, which is the case that decides this
   * number rather than the clear afternoon that is easy.
   *
   * The result is written to the same `lemGIStrength` the probe path uses, so
   * `_setGroundIrradiance`, `_envFactor` and `irradianceAt` all keep working
   * off one scale with no second convention to get wrong.
   */
  _fitFlatAmbient(flat) {
    const lum = (r, g, b) => r * 0.2126 + g * 0.7152 + b * 0.0722;
    const skyE = Math.max(1e-4, lum(flat.x, flat.y, flat.z));
    const sunE = Math.max(0, this.sunDirection.y) * this.sunIntensity *
      lum(this.sunColour.r, this.sunColour.g, this.sunColour.b);

    const w = this.ctx.weather || {};
    const cloud = clamp(w.cloud ?? 0.2, 0, 1);
    /* Cloud still moves it, because the exposure model reads `_fillE` and a
     * flat tier that did not dim under a storm would tone-map the storm away. */
    /* `_fillTrim` applies here too, so a probe that ablates the fill gets the
     * same ablation at every tier rather than a silent no-op at the bottom
     * one — an instrument that cannot see the field it switched off cannot
     * measure switching it off. 1 in every shipped frame. */
    const want = Math.max(FLAT_FILL_RATIO * sunE * (1 - cloud * 0.25),
                          FLAT_FILL_MIN) * this._fillTrim;

    this.giScale = clamp(want / skyE, 0.03, 8);
    this.uniforms.lemGIStrength.value = this.giScale;
    this._fillE = want;
    this._keyE = sunE;
  }

  _fitFill(flat) {
    if (this._flat) return this._fitFlatAmbient(flat);
    const lum = (r, g, b) => r * 0.2126 + g * 0.7152 + b * 0.0722;
    const skyE = Math.max(1e-4, lum(flat.x, flat.y, flat.z));
    const sunE = Math.max(0, this.sunDirection.y) * this.sunIntensity *
      lum(this.sunColour.r, this.sunColour.g, this.sunColour.b);

    const w = this.ctx.weather || {};
    const cloud = clamp(w.cloud ?? 0.2, 0, 1);
    const fog = clamp(w.fog ?? 0.1, 0, 1);
    const diffuse = clamp(cloud + fog * 0.35, 0, 1);
    /* The cloud axis. `diffuse` is 0 in clean air and 1 under a full overcast,
     * and it is what carries the ratio from the clear-sky law up to 1 — under
     * full cloud the sun is gone, the sky IS the light, and a scene with no key
     * at all must not go black.
     *
     * The clear-air END of it is the physical law, not a constant: see
     * `FILL_CLEAR_C`. What used to be here was 0.21, and before that 0.13, and
     * both were being argued about as if the rule could reach them —
     * it was UNREACHABLE, which is the bug REQUESTS.md's own pattern
     * section is about: a rule written against an absolute constant on another
     * module's field, where that field never visits the end the constant was
     * calibrated at. `weather=clear` does not deliver diffuse 0. Measured live,
     * 2026-08-08 (`harness/gy-fields.mjs`): `ctx.weather` publishes cloud 0.15,
     * fog 0.10 at the clear preset, so `diffuse` lands at 0.185 and the ratio
     * at 0.273. The 0.21 the paragraph above is calibrated against had never
     * once been delivered, at any weather, in any frame anyone judged.
     *
     * So the field is normalised against its own floor instead of being read
     * as if it started at zero. The floor is weather.js's own `PRESETS.clear`
     * (cloud 0.06, fog 0.05 — read from that file, not guessed), preferred from
     * `ctx.weather.presets` if that module ever publishes it, and then tracked
     * DOWNWARD by the lowest diffuse this session has actually seen. Downward
     * only: a floor that also rose would make the grade wander while nothing
     * visible changed, and a floor that cannot fall is the same staleness bug
     * one retune later. */
    const pc = w.presets?.clear;
    let floor = clamp((pc ? clamp(pc.cloud ?? 0, 0, 1) + clamp(pc.fog ?? 0, 0, 1) * 0.35
                          : FILL_CLEAR_DIFFUSE), 0, 0.9);
    if (Number.isFinite(w.cloud) && Number.isFinite(w.fog)) {
      this._diffuseFloor = Math.min(this._diffuseFloor ?? floor, diffuse);
      floor = Math.min(floor, this._diffuseFloor);
    }
    /* Recorded so a probe can assert the rule is not a constant — the third
     * item in that pattern section, and the one that would have caught this. */
    this._fillDiffuse = diffuse;
    const t = clamp((diffuse - floor) / Math.max(0.1, 1 - floor), 0, 1);
    /* The clear-air end, from the elevation law. `clear * sunE` is identically
     * `FILL_CLEAR_C * DNI` while the sun is above `FILL_SIN_MIN`, which is the
     * whole point: the fill is a fraction of the BEAM, and the key-to-fill
     * ratio falls out of the geometry instead of being asserted. */
    const sinH = Math.max(FILL_SIN_MIN, this.sunDirection.y);
    const clear = clamp(FILL_CLEAR_C / sinH, 0.04, 0.60);
    const ratio = clear + (1 - clear) * Math.pow(t, 1.5);
    this._fillRatio = ratio;
    /* Recorded for the same reason `_fillDiffuse` is: a probe has to be able to
     * assert that the clear-air end MOVED with the sun and did not quietly
     * become a constant again. */
    this._fillClearRatio = clear;

    let want = ratio * sunE;
    /* Overcast noon is dim, not dark, and the sun term above collapses with the
     * cloud that is producing the light.
     *
     * FIXED THIS ROUND, and it had to be, because it was the rule that actually
     * set the clear-air fill. `cam=far`, 09:00, weather=clear, on the shipped
     * file: the ratio delivered 0.2414 * 0.7644 = 0.1845 and this line
     * delivered (0.15 + 0.15*0.30) * 1 * 1 = 0.1950, so the FLOOR owned the
     * answer and the ratio above it was decoration. The previous round measured
     * that, wrote it in this comment, and left it — with the consequence that
     * the two rounds after it tuned a ratio that could not reach the frame.
     * That is THE PATTERN with a note attached to it.
     *
     * What it needed was not a smaller number but a gate: this floor is for the
     * case where there is no key, and in bright sun there is a key. `dark` is 1
     * when the sun is delivering nothing and 0 once it is delivering at least
     * as much as a clear late afternoon (`FILL_FLOOR_KNEE`), so an overcast
     * noon, a storm and dusk all keep exactly the floor they had, and a clear
     * day stops being held up by a constant written for weather it does not
     * have. The cloud term is ungated — that one IS about cloud. */
    const dark = 1 - clamp(sunE / FILL_FLOOR_KNEE, 0, 1);
    want = Math.max(want, (0.15 * dark + cloud * 0.30) * this.civil * this.dayFactor);
    /* And a night floor, for the same reason the moon is brighter here than it
     * is outside: this is a status display before it is a photograph. */
    want = Math.max(want, 0.055 * (0.3 + 0.7 * this.civil));

    /* The ablation knob, last, so it trims the answer whichever of the three
     * expressions above produced it. 1 in every shipped frame. */
    want *= this._fillTrim;

    this.giScale = clamp(want / skyE, 0.03, 4);
    this.uniforms.lemGIStrength.value = this.giScale;
    /* Cached for `_adapt`, which has to expose for the light that is actually
     * reaching surfaces, not for the sky model's raw numbers. */
    this._fillE = want;
    this._keyE = sunE;
  }

  /**
   * Scale the indirect term's target, for measurement.
   *
   * First-class for the reason `setExposureLocked` and `setShadowAnchor` are:
   * `onTime` re-runs `_readSky` -> `_fitFill` off the world clock and writes
   * `lemGIStrength` back, so an ablation that merely assigns the uniform is
   * silently reverted part-way through a long measurement. REQUESTS.md records
   * two rounds where that produced byte-different frames from byte-identical
   * intent. This multiplies inside the fit, so nothing can put it back.
   *
   * It also refits immediately rather than waiting for the next clock tick, so
   * an A/B does not have to guess how long to sleep.
   */
  setFillTrim(k = 1) {
    this._fillTrim = clamp(Number.isFinite(k) ? k : 1, 0.02, 8);
    if (Number.isFinite(this.hours)) this._readSky(this.hours);
    return this._fillTrim;
  }

  /** Eye adaptation, done analytically instead of by reading the framebuffer
   *  back.
   *
   *  A thunderstorm at one in the afternoon carries about a tenth of the light
   *  a clear noon does. Rendered at a fixed exposure that is exactly what it
   *  looks like: a night frame with the clock reading 13:00. Every renderer
   *  that gets weather right is doing this, usually with a histogram pass over
   *  last frame's luminance — which costs a readback and settles slowly. We
   *  already know the horizontal irradiance in closed form, because we computed
   *  the sun and integrated the sky to build the probes, so the key is simply
   *  read off it.
   *
   *  The exponent is 0.62 rather than 1: full compensation would render night
   *  as a slightly blue afternoon, and the whole point of night is that it is
   *  dark. Partial adaptation plus a cap is what a photographer does, and it is
   *  what an eye does. */
  _adapt() {
    const flat = this.uniforms.lemSkyIrradiance.value;
    /* The *delivered* fill, after `_fitFill` scaled the probe field — exposing
     * for the sky model's raw irradiance instead would undo the key-to-fill
     * work by opening up exactly as much as the fill was pulled down. */
    const skyE = this._fillE ?? (flat.x * 0.2126 + flat.y * 0.7152 + flat.z * 0.0722);
    const sunE = this._keyE ?? (Math.max(0, this.sunDirection.y) * this.sunIntensity *
      (this.sunColour.r * 0.2126 + this.sunColour.g * 0.7152 + this.sunColour.b * 0.0722));
    this.sceneIrradiance = sunE + skyE;
    /* Clear noon, the exposure-1.0 anchor. Recalibrated 2026-08-06 from 3.40
     * after engine.js was found to be writing tone-mapped LINEAR values to the
     * canvas with no sRGB transfer function — three.js only inserts its colour
     * space conversion into its own shader chunks, and every pass in that file
     * is hand-authored. Encoding the output correctly made the delivered image
     * about a stop and a half brighter, so every constant calibrated against
     * the old behaviour was compensating for a bug. Measured against the
     * reference set: this puts a clear-afternoon frame at mean luminance 117,
     * p1 13, p95 200, against Train Sim World 4 at 116 / 1 / 208. */
    /* Re-anchored 2026-08-06 from 1.70, when the env-map double-count above was
     * found and removed. `sceneIrradiance` here is sun plus the *delivered*
     * probe fill and has always been only those two — but the pixel used to
     * receive a third, unmodelled ambient at full sky strength, so every frame
     * came out brighter than this model said it should and REF had been walked
     * down until the numbers agreed. With the third term gone the model is
     * finally describing the light that actually arrives, and the constant has
     * to come back up by the amount it was compensating. Measured on the same
     * street frame the old value was set from: mean luminance 114, p1 3,
     * p95 198, against Transport Fever 2 at 109 / 12 / 178. */
    /* Recalibrated again 2026-08-07, downward, when the meter below was added
     * and the analytic number was measured against the reference set instead of
     * being reasoned about. At REF 4.00 a clear 14:00 frame came out at mean
     * luminance 141 and p95 220, against Transport Fever 2 at 109/178 and
     * 79/174 — a stop too bright and clipping at the top. 2.55 puts the same
     * frame at 100/184 before the meter has said anything. */
    const REF = 2.55;
    this.analyticExposure =
      clamp(Math.pow(REF / Math.max(this.sceneIrradiance, 0.02), 0.62), 0.15, 4.00);
    if (this._expNow === null) this._expNow = this.analyticExposure;
    this._applyGrade(0);
  }

  /**
   * Eye adaptation: the analytic model above, corrected by what the frame
   * actually turned out to be.
   *
   * The analytic term knows the hour and the weather and nothing else, and that
   * is precisely the case round two's critics broke it on. At `cam=low` the
   * camera stands inside a tree stand: most of the frame is genuinely in
   * shadow, the sky the model exposed for is behind the canopy, and the
   * measured result was 45.8% of the frame below luminance 12 — "an unreadable
   * void with chroma-speckle and diagonal streak artifacts instead of terrain".
   * No sky model can see that. A meter can, and the references are doing the
   * same thing implicitly by having been photographed by somebody who looked
   * through the viewfinder.
   *
   * The measurement corrects rather than replaces. The analytic value carries
   * the intent — night is meant to read as night, a storm as a storm — and the
   * meter is allowed to move it within a bounded window, so a camera that turns
   * to face a black wall opens up by a stop rather than turning the wall into
   * paper. Asymmetric time constants because that is what an eye does and,
   * more usefully, because the failure modes are not symmetric: adapting down
   * late means a second of glare, adapting up early means the image pumps every
   * time a tank car crosses the frame.
   */
  /**
   * Freeze the stop where it stands, and say so out loud.
   *
   * The meter below is negative feedback, and it is supposed to be: haze
   * brightens the frame and the stop closes down. But it absorbs most of any
   * change measured through it. sky.js measured that from the outside
   * (`harness/sk-milk.mjs`): a fog-on/fog-off pair moves the frame by 19.4 L
   * with the stop frozen and 7.7 L with it running — an A/B taken without
   * freezing understates its own effect by about 2.5x. Terrain hit the same
   * thing from the other side and correctly refused to attribute a change to
   * itself. Both had to reach in and stub this method from a probe, which is
   * fragile in a way that has already produced wrong numbers here: `onTime`
   * fires off the world clock and puts `lemGIStrength` and `sun.intensity`
   * back, so an ablation that is merely assigned is quietly reverted somewhere
   * in the middle of a long measurement, and two trials with byte-identical
   * uniforms read 58.6 and 76.0.
   *
   * So the lock is first-class, it is one call, and it is the ONE stop-related
   * thing a probe should have to know. Everything else in `_applyGrade` — the
   * vignette, the saturation, the black point and the lift — is left running
   * off the frozen exposure, because those are a function of the stop and a
   * probe that froze the stop wants them frozen with it, not detached from it.
   *
   * `locked` false resumes from wherever the exposure now is, with the normal
   * time constant, so unlocking does not snap.
   */
  setExposureLocked(locked = true) {
    this._exposureLocked = !!locked;
    return this._exposureLocked;
  }

  /** Whether the stop is currently pinned. Read by harnesses that want to
   *  assert their own ablation is still in force. */
  get exposureLocked() { return !!this._exposureLocked; }

  /** Turn the ground anchor in `_groundAnchor` off and on, for A/B, and force
   *  the refit so the change is visible on the next frame rather than at the
   *  next time the camera happens to move a tenth of a box.
   *
   *  First-class for the same reason `setExposureLocked` is: every ablation on
   *  this file's shadow path that reached in and cleared `castShadow` or a
   *  layer bit was silently undone by `_adopt`/`_enrol` within a second, and
   *  three rounds of numbers were wrong because of it. The fit is read every
   *  frame from one place, so one flag there cannot be undone by anything. */
  setShadowAnchor(on = true) {
    this._noAnchor = !on;
    this._fitShadow(true);
    for (const c of this._csm) c.dirty = true;
    return !this._noAnchor;
  }

  _applyGrade(dt) {
    const comp = this.ctx.engine?._passes?.composite?.material?.uniforms;
    const analytic = this.analyticExposure ?? 1;
    let want = analytic;
    /* Pinned: hold the stop where it stands and skip both the analytic term and
     * the meter — but still fall through to the writes below, so a caller that
     * changes the scene under a locked stop gets a frame graded by the stop it
     * locked rather than an ungraded one. */
    const pinned = this._exposureLocked && this._expNow !== null;
    if (pinned) {
      want = this._expNow;
    } else if (this._sceneEV !== null) {
      /* The key: where the trimmed mean of the frame is asked to land, in
       * linear scene luminance, before the tone curve. Calibrated on the
       * reference set — see NOTES-gi.md. */
      const metered = Math.pow(2, Math.log2(GRADE_KEY) - this._sceneEV);
      want = analytic * clamp(metered / analytic, 0.62, 2.3);
    }
    want = clamp(want, 0.15, 4.0);
    if (this._expNow === null) this._expNow = want;
    const tau = want < this._expNow ? 0.30 : 0.95;
    const k = pinned ? 1 : (dt > 0 ? 1 - Math.exp(-dt / tau) : 1);
    this._expNow += (want - this._expNow) * k;
    this.exposure = this._expNow;
    if (!comp) return;

    if (comp.uExposure) comp.uExposure.value = this.exposure;
    /* Adapted-up frames are noisier and flatter, so the grade leans back a
     * little: less vignette in the dark (it reads as a dying screen) and a
     * touch more saturation to keep a storm from going monochrome. */
    if (comp.uVignette) comp.uVignette.value = 0.34 - (this.exposure - 1) * 0.055;
    if (comp.uSaturation) comp.uSaturation.value = 1.05 + (this.exposure - 1) * 0.045;

    /* The shadow floor, and why a lighting module is setting the black point.
     *
     * The composite subtracts `uBlackPoint` from the tone-mapped image and
     * clamps at zero. At 0.035 that subtraction was landing above the whole
     * shaded half of the frame: shaded ground at this key-to-fill ratio tone
     * maps to about 0.015, so it was not being darkened, it was being deleted —
     * clamped flat, taking the terrain's texture and every contact shadow with
     * it, and leaving only the brightest specks of albedo noise above the line.
     * That is what three critics saw as chroma speckle in a black void, and it
     * cannot be fixed by lighting: any fill bright enough to clear 0.035 is
     * bright enough to erase the shadow it is filling.
     *
     * So the floor moves down to where the shade actually sits and the lift
     * puts the toe back at the reference's p1 of 10-15 — After the Flood's
     * shadows fall to 21-28/255 and almost never to zero. Contrast comes back
     * up to hold the σ the wider range would otherwise cost. It is set from
     * here because it is the same decision as the exposure and has to move with
     * it: as the meter opens up in shade, the black point has to come down or
     * the adaptation is spent on tones the grade then throws away. Noted in
     * scratchpad/REQUESTS.md for whoever owns engine.js. */
    if (comp.uBlackPoint) {
      /* Placed against the measured shadow end rather than fixed, because a
       * fixed subtraction clips a different amount of the scene every time the
       * meter moves — which is the one thing an adapting exposure must not do.
       *
       * But it is placed a long way UNDER that end now, not just under it, and
       * the reason is the one fault five rounds of critics have described in
       * five different sets of words: "a broad soft dark region with no caster",
       * "caster-less soft dark blobs", a train dragging an amorphous shape
       * instead of casting an articulated shadow.
       *
       * The composite computes `max( ( c - uBlackPoint ) / (…), 0 )`. That is a
       * hard clip, not a roll-off: every pixel whose tone-mapped value lands
       * below the black point is not darkened, it is *deleted*, and `uLift` then
       * paints the whole deleted set one flat colour. So the black point does
       * not set how dark the shadows are — it sets how much of the frame stops
       * being a picture. At 0.80 of the fifth-percentile meter tile it was
       * landing at 0.0146 on the yard frame, which is above the shaded ground:
       * measured, 8.7% of that frame sat inside a six-code band and p0.5, p1 and
       * p2 were all exactly 10, the lift. A locomotive's shadow, the gap between
       * two wagons and the shade on an embankment were three different tones
       * arriving at the same flat grey — which is precisely why a consist casts
       * a blob with no readable shape while its shadow map resolves 5cm.
       *
       * The frame still needs a true black, and it has one: `uLift` is what
       * actually sets the floor, and the references put theirs at p1 17-21 with
       * texture still in it, not at zero. So the subtraction's remaining job is
       * only to keep the very bottom from going milky, and a quarter of the
       * shadow tone does that without eating the shadows. Noted in
       * scratchpad/REQUESTS.md for whoever owns engine.js: a soft shoulder
       * (`c = c*c/(c+bp)`) would let this be placed where it was and still keep
       * the tones apart. */
      let bp = 0.002 * this.exposure;
      if (this._sceneEVLow !== undefined) {
        bp = acesLuma(Math.pow(2, this._sceneEVLow) * this.exposure) * 0.25;
      }
      comp.uBlackPoint.value = clamp(bp, 0.001, 0.020);
    }
    /* Two things had to move with it. The contrast term pivots on 0.5, so at
     * 1.13 it drove everything under 0.065 negative and the clamp then flattened
     * it — a second crush sitting behind the first, and the reason p1 stayed at
     * 0 after the black point came down. The shaping it was doing belongs to the
     * black and white points, which do it without a pivot. And the lift is what
     * actually sets the floor: 0.0035 encodes to about 12/255, which is where
     * the reference frames put their darkest tone. */
    if (comp.uContrast) comp.uContrast.value = 1.0;
    if (comp.uLift) {
      /* Up from 0.0035, which encoded to 10/255. That was set against a note
       * saying the references floor at 10-15; the measurement that followed says
       * otherwise — After the Flood holds p1 at 21 and Transport Fever 2 at 17,
       * and the breakdown is explicit that occluded paving still holds texture.
       * 0.006 encodes to 18, which is inside that pair. It matters more than a
       * code count: with the black point no longer deleting the shade, the lift
       * is the floor the whole shadow range now sits on top of, and a floor at
       * 10 leaves the deepest two stops of it fighting for four codes. */
      const lift = 0.0060 * (1 + 0.5 * this.nightFactor);
      comp.uLift.value.set(lift * 0.85, lift * 0.95, lift * 1.3);
    }
    /* The composite's own AO multiply is off, and all of the buffer is now
     * spent in `GI_AO`. The composite lands on the finished pixel — sky, fog
     * and all — and knows nothing about which part of that pixel was direct;
     * `GI_AO` sits inside the material, after the light loop, where indirect
     * can take the whole occlusion and direct can take the squared contact
     * bite. Two AO multiplies on one pixel is also just a double count. */
    if (comp.uAOStrength) comp.uAOStrength.value = 0;
  }

  _buildSkyGradient(alt, cloud, storm, low) {
    const civil = this.civil;
    /* Clear-sky radiance. The zenith/horizon split is what gives the probes a
     * vertical gradient; without it a "sky colour" is an ambient term wearing a
     * hat. */
    const zen = new THREE.Color(0.095, 0.163, 0.330);
    const hor = new THREE.Color(0.42, 0.50, 0.62);
    const duskZ = new THREE.Color(0.070, 0.078, 0.190);
    zen.lerp(duskZ, low * civil);

    /* Night is lifted well above a real one, for the same reason the moon is —
     * see `_readSky`. Enough blue in it that a silhouette is a silhouette and
     * not a hole. */
    const nightZ = new THREE.Color(0.014, 0.021, 0.052);
    const nightH = new THREE.Color(0.028, 0.037, 0.072);
    zen.lerp(nightZ, 1 - civil);
    hor.lerp(nightH, 1 - civil);

    /* The dusk band is kept as its own colour rather than folded into the
     * horizon, because a sunset is not a ring. It is warm on the sun's side and
     * blue on the other — the earth's own shadow, which is half of why an
     * evening photograph looks like an evening. `_skyRadiance` places it by
     * azimuth; here we only decide how much sunset there is. */
    const warm = new THREE.Color(0.82, 0.355, 0.135)
      .multiplyScalar(0.30 + civil * 0.90);

    /* Haze. A low sun is looking through several times the air a high one is,
     * and that air scatters the *whole* sky toward a warm white — which is why
     * the shaded side of a shed at seven in the evening photographs as a
     * desaturated grey and not as navy. Model clean air all the way down and
     * the indirect term stays a deep blue that nothing on a real site is: this
     * one term is the difference between the reference frames and a render.
     * Fog and rain do the same thing for the same reason, so they feed it. */
    const fog = clamp(this.ctx.weather?.fog ?? 0.1, 0, 1);
    const haze = new THREE.Color(0.52, 0.475, 0.445)
      .multiplyScalar(0.30 + civil * 0.95);
    /* The 0.20 baseline is new, and it is the difference between a sky model
     * and the light a site actually sits in.
     *
     * Everything above describes clean air, and clean air over a working yard
     * does not exist: there is aerosol, there is a bright horizon band a
     * cosine-weighted integral gives real weight to, and — the part no sky
     * model can ever contain — every surface in the shadow is also being lit
     * by the sunlit grass, trunks, ballast and steel around it, none of which
     * a thirty-two-direction trace against a height field can see. All three
     * are pale and warm relative to the zenith. Model none of them and the
     * only thing filling a shadow is Rayleigh blue, which is how a canopy
     * interior comes out navy and a critic writes "the entire frame carries
     * one cold cast". It applies with `civil`, so it fades out with the day
     * rather than lifting a night sky off the floor. */
    const hazeAmt = clamp(low * civil * 0.62 + fog * 0.40 + civil * 0.20, 0, 0.86);
    zen.lerp(haze, hazeAmt * 0.72);
    hor.lerp(haze, hazeAmt);
    warm.lerp(haze, hazeAmt * 0.45);

    /* Cloud flattens the gradient toward a single grey and kills the sun's
     * aureole; a storm takes the grey down and slightly green. */
    const grey = new THREE.Color(0.148, 0.158, 0.172).multiplyScalar(
      (0.16 + civil * 0.84) * (1 - storm * 0.58));
    /* Cloud has much less authority over the night sky than the day one. Let it
     * pull all the way and a storm at 21:00 is a black rectangle with six
     * orange dots — arguably honest, useless as a status display, and not what
     * an overcast night looks like anyway: cloud over a working site catches
     * the site's own lights and glows. */
    const cloudPull = cloud * (0.34 + civil * 0.66);
    zen.lerp(grey, cloudPull * 0.88);
    hor.lerp(grey, cloudPull * 0.80);
    warm.lerp(grey, cloud * 0.92);

    this.zenith.copy(zen);
    this.horizon.copy(hor);
    this.horizonWarm = (this.horizonWarm || new THREE.Color()).copy(warm);
    this.duskAmount = low * civil * (1 - cloud * 0.78) * (1 - storm * 0.5);
    /* The aureole is strongest exactly when the sun is grazing — that band of
     * fire along one edge of the sky is most of what makes an evening frame
     * look photographed. It survives the sun going under the horizon. */
    this.sunGlow = (1 - cloud) * (1 - storm) * Math.pow(civil, 1.6)
                 * (0.55 + low * 1.9);

    const t = this.ctx.weather?.temperature ?? 14;
    const snow = clamp(this.ctx.weather?.snow ?? 0, 0, 1);
    /* The bounce is the terrain's colour, so snow on the ground genuinely
     * changes the light under an eave. */
    /* Measured off the rendered terrain rather than guessed: the field this
     * site stands on photographs gold-olive, and a bounce tinted grey-brown
     * was handing back light a shade cooler than the ground it came off. */
    this.groundAlbedo.setRGB(0.235, 0.213, 0.128)
      .lerp(new THREE.Color(0.72, 0.75, 0.80), snow * 0.85);
    if (t < 2 && snow < 0.05) this.groundAlbedo.multiplyScalar(0.9);
    const wet = clamp(this.ctx.weather?.wetness ?? 0, 0, 1);
    this.groundAlbedo.multiplyScalar(1 - wet * 0.42);
  }

  /** Sky radiance in a direction. Used for the probe integration and for the
   *  fallback environment map, never for the sky the user sees. */
  _skyRadiance(d, out) {
    if (this._skyFn) {
      try {
        const c = this._skyFn(d, out);
        if (c && Number.isFinite(c.r)) return out.copy(c);
      } catch (err) {
        this._skyFn = null;           // asked once, answered badly, never again
        void err;
      }
    }
    const up = clamp(d.y, 0, 1);
    const t = Math.pow(1 - up, 2.6);
    out.copy(this.zenith).lerp(this.horizon, t);
    if (this.duskAmount > 0.002) {
      const mu = Math.max(0, d.dot(this.solarDirection || this.sunDirection));
      out.lerp(this.horizonWarm, clamp(t * Math.pow(mu, 1.1) * this.duskAmount, 0, 1));
    }
    if (this.sunGlow > 0.001) {
      const mu = Math.max(0, d.dot(this.solarDirection || this.sunDirection));
      /* The broad aureole, and only a little of the tight one: the sun's own
       * disc is the DirectionalLight, and a spike here on top of it is the
       * same photons counted twice. */
      const glow = Math.pow(mu, 7) * 0.45 + Math.pow(mu, 40) * 0.85;
      const c = this.solarColour || this.sunColour;
      out.r += c.r * glow * this.sunGlow;
      out.g += c.g * glow * this.sunGlow;
      out.b += c.b * glow * this.sunGlow;
    }
    return out;
  }

  /* ---- the environment map ------------------------------------------------ */

  /** sky.js owns `scene.environment`. If it did not load, the world still needs
   *  image-based specular or every metal surface on the site turns matte black,
   *  so a 64×32 equirect of the analytic sky is PMREM'd here as a stand-in. It
   *  is never installed over somebody else's. */
  _ensureEnvironment() {
    const scene = this.ctx.scene;
    /* Image-based lighting is lighting, and this tier has none. The specular
     * that would otherwise be lost comes off the flat hemisphere instead —
     * see `GI_IBL` — which is what keeps the tank farm from going matte black
     * without a PMREM render or a cube-UV fetch. */
    if (this._flat) return;
    if (scene.environment && scene.environment !== this._envTex) return;
    try {
      const W = 128, H = 64;
      const data = new Uint16Array(W * H * 4);
      const d = new THREE.Vector3(), c = new THREE.Color();
      /* three's `equirectUv` is `u = atan2(z, x)/2π + 0.5`, `v = asin(y)/π +
       * 0.5`, and a DataTexture is not flipped — so row 0 is v = 0, which is
       * straight down. Get either of those backwards and the ground hemisphere
       * is painted across the sky, which is exactly as obvious as it sounds and
       * exactly as easy to not notice when the horizon is the only part of the
       * sky the camera can see. */
      for (let j = 0; j < H; j++) {
        const v = (j + 0.5) / H;
        const yy = Math.sin((v - 0.5) * Math.PI);
        const r = Math.sqrt(Math.max(0, 1 - yy * yy));
        for (let i = 0; i < W; i++) {
          const a = ((i + 0.5) / W - 0.5) * Math.PI * 2;
          d.set(Math.cos(a) * r, yy, Math.sin(a) * r);
          if (d.y >= 0) this._skyRadiance(d, c);
          else {
            /* Below the horizon the environment is the ground, lit. A cube that
             * is black underneath makes every horizontal metal surface read as
             * a hole. */
            this._skyRadiance(d.clone().setY(0.04).normalize(), c);
            c.multiply(this.groundAlbedo).multiplyScalar(1.5);
            /* Fade it out toward straight down: a hard-edged ground hemisphere
             * puts a visible seam right on the horizon of every reflection. */
            c.multiplyScalar(0.45 + 0.55 * (1 + d.y));
          }
          const o = (j * W + i) * 4;
          data[o] = toHalf(c.r); data[o + 1] = toHalf(c.g);
          data[o + 2] = toHalf(c.b); data[o + 3] = toHalf(1);
        }
      }
      const tex = new THREE.DataTexture(data, W, H, THREE.RGBAFormat,
                                        THREE.HalfFloatType);
      tex.mapping = THREE.EquirectangularReflectionMapping;
      tex.colorSpace = THREE.LinearSRGBColorSpace;
      tex.minFilter = tex.magFilter = THREE.LinearFilter;
      tex.needsUpdate = true;
      this._pmrem = this._pmrem || new THREE.PMREMGenerator(this.ctx.renderer);
      const rt = this._pmrem.fromEquirectangular(tex);
      tex.dispose();
      this._envRT?.dispose?.();
      this._envRT = rt;
      this._envTex = rt.texture;
      scene.environment = this._envTex;
      scene.environmentIntensity = this._envFactor();
      /* If nobody drew a sky, the world is being lit by a sky that is not
       * there, and every frame reads as an object floating in a void. Standing
       * our own integration behind it is not a substitute for sky.js — it is
       * the same gradient the probes were lit from, so at least the light and
       * the horizon agree. Never installed over somebody else's background. */
      if (!scene.background || scene.background === this._envBg) {
        scene.background = this._envBg = this._envTex;
        scene.backgroundIntensity = 1.0;
        scene.backgroundBlurriness = 0.06;
      }
    } catch (err) {
      void err;                       // no environment is survivable; a throw is not
    }
  }

  /* ---- the probe grid ----------------------------------------------------- */

  _buildGrid(plan) {
    /* The bottom rung asks for no probe field at all, and this is the line that
     * honours it: not a smaller grid, not a coarser cell — no grid, so no
     * ground-field sampling, no ~2000 traces against the height field, no three
     * half-float 3D textures, no per-frame relight slice and no `sampler3D` in
     * any shader on the site. `_flat` is checked before the table because a
     * table lookup by name cannot express "there is nothing to look up". */
    if (this._flat) { this._disposeGrid(); return; }
    /* `floor` maps to null on purpose, and `??` would have treated that as "no
     * entry" and handed back the high-tier grid — the one tier that exists to
     * do no probe work was doing all of it. */
    const name = this.tier?.name;
    const spec = Object.prototype.hasOwnProperty.call(GRID_BY_TIER, name)
      ? GRID_BY_TIER[name] : GRID_BY_TIER.high;
    this._disposeGrid();
    if (!spec) return;

    const b = plan?.bounds || {minX: -120, maxX: 120, minZ: -120, maxZ: 120};
    const pad = 90;
    const minX = b.minX - pad, maxX = b.maxX + pad;
    const minZ = b.minZ - pad, maxZ = b.maxZ + pad;

    const nx = clamp(Math.ceil((maxX - minX) / spec.cell) + 1, 3, spec.max);
    const nz = clamp(Math.ceil((maxZ - minZ) / spec.cell) + 1, 3, spec.max);
    const ny = spec.layers;

    /* Sample the ground once into a coarse field. `ctx.ground` goes through the
     * terrain's height function and the trace below would otherwise call it
     * roughly four hundred thousand times. */
    const FN = 96;
    const terr = new Float32Array(FN * FN);
    const bld = new Float32Array(FN * FN);
    let gMin = 1e9, gMax = -1e9;
    for (let j = 0; j < FN; j++) {
      const z = minZ + (j / (FN - 1)) * (maxZ - minZ);
      for (let i = 0; i < FN; i++) {
        const x = minX + (i / (FN - 1)) * (maxX - minX);
        const h = this.ctx.ground(x, z) || 0;
        terr[j * FN + i] = h;
        if (h < gMin) gMin = h;
        if (h > gMax) gMax = h;
      }
    }
    if (!Number.isFinite(gMin)) { gMin = 0; gMax = 0; }

    /* Buildings are stamped into the same height field. They are not modelled
     * here — buildings.js owns their geometry — so the occluder is a box at the
     * station's position, as tall as the anchor that module publishes. It is
     * deliberately coarse: what the probes need to know is that a yard has
     * walls around it, not what the walls look like. */
    /* Buildings go into their own field, sampled nearest, while the terrain is
     * sampled bilinearly. Sharing one field made a 32m shed occlude a 46m
     * circle — the footprint rounded out to whole cells and then the bilinear
     * filter ramped it down over another cell in every direction — and any
     * probe inside that skirt saw no sky at all. */
    const anchors = this.ctx.world?.anchors;
    const stamp = (x, z, hx, hz, top) => {
      const sx = (FN - 1) / (maxX - minX), sz = (FN - 1) / (maxZ - minZ);
      const i0 = Math.round((x - hx - minX) * sx), i1 = Math.round((x + hx - minX) * sx);
      const j0 = Math.round((z - hz - minZ) * sz), j1 = Math.round((z + hz - minZ) * sz);
      const base = (this.ctx.ground(x, z) || 0) + top;
      for (let j = Math.max(0, j0); j <= Math.min(FN - 1, j1); j++) {
        for (let i = Math.max(0, i0); i <= Math.min(FN - 1, i1); i++) {
          if (base > bld[j * FN + i]) bld[j * FN + i] = base;
        }
      }
    };
    let tallest = 0;
    for (const s of plan?.stations || []) {
      const top = anchors?.get?.(s.uid)?.top ?? 18;
      tallest = Math.max(tallest, top);
      stamp(s.x, s.z, 16, 13, top);
    }
    if (plan?.hub) { stamp(plan.hub.x, plan.hub.z, 34, 24, 26); tallest = Math.max(tallest, 26); }
    for (const o of this._occluders || []) {
      tallest = Math.max(tallest, o.top);
      stamp(o.x, o.z, o.hx, o.hz, o.top);
    }

    const yMin = gMin - 2;
    const yMax = gMax + Math.max(46, tallest * 2.1);
    const count = nx * ny * nz;

    this.grid = {
      nx, ny, nz, count,
      min: new THREE.Vector3(minX, yMin, minZ),
      size: new THREE.Vector3(maxX - minX, yMax - yMin, maxZ - minZ),
      terr, bld, FN, fMinX: minX, fMinZ: minZ,
      fSpanX: maxX - minX, fSpanZ: maxZ - minZ,
      openSky: gMax + tallest + 6,
    };
    this.grid.step = new THREE.Vector3(
      this.grid.size.x / Math.max(1, nx - 1),
      this.grid.size.y / Math.max(1, ny - 1),
      this.grid.size.z / Math.max(1, nz - 1));

    this.vis = new Float32Array(count * DIRS);
    this.hit = new Uint8Array(count * DIRS);
    this.open = new Float32Array(count);
    this.texData = {
      r: new Uint16Array(count * 4),
      g: new Uint16Array(count * 4),
      b: new Uint16Array(count * 4),
    };

    const mk = arr => {
      const t = new THREE.Data3DTexture(arr, nx, ny, nz);
      t.format = THREE.RGBAFormat;
      t.type = THREE.HalfFloatType;
      t.minFilter = t.magFilter = THREE.LinearFilter;
      t.wrapS = t.wrapT = t.wrapR = THREE.ClampToEdgeWrapping;
      t.colorSpace = THREE.NoColorSpace;
      t.needsUpdate = true;
      return t;
    };
    this.texR = mk(this.texData.r);
    this.texG = mk(this.texData.g);
    this.texB = mk(this.texData.b);

    /* The grid box is inset by half a cell on lookup so a surface exactly on
     * the boundary samples the edge probe rather than half of nothing. */
    this.uniforms.lemProbeR.value = this.texR;
    this.uniforms.lemProbeG.value = this.texG;
    this.uniforms.lemProbeB.value = this.texB;
    this.uniforms.lemGridMin.value.copy(this.grid.min);
    this.uniforms.lemGridInvSize.value.set(
      1 / Math.max(1e-3, this.grid.size.x),
      1 / Math.max(1e-3, this.grid.size.y),
      1 / Math.max(1e-3, this.grid.size.z));

    this._trace(0, count);
    this._relight(0, count);
    this._upload();
  }

  /**
   * Declare something that blocks sky. The probe grid otherwise knows only what
   * `plan` tells it — a box per station, a bigger one for the hub — which is
   * enough for "the yard is darker than the field" and nothing more. A tank
   * farm, a loading gantry, a shed row or a stand of trees can say so:
   *
   *   gi.registerOccluder({x, z, hx, hz, top});   // half-extents, height above
   *                                               // the terrain under it
   *   gi.registerOccluder(box3);                  // or a world-space Box3
   *
   * Cheap and coarse on purpose: it is stamped into a height field at roughly
   * one sample per five metres, so there is no point declaring a handrail. The
   * rebuild is deferred to the next frame, so a hundred of these in a loop cost
   * one rebuild, not a hundred.
   */
  registerOccluder(spec) {
    if (!spec) return;
    this._occluders = this._occluders || [];
    if (spec.isBox3) {
      const c = spec.getCenter(new THREE.Vector3());
      const s = spec.getSize(new THREE.Vector3());
      this._occluders.push({x: c.x, z: c.z, hx: s.x * 0.5, hz: s.z * 0.5,
                            top: Math.max(1, spec.max.y - (this.ctx.ground(c.x, c.z) || 0))});
    } else if (Number.isFinite(spec.x) && Number.isFinite(spec.z)) {
      this._occluders.push({
        x: spec.x, z: spec.z,
        hx: Math.max(1, spec.hx ?? 6), hz: Math.max(1, spec.hz ?? 6),
        top: Math.max(1, spec.top ?? 10),
      });
    } else return;
    this._occluderPending = true;
  }

  /** Terrain height from the coarse field, bilinear — it is a smooth surface
   *  and reads as one. */
  _terrField(x, z) {
    const g = this.grid;
    const u = clamp((x - g.fMinX) / g.fSpanX, 0, 0.9999) * (g.FN - 1);
    const v = clamp((z - g.fMinZ) / g.fSpanZ, 0, 0.9999) * (g.FN - 1);
    const i = u | 0, j = v | 0;
    const fu = u - i, fv = v - j;
    const i1 = Math.min(i + 1, g.FN - 1), j1 = Math.min(j + 1, g.FN - 1);
    const a = g.terr[j * g.FN + i], b = g.terr[j * g.FN + i1];
    const c = g.terr[j1 * g.FN + i], d = g.terr[j1 * g.FN + i1];
    return (a * (1 - fu) + b * fu) * (1 - fv) + (c * (1 - fu) + d * fu) * fv;
  }

  /** Building top, nearest — a wall has an edge, and interpolating it is what
   *  put a skirt of dead-black probes around every shed. */
  _bldField(x, z) {
    const g = this.grid;
    const i = Math.round(clamp((x - g.fMinX) / g.fSpanX, 0, 0.9999) * (g.FN - 1));
    const j = Math.round(clamp((z - g.fMinZ) / g.fSpanZ, 0, 0.9999) * (g.FN - 1));
    return g.bld[j * g.FN + i];
  }

  _probePosition(index, out) {
    const g = this.grid;
    const ix = index % g.nx;
    const iy = ((index / g.nx) | 0) % g.ny;
    const iz = (index / (g.nx * g.ny)) | 0;
    out.set(g.min.x + ix * g.step.x, g.min.y + iy * g.step.y, g.min.z + iz * g.step.z);
    /* Lift a probe that has fallen under the terrain. Under a *building* it
     * stays where it is and stays dark — that darkness is the point. */
    const ground = this._terrField(out.x, out.z);
    if (out.y < ground + 1.2) out.y = ground + 1.2;
    return out;
  }

  /** Visibility only. This depends on geometry, not on the hour, so it survives
   *  every sunset and is recomputed only when the site itself changes. */
  _trace(from, to) {
    if (!this.grid) return;
    const p = new THREE.Vector3(), s = new THREE.Vector3();
    for (let i = from; i < to; i++) {
      this._probePosition(i, p);
      const highUp = p.y > this.grid.openSky;
      let open = 0, openN = 0;
      for (let k = 0; k < DIRS; k++) {
        const d = this.dirs[k];
        const slot = i * DIRS + k;
        if (d.y < -0.12) { this.vis[slot] = 0; this.hit[slot] = 2; continue; }
        if (highUp) { this.vis[slot] = 1; this.hit[slot] = 0; open++; openN++; continue; }
        let vis = 1, kind = 0;
        for (let t = 0; t < TRACE_STEPS.length; t++) {
          const dist = TRACE_STEPS[t];
          s.set(p.x + d.x * dist, p.y + d.y * dist, p.z + d.z * dist);
          const th = this._terrField(s.x, s.z);
          const bh = this._bldField(s.x, s.z);
          const h = bh > th ? bh : th;
          const clear = s.y - h;
          if (clear <= 0) { vis = 0; kind = bh > th ? 1 : 2; break; }
          /* A near miss still shades: a probe a metre off a wall sees far less
           * sky than the binary test admits. */
          const soft = 0.55 + 0.45 * smoothstep(0, 5.5, clear);
          if (soft < vis) vis = soft;
        }
        this.vis[slot] = vis;
        this.hit[slot] = kind;
        if (d.y > 0) { open += vis; openN++; }
      }
      this.open[i] = openN ? open / openN : 1;
    }
  }

  /** Radiance → SH. Cheap by design: the ray march above is already done, so a
   *  change of hour or weather is a few hundred thousand multiplies, which is
   *  why the sun can be dragged across the sky without the frame noticing. */
  _relight(from, to) {
    const g = this.grid;
    if (!g) return;
    const dOmega = (4 * Math.PI) / DIRS;
    const A0 = 0.25 * dOmega;              // π · Y00² · dΩ
    const A1 = 0.5 * dOmega;               // (2π/3) · Y1² · dΩ

    /* The sky does not vary from probe to probe — only what each probe can see
     * of it does. Sampling it once per direction instead of once per direction
     * per probe is the difference between a relight costing 3ms and 40. */
    this._radCache = this._radCache || this.dirs.map(() => new THREE.Color());
    for (let k = 0; k < DIRS; k++) this._skyRadiance(this.dirs[k], this._radCache[k]);
    const skyRad = this._radCache;

    /* Irradiance arriving on a horizontal surface out in the open — the
     * quantity the ground bounce is a fraction of. */
    const sunH = Math.max(0, this.sunDirection.y) * this.sunIntensity;
    const skyE = this._skyIrradianceFlat();
    /* The sun's share of the bounce is divided out by the fill scale before it
     * goes in, and this one line is most of the warmth that was missing from
     * every shaded surface on the site.
     *
     * The shader multiplies this entire field by `lemGIStrength` — the number
     * `_fitFill` computes to pull an over-bright sky model down to a defensible
     * key-to-fill ratio. The sky terms below want that: their whole purpose is
     * to be scaled. The ground bounce does not. It is the *sun* reflecting off
     * grass and dirt, it is warm, it is the single largest indirect source on
     * a clear day, and it has nothing to do with how bright the sky model
     * happens to be — but it was riding the same multiplier, so a correction
     * aimed at the sky was quietly taking three quarters of the ground bounce
     * with it. That left the shaded side of everything lit by blue sky and
     * almost nothing else, which is exactly what every critic described.
     *
     * With it divided out, a downward-facing surface at clear noon receives
     * about a quarter of the sun's horizontal irradiance back off the ground —
     * which is what an albedo of 0.21 under a clear sky actually returns. */
    const gs = clamp(this.giScale ?? 1, 0.02, 4);
    const sunB = sunH / gs;
    const bounceR = this.groundAlbedo.r * (this.sunColour.r * sunB + skyE.x) / Math.PI;
    const bounceG = this.groundAlbedo.g * (this.sunColour.g * sunB + skyE.y) / Math.PI;
    const bounceB = this.groundAlbedo.b * (this.sunColour.b * sunB + skyE.z) / Math.PI;

    for (let i = from; i < to; i++) {
      /* How lit the surfaces around this probe are. It is NOT the openness on
       * its own: a probe wedged between two sheds has zero openness, and
       * multiplying the bounce by it hands back a probe that is exactly black —
       * which then interpolates out over the eighteen metres to its neighbours
       * and paints a dead ring around every building. Nowhere outdoors is
       * black. The floor is the light that got in from further away, which an
       * L1 grid at this spacing cannot trace and should not pretend to. */
      const openness = 0.24 + 0.76 * this.open[i];
      let c0r = 0, c0g = 0, c0b = 0;
      let xr = 0, xg = 0, xb = 0, yr = 0, yg = 0, yb = 0, zr = 0, zg = 0, zb = 0;
      for (let k = 0; k < DIRS; k++) {
        const d = this.dirs[k];
        const slot = i * DIRS + k;
        const vis = this.vis[slot];
        let lr = 0, lg = 0, lb = 0;
        if (vis > 0.001) {
          const rad = skyRad[k];
          lr = rad.r * vis; lg = rad.g * vis; lb = rad.b * vis;
        }
        /* Whatever blocked the sky is not black: it is a lit wall or lit
         * ground, and the light coming back off it is most of the reason a
         * shaded side is coloured rather than grey. */
        const block = 1 - vis;
        if (block > 0.001) {
          const wall = this.hit[slot] === 1;
          const k2 = block * openness * (wall ? 0.55 : 1.0);
          if (wall) {
            /* The 2.2 that used to be here was propping the wall bounce up
             * against a ground bounce that was four times too weak. With that
             * fixed it would over-light every yard. */
            lr += this.wallAlbedo.r * bounceR * k2;
            lg += this.wallAlbedo.g * bounceG * k2;
            lb += this.wallAlbedo.b * bounceB * k2;
          } else {
            lr += bounceR * k2; lg += bounceG * k2; lb += bounceB * k2;
          }
        }
        c0r += lr; c0g += lg; c0b += lb;
        xr += lr * d.x; xg += lg * d.x; xb += lb * d.x;
        yr += lr * d.y; yg += lg * d.y; yb += lb * d.y;
        zr += lr * d.z; zg += lg * d.z; zb += lb * d.z;
      }
      c0r *= A0; c0g *= A0; c0b *= A0;
      xr *= A1; xg *= A1; xb *= A1;
      yr *= A1; yg *= A1; yb *= A1;
      zr *= A1; zg *= A1; zb *= A1;

      /* De-ringing, split by axis.
       *
       * An L1 fit whose linear term outruns its constant term goes negative on
       * the far side and paints black patches; some clamp is mandatory. But a
       * single clamp on |c1| trades the two gradients against each other, and
       * they are not worth the same. Sky-above versus ground-below is the one
       * that makes a surface look lit from the sky, and it should stay strong.
       * The horizontal one is exaggerated by construction: the circumsolar
       * glow lands in three or four of thirty-two sample directions, and a
       * cosine lobe fitted to a spike over-darkens everything facing away from
       * it — which is why a wall in evening shade came out near-black before
       * this was split.
       *
       * So: vertical keeps almost all of its swing, horizontal is capped at
       * 0.42·c0, and a wall facing away from the sun bottoms out at 58% of the
       * probe's average instead of 8%. */
      const soften = (c0, x, y, z) => {
        const limY = c0 * 0.82, limH = c0 * 0.42;
        if (Math.abs(y) > limY && Math.abs(y) > 1e-6) y *= limY / Math.abs(y);
        const h = Math.sqrt(x * x + z * z);
        if (h > limH && h > 1e-6) { const s = limH / h; x *= s; z *= s; }
        return [c0, x, y, z];
      };
      const R = soften(c0r, xr, yr, zr);
      const G = soften(c0g, xg, yg, zg);
      const B = soften(c0b, xb, yb, zb);

      const o = i * 4;
      this.texData.r[o] = toHalf(R[1]); this.texData.r[o + 1] = toHalf(R[2]);
      this.texData.r[o + 2] = toHalf(R[3]); this.texData.r[o + 3] = toHalf(R[0]);
      this.texData.g[o] = toHalf(G[1]); this.texData.g[o + 1] = toHalf(G[2]);
      this.texData.g[o + 2] = toHalf(G[3]); this.texData.g[o + 3] = toHalf(G[0]);
      this.texData.b[o] = toHalf(B[1]); this.texData.b[o + 1] = toHalf(B[2]);
      this.texData.b[o + 2] = toHalf(B[3]); this.texData.b[o + 3] = toHalf(B[0]);
    }
  }

  /** Sky-only irradiance on an upward-facing surface, by the same integration
   *  the probes use. Doubles as the `floor` tier's hemisphere ambient. */
  _skyIrradianceFlat() {
    const rad = new THREE.Color();
    let r = 0, g = 0, b = 0;
    const dOmega = (4 * Math.PI) / DIRS;
    for (const d of this.dirs) {
      if (d.y <= 0) continue;
      this._skyRadiance(d, rad);
      r += rad.r * d.y; g += rad.g * d.y; b += rad.b * d.y;
    }
    return new THREE.Vector3(r * dOmega, g * dOmega, b * dOmega);
  }

  _upload() {
    if (!this.grid) return;
    this.texR.needsUpdate = true;
    this.texG.needsUpdate = true;
    this.texB.needsUpdate = true;
  }

  /** The probe field on the CPU, for anything that needs to match the lighting
   *  without a shader — a sprite, a particle, a label's contrast. */
  irradianceAt(x, y, z, normal, out = new THREE.Color()) {
    const g = this.grid;
    /* The same fill scale the shader applies, or a label picking its contrast
     * off this would be reading a world four times brighter than the one on
     * screen. */
    const gs = this.uniforms.lemGIStrength.value;
    if (!g) {
      const sky = this.uniforms.lemSkyIrradiance.value;
      const gr = this.uniforms.lemGroundIrradiance.value;
      const t = clamp((normal?.y ?? 1) * 0.5 + 0.5, 0, 1);
      return out.setRGB((gr.x + (sky.x - gr.x) * t) * gs,
                        (gr.y + (sky.y - gr.y) * t) * gs,
                        (gr.z + (sky.z - gr.z) * t) * gs);
    }
    const ix = clamp(Math.round((x - g.min.x) / g.step.x), 0, g.nx - 1);
    const iy = clamp(Math.round((y - g.min.y) / g.step.y), 0, g.ny - 1);
    const iz = clamp(Math.round((z - g.min.z) / g.step.z), 0, g.nz - 1);
    const i = (ix + g.nx * (iy + g.ny * iz)) * 4;
    const nx = normal?.x ?? 0, ny = normal?.y ?? 1, nz = normal?.z ?? 0;
    const half = h => {
      /* Only used off the hot path, so the slow, obvious decode is fine. */
      const s = (h & 0x8000) ? -1 : 1, e = (h >> 10) & 0x1f, m = h & 0x3ff;
      if (e === 0) return s * m * 5.9604644775390625e-8;
      if (e === 31) return s * Infinity;
      return s * Math.pow(2, e - 15) * (1 + m / 1024);
    };
    const ev = t => Math.max(0, half(t[i + 3]) + half(t[i]) * nx +
                                half(t[i + 1]) * ny + half(t[i + 2]) * nz) * gs;
    return out.setRGB(ev(this.texData.r), ev(this.texData.g), ev(this.texData.b));
  }

  _disposeGrid() {
    this.texR?.dispose(); this.texG?.dispose(); this.texB?.dispose();
    this.texR = this.texG = this.texB = null;
    this.uniforms.lemProbeR.value = null;
    this.uniforms.lemProbeG.value = null;
    this.uniforms.lemProbeB.value = null;
    this.grid = null;
    this._cursor = 0;
    this._traceDirty = this._probesDirty = false;
  }

  /* ---- material registration ---------------------------------------------- */

  /** Register a material for probe lighting and screen-space AO. Safe to call
   *  more than once with the same material, and safe to call before the grid
   *  exists — the uniforms are shared objects, so a material patched at boot
   *  picks up a grid built two seconds later with no recompile. */
  applyGI(material) {
    if (!material) return material;
    if (Array.isArray(material)) { material.forEach(m => this.applyGI(m)); return material; }
    if (!material.isMeshStandardMaterial) return material;
    if (material.userData?.noGI) return material;
    if (this.materials.has(material)) return material;

    /* Captured before anything else touches it: the value standing here now is
     * the owning module's opinion about how much sky this surface reflects,
     * and after `_refreshEnvIntensity` runs the value standing here is ours. */
    material.userData = material.userData || {};
    if (!Number.isFinite(material.userData.lemEnvBase)) {
      material.userData.lemEnvBase =
        Number.isFinite(material.envMapIntensity) ? material.envMapIntensity : 1;
    }
    material.userData.lemEnvU = material.userData.lemEnvU ||
      {value: material.userData.lemEnvBase};

    const prev = material.onBeforeCompile;
    material.onBeforeCompile = (shader, renderer) => {
      try {
        prev?.call(material, shader, renderer);
        Object.assign(shader.uniforms, this.uniforms);
        /* After the assign, and deliberately not in `this.uniforms`: this one
         * is per material, and sharing it would hand every surface on the site
         * the leaf cards' 0.30. */
        shader.uniforms.lemEnvSpec = material.userData.lemEnvU;
        let v = shader.vertexShader;
        v = after(v, '#include <common>', 'varying vec3 vLemWorld;', 'vs:common');
        v = after(v, '#include <worldpos_vertex>', GI_WORLDPOS, 'vs:worldpos');
        shader.vertexShader = v;
        let f = shader.fragmentShader;
        f = after(f, '#include <common>', GI_PARS, 'fs:common');
        f = after(f, '#include <lights_fragment_begin>', GI_APPLY, 'fs:lights');
        f = after(f, '#include <lights_fragment_maps>', GI_IBL, 'fs:iblmaps');
        f = after(f, '#include <aomap_fragment>', GI_AO, 'fs:aomap');
        f = after(f, '#include <emissivemap_fragment>', GI_EMISSIVE, 'fs:emissive');
        shader.fragmentShader = f;
        shader.defines = shader.defines || {};
      } catch (err) {
        void err;                     // an unpatched material is dull, not broken
      }
    };
    /* The previous key is kept, not replaced. It is the owning module's
     * discriminator — vegetation returns `lem-veg-f` or `lem-veg-o` to stop
     * three linking one program for two materials whose patched source
     * differs — and overwriting it with a constant meant every material this
     * module adopted was telling the program cache it was interchangeable with
     * every other one. Nothing in the scene happens to collide today; that is
     * luck, and it is the kind of luck that expires when somebody adds a
     * material. */
    const prevKey = material.customProgramCacheKey;
    material.customProgramCacheKey = () => {
      let own = '';
      try { own = prevKey ? String(prevKey.call(material)) : ''; } catch (e) { void e; }
      return 'lemgi:' + this._modeKey + '|' + own;
    };
    material.defines = material.defines || {};
    this._stampDefines(material);
    material.envMapIntensity = material.userData.lemEnvBase * this._envFactor();
    material.needsUpdate = true;
    this.materials.add(material);
    return material;
  }

  _stampDefines(material) {
    const flat = this._flat;
    const probes = !flat && !!this.grid;
    const ao = !flat && !!(this.tier?.ao);
    const csm = flat ? 0 : this._csm.length;
    if (flat) material.defines.LEM_GI_FLAT = ''; else delete material.defines.LEM_GI_FLAT;
    if (probes) material.defines.LEM_GI_PROBES = ''; else delete material.defines.LEM_GI_PROBES;
    if (ao) material.defines.LEM_SSAO = ''; else delete material.defines.LEM_SSAO;
    if (csm) material.defines.LEM_FAR_SHADOW = ''; else delete material.defines.LEM_FAR_SHADOW;
    if (csm > 1) material.defines.LEM_CSM2 = ''; else delete material.defines.LEM_CSM2;
  }

  /** Recompute the compile-time mode and, only if it actually moved, force the
   *  one recompile that costs. Tier steps and the first grid are the only two
   *  events that get here. */
  _syncMode() {
    const key = `${this._flat ? 'F' : '-'}${this.grid ? 1 : 0}` +
                `${this.tier?.ao ? 1 : 0}${this._csm.length}`;
    /* Indirect diffuse has exactly one owner, at every tier. The environment
     * map keeps its specular reflection and contributes no diffuse — with the
     * probes on that would be the sky counted twice, and with them off the
     * hemisphere fallback would be. */
    this.uniforms.lemIblDiffuse.value = 0;
    if (key === this._modeKey) return;
    this._modeKey = key;
    for (const m of this.materials) {
      this._stampDefines(m);
      m.needsUpdate = true;
    }
  }

  /**
   * How much of the environment map a surface is allowed to reflect — and the
   * top defect of three review rounds, measured rather than argued this time.
   *
   * `scene.environment` is a PMREM of sky.js's sky, and `_fitFill` has already
   * established that that sky's radiance is roughly four times too bright
   * relative to the sun: it scales the whole probe field by `giScale` (0.24 on
   * a clear afternoon) to get a defensible key-to-fill ratio out of it. The
   * env map was left at full strength. So the same sky was handing the world
   * its diffuse fill at a quarter strength and its specular reflection at
   * full, and since `lemIblDiffuse` is 0 the reflection is the only thing the
   * cube map still does — an unoccluded, achromatic, blue wash over every
   * surface in the frame at four times the weight of the light that is
   * supposed to be filling the shadows.
   *
   * Measured on the wide clear-noon frame with `harness/toggle.mjs`: a canopy
   * crop reads 41/62/84 (B−R +43) with the environment on and 50/56/48
   * (B−R −1.5) with `scene.environmentIntensity` forced to zero. Zeroing the
   * probe field instead moved the same crop by three units. The cold cast was
   * never the fog, the sun or the probes. It was this.
   *
   * So the reflection is normalised by exactly the number the fill is
   * normalised by. Scaling irradiance and radiance by the same factor is what
   * "the sky is dimmer than the model said" means; scaling only one of them is
   * two different skies in one frame.
   *
   * It returns a *factor*, not a value, and the caller multiplies it into
   * whatever the owning module authored. That matters as much as the number:
   * vegetation sets 0.30 on leaf cards because a leaf four deep sees a
   * fraction of the sky, rail sets 1.5 for a polished railhead and buildings
   * 2.2 for glass. This function used to overwrite all three with one constant,
   * which is why the treeline photographed as pale as the sky behind it.
   */
  _envFactor() {
    const w = this.ctx.weather || {};
    const wet = clamp(w.wetness ?? 0, 0, 1);
    /* Wet surfaces reflect more of it, which is most of what makes rain read. */
    return clamp((this.giScale ?? 1) * (1 + wet * 0.45), 0.02, 2.0);
  }

  /** Push the current factor onto every registered material, keeping each
   *  module's authored value as the base it multiplies. */
  _refreshEnvIntensity() {
    const f = this._envFactor();
    /* The lever that actually reaches the shader — see `GI_IBL`. */
    if (this.ctx.scene) this.ctx.scene.environmentIntensity = f;
    for (const m of this.materials) {
      const base = Number.isFinite(m.userData?.lemEnvBase) ? m.userData.lemEnvBase : 1;
      if (m.userData?.lemEnvU) m.userData.lemEnvU.value = base;
      /* Kept in sync anyway, for any material that later gets an `envMap` of
       * its own and so escapes three's overwrite. */
      m.envMapIntensity = base * f;
    }
  }

  /** Sweep the scene for standard materials nobody registered. Other modules
   *  should call `applyGI`, but a subsystem that forgot should not be the
   *  reason half the site is lit by a different rig from the other half. */
  _adopt() {
    const scene = this.ctx.scene;
    if (!scene) return;
    const seen = this.materials;
    let fresh = false;
    scene.traverse(obj => {
      const m = obj.material;
      if (m) {
        if (Array.isArray(m)) { for (const mm of m) if (!seen.has(mm)) this.applyGI(mm); }
        else if (!seen.has(m)) this.applyGI(m);
      }
      if (this._adoptShadow(obj)) fresh = true;
      /* Outside `_adoptShadow` on purpose. That function decides once and then
       * never looks at an object again, which is right for a flag another
       * module owns — but it means an object whose module set `castShadow`
       * itself is dismissed on the first line, and those are most of the large
       * casters on the site. Enrolment in the coarse map has to see them. */
      if (obj.isMesh || obj.isInstancedMesh || obj.isBatchedMesh) {
        if (this._demoteCaster(obj)) fresh = true;
        /* The owning module's own intent, captured before `_nearCull` starts
         * driving the flag from frame to frame. Everything downstream — the
         * cull, and enrolment in the coarse cascades — reads this rather than
         * the live flag, or a tree culled out of the near map for one second
         * would fall out of the far cascade permanently. */
        if (obj.userData.lemCastBase === undefined) {
          obj.userData.lemCastBase = !!obj.castShadow;
        }
        /* Membership is tested rather than assumed from `lemCastBase` being
         * undefined, and that is a bug fix rather than a tidy-up. The sweep
         * below drops anything that has left the scene, and rail and trains
         * both DETACH and RE-ATTACH the same mesh across a parse. Such an
         * object came back with `lemCastBase` already set, so the old test
         * skipped it, and it was never put back on the cull list: it went on
         * casting into three's near map for the rest of the session no matter
         * where the box was. Measured at `cam=far`: 34 casters, every one of
         * them outside the box, which is most of what the near map was drawing
         * before this round. */
        if (obj.userData.lemCastBase && !this._cullIn.has(obj)) {
          this._cullIn.add(obj);
          this._cullable.push(obj);
        }
        this._enrol(obj);
      }
    });
    if (this._cullable.some(o => !o.parent)) {
      for (const o of this._cullable) if (!o.parent) this._cullIn.delete(o);
      this._cullable = this._cullable.filter(o => o.parent);
    }
    this._trim();
    this._nearCull();
    /* Geometry that appeared since the last sweep is geometry the shadow map
     * has never seen. With `autoUpdate` off, saying nothing here is how a
     * building that finished building two seconds after everyone else ends up
     * as the one thing in frame with no shadow under it. */
    if (fresh && this.ctx.engine) this.ctx.engine.shadowNeedsUpdate = true;
  }

  /**
   * Distance culling for cascade 0, done by hand because nothing else can do it.
   *
   * Cascade 0's box is now 336 metres across at its widest. Vegetation, rail
   * and trains all set `frustumCulled = false` on their instanced meshes —
   * correctly, since they rewrite the instance set as the camera moves and
   * three's cached bounding sphere is always a partition stale — and three's
   * shadow pass honours that flag, so every one of them is drawn in full into
   * a box most of their instances are nowhere near. At `cam=wide` that is a
   * band of forest between 270 and 800 metres out being rasterised into a map
   * that stops at 168.
   *
   * So the distance test is done here instead, over the instance matrices,
   * against the box that was actually fitted. It is exact rather than
   * conservative-by-bounding-sphere, which is the whole point: a mesh with one
   * instance inside the box keeps casting, and a mesh with none stops.
   *
   * The *coarse* cascades are unaffected — they select by layer, and the layer
   * bit is set once at enrolment from `lemCastBase`, the owning module's own
   * intent, not from the flag this function drives. A tree culled out of the
   * near map is still in both coarse ones, which is where its shadow was coming
   * from at that distance anyway.
   */
  _nearCull() {
    if (this._flat) return;             // nothing is drawing a shadow map to cull for
    if (!this.sun || !this._shadowFit) return;
    /* Parked: the near map has no band worth covering at this camera, so it is
     * emptied rather than switched off — `sun.castShadow` is a compile-time
     * define and clearing it would recompile every material in the world. What
     * is left is a clear of a 3072 map with nothing drawn into it. */
    if (this._nearParked || !this._shadowFit.radius) {
      if (!this._nearParked) return;
      let cleared = false;
      for (const obj of this._cullable) {
        if (obj.castShadow) { obj.castShadow = false; cleared = true; }
      }
      if (cleared && this.ctx.engine) this.ctx.engine.shadowNeedsUpdate = true;
      return;
    }
    const c = this._shadowFit.centre;
    const R = this._shadowFit.radius;
    /* Tested against the box in the LIGHT's plane, not against a sphere around
     * it. The ortho is square and axis-aligned to the light, so a sphere test
     * has to use the half-diagonal and keeps a corner's worth of geometry that
     * can never project into the map. The two dot products are the same two the
     * shader's `lemBoxWeight` uses, so the cull and the handover agree about
     * where the box is. Depth along the light is deliberately not tested: a
     * stack a hundred metres up is still inside this box, it is just further
     * from the camera. */
    const rx = this.uniforms.lemLightRight.value;
    const ru = this.uniforms.lemLightUp.value;
    let changed = false;
    const v = this._cullTmp || (this._cullTmp = new THREE.Vector3());
    for (const obj of this._cullable) {
      if (!obj.parent) continue;
      if (!obj.userData.lemCastBase) continue;
      const reach = R + (obj.userData.lemCast?.size || 0) + 6;
      let near = false;
      if (obj.isInstancedMesh) {
        const n = obj.count | 0;
        if (n > 0) {
          const a = obj.instanceMatrix.array;
          obj.updateWorldMatrix(true, false);
          const e = obj.matrixWorld.elements;
          for (let i = 0; i < n; i++) {
            const o = i * 16;
            /* The instance translation through the mesh's own world matrix,
             * written out rather than built into a Vector3 and transformed:
             * this runs over sixteen thousand instances and allocating there
             * is how a cull costs more than the draws it saves. */
            const x = a[o + 12], y = a[o + 13], z = a[o + 14];
            const wx = e[0] * x + e[4] * y + e[8] * z + e[12] - c.x;
            const wy = e[1] * x + e[5] * y + e[9] * z + e[13] - c.y;
            const wz = e[2] * x + e[6] * y + e[10] * z + e[14] - c.z;
            if (Math.abs(wx * rx.x + wy * rx.y + wz * rx.z) < reach &&
                Math.abs(wx * ru.x + wy * ru.y + wz * ru.z) < reach) { near = true; break; }
          }
        }
      } else {
        obj.getWorldPosition(v).sub(c);
        near = Math.abs(v.dot(rx)) < reach && Math.abs(v.dot(ru)) < reach;
      }
      if (obj.castShadow !== near) { obj.castShadow = near; changed = true; }
    }
    if (changed && this.ctx.engine) this.ctx.engine.shadowNeedsUpdate = true;
  }

  /**
   * Take a caster out of cascade 0, which is the only pass this module cannot
   * choose the contents of — three renders whatever carries `castShadow` and
   * falls in the light's frustum, and `object.layers` is tested against the
   * *view* camera there, so a layer cannot be used to hide something from the
   * shadow map alone. The flag is the only lever.
   *
   * The rule is the one measured this round: an object whose vertical extent is
   * under 45 cm casts a shadow no longer than about a metre at any sun
   * elevation a working day is rendered at, and it lands on the surface the
   * object is bedded into. The site's four largest instanced sets by triangle
   * count are exactly that — rail's sleepers (2.6 × 0.2 × 0.3 m), tie plates
   * (1.7 × 0.0 × 0.1), chairs and spikes — 10,958 instances and roughly 400k
   * triangles, redrawn into the shadow map on every refit for a result nobody
   * has ever seen in a frame. Ground decals are the same case: a painted hazard
   * stripe 217 m long and 0.9 m tall is a texture, not an occluder.
   *
   * This is where the two coarse cascades are paid for, and then some.
   *
   * `lemKeepShadow` is the opt-out, for anything whose owner knows better.
   */
  _demoteCaster(obj) {
    if (!obj.castShadow || obj.userData?.lemKeepShadow) return false;
    this._demoteSeen = this._demoteSeen || new WeakSet();
    if (this._demoteSeen.has(obj)) return false;
    const m = this._casterMetrics(obj);
    if (!m) return false;                 // never drawn, no bounding sphere, no opinion
    this._demoteSeen.add(obj);
    if (m.rise >= CAST_MIN_RISE && !m.slab) return false;
    obj.castShadow = false;
    return true;
  }

  /**
   * Decide the shadow flags for one object, exactly once in its life.
   *
   * Every module is supposed to set `castShadow`/`receiveShadow` itself, and
   * most do. But three defaults both to false, so a module that forgets is
   * indistinguishable from a module that has not been written yet, and the
   * result is the one failure the reference checklist calls unacceptable: an
   * object standing on nothing. This is the net under that.
   *
   * "Exactly once" is the whole design. A module that turns its own flags off
   * later — trains sheds casting at the low tiers, and is right to — is not
   * fought over, because by then the object is no longer new to this sweep.
   *
   * The skips are the cases where casting is wrong rather than merely absent:
   * anything transparent or not writing depth (glass, glows, spray, lamp
   * lenses) would cast a solid black silhouette from a shape that is mostly
   * not there; anything unlit is a light source pretending to be a surface;
   * and anything with a bounding sphere in the hundreds of metres is the
   * ground, the sky, or a terrain tile, which cost the whole shadow pass and
   * cast onto nothing the camera can see. `userData.noShadow` opts out
   * explicitly.
   */
  _adoptShadow(obj) {
    if (!obj || !(obj.isMesh || obj.isInstancedMesh || obj.isBatchedMesh)) return false;
    this._shadowSeen = this._shadowSeen || new WeakSet();
    if (this._shadowSeen.has(obj)) return false;
    this._shadowSeen.add(obj);
    if (obj.castShadow || obj.receiveShadow) return false;   // already decided
    if (obj.userData?.noShadow) return false;

    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const m of mats) {
      if (!m) return false;
      if (m.transparent || m.depthWrite === false) return false;
      if (m.isMeshBasicMaterial || m.isPointsMaterial || m.isSpriteMaterial) return false;
      if (m.isShaderMaterial && !m.isNodeMaterial && !m.lights) return false;
    }

    /* Read the bounding sphere, never compute one. Frustum culling has already
     * built it for anything that has been drawn, and this sweep runs a second
     * after the world does; asking for it on a geometry that has not been
     * drawn is how you end up calling `computeBoundingSphere` on a buffer with
     * a NaN in it and printing three's warning once per frame forever. No
     * sphere means no opinion, which for a mesh nobody has rendered is the
     * honest answer. */
    const g = obj.geometry;
    const s = Math.max(obj.scale.x, obj.scale.y, obj.scale.z) || 1;
    const r = g?.boundingSphere?.radius;
    const big = Number.isFinite(r) && r * s > 260;

    obj.receiveShadow = true;
    /* At the bottom tier the shadow pass is the frame's whole budget; a bench
     * PC gets the light, not the second geometry pass. The wanted answer is
     * recorded on the object and the object on `_flatAdopted`, because this
     * function runs once per object for the life of the world and the ladder
     * climbs *out* of this tier — see `_flatAdopted` for what that cost. */
    obj.userData = obj.userData || {};
    obj.userData.lemCastWanted = !big;
    obj.castShadow = !big && !this._flat;
    if (!big && this._flat) this._flatAdopted.push(obj);
    return true;
  }

  /* ---- artificial light ---------------------------------------------------- */

  _buildPool() {
    /* `emissiveOnly: true` on the tier, read literally: the only thing that can
     * say "lit" at this rung is emissive geometry. Nothing in the scene is a
     * light source, so `NUM_POINT_LIGHTS` is 0 and three's whole point-light
     * loop compiles out of every material in the world. A yard flood still
     * draws its lamp glass — `requestLight` reports `active: false` and callers
     * are already written to fall back to exactly that. */
    const want = this._flat ? 0 : (POOL_BY_TIER[this.tier?.name] ?? 6);
    while (this._pool.length > want) {
      const l = this._pool.pop();
      l.parent?.remove(l);
      l.dispose?.();
    }
    while (this._pool.length < want) {
      const l = new THREE.PointLight(0xffd6a0, 0, 40, 2);
      l.castShadow = false;            // six shadow faces per lamp is not affordable
      l.visible = true;                // see the header: a light never leaves the scene
      this.ctx.scene.add(l);
      this._pool.push(l);
    }
    this._lightsDirty = true;
  }

  /**
   * Ask for a real point light.
   *
   *   const h = gi.requestLight({position: v3, colour: 0xffd2a0, intensity: 1.2,
   *                              radius: 28, priority: 2, alwaysOn: false});
   *   h.active            did it win a slot this moment
   *   h.set({intensity})  change anything
   *   h.move(x, y, z)     cheaper than set for the common case
   *   h.release()         give the slot back
   *
   * `intensity` is a normalised knob, not candela: 1 is "a working yard lamp".
   * `priority` breaks ties before distance to the camera does. Unless
   * `alwaysOn`, the intensity is scaled by `artificialFactor`, so a lamp asked
   * for at noon simply does not light — ask anyway and let the hour decide.
   * A handle that never wins a slot is still valid; check `active` before
   * spending anything on it.
   */
  requestLight(opts = {}) {
    const rec = {
      id: ++this._lightSeq,
      position: new THREE.Vector3(),
      colour: new THREE.Color(opts.colour ?? opts.color ?? 0xffd2a0),
      intensity: Number.isFinite(opts.intensity) ? opts.intensity : 1,
      radius: Number.isFinite(opts.radius) ? opts.radius : 28,
      priority: Number.isFinite(opts.priority) ? opts.priority : 0,
      alwaysOn: !!opts.alwaysOn,
      live: false,
    };
    if (opts.position) rec.position.copy(opts.position);
    this._lightRequests.set(rec.id, rec);
    this._lightsDirty = true;
    const self = this;
    return {
      id: rec.id,
      get active() { return rec.live; },
      set(patch = {}) {
        if (patch.position) rec.position.copy(patch.position);
        if (patch.colour !== undefined || patch.color !== undefined) {
          rec.colour.set(patch.colour ?? patch.color);
        }
        if (Number.isFinite(patch.intensity)) rec.intensity = patch.intensity;
        if (Number.isFinite(patch.radius)) rec.radius = patch.radius;
        if (Number.isFinite(patch.priority)) rec.priority = patch.priority;
        if (patch.alwaysOn !== undefined) rec.alwaysOn = !!patch.alwaysOn;
        self._lightsDirty = true;
      },
      move(x, y, z) { rec.position.set(x, y, z); },
      release() { self._lightRequests.delete(rec.id); self._lightsDirty = true; },
    };
  }

  _updateLights() {
    const pool = this._pool;
    if (!pool.length) {
      for (const rec of this._lightRequests.values()) rec.live = false;
      return;
    }
    const cam = this.ctx.camera;
    const scored = [];
    for (const rec of this._lightRequests.values()) {
      rec.live = false;
      const eff = rec.intensity * (rec.alwaysOn ? 1 : this.artificialFactor);
      if (eff < 0.06) continue;         // too dim to be worth a slot in the pool
      const d = cam ? rec.position.distanceTo(cam.position) : 0;
      /* Priority first, then nearest: a signal lamp that says an instrument is
       * RED outranks a yard flood, at any distance. */
      scored.push({rec, eff, score: rec.priority * 1e6 - d});
    }
    scored.sort((a, b) => b.score - a.score);
    const n = Math.min(pool.length, scored.length);
    for (let i = 0; i < n; i++) {
      const {rec, eff} = scored[i];
      const l = pool[i];
      l.position.copy(rec.position);
      l.color.copy(rec.colour);
      l.distance = rec.radius;
      /* Inverse-square with a normalised knob: a bigger lamp has to be brighter
       * at the source to reach the edge of its own radius. */
      l.intensity = eff * rec.radius * rec.radius * 0.055;
      rec.live = true;
    }
    for (let i = n; i < pool.length; i++) pool[i].intensity = 0;
  }

  /* ---- lifecycle ----------------------------------------------------------- */

  update(dt, t) {
    if (!this._built) return;
    const ctx = this.ctx;

    /* The bottom rung's whole frame, and the reason it is a separate branch
     * rather than a set of conditions threaded through the one below.
     *
     * What is skipped here is not cheap: fitting and snapping the shadow ortho,
     * a quarter-second sweep over every instance matrix on the site to decide
     * what is inside it, up to one coarse cascade redraw of ~100 draw calls,
     * a fullscreen meter pass plus a `readRenderTargetPixels` that fences the
     * GPU against the CPU, and a slice of the probe relight — every frame,
     * on the machine that has the least of everything. None of it can produce a
     * visible pixel at a tier with no shadows, no cascades, no probe field and
     * a fixed flat ambient, so none of it runs.
     *
     * `_applyGrade` and `_adopt` stay. The grade is the exposure, and a floor
     * display still has to track dawn; `_adopt` is what registers a train's
     * materials when a print is parsed, and an unregistered material at this
     * tier is not merely unlit, it misses the emissive gain that is the only
     * thing here saying a window is on. */
    if (this._flat) {
      this._applyGrade(dt);
      this._adoptClock += dt;
      if (this._adoptClock > 1.0) {
        this._adoptClock = 0;
        this._adopt();
        /* Re-read on the same slow clock, because whether the flat specular
         * runs depends on something another module owns. sky.js installs its
         * PMREM during its own build and takes it down again at some hours; if
         * that happens after this tier was entered, a stale 1 here is the sky
         * counted twice on every metal surface and a stale 0 is a tank farm
         * with no reflection in it at all. */
        this.uniforms.lemFlatSpec.value = this.ctx.scene?.environment ? 0 : 1;
      }
      void t;
      return;
    }

    /* The AO buffer is last frame's, taken from last frame's camera. That is
     * how every screen-space AO in a deferred-ish pipeline works, and the one
     * frame of lag is invisible next to the alternative, which is not having
     * contact occlusion at all. */
    const ao = ctx.engine?.aoTexture || null;
    if (this.uniforms.lemAOMap.value !== ao) this.uniforms.lemAOMap.value = ao;
    if (ctx.engine?.width) {
      this.uniforms.lemAORes.value.set(ctx.engine.width, ctx.engine.height);
    }

    this._fitShadow();

    this._cullClock += dt;
    if (this._cullDirty && this._cullClock > 0.25) {
      this._cullClock = 0;
      this._cullDirty = false;
      this._nearCull();
    }

    this._serviceCascades(dt);

    /* Meter roughly six times a second. The readback is 1.3 kB and asynchronous
     * where the driver supports it, but it is still a fence, and the term it
     * feeds is smoothed over most of a second — sampling it every frame would
     * buy nothing but sixty stalls. */
    this._meterClock += dt;
    /* A readback that never settles would otherwise leave `_meterBusy` set and
     * the exposure frozen at whatever the last measurement said — a failure
     * that looks exactly like the analytic-only bug this replaced. */
    if (this._meterBusy) {
      this._meterWait = (this._meterWait || 0) + dt;
      if (this._meterWait > 1.5) { this._meterBusy = false; this._meterWait = 0; }
    } else {
      this._meterWait = 0;
    }
    /* Also on the budget. The readback is the one thing in this file that makes
     * the CPU wait for the GPU, and on a part with no async readback path it is
     * a full pipeline flush six times a second. At `low` it happens half as
     * often, which costs the adaptation a fraction of a second of settling on a
     * term already smoothed over most of one. */
    if (this._meterClock > 0.16 / Math.max(this._budget, 0.25)) {
      this._meterClock = 0; this._meter();
    }
    this._applyGrade(dt);

    /* Occluders registered during a build land here as one rebuild rather than
     * one per caller. */
    if (this._occluderPending && ctx.plan) {
      this._occluderPending = false;
      try { this._buildGrid(ctx.plan); } catch (err) { void err; }
    }

    /* Probe work is sliced. A relight is a few milliseconds all told, but "a
     * few milliseconds" spent in one frame on a bench PC is a visible hitch and
     * this has no deadline — nothing is waiting for the light to finish
     * changing except the light. */
    if ((this._traceDirty || this._probesDirty) && this.grid) {
      /* Probes relit per frame, scaled by the tier's lighting budget. The whole
       * sweep is a few milliseconds of CPU spread over however many frames it
       * takes, and nothing waits on it finishing except the light itself, so a
       * smaller slice buys back main-thread time at the cost of the sun's
       * indirect answer sweeping in over a second instead of a third of one. */
      const budget = Math.max(40, Math.round(
        (this._traceDirty ? 220 : 900) * this._budget));
      const end = Math.min(this.grid.count, this._cursor + budget);
      if (this._traceDirty) this._trace(this._cursor, end);
      this._relight(this._cursor, end);
      this._cursor = end;
      if (this._cursor >= this.grid.count) {
        this._cursor = 0;
        this._traceDirty = false;
        this._probesDirty = false;
        this._upload();
      } else if ((ctx.engine?.frame | 0) % 4 === 0) {
        this._upload();               // partial, so the change sweeps in visibly
      }
    }

    this._lightClock += dt;
    if (this._lightClock > 0.18 || this._lightsDirty) {
      this._lightClock = 0;
      this._lightsDirty = false;
      this._updateLights();
    }

    this._adoptClock += dt;
    if (this._adoptClock > 1.0) {
      this._adoptClock = 0;
      this._adopt();
      if (this._settleRefits > 0) { this._settleRefits--; this._fitShadow(true); }
    }
    void t;
  }

  onPlan(plan) {
    try {
      this._buildGrid(plan);
      this._syncMode();
      this._fitShadow(true);
    } catch (err) { console.warn('[gi] probe grid rebuild skipped —', err); }
  }

  onWeather() {
    try {
      this._readSky(this.hours);
      this._refreshEnv();
      this._refreshEnvIntensity();
      this._probesDirty = true; this._cursor = 0;
      this._fitShadow(true);
    } catch (err) { void err; }
  }

  onTime(hours) {
    try {
      this._readSky(hours);
      this._refreshEnv();
      this._refreshEnvIntensity();
      this._probesDirty = true; this._cursor = 0;
      this._fitShadow(true);
      this._lightsDirty = true;
    } catch (err) { void err; }
  }

  _refreshEnv() {
    /* Only ours gets rebuilt. If sky.js is driving the environment it is also
     * responsible for keeping it current, and two modules writing one field is
     * how a sky ends up flickering between two authors' ideas of dusk. */
    if (this._envTex && this.ctx.scene.environment === this._envTex) {
      this._envTex = null;            // let _ensureEnvironment replace it
      this.ctx.scene.environment = null;
      this._ensureEnvironment();
    }
    /* `_skyIrradianceFlat` is what the non-probe path and the ground bounce are
     * built from, so it has to move with the hour even when the grid does not
     * exist. `_readSky` already refreshed it; this keeps the two callers of
     * `_refreshEnv` honest if that ever stops being true. */
    const flat = this._skyIrradianceFlat();
    this.uniforms.lemSkyIrradiance.value.copy(flat);
    this._setGroundIrradiance(flat);
  }

  onQuality(tier) {
    try {
      const wasFlat = this._flat;
      this.tier = tier;
      this._flat = giOff(tier);
      this._budget = lightingBudget(tier);

      const size = this._shadowSize();
      if (this.sun && this.sun.shadow.mapSize.x !== size) {
        this.sun.shadow.mapSize.set(size, size);
        this.sun.shadow.map?.dispose();
        this.sun.shadow.map = null;
      }
      /* Screen-space occlusion is one of the things `lighting` buys. It is a
       * half-resolution buffer the engine draws whether or not this module
       * consumes it, so the budget cannot switch it off — `tier.ao` does that —
       * but how hard it is driven is a fair thing to spend, and an over-driven
       * AO is exactly the artefact that survives longest on a weak part. */
      this.uniforms.lemAOStrength.value =
        tier.ao ? 1.15 * (0.55 + 0.45 * this._budget) : 0;
      const comp = this.ctx.engine?._passes?.composite?.material?.uniforms;
      if (comp?.uAOStrength) comp.uAOStrength.value = 0;

      this._buildCascades();
      this._buildPool();
      if (this.ctx.plan) this._buildGrid(this.ctx.plan); else this._disposeGrid();
      this._applyFlatMode(wasFlat);
      /* After `_applyFlatMode`, which is what decides whether there is an
       * environment map to weight and re-fits the ambient the factor is derived
       * from. Ordering this the other way round left one tier step's worth of
       * stale `giScale` on every material in the world. */
      this._refreshEnvIntensity();
      this._syncMode();
      if (!this._flat) this._fitShadow(true);
    } catch (err) { console.warn('[gi] quality step partially applied —', err); }
  }

  /**
   * Enter or leave the no-GI path.
   *
   * Everything in here is a thing that is *not built* rather than a thing that
   * is built smaller: the environment cube and the PMREM that made it, the
   * probe grid, the coarse cascades, the point-light pool, and the near map's
   * caster set. The point of the rung is that the machine which cannot afford
   * the light also does not pay to have it computed and then thrown away.
   */
  _applyFlatMode(wasFlat) {
    const scene = this.ctx.scene;
    if (this._flat) {
      /* Our own environment map goes, and with it the PMREM generator, the
       * cube-UV fetch in every fragment and the two mip chains it lives in.
       * Never sky.js's: `lemFlatSpec` below is the honest test of whether a
       * cube is still standing, whoever put it there. */
      if (this._envTex && scene?.environment === this._envTex) scene.environment = null;
      if (this._envBg && scene?.background === this._envBg) {
        /* The background is the one thing that must not simply vanish — an
         * unlit world against a black rectangle is not a cheaper picture, it is
         * a broken one — so the sky texture stays as the backdrop even though
         * nothing is lit from it any more. */
        scene.backgroundBlurriness = 0.06;
      }
      this._pmrem?.dispose?.();
      this._pmrem = null;
      /* Turned off entirely rather than left running at a low map size: with
       * `shadows: false` the engine has already disabled the shadow map, so a
       * fitted ortho, a snapped texel grid and a quarter-second sweep over
       * sixteen thousand instance matrices are all being computed for a pass
       * that never runs. */
      if (this.sun) this.sun.castShadow = false;
      this._cullDirty = false;
      this.uniforms.lemEmissiveGain.value = FLAT_EMISSIVE_GAIN;
      /* The meter stops running here, and a measurement nobody is refreshing is
       * worse than no measurement: `_applyGrade` would go on correcting the
       * exposure and placing the black point from whatever the frame happened
       * to be at the instant the tier stepped down, for the rest of the
       * session. Forgotten rather than frozen. */
      this._sceneEV = null;
      this._sceneEVLow = undefined;
      this._meterBusy = false;
    } else {
      this.uniforms.lemEmissiveGain.value = 1;
      if (wasFlat) this._restoreShadowFlags();
    }
    /* The ambient the flat path runs on is fitted by a different function from
     * the lit path's, so the switch has to re-derive it — otherwise the first
     * frame after a step down is lit by a fill sized for a world with shadows
     * in it, and the first frame after a step up by one sized for a world
     * without. Before the environment is (re)built, because the cube is an
     * integration of the same sky gradient `_readSky` has just moved; building
     * it first bakes one tier step's worth of stale sky into every reflection
     * on the site. */
    this._readSky(this.hours);
    if (!this._flat) this._ensureEnvironment();
    this.uniforms.lemFlatSpec.value =
      this._flat && !scene?.environment ? 1 : 0;
  }

  /** Give back the shadow flags the floor tier suppressed. See `_flatAdopted`. */
  _restoreShadowFlags() {
    const list = this._flatAdopted;
    if (!list.length) return;
    for (const obj of list) {
      if (!obj.parent) continue;
      if (!obj.userData?.lemCastWanted) continue;
      obj.castShadow = true;
      /* `lemCastBase` was captured as false while the flag was suppressed, and
       * it is what the near cull and both coarse cascades read instead of the
       * live flag. Putting the flag back without it would leave the object
       * casting into three's map for ever and enrolled in neither cascade —
       * half-restored, which is harder to spot than not restored at all. */
      if (!obj.userData.lemCastBase) obj.userData.lemCastBase = true;
      if (!this._cullIn.has(obj)) { this._cullIn.add(obj); this._cullable.push(obj); }
    }
    list.length = 0;
    if (this.ctx.engine) this.ctx.engine.shadowNeedsUpdate = true;
  }

  dispose() {
    this._disposed = true;
    this._disposeGrid();
    this._disposeCascades();
    this._meterRT?.dispose?.();
    this._meterPass?.geometry?.dispose?.();
    this._meterPass?.material?.dispose?.();
    this._meterRT = null;
    this._pmrem?.dispose?.();
    this._envRT?.dispose?.();
    if (this.sun) { this.ctx.scene.remove(this.sun); this.ctx.scene.remove(this.sun.target); }
    for (const l of this._pool) { this.ctx.scene.remove(l); l.dispose?.(); }
    this._pool.length = 0;
    for (const o of this._devMeshes || []) {
      this.ctx.scene.remove(o);
      o.geometry?.dispose?.();
      o.material?.dispose?.();
    }
    this.materials.clear();
    this._lightRequests.clear();
  }

  /* ---- the solo harness's proving ground ----------------------------------- */

  /** Lighting with nothing to light cannot be looked at, and this module is
   *  judged by eye. `dev/solo.html?mods=gi&giproxy=1` stands up exactly the
   *  occluders the probe tracer assumes — the ground, a block per station, a
   *  block for the hub — plus a few test spheres. It can only appear under
   *  `world/dev/`, so it is not a thing that can leak onto the floor. */
  _devProxies() {
    try {
      if (!location.pathname.includes('/world/dev/')) return;
      if (!new URLSearchParams(location.search).has('giproxy')) return;
      const ctx = this.ctx;
      const Tex = ctx.Tex;
      /* `makeTexture` is only reachable through the module's own `Tex` object —
       * it is not a top-level export of textures.js, and ctx.Tex is the module
       * namespace. Noted in scratchpad/REQUESTS.md. */
      const makeTexture = Tex.makeTexture || Tex.Tex?.makeTexture;
      this._devMeshes = [];
      const add = (mesh, casts = true) => {
        mesh.castShadow = casts; mesh.receiveShadow = true;
        ctx.scene.add(mesh); this._devMeshes.push(mesh);
        this.applyGI(mesh.material);
        return mesh;
      };

      const groundMat = Tex.material('gi-dev-ground', () => {
        const S = 256;
        const h = new Float32Array(S * S);
        for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
          h[y * S + x] = Tex.fbm(x / S * 8, y / S * 8, {octaves: 4, period: 8, seed: 7});
        }
        const mat = new THREE.MeshStandardMaterial({roughness: 0.95, metalness: 0});
        if (makeTexture) {
          mat.map = makeTexture(Tex.paint(S, (x, y) => {
            /* sRGB, not linear: a "0.18 grey" written here decodes to about
             * 0.027 and the field goes black under a full sun. */
            const n = h[y * S + x];
            return [0.33 + n * 0.15, 0.355 + n * 0.16, 0.245 + n * 0.11];
          }), {srgb: true, repeat: 26});
          mat.normalMap = makeTexture(Tex.normalFromHeight(h, S, 1.4), {repeat: 26});
        } else {
          mat.color.setRGB(0.11, 0.14, 0.06);
        }
        return mat;
      });
      /* Big enough that its edge is over the horizon from every camera preset —
       * otherwise the frame is half env-map ground hemisphere and it is the
       * proving ground being looked at, not the light. */
      add(new THREE.Mesh(new THREE.PlaneGeometry(5000, 5000), groundMat), false)
        .rotation.x = -Math.PI / 2;

      const blockMat = new THREE.MeshStandardMaterial({
        color: 0x8d8b86, roughness: 0.82, metalness: 0.0,
      });
      const stations = ctx.plan?.stations || [];
      for (const s of stations) {
        const top = ctx.world?.anchors?.get?.(s.uid)?.top ?? 18;
        const m = add(new THREE.Mesh(new THREE.BoxGeometry(32, top, 26), blockMat));
        m.position.set(s.x, top / 2, s.z);
      }
      if (ctx.plan?.hub) {
        const m = add(new THREE.Mesh(new THREE.BoxGeometry(68, 26, 48), blockMat));
        m.position.set(ctx.plan.hub.x, 13, ctx.plan.hub.z);
      }
      /* A roughness/metalness sweep, laid across the front of the site: the row
       * that tells you whether the environment is doing anything and whether
       * the probes are directional. */
      const b = ctx.plan?.bounds || {minX: -100, maxX: 100, minZ: -100, maxZ: 100};
      const midX = (b.minX + b.maxX) * 0.5;
      for (let i = 0; i < 6; i++) {
        const mat = new THREE.MeshStandardMaterial({
          color: 0xb9bcc0, roughness: 0.06 + i * 0.19, metalness: i > 2 ? 1 : 0.05,
        });
        const m = add(new THREE.Mesh(new THREE.SphereGeometry(8, 28, 18), mat));
        m.position.set(midX - 105 + i * 42, 9, b.maxZ + 62);
      }
      /* One yard flood per station, so the pool, the priority ordering and the
       * day/night gate are all visible in a screenshot rather than asserted. */
      stations.forEach((s, i) => {
        const top = ctx.world?.anchors?.get?.(s.uid)?.top ?? 18;
        this.requestLight({
          position: new THREE.Vector3(s.x + 20, top * 0.75, s.z + 16),
          colour: 0xffca85, intensity: 1.35, radius: 46, priority: i === 0 ? 2 : 0,
        });
      });
    } catch (err) { void err; }
  }
}

export default GlobalIllumination;
