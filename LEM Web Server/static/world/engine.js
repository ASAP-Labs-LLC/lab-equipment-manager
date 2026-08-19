/* engine.js — the renderer, the camera rig, and the frame budget.
 *
 * Everything visual in the lab world is drawn through here. Three rules shaped
 * it, all of them from the brief:
 *
 *   1. It runs on a bench PC. That means WebGL2 on integrated graphics at
 *      1080p60 — so the frame budget is 16.6ms on a GPU with no headroom, and
 *      the quality ladder below is not a nicety, it is the reason the floor
 *      stays usable when someone opens it on the oldest machine in the lab.
 *   2. Detail comes from textures and light, not from triangles. There is a
 *      depth prepass, half-res ambient occlusion, and image-based lighting off
 *      a procedural sky; there is no 200k-triangle anything.
 *   3. It never blocks the page. The lab data around the map is the point of
 *      the screen; a GPU stall must not take the rails, the tally or the
 *      dialogs with it.
 */
import * as THREE from 'three';

/* The quality ladder.
 *
 * What it spends changed after Ryan looked at it running: "shrinking tree size
 * is a bad answer, increase vegetation draw distance. Do other things like
 * disable roughness maps and reduce texture resolution."
 *
 * He is right, and the old ladder had the priorities backwards. Thinning the
 * forest is the most *visible* saving available — the forest is most of the
 * frame, and a bench PC ends up looking at a different, emptier world than the
 * one everyone else sees. Roughness maps and texture resolution are nearly
 * invisible by comparison: dropping a roughness map costs a little sheen on wet
 * asphalt, and halving texture size costs detail you cannot resolve at the
 * distance a floor display is watched from. So those go first, and `treeRange`
 * stays at or above 1 all the way down — the forest keeps its extent even on
 * the slowest machine.
 *
 * `trees` remains a density multiplier, not a size one. Nothing here scales a
 * tree down; a small tree is a wrong tree, whereas a sparser stand is merely a
 * thinner one.
 *
 * Ryan again, on what to give up first: "on lower settings sacrifice lighting
 * for more vegetation." So the descent now spends the LIGHTING budget and
 * protects the forest. Shadow maps shrink from 2048 to 640 and switch off
 * entirely at the floor tier, ambient occlusion and bloom go early, and
 * `lighting` falls to a quarter for gi.js to spend as it sees fit — while
 * `trees` only ever falls from 1.00 to 0.90 and `treeRange` from 3.20 to 2.90.
 *
 * That is the right trade for this screen. An unshadowed forest still reads as
 * a forest; a shadowed clearing with nothing growing in it reads as a different
 * and emptier place, and the whole point of the map is what it looks like from
 * across a room.
 *
 * Ryan again: "for ultra like quadruple the vegetation LOD view distance." So
 * 3.20 became 12.80, and the rest of the ladder was pulled up with it — the
 * floor tier's 4.20 is still above what ULTRA used to be. Vegetation is
 * expected to clamp this to what the camera can actually see rather than
 * populating ground beyond the far plane; the multiplier is a licence, not a
 * quota.
 */
export const TIERS = [
  {name: 'ultra',  scale: 1.00, shadows: true,  shadow: 2048, ao: true,  aoScale: 0.5,
   bloom: true,  lighting: 1.00, trees: 1.00, treeRange: 12.80, particles: 1.00,
   reflections: true,  roughnessMaps: true,  textureScale: 1.00},
  {name: 'high',   scale: 1.00, shadows: true,  shadow: 1536, ao: true,  aoScale: 0.5,
   bloom: true,  lighting: 0.90, trees: 1.00, treeRange: 10.00, particles: 0.80,
   reflections: true,  roughnessMaps: true,  textureScale: 0.75},
  {name: 'medium', scale: 0.85, shadows: true,  shadow: 1024, ao: true,  aoScale: 0.4,
   bloom: false, lighting: 0.70, trees: 0.98, treeRange: 7.50, particles: 0.55,
   reflections: false, roughnessMaps: false, textureScale: 0.50},
  {name: 'low',    scale: 0.72, shadows: true,  shadow: 640,  ao: false, aoScale: 0.4,
   bloom: false, lighting: 0.45, trees: 0.94, treeRange: 5.60, particles: 0.30,
   reflections: false, roughnessMaps: false, textureScale: 0.35},
  /* The floor tier gives up lighting ENTIRELY rather than doing a cheap version
   * of it. Ryan: "maybe floor can have no lighting at all (like GI, it can
   * still have like a rudimentary emission system and all that but no shadows
   * or complex lighting)."
   *
   * That is the right shape. A quarter-strength irradiance probe field still
   * costs the probe field; switching it off and lighting the world with a flat
   * ambient plus emissive costs almost nothing, and on the slowest machine in
   * the lab a flatly-lit forest is a far better trade than a beautifully lit
   * clearing. `gi: false` is the instruction to gi.js. */
  {name: 'floor',  scale: 0.60, shadows: false, shadow: 512,  ao: false, aoScale: 0.4,
   bloom: false, lighting: 0.00, gi: false, trees: 0.90, treeRange: 4.20,
   particles: 0.15, emissiveOnly: true,
   reflections: false, roughnessMaps: false, textureScale: 0.25},
];

/* Where the operator's choice is remembered. A wall display is set up once and
 * left; making someone walk the tier down by hand on every reload, or watch the
 * adaptive ladder rediscover the same answer every morning, is not a setting. */
export const QUALITY_KEY = 'lem.world.quality';

export function storedQuality() {
  try {
    const v = localStorage.getItem(QUALITY_KEY);
    if (!v || v === 'auto') return null;
    return TIERS.findIndex(t => t.name === v) >= 0 ? v : null;
  } catch (_) { return null; }          // private browsing, embedded webview
}

export function storeQuality(name) {
  try {
    if (!name || name === 'auto') localStorage.removeItem(QUALITY_KEY);
    else localStorage.setItem(QUALITY_KEY, name);
  } catch (_) { /* not being able to remember it is not a reason to fail */ }
}

/* Render resolution, deliberately SEPARATE from the quality tier.
 *
 * It used to ride on the tier: `dpr = min(maxPixelRatio, 1.5) * tier.scale`, so
 * asking for a cheaper tier also silently dropped the resolution — measured, on
 * a 1920x1080 canvas: ultra and high 100% of CSS pixels, medium 72%, low 52%,
 * floor 36%. A third of the pixels is what "Chrome is rendering at a quarter"
 * looks like, and nothing in the UI said it was happening.
 *
 * Those two things are not the same choice. Tier decides how much WORK a frame
 * is — shadows, AO, bloom, vegetation. Resolution decides how SHARP it is. An
 * operator on a weak bench PC may well want the floor tier's cheap lighting at
 * full resolution, because text and instrument edges read better sharp than
 * they do lit, and that is their call to make rather than ours to bundle.
 *
 * `null` means "follow the tier", which is the old behaviour and stays the
 * default. Anything else is an explicit multiplier of CSS pixels. */
export const RESOLUTION_KEY = 'lem.world.resolution';

/* Fractions of the DISPLAY'S OWN RESOLUTION, not of CSS pixels.
 *
 * The first version of this got it wrong in a way worth recording, because it
 * is the same mistake in a new place: `scale` multiplied CSS pixels, so "Full
 * (1:1)" meant one rendered pixel per CSS pixel. On a Retina panel there are
 * TWO device pixels per CSS pixel, so "Full" was rendering a quarter of the
 * screen's pixels and only "Maximum" ever reached native. Ryan: "I have floor
 * on and quality full and it's still doing it... are you sure you aren't just
 * doing 100% of 36%?" — he was right, and the label was lying.
 *
 * These are now multiples of `devicePixelRatio`, so 1.00 is genuinely native on
 * whatever panel it runs on: 1280x800 on a 1x display, 2560x1600 on a 2x one. */
export const RESOLUTIONS = [
  {name: 'auto',  scale: null, label: 'Match quality'},
  {name: 'full',  scale: 1.00, label: 'Full — native'},
  {name: 'sharp', scale: 1.25, label: 'Sharp — 1.25x native, supersampled'},
  {name: 'max',   scale: 1.50, label: 'Maximum — 1.5x native'},
  /* Beyond native, and deliberately labelled as a test setting rather than a
   * quality one. This machine is far faster than the bench PCs the floor
   * actually runs on, so a fault that shows there can be invisible here — the
   * honest way to find it is to make this machine do more work than theirs, not
   * to conclude from a fast box that nothing is wrong. */
  {name: 'x2',    scale: 2.00, label: '2x native — stress test'},
  {name: 'x3',    scale: 3.00, label: '3x native — heavy stress test'},
  {name: 'x4',    scale: 4.00, label: '4x native — extreme, expect a slideshow'},
  {name: 'half',  scale: 0.70, label: 'Half'},
  {name: 'quart', scale: 0.50, label: 'Quarter'},
];

/* A ceiling on the backing store, in pixels.
 *
 * This pipeline allocates several full-size render targets (scene, AO, bloom,
 * depth), so the real memory cost is a multiple of this. The cap exists so a
 * mistyped setting cannot take the floor down with a context loss — losing the
 * map is a worse outcome than a soft frame. Raised to admit the stress tiers on
 * a normal panel: 4x native on a 1440x900 canvas is 33 Mpx, which a desktop GPU
 * will survive and a bench PC will not, which is exactly the point of it. */
const MAX_BACKING_PIXELS = 40e6;

/* Frame cadence. See the note in `start()`'s loop for why a slower steady rate
 * beats a faster uneven one. `null` is uncapped, which is right on a machine
 * with headroom and wrong on one without. */
export const FRAMECAP_KEY = 'lem.world.framecap';

export const FRAMECAPS = [
  {name: 'off', fps: null, label: 'Uncapped'},
  {name: '60',  fps: 60,   label: '60 fps — steady'},
  {name: '30',  fps: 30,   label: '30 fps — for a weak machine'},
];

export function storedFrameCap() {
  try {
    const v = localStorage.getItem(FRAMECAP_KEY);
    return FRAMECAPS.find(f => f.name === v) ? v : null;
  } catch (_) { return null; }
}

export function storeFrameCap(name) {
  try {
    if (!name || name === 'off') localStorage.removeItem(FRAMECAP_KEY);
    else localStorage.setItem(FRAMECAP_KEY, name);
  } catch (_) { /* as storeQuality */ }
}

export function storedResolution() {
  try {
    const v = localStorage.getItem(RESOLUTION_KEY);
    if (!v || v === 'auto') return null;
    return RESOLUTIONS.find(r => r.name === v) ? v : null;
  } catch (_) { return null; }
}

export function storeResolution(name) {
  try {
    if (!name || name === 'auto') localStorage.removeItem(RESOLUTION_KEY);
    else localStorage.setItem(RESOLUTION_KEY, name);
  } catch (_) { /* same reasoning as storeQuality */ }
}

const FULLSCREEN_VS = /* glsl */`
  out vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }`;

/* Horizon-based ambient occlusion at half resolution, reconstructing normals
 * from depth so there is no normal buffer and no extra geometry pass beyond
 * the depth prepass we already need for early-Z. */
const AO_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tDepth;
  uniform mat4 uProjInv;
  uniform mat4 uProj;
  uniform vec2 uRes;
  uniform float uRadius, uIntensity, uBias, uNear, uFar, uTime;
  layout(location = 0) out vec4 outColor;

  vec3 viewPos(vec2 uv, float d) {
    vec4 clip = vec4(uv * 2.0 - 1.0, d * 2.0 - 1.0, 1.0);
    vec4 v = uProjInv * clip;
    return v.xyz / v.w;
  }
  float depthAt(vec2 uv) { return texture(tDepth, uv).x; }

  void main() {
    float d = depthAt(vUv);
    if (d >= 1.0) { outColor = vec4(1.0); return; }   // sky occludes nothing
    vec3 p = viewPos(vUv, d);
    vec2 texel = 1.0 / uRes;
    vec3 pr = viewPos(vUv + vec2(texel.x, 0.0), depthAt(vUv + vec2(texel.x, 0.0)));
    vec3 pu = viewPos(vUv + vec2(0.0, texel.y), depthAt(vUv + vec2(0.0, texel.y)));
    vec3 n = normalize(cross(pr - p, pu - p));

    /* An interleaved rotation per 4x4 block; the bilateral blur that follows
     * turns the resulting dither into a smooth field. */
    float ang = mod(dot(floor(vUv * uRes), vec2(0.06711056, 0.00583715)), 1.0)
                * 6.2831853;
    float ca = cos(ang), sa = sin(ang);

    const int TAPS = 8;
    float occ = 0.0;
    for (int i = 0; i < TAPS; i++) {
      float fi = float(i);
      float a = fi * 2.3999632 + ang;                  // golden-angle spiral
      float r = sqrt((fi + 0.5) / float(TAPS)) * uRadius;
      vec3 dir = vec3(cos(a), sin(a), 0.0);
      vec3 s = p + (dir * r) + n * uBias;
      vec4 sc = uProj * vec4(s, 1.0);
      vec2 suv = (sc.xy / sc.w) * 0.5 + 0.5;
      if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0) continue;
      vec3 sp = viewPos(suv, depthAt(suv));
      vec3 diff = sp - p;
      float dist = length(diff);
      if (dist < 0.0001) continue;
      float ndl = max(dot(n, diff / dist), 0.0);
      /* Range check keeps a distant wall from occluding a near surface. */
      occ += ndl * (uRadius / (uRadius + dist)) *
             smoothstep(uRadius * 2.5, uRadius * 0.35, dist);
    }
    float ao = 1.0 - (occ / float(TAPS)) * uIntensity;
    outColor = vec4(clamp(ao, 0.0, 1.0), d, 0.0, 1.0);
    ca; sa; uTime;
  }`;

/* Depth-aware blur, run twice (x then y). Blurring across a silhouette is what
 * makes screen-space AO look like a smudge instead of contact shadow. */
const BLUR_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tAO;
  uniform vec2 uDir, uRes;
  layout(location = 0) out vec4 outColor;
  void main() {
    vec2 texel = uDir / uRes;
    vec2 c = texture(tAO, vUv).xy;
    float sum = c.x, wsum = 1.0;
    for (int i = 1; i <= 3; i++) {
      float fi = float(i);
      float w = exp(-0.5 * (fi * fi) / 2.25);
      for (float s = -1.0; s <= 1.0; s += 2.0) {
        vec2 uv = vUv + texel * fi * s;
        vec2 t = texture(tAO, uv).xy;
        float dw = exp(-abs(t.y - c.y) * 900.0);      // stop at depth edges
        sum += t.x * w * dw; wsum += w * dw;
      }
    }
    outColor = vec4(sum / wsum, c.y, 0.0, 1.0);
  }`;

/* Bloom: threshold, then a small separable blur chain. Cheap, and it is what
 * sells wet asphalt under a sodium lamp and a locomotive headlight at dusk. */
const BRIGHT_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tSrc;
  uniform float uThreshold, uSoft;
  layout(location = 0) out vec4 outColor;
  void main() {
    vec3 c = texture(tSrc, vUv).rgb;
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float k = smoothstep(uThreshold, uThreshold + uSoft, l);
    outColor = vec4(c * k, 1.0);
  }`;

const BLOOM_BLUR_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tSrc;
  uniform vec2 uDir, uRes;
  layout(location = 0) out vec4 outColor;
  void main() {
    vec2 texel = uDir / uRes;
    vec3 sum = texture(tSrc, vUv).rgb * 0.227027;
    const float o[3] = float[3](1.384615, 3.230769, 5.0);
    const float w[3] = float[3](0.316216, 0.070270, 0.008081);
    for (int i = 0; i < 3; i++) {
      sum += texture(tSrc, vUv + texel * o[i]).rgb * w[i];
      sum += texture(tSrc, vUv - texel * o[i]).rgb * w[i];
    }
    outColor = vec4(sum, 1.0);
  }`;

/* The composite: tone map, grade, vignette, and the anti-aliasing. Everything
 * before this point is linear HDR; this is the only place colour is decided. */
const COMPOSITE_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tScene, tBloom, tAO;
  uniform vec2 uRes;
  uniform float uExposure, uBloom, uVignette, uSaturation, uContrast;
  uniform float uAOStrength, uFilmGrain, uTime;
  uniform float uBlackPoint, uWhitePoint, uToe;
  uniform vec3 uLift, uGain;
  uniform int uHasBloom, uHasAO;
  layout(location = 0) out vec4 outColor;

  vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
  }
  float luma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

  /* The sRGB transfer function, applied here and nowhere else.
   *
   * This was missing, and it was the most consequential bug in the renderer.
   * three.js only inserts its colour-space conversion into its OWN shader
   * chunks; a hand-authored ShaderMaterial like every pass in this file gets
   * nothing, so setting renderer.outputColorSpace did exactly nothing for the
   * image on screen. Tone-mapped linear values went straight to a canvas that
   * treats them as encoded, which reads as roughly a stop and a half dark
   * through the midtones.
   *
   * It was invisible because everything downstream compensated: the world was
   * lit with about three times the physically sensible ambient, which is why
   * shadows would not read no matter how the sun was set. Found by measurement
   * — writing a known 0.5 returned byte 128 where an encoded pipeline returns
   * 188 — not by looking.
   *
   * It goes at the END of the composite rather than after the anti-aliasing on
   * purpose: FXAA is a luma-threshold filter designed to run on perceptual
   * values, so it wants the encoded image, not the linear one. */
  vec3 encodeSRGB(vec3 c) {
    return mix(c * 12.92,
               1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055,
               step(vec3(0.0031308), c));
  }

  void main() {
    vec3 c = texture(tScene, vUv).rgb;
    if (uHasAO == 1) {
      /* AO is already folded into indirect light by the material patch; this
       * is the small remaining darkening of creases the lighting cannot see. */
      float ao = texture(tAO, vUv).x;
      c *= mix(1.0, ao, uAOStrength);
    }
    if (uHasBloom == 1) c += texture(tBloom, vUv).rgb * uBloom;
    c *= uExposure;
    c = aces(c);
    /* Black and white point.
     *
     * Measured against the references, the image was soft at both ends: our
     * darkest tone sat at p1 23-38 where After the Flood and Transport Fever 2
     * reach 1-15, and our brightest at p95 227 against their 204-208. So the
     * frame had no true black and was burning out at the top — which reads as
     * haze laid over the picture rather than depth inside it, and it is what
     * four blind critics all described as washed out.
     *
     * This is a black/white point remap, not a contrast curve: it re-anchors
     * the ends and leaves the middle where the tone mapper put it. The toe
     * term softens the very bottom so shadows still hold detail instead of
     * clamping to a flat block — crushed black is its own kind of cheap.
     *
     * (No backticks in here: this shader lives inside a JS template literal,
     * and one in a comment ends the literal and takes the module with it.) */
    c = (c - uBlackPoint) / max(1e-4, uWhitePoint - uBlackPoint);
    c = max(c, vec3(0.0));
    c = c * (c + uToe) / (c + uToe * 0.5 + 1e-4) * (1.0 + uToe * 0.5);
    c = clamp(c, 0.0, 1.0);
    c = mix(vec3(luma(c)), c, uSaturation);
    c = (c - 0.5) * uContrast + 0.5;
    c = uLift + c * uGain;
    float d = distance(vUv, vec2(0.5));
    c *= 1.0 - uVignette * smoothstep(0.35, 0.95, d);
    /* A trace of grain. Digital-clean skies band on an 8-bit panel; this is
     * cheaper and more honest than a dither LUT. */
    float g = fract(sin(dot(vUv * uRes + uTime, vec2(12.9898, 78.233))) * 43758.5453);
    c = encodeSRGB(clamp(c, 0.0, 1.0));
    /* Grain and dither AFTER encoding, so a ~110-value sky gradient stops
     * banding on an 8-bit panel — which is one of the artefacts the critics
     * saw. In linear space the same amount of noise is invisible in the darks
     * and obvious in the brights. */
    c += (g - 0.5) * uFilmGrain;
    outColor = vec4(clamp(c, 0.0, 1.0), 1.0);
  }`;

/* FXAA. MSAA on a multi-pass HDR pipeline costs bandwidth we do not have on an
 * integrated part; this costs one pass and holds up at 1080p. */
const FXAA_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tSrc;
  uniform vec2 uRes;
  layout(location = 0) out vec4 outColor;
  float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
  void main() {
    vec2 texel = 1.0 / uRes;
    vec3 rgbM = texture(tSrc, vUv).rgb;
    float lM  = luma(rgbM);
    float lNW = luma(texture(tSrc, vUv + vec2(-1.0, -1.0) * texel).rgb);
    float lNE = luma(texture(tSrc, vUv + vec2( 1.0, -1.0) * texel).rgb);
    float lSW = luma(texture(tSrc, vUv + vec2(-1.0,  1.0) * texel).rgb);
    float lSE = luma(texture(tSrc, vUv + vec2( 1.0,  1.0) * texel).rgb);
    float lMin = min(lM, min(min(lNW, lNE), min(lSW, lSE)));
    float lMax = max(lM, max(max(lNW, lNE), max(lSW, lSE)));
    if (lMax - lMin < max(0.0312, lMax * 0.125)) { outColor = vec4(rgbM, 1.0); return; }
    vec2 dir = vec2(-((lNW + lNE) - (lSW + lSE)), ((lNW + lSW) - (lNE + lSE)));
    float reduce = max((lNW + lNE + lSW + lSE) * 0.03125, 0.0078125);
    float rcp = 1.0 / (min(abs(dir.x), abs(dir.y)) + reduce);
    dir = clamp(dir * rcp, -8.0, 8.0) * texel;
    vec3 a = 0.5 * (texture(tSrc, vUv + dir * (1.0 / 3.0 - 0.5)).rgb +
                    texture(tSrc, vUv + dir * (2.0 / 3.0 - 0.5)).rgb);
    vec3 b = a * 0.5 + 0.25 * (texture(tSrc, vUv + dir * -0.5).rgb +
                               texture(tSrc, vUv + dir *  0.5).rgb);
    float lB = luma(b);
    outColor = vec4((lB < lMin || lB > lMax) ? a : b, 1.0);
  }`;

function fullscreen(fragmentShader, uniforms) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(
    new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(
    new Float32Array([0, 0, 2, 0, 0, 2]), 2));
  const mat = new THREE.ShaderMaterial({
    vertexShader: FULLSCREEN_VS, fragmentShader, uniforms,
    depthTest: false, depthWrite: false, glslVersion: THREE.GLSL3,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.frustumCulled = false;
  return mesh;
}

export class Engine {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.clock = new THREE.Clock();
    this.time = 0;
    this.frame = 0;

    const gl2 = canvas.getContext('webgl2', {antialias: false, alpha: false,
                                             powerPreference: 'high-performance',
                                             stencil: false, depth: true});
    this.webgl2 = !!gl2;
    this.renderer = new THREE.WebGLRenderer({
      canvas, context: gl2 || undefined, antialias: !gl2,
      alpha: false, stencil: false, powerPreference: 'high-performance',
    });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.NoToneMapping;   // the composite owns it
    this.renderer.shadowMap.enabled = this.tier ? this.tier.shadows !== false : true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.shadowMap.autoUpdate = false;        // we drive it (see below)
    this.renderer.info.autoReset = false;

    this.scene = new THREE.Scene();
    /* The far plane has to outrun the content or extending the forest just
     * moves the boundary from "no trees" to "trees sliced off". Measured: the
     * land runs to 5900m, so 6800 clears it with room for the sky dome. The
     * near plane stays tight because depth precision is what keeps shadow acne
     * and z-fighting off the track at close range. */
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.6, 6800);
    this.camera.position.set(210, 120, 210);

    /* Two scene passes need a material override: the depth prepass, and the
     * shadow map. Both are set up once and reused. */
    this.depthMaterial = new THREE.MeshDepthMaterial();

    this.maxPixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    /* Null follows the tier; a number is the operator's own choice and is
     * obeyed at every tier, including floor. See RESOLUTION_KEY. */
    const pinnedCap = storedFrameCap();
    this.frameCapMode = pinnedCap || 'off';
    this.frameCap = pinnedCap
      ? (1000 / FRAMECAPS.find(f => f.name === pinnedCap).fps) : null;
    this._lastDrawn = 0;
    const pinnedRes = storedResolution();
    this.resolutionMode = pinnedRes || 'auto';
    this.renderScale = pinnedRes
      ? (RESOLUTIONS.find(r => r.name === pinnedRes)?.scale ?? null) : null;
    /* A stored preference is a decision, not a hint: if the operator has
     * picked a tier, the adaptive ladder stays out of the way entirely. It is
     * their screen and they have already told us what it can take. */
    const pinned = storedQuality();
    this.qualityMode = pinned || 'auto';
    /* Auto-detection probes from the BOTTOM up.
     *
     * Ryan: "when auto detecting graphics, go low to high please, the high to
     * low is almost crashing some systems lol." He is right, and starting at
     * ultra was indefensible: the very first frames a machine is asked to draw
     * were the heaviest it would ever see — full resolution, a 2048 shadow map,
     * ambient occlusion and bloom — before anything had measured whether it
     * could. A weak GPU meets its worst case cold, and a driver that gives up
     * there takes the tab with it.
     *
     * Starting at the floor tier costs a few seconds of a softer picture on a
     * fast machine and costs a slow one nothing at all. The climb below is
     * deliberately quicker than the descent for the same reason the descent
     * used to be quicker than the climb: you want to spend as little time as
     * possible on the wrong side of the answer. */
    this.tierIndex = pinned
      ? TIERS.findIndex(t => t.name === pinned)
      : (opts.tier ?? TIERS.length - 1);
    this.autoQuality = !pinned && opts.autoQuality !== false;
    this.probing = this.autoQuality;      // climbing, has not yet overshot
    this.tier = TIERS[this.tierIndex];
    this._origRoughness = new WeakMap();

    this._targets = {};
    this._passes = {};
    this._buildPasses();

    /* The frame budget, measured over a rolling window. `slow` and `fast` are
     * deliberately far apart: a ladder that steps on every wobble spends its
     * life rebuilding shadow maps. */
    this.samples = [];
    this.fps = 0;
    this.gpuMs = 0;
    this._sinceStep = 0;

    this.updaters = [];
    this.running = false;
    this._onResize = () => this.resize();
    window.addEventListener('resize', this._onResize);
  }

  /* ---- render targets and passes ---------------------------------------- */

  _rt(w, h, opts = {}) {
    const rt = new THREE.WebGLRenderTarget(Math.max(2, w | 0), Math.max(2, h | 0), {
      type: opts.float === false ? THREE.UnsignedByteType : THREE.HalfFloatType,
      minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter,
      depthBuffer: !!opts.depth, stencilBuffer: false,
      colorSpace: THREE.LinearSRGBColorSpace,
    });
    if (opts.depthTexture) {
      rt.depthTexture = new THREE.DepthTexture(rt.width, rt.height);
      rt.depthTexture.type = THREE.UnsignedIntType;
      rt.depthTexture.minFilter = THREE.NearestFilter;
      rt.depthTexture.magFilter = THREE.NearestFilter;
    }
    return rt;
  }

  _buildPasses() {
    const u = THREE.UniformsUtils;
    void u;
    this._passes.ao = fullscreen(AO_FS, {
      tDepth: {value: null}, uProjInv: {value: new THREE.Matrix4()},
      uProj: {value: new THREE.Matrix4()}, uRes: {value: new THREE.Vector2()},
      uRadius: {value: 1.35}, uIntensity: {value: 1.15}, uBias: {value: 0.035},
      uNear: {value: 0.1}, uFar: {value: 1000}, uTime: {value: 0},
    });
    this._passes.blur = fullscreen(BLUR_FS, {
      tAO: {value: null}, uDir: {value: new THREE.Vector2(1, 0)},
      uRes: {value: new THREE.Vector2()},
    });
    this._passes.bright = fullscreen(BRIGHT_FS, {
      tSrc: {value: null}, uThreshold: {value: 1.05}, uSoft: {value: 0.65},
    });
    this._passes.bloomBlur = fullscreen(BLOOM_BLUR_FS, {
      tSrc: {value: null}, uDir: {value: new THREE.Vector2(1, 0)},
      uRes: {value: new THREE.Vector2()},
    });
    this._passes.composite = fullscreen(COMPOSITE_FS, {
      tScene: {value: null}, tBloom: {value: null}, tAO: {value: null},
      uRes: {value: new THREE.Vector2()},
      uExposure: {value: 2.00}, uBloom: {value: 0.55}, uVignette: {value: 0.34},
      uSaturation: {value: 1.06}, uContrast: {value: 1.04},
      uAOStrength: {value: 0.55}, uFilmGrain: {value: 0.012}, uTime: {value: 0},
      /* Tuned by measurement, not by eye — see `harness/grade.py`. The targets
       * are the reference set's own numbers: p1 in the 1-15 range, p95 around
       * 204-208. Raising uBlackPoint pulls the floor down; lowering
       * uWhitePoint brings the ceiling in off the clip. */
      uBlackPoint: {value: 0.035}, uWhitePoint: {value: 1.18},
      uToe: {value: 0.06},
      uLift: {value: new THREE.Vector3(0.002, 0.003, 0.006)},
      uGain: {value: new THREE.Vector3(1.0, 0.995, 0.985)},
      uHasBloom: {value: 1}, uHasAO: {value: 1},
    });
    this._passes.fxaa = fullscreen(FXAA_FS, {
      tSrc: {value: null}, uRes: {value: new THREE.Vector2()},
    });
    this._quad = new THREE.Scene();
    this._quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  }

  _blit(pass, target) {
    this._quad.clear();
    this._quad.add(pass);
    this.renderer.setRenderTarget(target || null);
    this.renderer.render(this._quad, this._quadCam);
  }

  /* ---- sizing ------------------------------------------------------------ */

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const cssW = Math.max(1, rect.width | 0), cssH = Math.max(1, rect.height | 0);
    this.cssWidth = cssW; this.cssHeight = cssH;
    /* Resolution is the operator's choice when they have made one, and follows
     * the tier only when they have not.
     *
     * `renderScale` is a fraction of the DISPLAY's resolution, so it is
     * multiplied by devicePixelRatio: 1.0 is native on any panel. Getting this
     * wrong is what made "Full" a quarter of the pixels on a Retina screen. */
    const panel = window.devicePixelRatio || 1;
    let dpr = this.renderScale != null
      ? this.renderScale * panel
      : Math.min(this.maxPixelRatio, 1.5) * this.tier.scale;
    /* Clamp by total pixels rather than by ratio, because what actually runs
     * out is memory and fill rate, and both scale with the area. */
    const want = cssW * cssH * dpr * dpr;
    if (want > MAX_BACKING_PIXELS) dpr *= Math.sqrt(MAX_BACKING_PIXELS / want);
    const w = Math.max(2, Math.round(cssW * dpr));
    const h = Math.max(2, Math.round(cssH * dpr));
    if (this.width === w && this.height === h) return;
    this.width = w; this.height = h;

    this.camera.aspect = cssW / cssH;
    this.camera.updateProjectionMatrix();
    this.renderer.setPixelRatio(1);
    this.renderer.setSize(w, h, false);
    this.canvas.style.width = '100%';
    this.canvas.style.height = '100%';

    const T = this._targets;
    for (const key in T) T[key]?.dispose?.();
    const aoS = this.tier.aoScale;
    T.scene = this._rt(w, h, {depth: true, depthTexture: true});
    T.ao = this._rt(w * aoS, h * aoS, {float: false});
    T.aoBlur = this._rt(w * aoS, h * aoS, {float: false});
    T.bright = this._rt(w * 0.5, h * 0.5);
    T.bloomA = this._rt(w * 0.25, h * 0.25);
    T.bloomB = this._rt(w * 0.25, h * 0.25);
    T.ldr = this._rt(w, h, {float: false});

    this._passes.ao.material.uniforms.uRes.value.set(w * aoS, h * aoS);
    this._passes.blur.material.uniforms.uRes.value.set(w * aoS, h * aoS);
    this._passes.bloomBlur.material.uniforms.uRes.value.set(w * 0.25, h * 0.25);
    this._passes.composite.material.uniforms.uRes.value.set(w, h);
    this._passes.fxaa.material.uniforms.uRes.value.set(w, h);
    this.aoTexture = T.aoBlur.texture;
  }

  /* ---- the quality ladder ------------------------------------------------ */

  setTier(index, {force = false} = {}) {
    const next = Math.max(0, Math.min(TIERS.length - 1, index));
    if (next === this.tierIndex && !force) return false;
    this.tierIndex = next;
    this.tier = TIERS[next];
    this.width = this.height = 0;             // force a resize/retarget
    this.resize();
    /* Shadows off entirely is the single largest lighting saving available, and
     * it is what buys the forest at the floor tier. */
    this.renderer.shadowMap.enabled = this.tier.shadows !== false;
    this.applyMaterialQuality();
    this.shadowNeedsUpdate = true;
    this.updaters.forEach(m => m.onQuality?.(this.tier));
    return true;
  }

  /** The operator's own choice: a tier name, or 'auto' to hand it back to the
   *  ladder. Remembered locally so it survives a reload. */
  setQualityMode(mode) {
    const idx = TIERS.findIndex(t => t.name === mode);
    if (mode !== 'auto' && idx < 0) return false;
    this.qualityMode = mode;
    storeQuality(mode);
    if (mode === 'auto') {
      this.autoQuality = true;
      this.samples.length = 0;
    } else {
      this.autoQuality = false;
      this.setTier(idx, {force: true});
    }
    return true;
  }

  /** Render resolution, independently of the tier.
   *
   *  `'auto'` restores the old coupling (the tier decides). Any other name from
   *  RESOLUTIONS pins a multiplier of CSS pixels that every tier obeys — so the
   *  floor tier at 'full' is cheap lighting at a sharp 1:1, which is a
   *  combination the old code could not express and which is exactly what an
   *  operator on a weak bench PC is likely to want.
   *
   *  The resize path reads `renderScale` and reallocates every render target,
   *  so this takes effect on the next frame with no reload. */
  /** Hold a steady frame cadence instead of an uneven fast one. */
  setFrameCap(mode) {
    const spec = FRAMECAPS.find(f => f.name === mode);
    if (!spec) return false;
    this.frameCapMode = mode;
    this.frameCap = spec.fps ? 1000 / spec.fps : null;
    this._lastDrawn = 0;
    storeFrameCap(mode);
    return true;
  }

  setResolutionMode(mode) {
    const spec = RESOLUTIONS.find(r => r.name === mode);
    if (!spec) return false;
    this.resolutionMode = mode;
    this.renderScale = spec.scale;
    storeResolution(mode);
    /* `resize()` early-returns when the pixel size is unchanged, so clear the
     * cached size to force the reallocation through. */
    this.width = this.height = 0;
    this.resize();
    this.shadowNeedsUpdate = true;
    return true;
  }

  /** What the frame is actually being rendered at.
   *
   *  `pct` is measured against the DISPLAY's pixels, not against CSS pixels —
   *  a percentage of CSS pixels reads as 100% on a Retina panel that is in fact
   *  drawing a quarter of them, which is precisely how the last version of this
   *  control managed to be wrong while reporting itself correct. */
  resolutionInfo() {
    const cssW = this.cssWidth || 1, cssH = this.cssHeight || 1;
    const panel = window.devicePixelRatio || 1;
    const devW = Math.round(cssW * panel), devH = Math.round(cssH * panel);
    const pct = Math.round(100 * (this.width * this.height) / (devW * devH));
    return {mode: this.resolutionMode, scale: this.renderScale,
            w: this.width, h: this.height, cssW, cssH, devW, devH, panel, pct};
  }

  /** Material-level quality: the levers that cost the least to look at.
   *
   *  Roughness maps are stripped rather than regenerated, and the originals are
   *  kept per material so raising the tier puts them back. Dropping one costs a
   *  little sheen; thinning the forest costs the world. */
  applyMaterialQuality() {
    const want = this.tier.roughnessMaps;
    this.scene.traverse(o => {
      const mats = o.material
        ? (Array.isArray(o.material) ? o.material : [o.material]) : null;
      if (!mats) return;
      for (const m of mats) {
        if (!m) continue;
        if (!this._origRoughness.has(m) && m.roughnessMap) {
          this._origRoughness.set(m, m.roughnessMap);
        }
        const orig = this._origRoughness.get(m);
        if (!orig) continue;
        const next = want ? orig : null;
        if (m.roughnessMap !== next) {
          m.roughnessMap = next;
          m.needsUpdate = true;
        }
      }
    });
  }

  _judgeFrame(ms) {
    if (!this.autoQuality) return;
    this.samples.push(ms);
    if (this.samples.length < 45) return;
    const sorted = [...this.samples].sort((a, b) => a - b);
    const p80 = sorted[Math.floor(sorted.length * 0.8)];
    this.samples.length = 0;
    this._sinceStep++;
    /* 16.6ms is the target. Step down promptly, step back up only after the
     * frame has been comfortably clear for a while — a floor display that
     * oscillates between two tiers looks broken in a way that being one tier
     * low never does. */
    if (p80 > 19.5 && this.tierIndex < TIERS.length - 1) {
      /* Overshot. Stop climbing eagerly from here on — the machine has now
       * told us where its ceiling is, and hunting around it looks broken. */
      this.probing = false;
      this.setTier(this.tierIndex + 1); this._sinceStep = 0;
    } else if (p80 < 10.5 && this.tierIndex > 0 &&
               this._sinceStep > (this.probing ? 1 : 8)) {
      this.setTier(this.tierIndex - 1); this._sinceStep = 0;
    }
  }

  /* ---- the frame --------------------------------------------------------- */

  add(module) { this.updaters.push(module); return module; }

  start() {
    if (this.running) return;
    this.running = true;
    this.clock.start();
    const loop = () => {
      if (!this.running) return;
      this._raf = requestAnimationFrame(loop);
      /* Frame cadence.
       *
       * A frame that costs just over one refresh interval does not run
       * uniformly slower — the compositor alternates between presenting in one
       * interval and two, and the motion visibly hitches while the average
       * frame rate still looks fine. Measured on this machine at 2x native:
       * 54.8% of frames landed in one interval and 41.4% in two. That is vsync
       * beating, and it is what Ryan sees on Chrome where Safari's presentation
       * pacing hides it.
       *
       * Holding a slower but CONSISTENT cadence is the fix: a steady 60 reads
       * as smooth where an alternating 120/60 does not. `frameCap` is the
       * minimum interval between rendered frames; rAF still runs, we simply
       * decline to draw until the budget has elapsed. Deliberately a floor of
       * `cap - 1ms`, so a frame that arrives a hair early is not pushed into
       * the next interval and made to stutter by the very thing meant to fix
       * it. */
      if (this.frameCap) {
        const now = performance.now();
        if (now - this._lastDrawn < this.frameCap - 1) return;
        this._lastDrawn = now;
      }
      this.renderFrame();
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    this.running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
  }

  renderFrame() {
    const t0 = performance.now();
    const dt = Math.min(0.1, this.clock.getDelta());
    this.time += dt;
    this.frame++;
    if (!this.width) this.resize();

    for (const m of this.updaters) m.update?.(dt, this.time);

    this.renderer.info.reset();

    /* The shadow map is redrawn only when something asks for it — the sun
     * moving, the weather turning, geometry appearing. On a still frame it is
     * pure waste, and on integrated graphics it is the single most expensive
     * thing in the frame. */
    if (this.shadowNeedsUpdate) {
      this.renderer.shadowMap.needsUpdate = true;
      this.shadowNeedsUpdate = false;
    }

    const T = this._targets;
    const P = this._passes;

    /* 1. Beauty pass, straight into an HDR target with a depth texture. The
     *    AO below reads that depth; there is no separate prepass because the
     *    depth texture from this pass is the same data one frame earlier in
     *    the pipeline than the composite needs it. */
    this.renderer.setRenderTarget(T.scene);
    this.renderer.clear(true, true, false);
    this.renderer.render(this.scene, this.camera);

    /* 2. Ambient occlusion, half res, then a depth-aware blur in x and y. */
    if (this.tier.ao) {
      P.ao.material.uniforms.tDepth.value = T.scene.depthTexture;
      P.ao.material.uniforms.uProj.value.copy(this.camera.projectionMatrix);
      P.ao.material.uniforms.uProjInv.value.copy(this.camera.projectionMatrixInverse);
      P.ao.material.uniforms.uNear.value = this.camera.near;
      P.ao.material.uniforms.uFar.value = this.camera.far;
      P.ao.material.uniforms.uTime.value = this.time;
      this._blit(P.ao, T.ao);
      P.blur.material.uniforms.tAO.value = T.ao.texture;
      P.blur.material.uniforms.uDir.value.set(1, 0);
      this._blit(P.blur, T.aoBlur);
      P.blur.material.uniforms.tAO.value = T.aoBlur.texture;
      P.blur.material.uniforms.uDir.value.set(0, 1);
      this._blit(P.blur, T.ao);
      this.aoTexture = T.ao.texture;
    }

    /* 3. Bloom: threshold at half res, blur at quarter. */
    if (this.tier.bloom) {
      P.bright.material.uniforms.tSrc.value = T.scene.texture;
      this._blit(P.bright, T.bright);
      P.bloomBlur.material.uniforms.tSrc.value = T.bright.texture;
      P.bloomBlur.material.uniforms.uDir.value.set(1, 0);
      this._blit(P.bloomBlur, T.bloomA);
      P.bloomBlur.material.uniforms.tSrc.value = T.bloomA.texture;
      P.bloomBlur.material.uniforms.uDir.value.set(0, 1);
      this._blit(P.bloomBlur, T.bloomB);
      P.bloomBlur.material.uniforms.tSrc.value = T.bloomB.texture;
      P.bloomBlur.material.uniforms.uDir.value.set(1.6, 0);
      this._blit(P.bloomBlur, T.bloomA);
      P.bloomBlur.material.uniforms.tSrc.value = T.bloomA.texture;
      P.bloomBlur.material.uniforms.uDir.value.set(0, 1.6);
      this._blit(P.bloomBlur, T.bloomB);
    }

    /* 4. Composite to LDR, then FXAA to the canvas. */
    const cu = P.composite.material.uniforms;
    cu.tScene.value = T.scene.texture;
    cu.tBloom.value = T.bloomB.texture;
    cu.tAO.value = this.aoTexture;
    cu.uHasBloom.value = this.tier.bloom ? 1 : 0;
    cu.uHasAO.value = this.tier.ao ? 1 : 0;
    cu.uTime.value = this.time;
    this._blit(P.composite, T.ldr);
    P.fxaa.material.uniforms.tSrc.value = T.ldr.texture;
    this._blit(P.fxaa, null);

    const info = this.renderer.info.render;
    this.drawCalls = info.calls;
    this.triangles = info.triangles;
    const ms = performance.now() - t0;
    this.cpuMs = ms;
    this.fps = this.fps ? this.fps * 0.9 + (1 / Math.max(dt, 0.0001)) * 0.1
                        : 1 / Math.max(dt, 0.0001);
    this._judgeFrame(dt * 1000);
  }

  dispose() {
    this.stop();
    window.removeEventListener('resize', this._onResize);
    for (const key in this._targets) this._targets[key]?.dispose?.();
    this.updaters.forEach(m => m.dispose?.());
    this.renderer.dispose();
  }
}
