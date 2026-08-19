/* sky.js — the atmosphere the whole world is lit by.
 *
 * Everything else on the floor borrows its colour from here: the terrain, the
 * station roofs, the tank cars, the rails. So this file owns four things that
 * have to agree with each other or the render reads as fake:
 *
 *   1. The sky itself — Rayleigh + Mie single scattering with ozone absorption,
 *      integrated properly, so dawn, noon, dusk and night are the *same* model
 *      at different sun elevations rather than six hand-picked gradients. The
 *      earth's shadow falls out of the light-ray march, which is what gives the
 *      twilight wedge and the band that sits on the horizon after sunset.
 *   2. The sun, the moon and the stars, drawn at full resolution on top.
 *   3. Cloud cover, raymarched through a slab against the planet sphere and lit
 *      by the same sun colour the ground gets. Overcast and storm are these
 *      clouds with the coverage threshold walked down.
 *   4. The light the rest of the world is given: `sunDirection`, `sunColour`,
 *      `sunIntensity`, `ambientColour`, `isNight` for gi.js, `scene.fog` for
 *      every material, and a PMREM environment map for image-based lighting.
 *
 * The one expensive thing — the scattering integral — runs into a 256x128
 * equirectangular LUT, and only when the sun or the weather actually moved.
 * What runs per pixel per frame is a texture fetch, a sun disc and the clouds.
 * That is the whole reason a physical sky fits in a background's share of the
 * frame.
 *
 * Grey fog under a golden sunset is the single most common tell of a fake sky,
 * so the fog colour is not a constant: it is this model evaluated on the CPU
 * around the horizon, in the same units the shader works in, every time
 * anything changes. The JS integral below is a deliberate transcription of the
 * GLSL one, and the constants they share are generated from one table — if you
 * change one, change both, and the constants take care of themselves.
 */
import * as THREE from 'three';
import * as TexNS from 'world/textures.js';

/* textures.js exports its helpers twice — as named exports and as a `Tex`
 * bundle — and `makeTexture` only appears on the bundle. Merging both is the
 * only way to reach all of them without depending on which spelling survives. */
const Tex = Object.assign({}, TexNS.Tex || {}, TexNS);

const DEG = Math.PI / 180;
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const smoothstep = (e0, e1, x) => {
  const t = clamp((x - e0) / (e1 - e0 || 1e-6), 0, 1);
  return t * t * (3 - 2 * t);
};
const mix = (a, b, t) => a + (b - a) * t;
const luma = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
/* The sky's shoulder, in one place because the dome applies it in GLSL and the
 * fog colour has to be put through the identical curve in JS. */
const SKY_KNEE = 0.62;
const SKY_CEIL = 1.45;

/* ---- the atmosphere ------------------------------------------------------ */

/* Standard earth numbers, in metres.
 *
 * Ozone is not decoration. Without it the noon zenith is a flat cyan and every
 * twilight is red, because the only thing left filtering a long path is
 * Rayleigh. Ozone absorbs in the green and the orange and barely at all in the
 * blue, so it is the reason the zenith is a deep blue at noon and the reason
 * the sky above a set sun goes blue rather than brown. It is one extra tent
 * profile and it changes more about the look than anything else here.
 *
 * `eye` is how high the observer sits: the horizon haze is an integral along
 * the ray, so an observer at sea level gets a shallower, whiter horizon than
 * one on a hill. 300m reads as a site with a view, which the lab floor is. */
const A = {
  Rg: 6360000, Ra: 6420000, Hr: 8000, Hm: 1200, eye: 300,
  betaR: [5.8e-6, 13.5e-6, 33.1e-6], betaM: 21e-6, g: 0.76,
  betaO: [0.650e-6, 1.881e-6, 0.085e-6], Oc: 25000, Ow: 15000,
  /* Irradiance in the units the composite tone maps. This is the physical
   * scale the *light* is published in; what the dome draws is this times
   * `skyStop` below, which is a camera decision rather than a physical one. */
  sunI: 26.0,
  /* The isotropic multiple-scatter source, as a fraction of the irradiance.
   * Single scattering alone leaves the sky away from the sun far too dark and
   * far too saturated; this is the cheapest honest stand-in for the light that
   * bounced more than once.
   *
   * Up from 0.10 to pay back what taking the aerosol out cost the *fill*. Mie
   * scattering is achromatic and forward-peaked: dropping `mieBase` correctly
   * removed a white veil from the horizon, but it also removed the light that
   * was filling the shaded side of every building, and the frame came back
   * with a first percentile of 0 where the reference set holds 12-17. This is
   * the isotropic term, so it lifts the fill without touching the horizon —
   * which is the whole reason the two are separate numbers. */
  ms: 0.110,
  /* …and the colour that light has. Bounced light is sky light, and sky light
   * is blue, so weighting the source by the Rayleigh cross-section is the
   * difference between a horizon that goes pale blue and one that goes khaki.
   * At 1.0 it is pure Rayleigh chromaticity and the zenith turns to poster
   * cyan; three quarters keeps the blue in the bounce without the poster. Under
   * half — where this sat — made the bounce nearly grey, and since the bounce is
   * most of what fills the low sky away from the sun, a grey bounce is a grey
   * low sky, which is what three rounds of critics called a milky horizon. */
  msTintPow: 0.75,
  /* Aerosol in clear air, before fog and cloud add theirs.
   *
   * This was 1.0 and it was the single largest reason the frame had no aerial
   * perspective. Mie scattering is achromatic and forward-peaked, so a high
   * aerosol load whitens the horizon far faster than it whitens the zenith: at
   * 1.0 the measured low sky came back at byte 217 with a blue-minus-red of
   * only +10, i.e. white. Every distant object then fades into white, because
   * the fog colour is the horizon colour by construction. 0.42 is still a
   * forested valley rather than a mountaintop, and it leaves the horizon pale
   * *blue* — which is the colour the far ridge has to take to read as far. */
  mieBase: 0.42,
  /* The sky's own stop.
   *
   * `sunI` is chosen so a lit surface lands where the composite wants it, and
   * the composite has exactly one exposure for a scene that runs from noon to
   * midnight. At that exposure the sky itself comes out three stops hot: the
   * horizon integrates past 1.0 linear, ACES flattens it toward white, and the
   * fog — which is this model sampled around the horizon — becomes a white
   * veil laid over the picture. Four blind critics called that out.
   *
   * So the dome and the fog are drawn at `skyStop`, and everything published
   * as *light* (`sunIntensity`, `ambientColour`, the environment map) stays at
   * the physical scale, because gi.js and the materials are tuned against it.
   * `duskStop` opens back up as the sun goes down, the way a camera does: at
   * one fixed stop a golden hour either blows its horizon or loses its zenith
   * to the black point, and there is no setting that does neither.
   *
   * 0.32 was still two stops hot and it is why the sky read as empty. Measured
   * at 14:00 the sky band came back at luminance 203-217 out of 255 — sitting
   * on the shoulder of the ACES curve, where a two-to-one difference in
   * radiance survives as four bytes. Everything in it therefore flattened to
   * one value: the cloud tops were ten bytes off the sky behind them, the far
   * ridge was twenty-seven off the horizon, and the frame had no ladder at any
   * distance. Transport Fever 2's own sky, measured the same way, sits at
   * 125-150. At 0.088 ours lands there, which puts the whole sky back on the
   * straight part of the curve where value differences are visible again — and
   * nothing else in the frame moves, because this is the sky's stop alone.
   *
   * `duskStop` rises to match: it is a multiplier on this, and the night floor
   * was sized against the old value. 0.088 x 4.8 is a stop and a half of headroom that twilight and
   * the airglow floor were tuned to clear the composite's black point with. */
  skyStop: 0.088,
  duskStop: 4.8,
  /* The night floor: airglow, zodiacal light, distant towns, and every order of
   * scattering this model does not run. Sized to land just clear of the
   * composite's black point rather than by any physical argument — which is
   * why it is scaled with `duskStop` above: both are the same decision about
   * how open the sky's aperture is once there is nothing left to blow. */
  nightGlow: [0.130, 0.160, 0.258],
};

/* One text block, generated from the table above, pasted into the shader. Two
 * copies of these numbers that can drift apart is how a sky ends up with a
 * horizon that does not match its own fog. */
const ATMO_GLSL = /* glsl */`
  #define PI 3.14159265359
  const float Rg = ${A.Rg.toFixed(1)};
  const float Ra = ${A.Ra.toFixed(1)};
  const float Hr = ${A.Hr.toFixed(1)};
  const float Hm = ${A.Hm.toFixed(1)};
  const float Oc = ${A.Oc.toFixed(1)};
  const float Ow = ${A.Ow.toFixed(1)};
  const float EYE = ${A.eye.toFixed(1)};
  const vec3  BETA_R = vec3(${A.betaR.map(v => v.toExponential(4)).join(', ')});
  const vec3  BETA_O = vec3(${A.betaO.map(v => v.toExponential(4)).join(', ')});
  const float BETA_M = ${A.betaM.toExponential(4)};
  const float MIE_G  = ${A.g};
  const vec3  NIGHT_GLOW = vec3(${A.nightGlow.map(v => v.toExponential(4)).join(', ')});

  /* Far root of a ray against a sphere centred on the origin. Negative means
   * the ray misses, which for the ground sphere is the ordinary case. */
  float raySphereFar(vec3 ro, vec3 rd, float r) {
    float b = dot(ro, rd);
    float c = dot(ro, ro) - r * r;
    float d = b * b - c;
    if (d < 0.0) return -1.0;
    return -b + sqrt(d);
  }
  float raySphereNear(vec3 ro, vec3 rd, float r) {
    float b = dot(ro, rd);
    float c = dot(ro, ro) - r * r;
    float d = b * b - c;
    if (d < 0.0) return -1.0;
    return -b - sqrt(d);
  }
`;

/* The scattering integral, shared by the LUT shader and mirrored by `scatterJS`
 * below.
 *
 * The inner light march is what earns the twilight: a sample whose path to the
 * sun passes through the planet contributes nothing, so as the sun sets the
 * shadow of the earth climbs the sky from the east and the last lit air is the
 * thin, long-path, ozone-filtered band low in the west.
 *
 * Both marches step on a curve, not evenly, and that is not an optimisation —
 * it is the difference between a horizon and a mistake. A ray three degrees up
 * runs three hundred kilometres before it leaves the atmosphere, and fourteen
 * even steps put the first sample ten kilometres up, past the scale height,
 * with a single twenty-kilometre slab of optical depth already applied to it.
 * The integral cannot converge from there: blue is over-extinguished, red
 * survives, and the horizon comes out khaki under a blue sky. Cubing the view
 * parameter and squaring the light one puts most of the samples in the first
 * few kilometres, where all the air actually is, and the horizon lands where
 * it belongs — pale, slightly blue — at exactly the same step count. */
const SCATTER_GLSL = /* glsl */`
  vec3 scatter(vec3 rd, vec3 sunDir, float mieMul, vec3 msSrc, float irradiance,
               int viewSteps, int lightSteps) {
    vec3 ro = vec3(0.0, Rg + EYE, 0.0);
    float tTop = raySphereFar(ro, rd, Ra);
    if (tTop <= 0.0) return vec3(0.0);
    float tGround = raySphereNear(ro, rd, Rg);
    if (tGround > 0.0) tTop = min(tTop, tGround);

    float invN = 1.0 / float(viewSteps);
    float odR = 0.0, odM = 0.0, odO = 0.0, t = 0.0;
    vec3 sumR = vec3(0.0), sumM = vec3(0.0), sumMS = vec3(0.0);

    for (int i = 0; i < 20; i++) {
      if (i >= viewSteps) break;
      float f = float(i + 1) * invN;
      float t1 = tTop * f * f * f;
      float seg = t1 - t;
      vec3 p = ro + rd * (t + seg * 0.5);
      float h = max(length(p) - Rg, 0.0);
      float hr = exp(-h / Hr) * seg;
      float hm = exp(-h / Hm) * seg;
      float ho = max(0.0, 1.0 - abs(h - Oc) / Ow) * seg;
      odR += hr; odM += hm; odO += ho;

      float tl = raySphereFar(p, sunDir, Ra);
      float invL = 1.0 / float(lightSteps);
      float odLR = 0.0, odLM = 0.0, odLO = 0.0, tl2 = 0.0;
      bool blocked = false;
      for (int j = 0; j < 10; j++) {
        if (j >= lightSteps) break;
        float g = float(j + 1) * invL;
        float l1 = tl * g * g;
        float segL = l1 - tl2;
        vec3 q = p + sunDir * (tl2 + segL * 0.5);
        float hl = length(q) - Rg;
        if (hl < 0.0) { blocked = true; break; }
        odLR += exp(-hl / Hr) * segL;
        odLM += exp(-hl / Hm) * segL;
        odLO += max(0.0, 1.0 - abs(hl - Oc) / Ow) * segL;
        tl2 = l1;
      }
      if (!blocked) {
        vec3 tau = BETA_R * (odR + odLR)
                 + BETA_M * 1.1 * mieMul * (odM + odLM)
                 + BETA_O * (odO + odLO);
        vec3 att = exp(-tau);
        sumR += att * hr;
        sumM += att * hm;
      }
      vec3 tv = exp(-(BETA_R * odR + BETA_M * 1.1 * mieMul * odM + BETA_O * odO));
      sumMS += tv * (hr * BETA_R + vec3(hm * BETA_M * mieMul * 0.7));
      t = t1;
    }

    float mu = dot(rd, sunDir);
    float phaseR = 3.0 / (16.0 * PI) * (1.0 + mu * mu);
    float g2 = MIE_G * MIE_G;
    float phaseM = 3.0 / (8.0 * PI) * ((1.0 - g2) * (1.0 + mu * mu)) /
                   ((2.0 + g2) * pow(max(1.0 + g2 - 2.0 * MIE_G * mu, 1e-4), 1.5));
    /* The bounced light is not quite isotropic — it keeps a memory of the
     * sun's direction, which is why the western sky stays brighter than the
     * eastern one long after the direct beam has gone. */
    float msPhase = 0.72 + 0.62 * max(mu, 0.0);
    return (sumR * BETA_R * phaseR + sumM * BETA_M * mieMul * phaseM) * irradiance
         + sumMS * msSrc * msPhase;
  }
`;

/* The twilight layer.
 *
 * This one is authored, and it is the only thing here that is. Single (or even
 * double) scattering genuinely goes to nothing once the sun is more than a
 * couple of degrees down — the air we can see is in the earth's shadow and the
 * air still in sunlight is eighty kilometres up, where there is nothing left to
 * scatter with. Real twilight is carried by high-order transport this model
 * does not run. So from the moment the sun touches the horizon a fitted band
 * takes over: orange low in the sun's quarter, through salmon, into the deep
 * blue overhead that a blue hour actually is, dying out by about -17 degrees.
 * The physical model still owns everything above the horizon, and the two meet
 * where they are both weak, so there is no visible handover. */
const TWILIGHT_GLSL = /* glsl */`
  vec3 twilight(vec3 dir, vec3 sunDir, float amp) {
    if (amp <= 0.0) return vec3(0.0);
    vec2 dh = normalize(vec2(dir.x, dir.z) + 1e-5);
    vec2 sh = normalize(vec2(sunDir.x, sunDir.z) + 1e-5);
    float muh = dot(dh, sh);
    float az = 0.24 + 0.76 * pow(max(muh, 0.0), 1.6);
    az = mix(az, 0.5, clamp(abs(dir.y) * 1.7, 0.0, 1.0));
    float band = exp(-max(dir.y, 0.0) * 3.4) * 0.85 + 0.15;
    float t = clamp(max(dir.y, 0.0) * 3.0 + (1.0 - az) * 0.55, 0.0, 1.0);
    vec3 warm = vec3(1.00, 0.325, 0.075);
    vec3 cool = vec3(0.13, 0.235, 0.62);
    return mix(warm, cool, t) * (amp * band * az);
  }
`;

/* ---- the LUT pass -------------------------------------------------------- */

/* Elevation is stored on a square-root curve. A linear equirect spends half its
 * rows on the empty upper sky and resolves the horizon — where every
 * interesting gradient lives — at four degrees a texel, which bands visibly at
 * sunset. This puts roughly a third of the rows inside ten degrees of the
 * horizon for free. */
const LUT_MAPPING_GLSL = /* glsl */`
  vec3 lutDirection(vec2 uv) {
    float phi = (uv.x - 0.5) * 2.0 * PI;
    float s = uv.y * 2.0 - 1.0;
    float y = sign(s) * s * s;
    float c = sqrt(max(1.0 - y * y, 0.0));
    return vec3(c * sin(phi), y, c * cos(phi));
  }
  vec2 lutUv(vec3 d) {
    float phi = atan(d.x, d.z);
    float s = sign(d.y) * sqrt(abs(d.y));
    return vec2(phi / (2.0 * PI) + 0.5, s * 0.5 + 0.5);
  }
`;

const LUT_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform vec3 uSunDir, uMoonDir, uMS;
  uniform float uMieMul, uSunI, uMoonI, uNightLift, uTwiAmp;
  layout(location = 0) out vec4 outColor;
  ${ATMO_GLSL}
  ${LUT_MAPPING_GLSL}
  ${SCATTER_GLSL}
  ${TWILIGHT_GLSL}

  void main() {
    vec3 dir = normalize(lutDirection(vUv));
    vec3 col = scatter(dir, uSunDir, uMieMul, uMS, uSunI, 14, 6);
    col += twilight(dir, uSunDir, uTwiAmp);

    /* Moonlight is the same integral at a twentieth of the irradiance and a
     * colder white — it is what keeps a clear night blue instead of black, and
     * it is why the sky washes out around a high moon. */
    if (uMoonI > 0.0) {
      col += scatter(dir, uMoonDir, uMieMul, vec3(0.0), uMoonI, 8, 4)
             * vec3(0.72, 0.84, 1.0);
    }

    /* Airglow, and the integrated light of everything we are not modelling.
     * Without a floor the night side of the terminator is pure black, which no
     * camera and no eye has ever seen outdoors — and the floor has to clear the
     * composite's black point of 0.035 or it may as well not be there, which is
     * what was happening: at 21:00 every channel measured zero. */
    float hz = 1.0 - abs(dir.y);
    col += uNightLift * NIGHT_GLOW * (0.55 + 0.75 * hz * hz);

    outColor = vec4(max(col, vec3(0.0)), 1.0);
  }
`;

/* ---- the dome ------------------------------------------------------------ */

const DOME_VS = /* glsl */`
  out vec3 vDir;
  void main() {
    vDir = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const DOME_FS = /* glsl */`
  precision highp float;
  precision highp sampler3D;
  in vec3 vDir;

  uniform sampler2D uSkyLut;
  uniform sampler3D uNoise;
  uniform sampler2D uDetail;
  uniform vec3 uSunDir, uMoonDir;
  uniform vec3 uSunLight, uSkyLight, uFogColour, uSunTint;
  uniform vec2 uWind;
  uniform float uTime, uCloud, uRain, uFogAmt, uNight, uMoonPhase, uMoonI;
  uniform float uCloudBase, uCloudThick, uCloudDensity, uStars, uSunDisc;
  uniform float uSkyStop, uHiDesat, uSkyCeil, uDiscGain;
  uniform vec2 uLutTexel;
  uniform int uCloudSteps, uDetailOn;
  layout(location = 0) out vec4 outColor;

  ${ATMO_GLSL}
  ${LUT_MAPPING_GLSL}

  float hash13(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.zyx + 31.32);
    return fract((p.x + p.y) * p.z);
  }

  /* Interleaved gradient noise — the cheapest well-distributed per-pixel value
   * there is, and unlike a sine hash it does not clump into visible lattices
   * on an ordered grid, which is the whole point of using it as a dither. */
  float ign(vec2 p) {
    return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715))));
  }
  float remap(float v, float a, float b, float c, float d) {
    return c + (v - a) / max(b - a, 1e-5) * (d - c);
  }
  float hg(float mu, float g) {
    float g2 = g * g;
    return (1.0 - g2) / (4.0 * PI * pow(max(1.0 + g2 - 2.0 * g * mu, 1e-4), 1.5));
  }

  /* ---- stars ----------------------------------------------------------- */

  /* One candidate star per grid cell, placed by hash. Missing the stars that
   * would straddle a cell boundary is invisible; checking 27 neighbours to
   * catch them is not affordable on a background. */
  vec3 stars(vec3 dir) {
    vec3 col = vec3(0.0);
    for (int layer = 0; layer < 2; layer++) {
      float scale = layer == 0 ? 190.0 : 430.0;
      float gain  = layer == 0 ? 1.0 : 0.30;
      float cut   = layer == 0 ? 0.962 : 0.987;
      vec3 p = dir * scale;
      vec3 cell = floor(p);
      float h = hash13(cell);
      if (h < cut) continue;
      vec3 off = vec3(hash13(cell + 11.3), hash13(cell + 27.7), hash13(cell + 41.1));
      float d = length(p - cell - off);
      float mag = pow(fract(h * 731.0), 5.0);
      /* Scintillation, not a strobe: slow, and strongest low in the sky where
       * the air path is longest. */
      float tw = 0.72 + 0.28 * sin(uTime * (1.3 + fract(h * 97.0) * 2.4)
                                   + h * 63.0) * (1.0 - abs(dir.y) * 0.6);
      float bright = smoothstep(0.40, 0.0, d) * mag * gain * tw;
      /* A crude blackbody spread — most white, a few blue, a few amber. A
       * uniformly white field reads as noise rather than as a sky. */
      float ct = fract(h * 313.7);
      vec3 tint = mix(vec3(1.0, 0.84, 0.68), vec3(0.74, 0.85, 1.0),
                      smoothstep(0.25, 0.85, ct));
      col += bright * tint;
    }
    return col * 0.85;
  }

  /* A band of unresolved stars across a tilted great circle, textured with the
   * detail noise so it has structure instead of being an airbrushed smear. */
  vec3 milkyWay(vec3 dir) {
    vec3 axis = normalize(vec3(0.42, 0.58, -0.70));
    float band = 1.0 - abs(dot(dir, axis));
    float m = smoothstep(0.80, 1.0, band);
    if (m <= 0.0) return vec3(0.0);
    vec2 uv = vec2(atan(dir.x, dir.z) * 0.159 + 0.5, dir.y * 0.5 + 0.5);
    float n = texture(uDetail, uv * vec2(3.0, 2.0)).r;
    float dust = texture(uDetail, uv * vec2(7.0, 4.0) + 0.31).g;
    float v = m * m * (0.35 + n * 0.9) * (0.40 + dust * 0.80);
    return v * vec3(0.016, 0.018, 0.030);
  }

  /* ---- the moon -------------------------------------------------------- */

  vec3 moon(vec3 dir) {
    float ca = dot(dir, uMoonDir);
    float ang = acos(clamp(ca, -1.0, 1.0));
    /* The halo first: it is what the sky around a bright moon actually does,
     * and it reaches far enough out that the disc test below would miss it. */
    vec3 col = vec3(0.42, 0.50, 0.68) * exp(-ang * 26.0) * uMoonI * 0.055;
    const float R = 0.0091;                 // ~0.52 deg across, as it really is
    float disc = 1.0 - smoothstep(R * 0.94, R * 1.06, ang);
    if (disc <= 0.0) return col;

    /* A local frame on the disc, so the terminator and the maria stay put
     * instead of swimming as the camera turns. */
    vec3 up = abs(uMoonDir.y) > 0.95 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
    vec3 rx = normalize(cross(up, uMoonDir));
    vec3 ry = cross(uMoonDir, rx);
    vec2 d2 = vec2(dot(dir, rx), dot(dir, ry)) / R;
    float r2 = clamp(dot(d2, d2), 0.0, 1.0);
    vec3 n = vec3(d2, sqrt(max(1.0 - r2, 0.0)));

    /* Phase: light from a direction rotated in the disc plane. A full disc is
     * a paper cutout; the terminator is what makes it a sphere. */
    float ph = uMoonPhase * 2.0 - 1.0;
    vec3 l = normalize(vec3(ph, 0.18, sqrt(max(1.0 - ph * ph, 0.02))));
    float lam = pow(clamp(dot(n, l), 0.0, 1.0), 0.62);   // regolith backscatter

    float mare = texture(uDetail, d2 * 0.42 + 0.5).b;
    float albedo = mix(0.60, 1.0, smoothstep(0.35, 0.75, mare));
    vec3 c = vec3(0.95, 0.94, 0.90) * albedo * (lam + 0.035);
    return col + c * disc * 6.5;
  }

  /* ---- clouds ---------------------------------------------------------- */

  float cloudDensity(vec3 p, float cov, float detailOn) {
    float h = length(p) - Rg;
    float hf = clamp((h - uCloudBase) / uCloudThick, 0.0, 1.0);

    /* Sampled against the altitude, not the planet-centred y, which is six and
     * a third million metres: a texture coordinate built from it spends its
     * whole float, and the sampler's subtexel fixed point, on the radius of
     * the earth, leaving the fifteen hundred metres that actually matter
     * quantised into visible steps. That was the horizontal banding printed
     * across our horizon band. (No backticks in this file's shader comments:
     * the GLSL lives in a JS template literal and one would end it.) */
    vec3 q = vec3(p.x, h, p.z) * 0.00009;
    q.xz += uWind * uTime * 0.0016;
    vec4 n = texture(uNoise, q);

    /* Perlin remapped by a worley FBM — the standard trick that turns a smooth
     * blob into cauliflower without a second march. */
    float worley = n.g * 0.625 + n.b * 0.25 + n.a * 0.125;
    float shape = remap(n.r, worley - 1.0, 1.0, 0.0, 1.0);

    float d = remap(shape, 1.0 - cov, 1.0, 0.0, 1.0);
    if (d <= 0.0) return 0.0;

    /* Height profile: cumulus are flat-bottomed and taper at the top. As
     * coverage climbs toward overcast the profile flattens into a slab, which
     * is exactly what stratus is. */
    float base = smoothstep(0.0, 0.22, hf);
    float top  = 1.0 - smoothstep(0.42, 1.0, hf);
    float profile = mix(base * top, base * (1.0 - smoothstep(0.78, 1.0, hf)),
                        smoothstep(0.55, 0.95, cov));
    d *= profile;

    if (detailOn > 0.5 && d > 0.0) {
      vec3 dq = vec3(p.x, h, p.z) * 0.00031;
      dq.xz += uWind * uTime * 0.006;
      vec4 dn = texture(uNoise, dq);
      float det = dn.g * 0.6 + dn.b * 0.4;
      /* Erode hardest at the wisps and barely at all in the core, or the whole
       * cloud dissolves instead of fraying. */
      d = remap(d, det * 0.42 * (1.0 - hf * 0.5), 1.0, 0.0, 1.0);
    }
    return clamp(d, 0.0, 1.0);
  }

  vec4 clouds(vec3 rd, vec3 skyBehind) {
    if (uCloudSteps <= 0 || rd.y < -0.03) return vec4(0.0);
    vec3 ro = vec3(0.0, Rg + EYE, 0.0);
    float t0 = raySphereFar(ro, rd, Rg + uCloudBase);
    float t1 = raySphereFar(ro, rd, Rg + uCloudBase + uCloudThick);
    if (t1 <= 0.0) return vec4(0.0);
    t0 = max(t0, 0.0);

    /* A ray skimming the horizon crosses hundreds of kilometres of slab. Past
     * ~26 km the steps are wider than the cloud features and the march turns
     * into aliasing, so it is cut there and the remainder handed to the haze,
     * which is where a real horizon's clouds go anyway. */
    float len = min(t1 - t0, 26000.0);
    if (len <= 0.0) return vec4(0.0);

    /* And the slab has to stop somewhere. Within a couple of degrees of the
     * horizon the sphere's curvature means the ray enters the cloud deck fifty
     * to two hundred kilometres out and crosses a sliver of it, so the march
     * samples cloud features far smaller than a step: what came back was a row
     * of hard dashes laid along the horizon in every frame. Nothing at that
     * range is a cloud any more, it is haze, and the fade hands it over. */
    float far = smoothstep(70000.0, 26000.0, t0);
    if (far <= 0.0) return vec4(0.0);

    int steps = uCloudSteps;
    float seg = len / float(steps);
    /* Dither the entry point per pixel. Without it the slab shows as
     * concentric rings wherever a step boundary lines up with the coverage
     * threshold.
     *
     * Interleaved gradient noise, not the hash. Both trade banding for noise,
     * but a white-noise hash puts that noise at every spatial frequency
     * including the low ones the eye is most sensitive to, which is why the
     * clouds came back speckled once they were dense enough for one step to be
     * a large jump in alpha. IGN is a low-discrepancy ordered pattern: the same
     * variance, spent almost entirely above the frequency FXAA and the
     * composite's grain absorb. (The hash also degenerates here — its third
     * component was the constant 1.0, so a third of its mixing did nothing.) */
    float jitter = ign(gl_FragCoord.xy + 0.5);
    /* …and how much of a step to offset by has to fall with the step count.
     * The dither trades banding for noise at a fixed exchange rate: one step's
     * worth of optical depth. At fourteen steps that is a few percent of alpha
     * and invisible; at the two the floor tier can afford it is most of the
     * cloud, and neighbouring pixels sample kilometres apart, which is what
     * printed a coarse stipple across the whole deck at the bottom of the
     * ladder. Down there a few concentric rings are the better trade. */
    jitter *= clamp(float(steps) * 0.09, 0.25, 1.0);
    float t = t0 + seg * jitter;

    /* Multiple scattering inside the cloud, as three octaves.
     *
     * This is the fix for "the sky is empty". A single-scattering march with a
     * Henyey-Greenstein phase gives a cloud that is only bright when it is
     * between you and the sun; look ninety degrees away from the light and the
     * phase term collapses to a twentieth of its forward value, and the cloud
     * comes back the same brightness as the sky behind it. Measured: our cloud
     * tops were ten bytes off the sky. They were being drawn — nobody could see
     * them.
     *
     * A real cloud is bright in every direction because the light inside it has
     * bounced dozens of times, and each bounce both attenuates less (the path
     * is shorter) and remembers the sun's direction less. So: three terms with
     * geometrically decaying contribution, geometrically decaying extinction,
     * and a phase walked toward isotropic. It is Schneider's approximation and
     * it costs three exponentials per marched sample: mu is constant along a
     * ray, so the three phase values are computed once per pixel rather than
     * once per step. (No backticks in this file's shader comments — the GLSL
     * lives in a JS template literal and one would end it.) */
    float mu = dot(rd, uSunDir);
    float ph0 = mix(hg(mu, 0.80), hg(mu, -0.36), 0.42) * 5.6 + 0.16;
    float ph1 = mix(hg(mu, 0.42), hg(mu, -0.19), 0.42) * 5.6 + 0.42;
    const float ph2 = 0.92;

    /* Coverage never reaches zero. The clear preset is 0.15 cloud, and a clear
     * afternoon is not an empty sky — it is a handful of fair-weather cumulus,
     * and a critic looking for "what is the key light consistent with" finds
     * nothing at all without them. The floor here is what puts three or four
     * of them in a wide shot; the slope is what still gets to overcast.
     *
     * The floor was 0.28, which with the demo's fair preset came to 0.54 — and
     * once the clouds were made opaque and properly lit that stopped being a
     * few cumulus and became a lid from horizon to horizon, over a ground
     * carrying hard sun shadows. Two things that cannot both be true in one
     * frame is worse than an empty sky. At 0.10 the same preset gives 0.38,
     * which is broken cumulus with blue between them, and overcast still
     * arrives on time because the slope carries it. */
    float cov = clamp(0.10 + uCloud * 0.82, 0.0, 1.0);
    float sigma = uCloudDensity;
    float detailOn = float(uDetailOn);

    vec3 acc = vec3(0.0);
    float trans = 1.0;

    for (int i = 0; i < 14; i++) {
      if (i >= steps || trans < 0.02) break;
      vec3 p = ro + rd * t;
      float d = cloudDensity(p, cov, detailOn);
      if (d > 0.004) {
        /* Two light samples. A third would be better and cost a third of the
         * shader; the height term below stands in for the rest of it. */
        float ld = cloudDensity(p + uSunDir * 1100.0, cov, 0.0);
        float ld2 = cloudDensity(p + uSunDir * 3300.0, cov, 0.0);
        /* The optical depth toward the sun. The octaves below each see a
         * fraction of it, and the last of them — barely attenuated, nearly
         * isotropic — is the diffuse transmission that keeps a self-shadowing
         * cloud from being black inside and an overcast deck from being a black
         * lid. It replaces the flat 0.055 floor that used to stand in for it,
         * which was flat and therefore read as flat. */
        float sOD = (ld * 1.5 + ld2 * 0.85) * sigma * 1400.0;
        float lit = ph0 * exp(-sOD)
                  + 0.45 * ph1 * exp(-sOD * 0.45)
                  + 0.20 * ph2 * exp(-sOD * 0.18);

        float ext = d * sigma * seg;
        float tr = exp(-ext);
        /* Powder: the dark rim on a cloud edge facing the light, which is the
         * one cue that says "this is a volume" from a single scattering term. */
        float powder = 1.0 - exp(-ext * 2.4);

        float hf = clamp((length(p) - Rg - uCloudBase) / uCloudThick, 0.0, 1.0);
        /* The base of a cumulus is not lit by the sun and it is not lit by the
         * whole sky either — it sees the ground and the underside of its own
         * neighbours. Taking it down to a seventh of the sky term, and cooling
         * it as it goes, is what turns a white blob into a cloud with a
         * bruised grey bottom and a lit top, which is the one thing the
         * reference's clouds do that ours did not.
         *
         * How dark depends on how much sky there is left. An isolated cumulus
         * has a bright hemisphere under it and still shades its own base; a
         * solid overcast deck has nothing under it but the ground, and yet it
         * is a grey lid rather than a black one, because the light reaching
         * its underside came down through it. One floor for both is either a
         * flat cotton-wool cumulus or a night-time overcast, and we had the
         * second: the top of an overcast frame was coming out near black. */
        float baseLit = mix(0.13, 0.55, smoothstep(0.45, 0.98, uCloud));
        vec3 ambient = uSkyLight * mix(baseLit, 1.0, hf * hf)
                     * mix(vec3(0.74, 0.80, 0.95), vec3(1.0), hf)
                     * (1.0 - uRain * 0.45);
        vec3 direct = uSunLight * lit * mix(0.62, 1.0, powder);

        acc += trans * (1.0 - tr) * (direct + ambient);
        trans *= tr;
      }
      t += seg;
    }

    float alpha = (1.0 - trans) * far;
    /* Aerial perspective: clouds near the horizon are seen through the same air
     * the terrain is, so they take the horizon's colour and lose contrast. This
     * used to start at 0.30 — seventeen degrees up — which is most of the sky a
     * ground-level camera can see, and it was quietly deleting the entire cloud
     * layer at every camera angle the floor actually uses. It belongs in the
     * last few degrees, where the clouds really are a hundred kilometres out. */
    float haze = smoothstep(0.13, 0.005, rd.y);
    vec3 horizon = mix(skyBehind, uFogColour, 0.5);
    acc = mix(acc, horizon * alpha, haze * 0.80);
    alpha *= 1.0 - haze * 0.35;
    return vec4(acc, clamp(alpha, 0.0, 1.0));
  }

  /* ---------------------------------------------------------------------- */

  void main() {
    vec3 dir = normalize(vDir);

    /* The LUT is a coarse grid of a smooth, steeply curved function, and
     * bilinear filtering reconstructs it as a piecewise-linear surface: the
     * slope jumps at every texel boundary, and the eye reads those jumps as
     * Mach bands — vertical columns down the azimuth texels and horizontal
     * stripes across the elevation ones. A critic found both printed into our
     * frames. Jittering the fetch by up to a texel per pixel trades that
     * structure for noise, which the composite's grain then hides, and it is
     * the same trick and the same reason as the jitter in the cloud march. */
    vec2 luv = lutUv(dir);
    float dth = ign(gl_FragCoord.xy);
    luv += (vec2(dth, fract(dth * 7.919)) - 0.5) * uLutTexel;
    vec3 col = texture(uSkyLut, luv).rgb;

    if (uNight > 0.001) {
      vec3 night = stars(dir) + milkyWay(dir);
      /* Stars are behind the air, not in front of it: they are extinguished by
       * the same haze that whitens the horizon, so they thin out as they set. */
      night *= smoothstep(-0.02, 0.22, dir.y);
      col += night * uNight * uStars;
    }
    if (uMoonI > 0.0 && dir.y > -0.08) col += moon(dir);

    /* The sun disc, drawn analytically because the LUT cannot resolve half a
     * degree. uSunTint is the transmittance of the whole air column toward the
     * sun, so the disc reddens and dims into the horizon on its own and is
     * simply gone once it is below it.
     *
     * It is held out of the sky colour and added after the shoulder at the
     * bottom of this function, because the shoulder exists precisely to stop
     * the sky reaching the clip and the disc is the one thing that should. */
    vec3 discAdd = vec3(0.0);
    if (uSunDisc > 0.0) {
      float ca = dot(dir, uSunDir);
      float ang = acos(clamp(ca, -1.0, 1.0));
      /* 0.0058 rad is 0.66 degrees across, against the sun's true 0.53. The
       * extra is the swelling every low sun has through a long air path, and it
       * is also the difference between fourteen pixels and eighteen at 1080p.
       * Fourteen was not enough: at the only elevations this camera can ever
       * see the sun — it cannot pitch above the horizon, so the sun is only
       * ever in shot within a few degrees of setting — the disc sits inside
       * fifteen degrees of glare that the tone curve has already taken to
       * within a few bytes of white, and a small white dot on a nearly white
       * field is not a sun. */
      const float R = 0.0058;
      float disc = 1.0 - smoothstep(R * 0.93, R * 1.07, ang);
      if (disc > 0.0) {
        float r = clamp(ang / R, 0.0, 1.0);
        float limb = 1.0 - 0.62 * (1.0 - sqrt(max(1.0 - r * r, 0.0)));
        /* Pulled most of the way to neutral, and then driven hard enough to
         * clip in all three channels. The glare around a setting sun is cream —
         * red and green at the ceiling, blue well under it — so a disc carrying
         * the same transmittance tint as its surroundings has no edge to find.
         * A disc that clips the blue channel too does, and it is the only thing
         * in the frame that reaches white at that hour. */
        vec3 core = mix(uSunTint, vec3(dot(uSunTint, vec3(0.34, 0.42, 0.24))), 0.62);
        discAdd = core * disc * limb * uDiscGain * uSunDisc;
      }
      /* The tight aureole. The LUT's Mie term carries the broad glow; this is
       * the couple of degrees right around the disc that a 256-wide LUT
       * smears into a flat patch. The third term is much wider and much
       * fainter — twenty-odd degrees of lift around the sun. It is what tells
       * a frame where the key light is when the disc itself is out of shot,
       * which at the floor's camera angles is most of the time, and it is
       * small enough not to spend any of the highlight budget. */
      col += uSunTint * exp(-ang * ang * 900.0) * 2.0 * uSunDisc;
      col += uSunTint * exp(-ang * 9.0) * 0.28 * uSunDisc;
      col += uSunTint * exp(-ang * 2.3) * 0.075 * uSunDisc;
    }

    vec4 cl = clouds(dir, col);
    col = col * (1.0 - cl.a) + cl.rgb;

    /* Below the horizon there is no sky, only the air in front of whatever the
     * terrain puts there. Ending on the fog colour is what stops a visible
     * seam where the ground meets the dome.
     *
     * It is graded rather than flat, and that matters more than it sounds. The
     * further down you look the nearer the ground you are looking at, so the
     * less air is in the way and the darker it gets. Filling the whole lower
     * hemisphere with one value instead gave a pale slab under the horizon
     * that read as a white wall wherever the terrain ran out — which is
     * exactly what "the horizon line is lost entirely" meant. */
    float below = smoothstep(0.010, -0.055, dir.y);
    float down = smoothstep(0.0, -0.30, dir.y);
    /* Land haze is lit by the land as well as by the sky, so it loses some of
     * the horizon's colour on the way down rather than simply darkening into a
     * saturated version of it. */
    float fl = dot(uFogColour, vec3(0.2126, 0.7152, 0.0722));
    vec3 ground = mix(uFogColour, vec3(fl) * vec3(1.04, 1.0, 0.94), down * 0.55)
                * mix(0.86, 0.32, down);
    col = mix(col, ground, below);
    /* And in thick weather the lower sky is simply the fog, all the way up. */
    col = mix(col, uFogColour, pow(uFogAmt, 1.4) * smoothstep(0.55, -0.05, dir.y));

    /* The sky's own stop, then the highlight desaturation a film stock does
     * and a per-channel ACES curve does not: without it the horizon band under
     * a low sun stays a saturated orange stripe all the way to clipping, where
     * the reference has it going pale cream as it brightens. */
    col *= uSkyStop;
    float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
    col = mix(col, vec3(l), smoothstep(0.42, 2.4, l) * uHiDesat);

    /* And a shoulder on the sky, and only on the sky.
     *
     * "Sky is clipped to pure white" is the one criticism that survived two
     * rounds of exposure tuning, and it survived because it is not really an
     * exposure problem. A low sun blows fifteen degrees of horizon past the
     * composite's ceiling however the stop is set; drop the stop far enough to
     * hold it and the zenith goes to the black point. What the sky needs is the
     * shoulder a film stock has and a per-channel ACES curve on a fixed
     * exposure does not: identity through everything the frame is actually
     * made of, then an asymptote so no amount of Mie forward-scatter can put
     * the air itself at the ceiling.
     *
     * The knee is where it starts and uSkyCeil is where the air tops out; the
     * composite maps that ceiling to about byte 232, so the sky is always
     * readably short of white and the sun disc — added below, outside the
     * shoulder — is the only thing in frame that reaches it.
     *
     * It is a uniform and not a constant because the environment dome runs this
     * same shader to make the light the world is lit by, and a shoulder there
     * would be a lie told to every reflective surface on the site: uSkyCeil is
     * zero on that pass, which switches it off. */
    if (uSkyCeil > 0.0) {
      const float KNEE = ${SKY_KNEE.toFixed(3)};
      vec3 over = max(col - KNEE, vec3(0.0));
      col = min(col, vec3(KNEE)) + over / (1.0 + over / max(uSkyCeil - KNEE, 1e-3));
    }

    col += discAdd * uSkyStop;
    outColor = vec4(max(col, vec3(0.0)), 1.0);
  }
`;

/* ---- aerial perspective --------------------------------------------------- */

/* Distance haze is the one thing in this file that has to happen inside every
 * other subsystem's shader, and none of those files are ours to edit. So the
 * fog chunks three assembles its materials from are rewritten once, here,
 * before any material exists — sky is the first subsystem built, which is the
 * only reason this is safe. Everything the old chunks declared is still
 * declared, `vFogDepth` included, because labels.js replaces `fog_fragment`
 * with its own damped copy and reads exactly those names.
 *
 * What changes is the model. `FogExp2` is a single global density: the same
 * air at the top of a ridge as at the bottom of the valley, and a curve that
 * runs all the way to fully opaque. A blind critic put it exactly right — "a
 * uniform white veil applied over everything rather than depth-graded aerial
 * perspective; near treeline and far ridge are washed to the same low
 * contrast". Two corrections:
 *
 *   `FOG_H` — density falls off with height, so the integral is taken along
 *   the ray between the camera's altitude and the fragment's. Mist collects in
 *   the valley and thins over the ridge above it, which is what makes a range
 *   of hills read as a range rather than as one flat cut-out. The closed form
 *   is exact for an exponential profile and costs two exponentials.
 *
 *   `FOG_MAX` — the far field flattens toward, and stops at, 86%. The manifest
 *   asks for a far field at about 20% contrast; at 100% it is at zero, the
 *   silhouette is gone and there is nothing left to read. Capping it is the
 *   difference between distance and deletion.
 *
 * At the camera's own altitude with no height difference the height term is
 * exactly 1, so `density` still scales the haze monotonically and a value set
 * by another module still moves it in the direction that module intended — but
 * the curve is no longer three's, see `FOG_P`, and since 2026-08-09 the model's
 * own scale lives in `FOG_S` inside the chunk rather than in `scene.fog.density`,
 * so the published density is NOT the extinction any more. The whole model, in
 * one line, is
 *
 *     fogFactor = FOG_MAX * (1 - exp(- shell(density*FOG_S*depth*hterm)^FOG_P * FOG_K))
 *
 * where `shell` subtracts the first `FOG_T0` of optical depth with a `FOG_W`
 * knee. Anything that needs the real number should evaluate that; the four
 * harness instruments in this file's notes all parse it back out of the
 * compiled `THREE.ShaderChunk.fog_fragment` rather than copying it.
 *
 * The third correction, and the one the last two rounds were missing:
 *
 *   `FOG_K` — the haze is **chromatic**. Both of the above are value-only, and
 *   value alone cannot carry distance in this frame, because the tone curve
 *   compresses the top of its range: the far ridge and the horizon behind it
 *   measured twenty-seven bytes apart out of two hundred, which is nothing.
 *   Air scatters blue about five times as hard as red, so a distant surface
 *   loses its red last: it goes *blue* well before it goes pale. That is what
 *   the reference's layered ridges are doing — tf2-12's far ridge measures a
 *   blue-minus-red of +52 against a mid-ground of +10 — and a single scalar
 *   `fogFactor` cannot express it at any density.
 *
 *   So the factor is a vec3. These are the Rayleigh cross-sections normalised
 *   to their luminance-weighted mean and pulled back toward grey, because real
 *   haze is part aerosol and aerosol is achromatic; pure Rayleigh ratios put
 *   blue at five times red and turn the middle distance into a bruise.
 *
 * The fourth correction, 2026-08-07, and the one that made all of the above
 * measure as a wash:
 *
 *   **The vec3 was inside the square.** `1 - exp(-(tau*K)^2)` squares the
 *   weights along with the distance, so [0.80, 1.00, 1.42] was not a 2:1
 *   blue-to-red ratio of optical depth as the comment beside it claimed — it
 *   was 1.42²/0.80², **3.16:1**, and blue reached any given opacity at 0.56 of
 *   the range red did. The comment and the code had disagreed since the vec3
 *   went in and nobody read the code. An ablation on distant canopy put the
 *   whole of the blue-white far tier on this: fog on 41/81/104 (B−R +62), fog
 *   off 29/48/19 (B−R −9). The material path was never at fault.
 *
 *   The weight now multiplies the squared optical depth, `1 - exp(-tau^2 * K)`,
 *   so K is a ratio of extinction and means what it reads as.
 *
 * Calibrated against `refs/tf2-12.jpg`, which is the bar for layered distance.
 * Its own distant woods measure a blue-minus-red of +11 at 700m, +23 at 1.5km
 * and +41 at 3km. Note what that is not: blue exceeds red at *every* range in
 * the reference — aerial perspective is blue, and an acceptance test demanding
 * otherwise is stricter than the bar. What was wrong with ours was the rate.
 * We measured +57 at 550m: roughly five times the reference's bias at half its
 * distance, which is why six rounds of critics called it a veil rather than a
 * conveyor of depth. The target is that gradient, arriving at that rate. */
const FOG_Y0 = 0.0;      // the graded site plane, in metres — the sea is at -41
/* The e-folding height of the haze, and it is a boundary layer, not a valley
 * mist.
 *
 * This was 130 m, and a terrain round measured what that does: at the default
 * `cam=far` the height term hands the waterline 0.422 and ground twenty metres
 * up 0.334, so the beach — the two-metre band an art director looks at first —
 * was carrying 26% more optical depth than the land immediately behind it,
 * purely for being at sea level. The note that came back said sky.js "makes it
 * worst exactly at the beach", and it was right about the mechanism.
 *
 * It is also a scale error rather than a physical law. 130 m is the profile of
 * mist lying in a river valley on a still morning. This site is an island: the
 * sea is at -41 m, the highest ground is about +60, and the air over it is a
 * marine boundary layer whose aerosol is mixed to several hundred metres.
 * 400 m is the low end of that and it is still a profile — the term has not
 * been deleted — but the beach's penalty over the ground behind it falls from
 * 26% to 6.6% (measured, `harness/sk-haze.mjs`: hterm 0.699 at the waterline
 * against 0.656 inland). The whole term is also nearer 1 now, which is why the
 * base density below came down with it: `density` is only ever multiplied by
 * this, and the two together are one number.
 *
 * 2026-08-08, second pass: 400 -> 900, and this is the one lever in the model
 * that is NOT just another spelling of `density`.
 *
 * Everything else here — the density and the exponent — multiplies or shapes the
 * optical depth of every camera at once. The height term does not: it is an
 * integral between the CAMERA's altitude and the fragment's, so what it is worth
 * depends on how high the observer is. The judged frame is an aerial at 407 m.
 * The site is also walked at 24 m, and washing that out to buy depth in the
 * aerial is the failure five earlier rounds were written up for. Measured
 * (`sk-haze.mjs`, both cameras, at 400 m and at 900 m):
 *
 *                        hterm at 400 m   at 900 m
 *    cam=far   (y 407)       0.63           0.81      x1.28
 *    cam=low   (y  24)       0.971          0.987     x1.02
 *
 * So nine hundred metres of e-folding height buys 28% more air in the frame the
 * art director judges and 2% more in the frame the operator works in. The
 * rendered consequence is the same shape: at `cam=low` the 0-300 m band's fog
 * factor stays 0.000 and 300-600 m moves 0.012 -> 0.013.
 *
 * Physically it is the same boundary layer, read at its middle instead of its
 * floor. A marine layer over open water is 500-1500 m deep; 400 was the bottom
 * of that range and it put an observer at 407 m most of the way out of the
 * haze, looking down through its top. 900 puts them inside it, which is what an
 * aerial over an island at this latitude actually looks like. The beach's height
 * penalty over the ground behind it, which is what moved this number last time,
 * improves again: 6.6% -> 3.8% (hterm 0.825 at the waterline, 0.795 twenty
 * metres up). */
const FOG_H = 900.0;
const FOG_MAX = 0.88;
/* `FogExp2` squares the optical depth, and nothing physical does: Beer-Lambert
 * is `exp(-tau)`, and the square is a shape three chose because it keeps the
 * near field clear. Since nothing about it is physical, the only question this
 * exponent answers is *where in distance the transition sits*, and the answer
 * belongs to the world it is applied to rather than to a photograph.
 *
 * 1.5 was fitted to tf2-12's three rungs at 700 m / 1.5 km / 3 km. This world
 * is not shaped like that photograph. Its subject — the island, its coastline
 * and everything built on it — sits at 350 to 900 m, and its far field is open
 * water from 1.5 to 6 km with a mainland standing across the back of it. At
 * 1.5 and a density that keeps the subject paintable, the whole map lands in
 * the toe of the curve: measured with `harness/sk-haze.mjs`, optical depth ran
 * from 0.05 at 300 m to 0.31 at 1.8 km, `tau^1.5` shrank all of it, and NOTHING
 * IN THE FRAME WAS EVER MORE THAN 15% HAZED. That is the whole of "no depth cue
 * at the horizon": there was no far field, because the model had not reached it
 * yet at the point where the world runs out.
 *
 * What the frame needs is a factor of about ten in haze between 700 m and
 * 1.85 km, which is a factor of 2.6 in distance — so the exponent wants to be
 * log(10)/log(2.6), near 3. That is not a licence to keep raising it: the
 * transition still has to be a couple of octaves of distance wide or it reads
 * as a wall of fog at a fixed range. Three is where those two meet.
 *
 * Measured at `cam=far&time=9&quality=ultra` (`sk-haze.mjs`, `sk-mainedge.mjs`),
 * against the same frame at 1.5:
 *
 *              300-600 m   waterline   900-1300 m   sea at the mainland's foot
 *    P=1.5       0.018       0.037       0.047            0.144
 *    P=3.0       0.010       0.035       0.062            0.389
 *
 * i.e. the near and middle field got *clearer* and the far field arrived. The
 * blue-minus-red ladder moved the same way and toward the reference, not away
 * from it: `skyfog.mjs --map` + `fogmap.py` at `cam=wide`, ultra, on distant
 * canopy, 600-900 m went from +22.3 to +10.3 against tf2-12's +11 at 700 m.
 * It is one `pow` inside a branch the fragment already took.
 *
 * 3.0 -> 2.75, 2026-08-08, and this is the half of that change that was wrong.
 *
 * A cube fixed the far field by moving the whole toe of the curve out past the
 * subject. A blind art director then named the result as the single largest
 * remaining defect in the frame, and named it from the defect alone: the island
 * "sits on top of a receding seascape like a decal", because the sea is graded
 * and the trees on the island are not. That reads as a fog term missing from the
 * instanced-foliage pass. It is not — every vegetation material is `fog: true`
 * and takes this chunk. It is this exponent.
 *
 * Where the canopy actually is, measured rather than assumed (`skyfog.mjs --map`
 * at `cam=far`, the instanced cells only): 584 m to 1281 m, median 745, and 70%
 * of it inside 900 m. At P=3 the whole of that 70% was under 4.5% hazed.
 *
 * The obvious move — drop the exponent — does not work on its own, because with
 * the far field held it raises the floor as fast as it raises the middle. On
 * canopy PIXELS (`harness/sk-canopy.py`: mask built on a fog-off frame of the
 * same world so the haze cannot have moved it, distance from the cell map), at
 * `cam=far`, 09:00, ultra, blue-minus-red:
 *
 *              fog off   P=3.0   P=2.75   P=2.5   P=2.25   P=2.0
 *    550-700m    -9.3     +3.8    +7.9    +12.7   +18.7    +24.7
 *    700-850m    -8.8     +9.8   +14.2    +19.0   +24.7    +29.9
 *   1150-1300m  -12.0    +30.6   +34.3    +37.9   +41.6    +44.8
 *    ladder       -2.7    +26.8   +26.4    +25.2   +22.9    +20.1
 *
 * i.e. below 2.75 the ladder the critic asked for gets SHORTER while the near
 * band turns blue — that is the veil six earlier rounds were failed for, and
 * `fogbands`/`fogmap` could not see it because a 32x32 cell of "canopy" at
 * 1.2 km is half sea.
 *
 * What does work is the exponent and the height term together: `FOG_H` above
 * raises the aerial camera's optical depth by 28% and the ground camera's by 2%,
 * so the curve can be re-steepened at 2.75 and still land the island twice as
 * hazed as it was. Same instrument, same frame, three captures back to back so
 * a live terrain round cannot get between them, repeatable to 0.2 —
 * P=3.0/H=400/0.00065 against P=2.75/H=900/0.00058:
 *
 *                       L before / after      B-R before / after
 *    550-700 m           45.3  ->  46.7        +3.6  ->  +13.7
 *    700-850 m           54.2  ->  57.5       +10.4  ->  +21.3
 *    850-1000 m          64.6  ->  70.1       +17.7  ->  +29.2
 *   1000-1150 m          72.7  ->  80.3       +27.8  ->  +38.9
 *   1150-1300 m          76.0  ->  86.8       +33.0  ->  +44.5
 *    ladder across it    30.7  ->  40.1        29.4  ->   30.8
 *
 * and on the operator's own instrument of record, `skyfog.mjs --map` +
 * `fogmap.py`, blue-minus-red by TRUE per-cell distance, same frame:
 *
 *     300-600 m   -0.4 -> +7.9      600-900 m   +4.6 -> +13.8
 *     900-1300 m +22.0 -> +30.6    1300-2000 m +36.3 -> +37.4
 *
 * The last of those is the mainland, which is `fog: false` and rolls its own
 * haze in terrain.js, so it correctly does not move — everything that takes
 * this chunk moves by about nine, which is the discontinuity closing.
 *
 * The fog-off frame's own ladder over the same canopy pixels is +2.6 L and
 * -2.1 B-R: the canopy has no intrinsic depth cue at all, which is why this
 * term has to carry all of it, and why the sea — which grades itself,
 * bathymetrically — looked like a different world from the island standing in
 * it.
 *
 * 2.75 -> 3.25, 2026-08-08, and this is the exponent doing the OTHER half of
 * its job. The round above bought a depth ladder the art director then rated
 * above the shipped reference — and named the price in the same paragraph: the
 * correction "was applied globally rather than by depth alone, so the near
 * plane is hazed too", "the whole frame sits in a milk bath".
 *
 * That was measured before anything moved, and the mechanism is not the one it
 * sounds like. `harness/sk-milk.mjs` renders four states in ONE page session —
 * fog live and fog pinned to 1e-9, each with gi.js's adaptive exposure running
 * and with it frozen — so the haze's share of a pixel is a within-frame
 * difference at a fixed stop. At `cam=far`, 09:00, ultra:
 *
 *              nearest ground   frame p1   frame p5   frame sd   frame p95
 *    fog off        40.6          16.1       17.9       52.4       170.6
 *    fog on         50.0          26.5       33.7       47.5       170.7
 *
 * The haze lifts the bottom of the frame by ten codes and the top of it by
 * nothing. That is not an exposure error and it cannot be fixed by one: it is
 * airlight, which is ADDITIVE, and the fog colour's scene radiance at this hour
 * is 0.146 against 0.005 for the nearest ground — twenty-eight to one. A fog
 * factor of 0.030 on a pixel that dark is a 28% lift of its value and 0% of the
 * highlights, which is exactly what "a milk bath" and "never reaches full local
 * colour" describe. The number to watch is not the fog factor, it is the fog
 * factor times the ratio of the fog colour to the darkest thing in frame.
 *
 * THE OTHER HALF OF THAT MEASUREMENT, AND IT INVALIDATES A NUMBER IN EVERY NOTE
 * ABOVE. gi.js runs an adaptive exposure: `_applyGrade` meters the frame and
 * writes the composite's stop. Haze BRIGHTENS the frame, so the meter answers by
 * stopping DOWN — it is negative feedback, not the runaway it was suspected of
 * being, and it is why the milk bath is not worse than it is. But it also means
 * a fog-off control frame is not the same photograph at a different density: at
 * `cam=far`, 09:00, the exposure sits at 3.18 with the haze live and pegs at its
 * 4.00 ceiling with the haze pinned out, a 26% difference in stop. The loop
 * absorbs somewhat over half of any change made here — the same fog ablation
 * measures 19.4 luminance of frame mean at a frozen stop and 7.7 with the meter
 * left running. Every "what the haze costs" figure in this file that came from a
 * fog-on/fog-off pair without freezing the grade is therefore understated by
 * roughly a factor of two and a half. Nothing in the model was wrong; the
 * control was. `sk-milk.mjs --fixexp` is the fix, and it is also the only way to
 * compare two fog curves across two page loads.
 *
 * So the near field wants less optical depth and the far field wants what it
 * has, which is the one thing this exponent is for. Density is raised to hold
 * the far end EXACTLY — `harness/sk-curve.mjs` evaluates the compiled chunk
 * against a dumped frame's real per-pixel depths and heights
 * (`harness/sk-geodump.mjs`), so the pair is solved rather than swept:
 *
 *                        450-600  600-750  750-900  900-1100  1100-1400
 *    P=2.75 d 5.797e-4    0.0193   0.0297   0.0519    0.0882     0.1415
 *    P=3.25 d 6.377e-4    0.0133   0.0222   0.0431    0.0809     0.1415
 *
 * and rendered, `sk-milk.mjs` at a frozen exposure of 3.20 so two page loads are
 * two fog curves and not two stops — what the haze ADDS to each band:
 *
 *                        600-750  750-900  900-1100  1100-1400   ladder
 *    P=2.75               +12.3    +19.1     +29.1      +45.0      36.2 L
 *    P=3.25                +9.2    +15.7     +25.4      +44.1      42.0 L
 *
 * The near band gives up a quarter of its haze, the rim trees give up none, and
 * the ladder between them gets SIX LUMINANCE LONGER. Frame-wide: p1 26.5 -> 23.7,
 * p5 33.7 -> 30.9, sd 47.5 -> 48.7, mean adjacent-pixel |dL| 3.32 -> 3.41, and
 * the frame's mean saturation goes DOWN, 40.2 -> 38.4 — the near field is
 * recovered by removing a veil, not by adding chroma, which was the one way it
 * was not allowed to be bought.
 *
 * What caps it, and the reason this is 3.25 and not 3.5. Density has to rise to
 * hold the far end, and the far end that binds is not on the island: it is the
 * open water at the mainland's foot, where terrain.js's `_rangeMaterial` is
 * `fog: false` and gives itself 0.542 of its own haze. The note under `density`
 * below set that as a ceiling — our sea must agree with the headland FROM
 * BELOW — and this change crosses it, so the ceiling is restated here as what it
 * actually was. `harness/sk-mainedge.mjs`, nine columns, ultra, 2005 m, both
 * curves measured back to back:
 *
 *                     sea's fog factor   headland's own   rendered step, L
 *    P=2.75                 0.511            0.542             17.0
 *    P=3.25                 0.603            0.542             11.3
 *
 * The proxy crosses and the thing the proxy stood for improves: the sea claims
 * more air than the headland now, and the RENDERED join gets a third softer,
 * because the two materials paint different base colours and a fog factor was
 * never the quantity a viewer sees. The error to avoid is the water in front
 * coming out PALER than the land behind it, and that is still 11.3 luminance
 * away — the sea reads 148.6 against the headland's 159.9. That margin, not the
 * factor, is the cap: 3.5 spends most of what is left of it.
 *
 * What it costs, stated rather than buried: the blue-minus-red of the near band
 * falls with its value, +9.7 -> +3.8 at 600-750 m, against tf2-12's +11 at
 * 700 m. That anchor is retired here on purpose. It CAN be held — widening
 * `FOG_K` to [0.62, 1.00, 1.55] restores +9.7 at 600-750 for no luminance at all,
 * because the chromatic weights are nearly luminance-neutral by construction —
 * and it was measured and rejected: it takes 1100-1400 m to +55.0 B-R against
 * the reference's +20-odd, and the frame's mean saturation from 40.2 to 45.2.
 * The brief on this frame is that it survives being put behind UI on a narrow,
 * unified, low-saturation range, so a near band slightly under the reference's
 * blue is the cheaper of the two errors. The LADDER, which is what the depth
 * read is actually made of, lengthens either way: +3.8 -> +35.2 here against
 * +9.7 -> +38.9 before.
 *
 * On the two instruments the last round was judged on, `cam=far`, 09:00, ultra,
 * three captures of the same world back to back. `skyfog.mjs --map` + `fogmap.py`,
 * blue-minus-red by TRUE per-cell distance:
 *
 *                    fog off   before   after      tf2-12
 *      300-600 m      -19.7     -2.9     -7.3
 *      600-900 m      -10.2    +12.9     +8.7      +11 at 700 m
 *      900-1300 m      -6.8    +30.1    +28.5
 *     1300-2000 m     +35.6    +37.0    +36.9      (mainland: fog:false, must not move)
 *
 * and `sk-canopy.py`, which masks CANOPY PIXELS on a fog-off frame of the same
 * world and takes the distance from the cell map, so neither half of it is
 * guessing — the instrument built for the decal verdict:
 *
 *                    L: off  before  after  |  B-R: off  before  after
 *      550-700 m       43.6   48.4   46.2   |     -8.9   +12.8   +7.7
 *      700-850 m       43.9   56.2   53.7   |     -9.5   +20.7  +16.1
 *      850-1000 m      44.9   65.5   63.1   |    -10.4   +27.9  +24.4
 *     1000-1150 m      45.8   77.6   76.4   |    -10.9   +36.6  +34.6
 *     1150-1300 m      43.5   88.3   88.9   |     -8.4   +46.5  +46.3
 *      ladder                  39.9   42.7  |             33.7   38.6
 *
 * The rim trees do not move, the foreground canopy gives back nearly half of
 * what the haze had added to it, and BOTH ladders get longer. The open water is
 * the check on the other side, because it grades itself and a heavier far field
 * flattens it: `sk-water.py`, p90 minus p10 of water luminance over a mask built
 * on the fog-off frame, 80.8 -> 86.1 — the shelf-to-deep read comes BACK, since
 * most of the water in frame is nearer than the band the density was raised for.
 *
 * 3.25 -> 1.15, 2026-08-09, and this is the exponent giving BACK the job it
 * should never have had. Everything above it is a record of one number being
 * asked to do two things: hold a far field at 1.3-6 km and keep a subject at
 * 350-1000 m out of the haze. It cannot, because it is a single power law and
 * those two demands are a start and an end. Every round it climbed, the far
 * field held and the near band got a little clearer and a little less blue, and
 * the shadow under the plant kept dying anyway — 0.0533 of haze over the
 * subject at P=3.25, against 0.0533 at P=2.75 and 0.0533 at P=3.0, because
 * density was re-solved each time to put the far end back and the near end came
 * with it. That is the whole of the table under FOG_T0.
 *
 * With a shell doing the start, the exponent is free to do what it is for, and
 * what it is for is the WIDTH of the transition. Lower is wider. 1.15 against
 * 3.25, at the shell and scale that hold DECAL and JOIN fixed
 * (`harness/sh-solve.mjs`, evaluated on the dumped geometry, so a band is the
 * mean over the pixels really in it):
 *
 *              450-600  600-750  750-900  900-1050  1050-1200  1200-1400  1800-2200
 *    P=3.25     0.0133   0.0222   0.0431    0.0747     0.1168     0.1830     0.5343
 *    P=1.15     0.0000   0.0001   0.0022    0.0222     0.0857     0.1968     0.5369
 *
 * The far half is untouched to the third decimal and the first kilometre gives
 * up essentially all of its haze. It is not free and the cost is named under
 * FOG_T0: the 450-750 m band loses its aerial perspective outright, and the
 * choice of WHICH row of that table ships is a choice about how much near-field
 * air the shadow is worth. */
const FOG_P = 1.15;
/* Extinction ratio, not its square: about 1.5:1 blue-to-red. Kept modest
 * because the fog colour is the horizon and the horizon is already blue — the
 * far field arrives at the reference's +40 by *becoming the sky*.
 *
 * 2026-08-09: THIS IS WHERE THE SHELL'S BILL IS PAID, and it is stated here
 * rather than buried because it is the one thing the shell genuinely destroys.
 * The sentence that used to end this note said K "only has to carry the first
 * few hundred metres". It no longer carries them, because there is nothing
 * there to carry: the clear shell takes the fog factor at 600-900 m from 0.022
 * to 0.0002, and a chromatic weight on a factor of two ten-thousandths is a
 * chromatic weight on nothing.
 *
 * Measured, `skyfog.mjs --map` + `fogmap.py`, blue-minus-red by TRUE per-cell
 * distance, `cam=far`, 09:00, ultra, three captures of the same world:
 *
 *                    fog off   before   after     tf2-12
 *      300-600 m      -20.4     -7.9    -20.0
 *      600-900 m      -13.2     +6.9    -12.2     +11 at 700 m
 *      900-1300 m      -9.7    +28.2    +13.6
 *     1300-2000 m     +35.6    +37.1    +35.5     (mainland: fog:false, held)
 *
 * The near two bands land on their own fog-off colour to within a luminance,
 * i.e. the air at 700 m is now invisible, where the reference's is +11. The
 * previous round retired that anchor deliberately (+9.7 -> +3.8) as the cheaper
 * of two errors; this round spends the rest of it. What is bought for it is
 * the whole of the shadow work above, and the LADDER in this quantity gets
 * longer too — before spans +45.0 from the near band to the mainland, after
 * spans +55.5 — so what is lost is the near field's absolute blue, not the
 * progression.
 *
 * WIDENING K DOES NOT BUY IT BACK, and the arithmetic is one line rather than a
 * page load. At 600-900 m the green factor is 2e-4; the most extreme chromatic
 * weight worth considering, [0.4, 1.0, 2.2], puts the blue factor at 4.4e-4,
 * and the fog colour is about 190 in the blue channel against a canopy pixel's
 * 42, so the blue the haze can add there is 4.4e-4 x 148 = 0.07 of one code.
 * There is no chromatic tilt available in a band with no extinction in it. The
 * only lever that reaches 700 m is the shell itself, which is to say the trade
 * under FOG_T0 and nothing else. K stays where it is because the band it still
 * governs — 900-1500 m, where the factor runs 0.02 to 0.20 — is exactly where
 * it was tuned, and the far field is unmoved. */
const FOG_K = [0.82, 1.00, 1.24];

/* ---- the clear shell, 2026-08-09 ------------------------------------------
 *
 * THE DEFECT THIS EXISTS FOR. Five rounds in three other files chased "the
 * plant casts no visible shadow, nothing reads as solid, the frame has no black
 * point", and gi.js's decomposition finally put the blame here: with the stop
 * pinned and each term ablated alone over the same pixels, the shadow step ran
 * base 0.79 stops, 0.97 with gi's fill cut 50x, and **1.61 with the haze
 * removed and nothing else changed** (`harness/sn-decomp.mjs`, re-run at the
 * head of this round; gi measured 0.74 / 1.00 / 1.44 the day before). The haze
 * was over half the shadow.
 *
 * THE MECHANISM, which is not the fog factor. `harness/sh-run.mjs` classifies
 * every flat ground sample by ray-casting at the sun and then evaluates the
 * COMPILED chunk on that sample's own view depth and world height. The
 * sun-occluded samples sit at 722-1044 m, median 902, and carry a median fog
 * factor of **0.0665**. Three per cent, six per cent — these look like nothing,
 * and the note under FOG_P already says why they are not: airlight is ADDITIVE,
 * the fog colour's scene radiance at 09:00 is 0.146, and a shadowed pixel is
 * near 0.008. Linearising the composite around each population (base against
 * the fog-off ablation, same pixels, same stop):
 *
 *     shadowed   0.0372 -> 0.0817 display-linear   the haze is 55% of the pixel
 *     open       0.1132 -> 0.1424                  the haze is 21% of the pixel
 *
 * A fog factor of 0.0665 doubles a shadow and adds 8% to the ground beside it.
 * That is the whole missing stop, and no shadow map and no fill ratio can
 * reach it — `sn-deep.mjs` measured the map leaking only 7.9% of the key, so a
 * PERFECT shadow map with zero fill still could not exceed 1.44 stops while the
 * haze was where it was.
 *
 * WHY THE EXPONENT COULD NOT FIX IT. FOG_P moves the whole curve's toe. To get
 * the subject's 0.0665 down to 0.02 while holding the canopy ladder's top rung
 * at 1150-1300 m needs a factor of 7.75 across a factor of 1.39 in distance,
 * i.e. an exponent of 6.2 — a wall of fog at a fixed range, and the note above
 * FOG_P already says why 3.5 was the ceiling. The exponent is a *scale* on
 * where the transition sits; it cannot move the start and the end apart.
 *
 * WHAT DOES. Subtract a fixed optical depth before the curve is applied. The
 * first `FOG_T0` of extinction buys no haze at all, and everything past it is
 * shaped as before. That decouples the two ends: the shell sets where the
 * aerial perspective STARTS and the exponent sets how fast it arrives, so the
 * exponent can come back DOWN toward the physical 1 — which widens the
 * transition — while the subject sits in front of the whole thing.
 *
 * It is exactly as unphysical as the exponent it replaces and for the same
 * reason: Beer-Lambert has no shell and no square. What it is is the honest
 * spelling of the thing this world actually needs and a photograph does not —
 * a subject at 350-1000 m that must stay in local colour, and a far field at
 * 1.3-6 km that must recede. `FOG_P = 3.25` was buying the first by shrinking
 * the whole map, which is why it had to keep climbing and why it kept taking
 * the near band's blue with it.
 *
 * SOLVED, NOT SWEPT. `harness/sk-geodump.mjs` dumps every ground sample's true
 * view depth, world height and land/sea flag once; `harness/sh-solve.mjs` then
 * solves (shell, scale) so that TWO anchors land exactly where they are today
 * and reports what the third one came out at:
 *
 *    DECAL  land 1150-1300 m  0.1596   the canopy ladder's top rung. The blind
 *                                      "island sits on the seascape like a
 *                                      decal" verdict is RESOLVED and this is
 *                                      the number that resolved it.
 *    JOIN   sea  1900-2100 m  0.5461   our sea must agree with terrain.js's
 *                                      mainland (0.542, its own material) and
 *                                      must agree FROM BELOW. A ceiling.
 *    SHADOW land  750-1050 m  0.0533   the read-out. Lower is the whole point.
 *
 *      P     shell    scale   SHADOW   x base   450-600   600-750   750-900
 *    3.25   0.0000    1.000   0.0533    1.000    0.0133    0.0222    0.0430
 *    2.75   0.1351    1.136   0.0476    0.893    0.0080    0.0162    0.0369
 *    2.25   0.3102    1.311   0.0389    0.729    0.0025    0.0083    0.0275
 *    1.75   0.5436    1.545   0.0254    0.476    0.0002    0.0017    0.0139
 *    1.50   0.6901    1.692   0.0175    0.329    0.0001    0.0005    0.0073
 *    1.15   0.9378    1.940   0.0087    0.163    0.0000    0.0001    0.0022
 *
 * Every row holds DECAL and JOIN to four decimals; they differ only in how the
 * first kilometre is spent. The bottom rows buy the shadow back almost whole
 * and cost the near field its aerial perspective entirely — 0.0001 of haze at
 * 600 m is not depth-graded air, it is no air. The fear that came with that,
 * and it was the reason for going carefully, is that the near island would go
 * back to being ungraded against a sea that grades itself bathymetrically,
 * which is the decal verdict returning by another door. IT DOES NOT, and the
 * instrument built for that verdict is the one that says so — see the sk-canopy
 * and sk-water rows below. Both ladders get LONGER, because the far rungs are
 * held by construction and only the near ones come down, and the sea's own
 * shelf-to-deep read comes back at the same time. What is genuinely lost is
 * named under FOG_K: the near band's aerial-perspective COLOUR.
 *
 * `FOG_S` and NOT the published density, which is the other half of the
 * decision. A shell has to be paid for with more extinction beyond it, and the
 * obvious place to put it was `scene.fog.density`. That number is read by two
 * other files — labels.js hardcodes `1 - exp(-fogDensity^2 * vFogDepth^2)` in
 * `dampFog` and scales it by 0.55 on the status bars — so raising it from
 * 6.377e-4 to 1.237e-3 would have taken a status board at 900 m from 0.154 of
 * fog to 0.391 with nothing in this file intending it. The status boards are
 * this round's acceptance test (`harness/sn-floor.mjs`, red at weber 0.15), so
 * the scale is baked into the chunk instead and `scene.fog.density` is left
 * exactly where it was. The side benefit is that thick weather does not move:
 * at `fog: 0.9` the optical depth at 100 m is 3.0 and a shell of 0.94 is a
 * third of it, where a 94% density rise would have been the whole preset.
 *
 * WHICH ROW SHIPS, decided on the render and not on the table. Four candidates
 * were rendered at `cam=far`, 09:00, ultra, each one a page load with the stop
 * PINNED to 3.1665 so that two frames are two fog curves and not two stops, and
 * the "before" row re-taken through `__lemFog {p: 3.25, s: 1, t0: 0}` on this
 * build so nothing else can have moved (`harness/sh-run.mjs`):
 *
 *                 shadow step   haze over    haze over    frame    frame
 *                    (stops)     shadow        open        p1       p5
 *    before P=3.25     0.751      0.0665       0.0400      24.3     31.8
 *    P=1.75            0.869      0.0406       0.0114      18.0     23.1
 *    P=1.50            0.876      0.0272       0.0043      17.8     22.2
 *    P=1.15            1.096      0.0127       0.0012      17.4     21.5
 *
 * and the two ends of that, re-run with gi's meter LEFT RUNNING, because that
 * is the frame that ships and the meter gives back somewhat over half of
 * anything done here — it opens from 3.165 to 3.800 when the haze comes off:
 *
 *                 stops   p1     p5    p50    %under 20   450-600 m   1400-1800 m
 *    before       0.755   24.3   31.8  118.8     0.07        53.8        134.7
 *    P=1.15       1.072   18.1   23.2  126.4     2.31        54.7        151.9
 *
 * so at the exposure the operator actually sees: the shadow step gains a third
 * of a stop, the frame's bottom percentile drops six codes, the fraction of the
 * frame under L20 goes from nothing to 2.3%, and the depth ladder gets LONGER,
 * 80.9 luminance from the near band to the far one against 97.2. That last is
 * the one that had to be defended and it moved the right way: the far field is
 * held by construction and the near field is what came down.
 *
 * THE WALL THAT ISN'T. The obvious objection to a shell is that it makes a bank
 * of fog at a fixed range. It does the opposite, and this is the whole reason
 * the shape works. Solving the compiled form for the distance at which f
 * reaches a tenth and nine tenths of FOG_MAX:
 *
 *       P     shell ends   f = 10%   f = 50%   f = 90%   octaves 10->90
 *     3.25         0 m      981 m    1751 m    2534 m        1.37
 *     1.75       690       1040      1719      2733          1.39
 *     1.50       799       1058      1707      2820          1.41
 *     1.15       948       1090      1682      3035          1.48
 *
 * The transition is WIDER than the one that ships today, not narrower, and the
 * middle of it does not move at all. What the shell buys is the START: aerial
 * perspective now begins at 950 m instead of at the lens, and arrives in the
 * same place at the same rate. A steeper exponent was the only tool for
 * "clear near field" before, and it paid for it by squeezing the whole
 * transition — which is why FOG_P had climbed to 3.25 and why the near band's
 * blue had to be retired to get there.
 *
 * THE DEPTH LADDER, which is the thing this change was most likely to break and
 * the one verdict in the file that is praise rather than a defect: "B builds
 * four distinct depth planes — near island, near sea, far sea, headland — each
 * a separate value step". Three instruments, three axes, all `cam=far`, 09:00,
 * ultra, captures taken back to back under one protocol with the "before" run
 * through the `t0: 0` override:
 *
 *    LAND, median L by true distance (`sh-run.mjs`, meter left running)
 *                    450-600   600-750   750-900   900-1100  1100-1400  1400-1800   span
 *      before          53.8      54.8      77.8      101.8      118.9      134.7    80.9
 *      after           54.7      51.3      74.4       99.8      128.9      151.9    97.2
 *
 *    CANOPY PIXELS, masked on a fog-off frame of the same world (`sk-canopy.py`)
 *                    550-700   700-850   850-1000  1000-1150  1150-1300   span
 *      fog off         43.0      44.3      47.5       45.9       46.3       3.3
 *      before          44.6      53.6      63.9       76.3       91.0      46.4
 *      after           42.1      44.0      49.4       64.7       93.9      51.8
 *
 *    OPEN WATER, p90-p10 of luminance over a fog-off mask (`sk-water.py`)
 *      fog off 52.5    before 73.1    after 88.9
 *
 * Every ladder is longer than it was. The canopy's is the decisive one: it is
 * the instrument built for the decal verdict, its far rung does not move
 * (91.0 -> 93.9) and its near rungs come back to the material's own colour, so
 * the island is graded harder from end to end than it was and the sea grades
 * itself harder as well. The land ladder is 20% longer and the water's
 * shelf-to-deep spread is up 22%.
 *
 * THE JOIN AT THE MAINLAND'S FOOT, which is the constraint the scale was capped
 * by and the one error a viewer reads instantly — the water in front coming out
 * PALER than the land behind it. `sk-mainedge.mjs` (taught the new terms the
 * same way, parsed out of the compiled chunk), nine columns across the
 * shoreline, ultra, mean at 2005 m:
 *
 *                    sea's fog factor   mainland's own   rendered step, L
 *      P=3.25              0.603             0.542            11.3
 *      P=1.15              0.586             0.542            12.1
 *
 * The sea still claims a little more air than the headland does and now claims
 * slightly less of it than before, the step is unchanged inside the noise of a
 * terrain edit, and the sea reads 154.7 against the headland's 166.9 — twelve
 * luminance on the safe side. The far field was held by construction and it
 * measures as held.
 *
 * THE BUDGET. `harness/sh-perf.mjs`, same page, the shell on and off:
 *
 *                  draws   triangles   first frame   frame p50 / p95   chunk
 *      before        200   1,170,134      3254 ms      8.0 / 9.7 ms   1042 ch
 *      after         200   1,170,092      3316 ms      8.0 / 9.9 ms   1167 ch
 *
 * Draw calls and triangles are untouched — the change is four ALU ops and one
 * `log` inside a branch every fogged fragment already took — and 62 ms of first
 * frame and 0.2 ms of p95 are inside the run-to-run spread of this machine.
 *
 * THE STATUS BOARDS, which is the acceptance test and not an aesthetic one.
 * `harness/sh-floor.mjs` — sn-floor.mjs with the fog override added and nothing
 * else changed, so the frozen rig and the per-capture box recompute the author
 * warned about are intact — on the real page at :5612/floor, seven seeded
 * instruments, five captures per state, medians, 09:00:
 *
 *      board          weber before   after   A/A noise
 *      PAC Flash 2        0.380       0.460     0.010
 *      Multitek NS        0.200       0.190     0.004
 *      OptiMPP 2          0.160       0.160     0.008
 *      Koehler CP         0.090       0.130     0.021
 *      OptiMPP 1          0.010       0.060     0.009
 *      Multitek S         0.010       0.030     0.012
 *      PAC Flash 1        0.010       0.010     0.006
 *
 * No board loses contrast beyond the noise and the two that were sitting at
 * 0.010 — the failure mode gi's fill cut produced — come UP. The type inside
 * the cards gains as well, p95-p05 within the card up on all seven (89.4 ->
 * 113.4 on the worst of them). The reason it is an improvement rather than a
 * wash is the one under FOG_S: the boards' own fog comes from labels.js's
 * hardcoded curve on `scene.fog.density`, which this change deliberately does
 * not touch, while the world behind them got its local colour back.
 *
 * WHAT IS LEFT, AND WHOSE IT IS. `sn-decomp.mjs` re-run on this build, same
 * protocol, each term ablated alone over the same pixels with the stop pinned:
 *
 *      ablation                    before this round     after
 *      base                             0.79 stops       1.16
 *      gi's fill cut 50x                0.97             1.53
 *      AERIAL PERSPECTIVE KILLED        1.61             1.66
 *      fog and fill both killed         2.25             2.35
 *
 * and `sn-bar.mjs`, which is the instrument that names the BAR specifically —
 * ground occluded by something over six metres tall that is not vegetation —
 * run twice back to back at ONE pinned stop of 3.7992, the "before" through the
 * `t0: 0` override (`harness/sh-bar.mjs`, sn-bar with the override added):
 *
 *                                     before     after
 *      BAR, tall built casters          0.74      0.98   stops
 *      shadow step, all occluders       1.00      1.40
 *      FORM: lit hill face vs shaded    2.13      2.95
 *      foliage median L                 54.5      37.9
 *      frame p1 / p5                26.1/34.8  18.1/23.2
 *      frame % under L32                3.53     11.97
 *      frame % under L20                0.03      2.30
 *      ground-only % under L32          6.43     21.80
 *
 * The three verdicts five rounds were spent on are all in that table. "No
 * visible shadow": the bar gains a third of a stop. "Nothing reads as solid":
 * the form step — which is pure shading, no cast shadow in it, and which
 * buildings.js spent a round on — gains 0.82 stops, because it was being filled
 * in by airlight exactly like everything else. "No black point": the frame goes
 * from 0.03% under L20 to 2.30%, and a fifth of the ground is now under L32.
 * "No foliage interior": the canopy's median drops 17 codes.
 *
 * The haze was 55% of the shadowed pixel and is now 32%; gi's fill was 17% and
 * is now 26%. What is still available HERE is 0.50 stops, and it costs the far
 * field, because everything cheaper than that has been spent. What is available
 * in gi.js is now 0.37 stops of fill, which is more than twice what it was
 * worth when gi measured it and declined to spend it — the 6.7x cut that bought
 * 0.18 stops then is a different trade against a frame with a black point in
 * it. That is a note for gi's owner and not a request.
 *
 * THE EXPOSURE METER TAKES HALF OF THIS AND THAT IS NOT A DEFECT. gi meters the
 * frame and writes the composite's stop; less haze means a darker frame means
 * the meter opens up, from 3.165 to 3.800 at `cam=far` 09:00. So the shadow
 * comes back part of the way in absolute value and the frame does not get
 * dimmer — median L goes UP, 118.8 to 126.4, while p1 goes DOWN, 24.3 to 18.1.
 * That is the shape of the win: the histogram got WIDER at both ends rather
 * than sliding down. Frame standard deviation 47.4 -> 55.6 and frame mean
 * saturation 84.6 -> 82.9, so the extra contrast is not bought with chroma,
 * which the "narrow, unified, low-saturation range behind UI" brief forbids. */
const FOG_S = 1.940;
/* The shell, in optical depth. At the clear-air density and the aerial
 * camera's height term this is the first 948 m of the view; at `cam=low` it is
 * 771 m, and at `fog: 0.9` it is 31 m, because it is an optical depth and not a
 * distance — thick air uses it up immediately, which is what a shell of clear
 * air physically does, and it is why the fog preset does not move. Evaluating
 * the compiled chunk at `cam=low` under `weather=fog` (density 0.01565, height
 * term 0.98), old curve against new:
 *
 *              60 m    100 m    150 m    200 m
 *    before    0.480    0.866    0.879    0.879
 *    after     0.503    0.792    0.868    0.879
 *
 * The half-closed range does not move at all and full closure goes from about
 * 100 m to about 130 m. The anchor under `density` — "`fog: 0.9` still shuts
 * the view down at roughly a hundred metres" — is intact, and it is intact
 * because the scale went into FOG_S rather than into the density the weather
 * term is added to.
 *
 * The other camera that matters is the one the site is WALKED at, and five
 * earlier rounds were written up for washing it out to buy depth in the aerial.
 * `cam=low` (y 24), rendered, two page loads back to back, the "before"
 * through the `t0: 0` override. gi's meter pegs at its 4.00 ceiling in BOTH,
 * so this pair is a true single-stop comparison and not two exposures — the
 * haze at this altitude is too small to move the meter either way. Fog factor
 * per band, from the compiled chunk on each sample's own depth and height:
 *
 *              300-450  450-600  750-900  900-1100  1100-1400  1400-1800
 *    before     0.0047   0.0156   0.0804    0.1547     0.3174     0.3711
 *    after      0.0000   0.0000   0.0286    0.1493     0.3561     0.4104
 *
 * The walked view goes to ZERO haze out to 600 m — the whole of a site whose
 * longest bay is 44 m and whose floor is a few hundred metres across — gives up
 * two thirds of it at 750-900, and past a kilometre gains three to four
 * hundredths, because at this altitude the height term is 0.98, the shell is
 * used up by 771 m and the shallower exponent is a little heavier beyond it.
 * That is the right shape: the operator's working distance loses its veil and
 * the backdrop behind the site keeps receding.
 *
 * And what it costs the ground camera, which is the question five earlier
 * rounds were failed on — washing out the walked frame to buy depth in the
 * aerial. Nothing, in either direction:
 *
 *                   p1     p5    p50    mean   % under 20   shadow step
 *    before        25.3   31.4  110.2   117.0     0.20        2.253 stops
 *    after         24.8   31.0  109.8   116.5     0.24        2.279
 *
 * Half a code on every percentile. The ground camera already had its blacks —
 * it was never the frame with the problem, and this change does not take
 * anything from it to fix the one that was. */
const FOG_T0 = 0.9378;
/* The shell's knee, in the same units. A hard `max(tau - t0, 0)` has a C1
 * break, and a C1 break in a term that varies smoothly with depth draws a
 * contour on the ground at the radius where it bites. This is the numerically
 * stable softplus, so the onset is a bend rather than an edge; 0.06 of optical
 * depth is about 80 m of distance at the aerial camera, which is under the
 * width of one tank. Set it to 0 for the hard form. */
const FOG_W = 0.06;

/* Anything a harness or another subsystem needs to pin about the haze goes
 * through here, read once at build for the parts baked into the shader chunk
 * (`k`, `max`, `h`, `s`, `t0`, `w`) and live for the density. See
 * `setFogDensity` below for why an override exists at all. */
function fogOverride() {
  const o = globalThis.__lemFog;
  return (o && typeof o === 'object') ? o : {};
}


function patchFogChunks(THREE_) {
  const C = THREE_ && THREE_.ShaderChunk;
  if (!C || !C.fog_fragment || C.__lemAerial) return false;
  const f = n => n.toFixed(1);
  /* All seven are compiled into every material in the world, so they can only
   * be overridden before the first one is built — which is here, and is why a
   * sweep over them costs a page reload rather than a uniform write. */
  const ov = fogOverride();
  const K = Array.isArray(ov.k) && ov.k.length === 3 ? ov.k.map(Number) : FOG_K;
  const MAX = Number.isFinite(ov.max) ? ov.max : FOG_MAX;
  const H = Number.isFinite(ov.h) ? ov.h : FOG_H;
  const P = Number.isFinite(ov.p) ? ov.p : FOG_P;
  const S = Number.isFinite(ov.s) ? ov.s : FOG_S;
  const T0 = Number.isFinite(ov.t0) ? ov.t0 : FOG_T0;
  const SW = Number.isFinite(ov.w) ? ov.w : FOG_W;
  /* The shell is emitted only when there is one, so `t0: 0` reproduces the
   * pre-shell chunk EXACTLY rather than approximately — the "before" row of
   * every A/B in this file is taken through that override on the shipping
   * build, and a softplus with t0 = 0 is not the identity. */
  const shell = !(T0 > 0) ? '' : (SW > 0 ? `
    float lemX = lemTau - ${T0.toFixed(4)};
    lemTau = max( lemX, 0.0 )
      + ${SW.toFixed(4)} * log( 1.0 + exp( - abs( lemX ) / ${SW.toFixed(4)} ) );` : `
    lemTau = max( lemTau - ${T0.toFixed(4)}, 0.0 );`);
  C.fog_pars_vertex = `#ifdef USE_FOG
  varying float vFogDepth;
  varying float vFogHeight;
#endif`;
  /* `mvPosition` is the only position in scope here that survives skinning,
   * morphing and instancing alike, so the world height is recovered from it
   * rather than from `transformed` — an instanced tree would otherwise be
   * fogged as if it stood at the origin of its batch. The view matrix is
   * rigid, so its inverse rotation is its transpose and one dot product is the
   * whole of it. */
  C.fog_vertex = `#ifdef USE_FOG
  vFogDepth = - mvPosition.z;
  vFogHeight = dot( viewMatrix[1].xyz, mvPosition.xyz ) + cameraPosition.y;
#endif`;
  C.fog_pars_fragment = `#ifdef USE_FOG
  uniform vec3 fogColor;
  varying float vFogDepth;
  varying float vFogHeight;
  #ifdef FOG_EXP2
    uniform float fogDensity;
  #else
    uniform float fogNear;
    uniform float fogFar;
  #endif
#endif`;
  C.fog_fragment = `#ifdef USE_FOG
  #ifdef FOG_EXP2
    float lemH0 = cameraPosition.y - ${f(FOG_Y0)};
    float lemH1 = vFogHeight - ${f(FOG_Y0)};
    float lemA = exp( - max( lemH0, -600.0 ) / ${f(H)} );
    float lemB = exp( - max( lemH1, -600.0 ) / ${f(H)} );
    float lemDy = lemH1 - lemH0;
    float lemAvg = abs( lemDy ) < 1.0
      ? 0.5 * ( lemA + lemB )
      : ${f(H)} * ( lemA - lemB ) / lemDy;
    float lemTau = fogDensity * ${S.toFixed(4)} * vFogDepth * clamp( lemAvg, 0.0, 6.0 );${shell}
    /* pow of zero is undefined in GLSL and depth is zero at the near plane. */
    float lemU = pow( max( lemTau, 1e-5 ), ${P.toFixed(3)} );
    /* The chromatic weight multiplies the optical depth and is never inside
     * the power with it: raising it too turned a stated 2:1 into a measured
     * 3.16:1. See the note above the constants. */
    vec3 fogFactor = vec3( ${MAX.toFixed(3)} )
      * ( 1.0 - exp( - lemU * vec3( ${K.map(v => v.toFixed(3)).join(', ')} ) ) );
  #else
    vec3 fogFactor = vec3( smoothstep( fogNear, fogFar, vFogDepth ) );
  #endif
  gl_FragColor.rgb = mix( gl_FragColor.rgb, fogColor, fogFactor );
#endif`;
  C.__lemAerial = true;
  return true;
}

/* ---- the JS half of the model -------------------------------------------- */

/* A transcription of `scatter` above, for the dozen directions the CPU needs:
 * the sun's own colour, the ambient, and the fog. A GPU readback would avoid
 * the duplication and cost a pipeline flush every time the weather changed;
 * twelve integrals cost nothing and never stall. */
function scatterJS(rd, sunDir, mieMul, msSrc, irradiance, viewSteps = 12, lightSteps = 5) {
  const Rg = A.Rg, Ra = A.Ra;
  const roy = Rg + A.eye;
  const sphere = (ox, oy, oz, dx, dy, dz, r, near) => {
    const b = ox * dx + oy * dy + oz * dz;
    const c = ox * ox + oy * oy + oz * oz - r * r;
    const d = b * b - c;
    if (d < 0) return -1;
    const s = Math.sqrt(d);
    return near ? -b - s : -b + s;
  };
  let tTop = sphere(0, roy, 0, rd[0], rd[1], rd[2], Ra, false);
  if (tTop <= 0) return [0, 0, 0];
  const tGround = sphere(0, roy, 0, rd[0], rd[1], rd[2], Rg, true);
  if (tGround > 0) tTop = Math.min(tTop, tGround);

  let odR = 0, odM = 0, odO = 0, t = 0;
  const sumR = [0, 0, 0], sumM = [0, 0, 0], sumMS = [0, 0, 0];

  for (let i = 0; i < viewSteps; i++) {
    const f = (i + 1) / viewSteps;
    const t1 = tTop * f * f * f;
    const seg = t1 - t;
    const px = rd[0] * (t + seg * 0.5);
    const py = roy + rd[1] * (t + seg * 0.5);
    const pz = rd[2] * (t + seg * 0.5);
    const h = Math.max(Math.hypot(px, py, pz) - Rg, 0);
    const hr = Math.exp(-h / A.Hr) * seg;
    const hm = Math.exp(-h / A.Hm) * seg;
    const ho = Math.max(0, 1 - Math.abs(h - A.Oc) / A.Ow) * seg;
    odR += hr; odM += hm; odO += ho;

    const tl = sphere(px, py, pz, sunDir[0], sunDir[1], sunDir[2], Ra, false);
    let odLR = 0, odLM = 0, odLO = 0, tl2 = 0, blocked = false;
    for (let j = 0; j < lightSteps; j++) {
      const g = (j + 1) / lightSteps;
      const l1 = tl * g * g;
      const segL = l1 - tl2;
      const s = tl2 + segL * 0.5;
      const hl = Math.hypot(px + sunDir[0] * s, py + sunDir[1] * s,
                            pz + sunDir[2] * s) - Rg;
      if (hl < 0) { blocked = true; break; }
      odLR += Math.exp(-hl / A.Hr) * segL;
      odLM += Math.exp(-hl / A.Hm) * segL;
      odLO += Math.max(0, 1 - Math.abs(hl - A.Oc) / A.Ow) * segL;
      tl2 = l1;
    }
    for (let c = 0; c < 3; c++) {
      if (!blocked) {
        const tau = A.betaR[c] * (odR + odLR) + A.betaM * 1.1 * mieMul * (odM + odLM)
                  + A.betaO[c] * (odO + odLO);
        const att = Math.exp(-tau);
        sumR[c] += att * hr;
        sumM[c] += att * hm;
      }
      const tv = Math.exp(-(A.betaR[c] * odR + A.betaM * 1.1 * mieMul * odM
                            + A.betaO[c] * odO));
      sumMS[c] += tv * (hr * A.betaR[c] + hm * A.betaM * mieMul * 0.7);
    }
    t = t1;
  }

  const mu = rd[0] * sunDir[0] + rd[1] * sunDir[1] + rd[2] * sunDir[2];
  const phaseR = (3 / (16 * Math.PI)) * (1 + mu * mu);
  const g2 = A.g * A.g;
  const phaseM = (3 / (8 * Math.PI)) * ((1 - g2) * (1 + mu * mu)) /
                 ((2 + g2) * Math.pow(Math.max(1 + g2 - 2 * A.g * mu, 1e-4), 1.5));
  const msPhase = 0.72 + 0.62 * Math.max(mu, 0);
  return [0, 1, 2].map(c =>
    (sumR[c] * A.betaR[c] * phaseR + sumM[c] * A.betaM * mieMul * phaseM) * irradiance
    + sumMS[c] * msSrc[c] * msPhase);
}

/** The twilight layer, mirroring `TWILIGHT_GLSL`. */
function twilightJS(dir, sunDir, amp) {
  if (amp <= 0) return [0, 0, 0];
  const dl = Math.hypot(dir[0], dir[2]) + 1e-5;
  const sl = Math.hypot(sunDir[0], sunDir[2]) + 1e-5;
  const muh = (dir[0] * sunDir[0] + dir[2] * sunDir[2]) / (dl * sl);
  let az = 0.24 + 0.76 * Math.pow(Math.max(muh, 0), 1.6);
  az = mix(az, 0.5, clamp(Math.abs(dir[1]) * 1.7, 0, 1));
  const band = Math.exp(-Math.max(dir[1], 0) * 3.4) * 0.85 + 0.15;
  const t = clamp(Math.max(dir[1], 0) * 3.0 + (1 - az) * 0.55, 0, 1);
  const warm = [1.00, 0.325, 0.075], cool = [0.13, 0.235, 0.62];
  return [0, 1, 2].map(i => mix(warm[i], cool[i], t) * amp * band * az);
}

/** How strong that layer is, from the sun's elevation in degrees. */
function twilightAmount(altDeg) {
  const rise = clamp((4.5 - altDeg) / 5.0, 0, 1);
  const fall = Math.exp(-Math.max(-altDeg, 0) / 5.0);
  return rise * fall * smoothstep(-17, -11, altDeg);
}

/** Transmittance of the whole air column toward `dir` — the colour a direct
 *  beam arrives with. Below the horizon it goes to nothing, which is what makes
 *  the sun set rather than merely dim. */
function transmittance(dir, mieMul, steps = 10) {
  const Rg = A.Rg, Ra = A.Ra, oy = Rg + A.eye;
  const b = oy * dir[1];
  const d = b * b - (oy * oy - Ra * Ra);
  if (d < 0) return [0, 0, 0];
  const tTop = -b + Math.sqrt(d);
  const seg = tTop / steps;
  let odR = 0, odM = 0, odO = 0;
  for (let i = 0; i < steps; i++) {
    const s = seg * (i + 0.5);
    const h = Math.hypot(dir[0] * s, oy + dir[1] * s, dir[2] * s) - Rg;
    if (h < 0) return [0, 0, 0];
    odR += Math.exp(-h / A.Hr) * seg;
    odM += Math.exp(-h / A.Hm) * seg;
    odO += Math.max(0, 1 - Math.abs(h - A.Oc) / A.Ow) * seg;
  }
  return [0, 1, 2].map(k => Math.exp(-(A.betaR[k] * odR + A.betaM * 1.1 * mieMul * odM
                                       + A.betaO[k] * odO)));
}

/** The isotropic multiple-scatter source: how brightly lit the air around us is
 *  by light that already bounced once. It depends only on the sun's elevation,
 *  so it is one CPU calculation per change and a uniform for the shader. */
const MS_TINT = (() => {
  const mean = (A.betaR[0] + A.betaR[1] + A.betaR[2]) / 3;
  return A.betaR.map(b => Math.pow(b / mean, A.msTintPow));
})();

function msSource(sunDir, mieMul) {
  const altDeg = Math.asin(clamp(sunDir[1], -1, 1)) / DEG;
  const tint = transmittance(sunDir, mieMul);
  /* Purely a daylight term: below the horizon the authored twilight layer owns
   * the sky, and leaving this running there would double-count it. */
  const day = smoothstep(-1, 2, altDeg);
  return tint.map((v, c) => v * MS_TINT[c] * A.sunI * A.ms * day);
}

/** How far open the sky's own aperture is, from the sun's elevation. One stop
 *  cannot hold both a noon horizon and a golden-hour zenith inside the
 *  composite's fixed range; see `A.skyStop`. */
function skyStop(sunAltDeg) {
  /* Held shut through the whole working day and only opening once the sun is
   * genuinely low. Opening it gradually from twenty-five degrees put a
   * mid-morning sky a third of a stop hot and pushed p95 to 222, well past the
   * 204-208 the reference set holds; there is nothing to compensate for at ten
   * degrees, because at ten degrees the sky is still bright. */
  return A.skyStop * mix(A.duskStop, 1, smoothstep(-8, 12, sunAltDeg));
}

/* ---- where the sun is ---------------------------------------------------- */

/* A real solar-position formula, at a site chosen so that the six times this
 * sky is reviewed at each land somewhere different: 6.2 is the sun just clear
 * of the hills, 8 is mid-morning, 13 is noon, 18.4 is golden, 20.5 is the blue
 * hour with the band still on the horizon, and 22 is dark. That takes a
 * high-latitude spring — 62°N in early April, on summer time, so solar noon
 * falls at 12:17. A lower latitude squeezes dusk into twenty minutes and half
 * the day looks the same. */
const SITE = {lat: 62 * DEG, decl: 6.5 * DEG, noon: 12.29};

function celestial(hours, decl, lat = SITE.lat) {
  const H = (hours - SITE.noon) * 15 * DEG;
  const sinAlt = clamp(Math.sin(lat) * Math.sin(decl) +
                       Math.cos(lat) * Math.cos(decl) * Math.cos(H), -1, 1);
  const alt = Math.asin(sinAlt);
  const cosAlt = Math.max(Math.cos(alt), 1e-4);
  const sinAz = -Math.cos(decl) * Math.sin(H) / cosAlt;
  const cosAz = (Math.sin(decl) - Math.sin(lat) * sinAlt) /
                Math.max(Math.cos(lat) * cosAlt, 1e-4);
  const az = Math.atan2(sinAz, cosAz);
  /* North is -Z and east is +X, so azimuth runs clockwise from -Z the way a
   * compass bearing does, and the sun comes up over +X in the morning. */
  return [cosAlt * Math.sin(az), sinAlt, -cosAlt * Math.cos(az)];
}

/* ---- procedural noise volumes -------------------------------------------- */

function hash3(x, y, z, seed) {
  let h = x * 374761393 + y * 668265263 + z * 1103515245 + seed * 2246822519;
  h = (h ^ (h >>> 13)) * 1274126177;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function valueNoise3(x, y, z, period, seed) {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
  const xf = x - xi, yf = y - yi, zf = z - zi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf), w = zf * zf * (3 - 2 * zf);
  const m = a => ((a % period) + period) % period;
  const g = (a, b, c) => hash3(m(a), m(b), m(c), seed);
  const c00 = mix(g(xi, yi, zi), g(xi + 1, yi, zi), u);
  const c10 = mix(g(xi, yi + 1, zi), g(xi + 1, yi + 1, zi), u);
  const c01 = mix(g(xi, yi, zi + 1), g(xi + 1, yi, zi + 1), u);
  const c11 = mix(g(xi, yi + 1, zi + 1), g(xi + 1, yi + 1, zi + 1), u);
  return mix(mix(c00, c10, v), mix(c01, c11, v), w);
}

/** Inverted 3D worley on a wrapping lattice. Feature points are precomputed
 *  because the 27-neighbour loop is the whole cost of the volume, and hashing
 *  inside it triples that for nothing. */
function worley3Field(size, period, seed) {
  const pts = new Float32Array(period * period * period * 3);
  for (let z = 0; z < period; z++) {
    for (let y = 0; y < period; y++) {
      for (let x = 0; x < period; x++) {
        const i = ((z * period + y) * period + x) * 3;
        pts[i] = x + hash3(x, y, z, seed);
        pts[i + 1] = y + hash3(x, y, z, seed + 17);
        pts[i + 2] = z + hash3(x, y, z, seed + 53);
      }
    }
  }
  const out = new Float32Array(size * size * size);
  const scale = period / size;
  const wrap = a => ((a % period) + period) % period;
  for (let z = 0; z < size; z++) {
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const px = x * scale, py = y * scale, pz = z * scale;
        const cx = Math.floor(px), cy = Math.floor(py), cz = Math.floor(pz);
        let best = 1e9;
        for (let oz = -1; oz <= 1; oz++) {
          for (let oy = -1; oy <= 1; oy++) {
            for (let ox = -1; ox <= 1; ox++) {
              const gx = cx + ox, gy = cy + oy, gz = cz + oz;
              const i = ((wrap(gz) * period + wrap(gy)) * period + wrap(gx)) * 3;
              const dx = pts[i] + (gx - wrap(gx)) - px;
              const dy = pts[i + 1] + (gy - wrap(gy)) - py;
              const dz = pts[i + 2] + (gz - wrap(gz)) - pz;
              const d = dx * dx + dy * dy + dz * dz;
              if (d < best) best = d;
            }
          }
        }
        /* Inverted: 1 at a feature point, 0 a cell away. Distances are already
         * in lattice units, so there is nothing to normalise by. */
        out[(z * size + y) * size + x] = clamp(1 - Math.sqrt(best), 0, 1);
      }
    }
  }
  return out;
}

/* ---- the subsystem -------------------------------------------------------- */

/* Up two at the top of the ladder, and free: the march's loop bound is already
 * 14, so this spends no extra register pressure and no extra branch. Denser
 * clouds make each step a bigger jump in optical depth, and the per-pixel entry
 * jitter that hides the step boundaries turns that into visible speckle at the
 * cloud edges. Shorter steps is the only real cure. */
const CLOUD_STEPS = {ultra: 14, high: 11, medium: 7, low: 4, floor: 2};

export class Sky {
  constructor(ctx) {
    this.ctx = ctx;

    /* The contract gi.js reads. `sunDirection` is the dominant celestial light:
     * the sun by day and the moon once the sun is well down, so a directional
     * light driven straight off it lights and shadows the night without gi.js
     * having to know there is a moon. `isNight` says which one it is, and
     * `moonDirection` / `trueSunDirection` are there for anyone who needs the
     * distinction. `sunColour` is a normalised chromaticity and `sunIntensity`
     * carries the magnitude, so a DirectionalLight can take them as-is. */
    this.sunDirection = new THREE.Vector3(0.4, 0.6, 0.5).normalize();
    this.sunColour = new THREE.Color(1, 0.96, 0.9);
    this.ambientColour = new THREE.Color(0.35, 0.45, 0.62);
    this.sunIntensity = 2.6;
    this.isNight = false;

    this.moonDirection = new THREE.Vector3(-0.4, 0.5, -0.5).normalize();
    this.trueSunDirection = this.sunDirection.clone();
    this.horizonColour = new THREE.Color(0.5, 0.6, 0.75);
    this.fogColour = new THREE.Color(0.5, 0.6, 0.75);

    this.hours = ctx.world?.timeOfDay ?? ctx.timeOfDay ?? 13;
    this._fogPin = null;
    this._tier = ctx.quality?.name || 'ultra';
    this._dirty = true;
    this._envDirty = true;
    this._envOk = true;
    this._lastEnvAt = -99;
    this._t = 0;
    this._ok = false;
  }

  /* ---- build ------------------------------------------------------------ */

  async build() {
    try {
      const ctx = this.ctx;
      const renderer = ctx.renderer;
      if (!renderer) return;

      /* Before anything else, and before any other subsystem has made a
       * material: distance haze has to live inside their shaders. */
      try { patchFogChunks(THREE); }
      catch (err) { console.warn('[sky] fog chunks left as three shipped them', err); }

      this._noise = this._buildCloudVolume();
      this._detail = this._buildDetailTexture();

      /* 512x256, not 256x128. The LUT is only redrawn when the sun or the
       * weather moves, so its cost is measured in whole seconds of not
       * happening; what it buys every frame is four times finer reconstruction
       * of the steepest part of the gradient, which is half of why the horizon
       * banded. The dither in the dome is the other half. */
      this._lutW = 512; this._lutH = 256;
      this._lut = new THREE.WebGLRenderTarget(this._lutW, this._lutH, {
        type: THREE.HalfFloatType,
        minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter,
        wrapS: THREE.ClampToEdgeWrapping, wrapT: THREE.ClampToEdgeWrapping,
        depthBuffer: false, stencilBuffer: false,
        colorSpace: THREE.LinearSRGBColorSpace,
      });

      this._lutPass = this._fullscreen(LUT_FS, {
        uSunDir: {value: new THREE.Vector3(0, 1, 0)},
        uMoonDir: {value: new THREE.Vector3(0, -1, 0)},
        uMS: {value: new THREE.Vector3(0, 0, 0)},
        uMieMul: {value: 1.0}, uSunI: {value: A.sunI},
        uMoonI: {value: 0.0}, uNightLift: {value: 0.0}, uTwiAmp: {value: 0.0},
      });
      this._lutScene = new THREE.Scene();
      this._lutScene.add(this._lutPass);
      this._lutCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

      this._uniforms = {
        uSkyLut: {value: this._lut.texture},
        uNoise: {value: this._noise},
        uDetail: {value: this._detail},
        uSunDir: {value: new THREE.Vector3(0, 1, 0)},
        uMoonDir: {value: new THREE.Vector3(0, -1, 0)},
        uSunLight: {value: new THREE.Vector3(2, 1.9, 1.7)},
        uSkyLight: {value: new THREE.Vector3(0.3, 0.4, 0.6)},
        uFogColour: {value: new THREE.Vector3(0.5, 0.6, 0.75)},
        uSunTint: {value: new THREE.Vector3(1, 1, 1)},
        uWind: {value: new THREE.Vector2(1, 0)},
        uTime: {value: 0},
        uCloud: {value: 0.25}, uRain: {value: 0}, uFogAmt: {value: 0.1},
        uNight: {value: 0}, uMoonPhase: {value: 0.76}, uMoonI: {value: 0},
        uCloudBase: {value: 1500}, uCloudThick: {value: 1400},
        uCloudDensity: {value: 0.0021},
        uStars: {value: 1}, uSunDisc: {value: 1},
        uCloudSteps: {value: CLOUD_STEPS[this._tier] ?? 10},
        uDetailOn: {value: 1},
        uSkyStop: {value: A.skyStop}, uHiDesat: {value: 0.34},
        uSkyCeil: {value: SKY_CEIL}, uDiscGain: {value: 130.0},
        uLutTexel: {value: new THREE.Vector2(1 / this._lutW, 1 / this._lutH)},
      };

      const mat = new THREE.ShaderMaterial({
        vertexShader: DOME_VS, fragmentShader: DOME_FS,
        uniforms: this._uniforms, glslVersion: THREE.GLSL3,
        side: THREE.BackSide, depthTest: false, depthWrite: false,
        fog: false, toneMapped: false,
      });
      this._material = mat;

      /* Radius one, parked on the camera every frame. A dome big enough to sit
       * inside the far plane is the usual approach and it fights the depth
       * range for nothing: with no depth test and the first draw slot, one unit
       * across is exactly as much sky as four kilometres of it. */
      const geo = new THREE.SphereGeometry(1, 72, 36);
      this._geometry = geo;
      this._dome = new THREE.Mesh(geo, mat);
      this._dome.frustumCulled = false;
      this._dome.renderOrder = -1000;
      this._dome.matrixAutoUpdate = false;
      ctx.scene.add(this._dome);

      /* A second dome at the origin, in its own scene, is what PMREM renders
       * for the environment map. It shares every uniform *object* with the
       * visible dome — same sun, same clouds, same weather, one update — but
       * carries its own stop, pinned at the physical scale. The light the
       * world receives has to stay in the units gi.js and the materials are
       * tuned in; only what the camera sees is re-exposed. */
      const envUniforms = Object.assign({}, this._uniforms, {
        uSkyStop: {value: 1.0}, uHiDesat: {value: 0.0}, uSkyCeil: {value: 0.0},
        /* The disc stays at the gain the world's lighting was tuned against.
         * The visible dome drives it harder so the disc survives the glare it
         * sits inside, but that is a camera decision, and pushing it into the
         * environment map instead re-lights the entire site off a hotter sun —
         * measured: raising it here alone moved the whole frame's mean
         * luminance by twenty-three and its first percentile by twenty-nine,
         * because gi.js grades against what this map gives it. */
        uDiscGain: {value: 55.0},
      });
      this._envMaterial = new THREE.ShaderMaterial({
        vertexShader: DOME_VS, fragmentShader: DOME_FS,
        uniforms: envUniforms, glslVersion: THREE.GLSL3,
        side: THREE.BackSide, depthTest: false, depthWrite: false,
        fog: false, toneMapped: false,
      });
      this._envScene = new THREE.Scene();
      this._envDome = new THREE.Mesh(geo, this._envMaterial);
      this._envDome.frustumCulled = false;
      this._envScene.add(this._envDome);

      try {
        this._pmrem = new THREE.PMREMGenerator(renderer);
        this._pmrem.compileCubemapShader?.();
      } catch (err) {
        this._pmrem = null; this._envOk = false;
        console.warn('[sky] no PMREM — the world falls back to flat ambient', err);
      }

      /* One fog object, kept for the life of the map. Other subsystems read
       * `scene.fog` at build time to size their own distance fades, so
       * replacing it later would leave them holding a dead one. */
      this._fog = new THREE.FogExp2(0x8fa6bd, 0.0006);
      ctx.scene.fog = this._fog;
      /* The pin, if something set one before the world booted. */
      const pin = fogOverride().density;
      this._fogPin = Number.isFinite(pin) ? pin : null;

      /* gi.js is built right after this one and needs somewhere to look. */
      ctx.sky = this;

      this._ok = true;
      this._recompute();
      this._renderLut();
      this._buildEnvironment();
    } catch (err) {
      /* A missing sky is a flat grey map, not a dead one. */
      console.warn('[sky] build failed — the map continues unlit', err);
      this._ok = false;
    }
  }

  _fullscreen(fs, uniforms) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
    geo.setAttribute('uv', new THREE.BufferAttribute(
      new Float32Array([0, 0, 2, 0, 0, 2]), 2));
    const mat = new THREE.ShaderMaterial({
      vertexShader: 'out vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',
      fragmentShader: fs, uniforms, glslVersion: THREE.GLSL3,
      depthTest: false, depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.frustumCulled = false;
    return mesh;
  }

  /* ---- generated textures ------------------------------------------------ */

  /** 32³ RGBA: low-frequency perlin in R, three octaves of inverted worley in
   *  GBA. One fetch gives the shape and its erosion, which is the difference
   *  between a cloud march costing one texture read per step and one costing
   *  six. */
  _buildCloudVolume() {
    const N = 32;
    const w1 = worley3Field(N, 4, 3), w2 = worley3Field(N, 8, 11),
          w3 = worley3Field(N, 16, 29);
    const data = new Uint8Array(N * N * N * 4);
    for (let z = 0; z < N; z++) {
      for (let y = 0; y < N; y++) {
        for (let x = 0; x < N; x++) {
          const i = (z * N + y) * N + x;
          let p = 0, amp = 1, norm = 0, per = 4;
          for (let o = 0; o < 4; o++) {
            p += valueNoise3(x / N * per, y / N * per, z / N * per, per, 7 + o * 131) * amp;
            norm += amp; amp *= 0.5; per *= 2;
          }
          p /= norm;
          /* Billow it a little: clouds are lumps, and a plain value noise gives
           * a smoke plume instead. */
          p = clamp((p - 0.36) * 1.9, 0, 1);
          data[i * 4] = p * 255;
          data[i * 4 + 1] = w1[i] * 255;
          data[i * 4 + 2] = w2[i] * 255;
          data[i * 4 + 3] = w3[i] * 255;
        }
      }
    }
    const tex = new THREE.Data3DTexture(data, N, N, N);
    tex.format = THREE.RGBAFormat;
    tex.type = THREE.UnsignedByteType;
    tex.minFilter = tex.magFilter = THREE.LinearFilter;
    tex.wrapS = tex.wrapT = tex.wrapR = THREE.RepeatWrapping;
    tex.unpackAlignment = 1;
    tex.needsUpdate = true;
    return tex;
  }

  /** A small tileable FBM used for the maria on the moon and the dust lanes in
   *  the Milky Way — the two places where a flat value would read as a decal. */
  _buildDetailTexture() {
    const size = 128;
    const cv = Tex.paint(size, (x, y, u, v) => [
      Tex.fbm(u * 6, v * 6, {octaves: 5, period: 6, seed: 5}),
      Tex.fbm(u * 12 + 3.1, v * 12 + 7.7, {octaves: 4, period: 12, seed: 91}),
      Tex.fbm(u * 3, v * 3, {octaves: 4, period: 3, seed: 313}),
    ]);
    const tex = Tex.makeTexture
      ? Tex.makeTexture(cv, {repeat: 1, aniso: 4})
      : new THREE.CanvasTexture(cv);
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.colorSpace = THREE.NoColorSpace;
    tex.needsUpdate = true;
    return tex;
  }

  /* ---- the state of the sky ---------------------------------------------- */

  /** Everything that depends on the sun or the weather, recomputed in one
   *  place. Called only when one of them changed — never per frame. */
  _recompute() {
    if (!this._ok) return;
    const w = this.ctx.weather || {};
    const cloud = clamp(w.cloud ?? 0.25, 0, 1);
    const fogAmt = clamp(w.fog ?? 0.1, 0, 1);
    const rain = clamp(Math.max(w.rain ?? 0, (w.snow ?? 0) * 0.6), 0, 1);

    /* The Mie multiplier — how much aerosol is in the air. Fog is most of it;
     * cloud adds a little, because a covered sky is a hazier one. */
    const mieMul = A.mieBase + fogAmt * 4.5 + cloud * 0.5;

    const sun = celestial(this.hours, SITE.decl);
    /* The moon runs its own path, half a day out of step and a little south,
     * which is enough for it to be up through most of the night and gone by
     * lunchtime without modelling an actual lunar orbit. */
    const moon = celestial(this.hours + 12.2, -6 * DEG);
    const sunAltDeg = Math.asin(clamp(sun[1], -1, 1)) / DEG;

    this.trueSunDirection.set(sun[0], sun[1], sun[2]);
    this.moonDirection.set(moon[0], moon[1], moon[2]);

    /* Night runs from the sun touching the horizon to the end of civil
     * twilight; between those two the world is lit by an afterglow that
     * belongs to neither. */
    const nightF = smoothstep(0.5, -6, sunAltDeg);
    this.isNight = nightF > 0.5;

    const ms = msSource(sun, mieMul);
    const twiAmp = twilightAmount(sunAltDeg);
    const sunTint = transmittance(sun, mieMul);
    const moonUp = smoothstep(-2, 6, Math.asin(clamp(moon[1], -1, 1)) / DEG);
    const moonI = nightF * moonUp * A.sunI * 0.05;

    /* --- the sky's own colours, from the same integral the shader runs.
     * Sampling has to dodge the sun: the Mie forward lobe is thirty times the
     * background right around the disc, and a fog colour that includes it goes
     * white the moment the camera happens to face the light. */
    const sample = d => {
      const c = scatterJS(d, sun, mieMul, ms, A.sunI);
      const t = twilightJS(d, sun, twiAmp);
      const hz = 1 - Math.abs(d[1]);
      const lift = nightF * (0.55 + 0.75 * hz * hz);
      const mc = moonI > 0
        ? scatterJS(d, moon, mieMul, [0, 0, 0], moonI, 8, 4) : [0, 0, 0];
      const tint = [0.72, 0.84, 1.0], glow = A.nightGlow;
      return [0, 1, 2].map(i => c[i] + t[i] + mc[i] * tint[i] + glow[i] * lift);
    };

    const sunAz = Math.atan2(sun[0], sun[2]);
    const hz = (az, el) => [Math.cos(el) * Math.sin(az), Math.sin(el),
                            Math.cos(el) * Math.cos(az)];

    /* Eight points around the horizon, offset so none sits in the sun's own
     * azimuth, with the two brightest thrown away. That leaves a mean that is
     * warm when the light is warm and never spikes on the aureole. */
    const ring = [];
    for (let k = 0; k < 8; k++) ring.push(sample(hz(sunAz + (22.5 + k * 45) * DEG, 3 * DEG)));
    ring.sort((a, b) => luma(a) - luma(b));
    const horizon = [0, 1, 2].map(i =>
      ring.slice(0, 6).reduce((s, c) => s + c[i], 0) / 6);

    const zenith = sample([0, 1, 0]);
    const mid = [];
    for (let k = 0; k < 4; k++) mid.push(sample(hz(sunAz + (45 + k * 90) * DEG, 45 * DEG)));
    mid.sort((a, b) => luma(a) - luma(b));
    const upper = [0, 1, 2].map(i => mid.slice(0, 3).reduce((s, c) => s + c[i], 0) / 3);

    /* Overcast flattens the sky toward one luminance and drains the blue out of
     * it. Without this the heavy presets stay saturated, because the scattering
     * model has no idea there is a cloud deck above it. */
    const overcast = smoothstep(0.35, 0.95, cloud);
    const damp = 1 - overcast * (0.32 + rain * 0.30);
    const flatten = (c, k) => {
      const g = (c[0] + c[1] + c[2]) / 3;
      return c.map(v => mix(v, g * (1 - rain * 0.22), k) * damp);
    };
    const horizonW = flatten(horizon, overcast * 0.55);
    const upperW = flatten(upper, overcast * 0.55);
    const zenithW = flatten(zenith, overcast * 0.70);

    /* Ambient: the hemisphere average, in roughly the proportions a
     * cosine-weighted integral would give zenith, mid-sky and horizon. */
    const ambient = [0, 1, 2].map(i =>
      zenithW[i] * 0.34 + upperW[i] * 0.42 + horizonW[i] * 0.24);

    /* Direct sunlight. `sunTint` is a transmittance, so this already has the
     * right colour and the right relative brightness; the cloud deck takes its
     * share off the top. */
    const cloudBlock = 1 - overcast * (0.72 + rain * 0.18);
    /* What reaches the top of the cloud deck, and what reaches the ground under
     * it. Only the second has the deck's share taken off: the clouds are lit by
     * the first, because the march already works out how much of itself each
     * sample is standing behind. Handing them the blocked figure was counting
     * the deck twice, and an overcast sky came back as a black lid. */
    const directTop = sunTint.map(v => v * A.sunI * 0.105);
    const direct = directTop.map(v => v * cloudBlock);

    /* --- publish ------------------------------------------------------- */

    const dayKey = smoothstep(-1.2, 0.8, sunAltDeg);
    const keyDir = dayKey > 0.5 ? sun : moon;
    this.sunDirection.set(keyDir[0], keyDir[1], keyDir[2]);
    if (this.sunDirection.lengthSq() < 1e-6) this.sunDirection.set(0, 1, 0);
    this.sunDirection.normalize();

    if (dayKey > 0.5) {
      const peak = Math.max(direct[0], direct[1], direct[2], 1e-5);
      this.sunColour.setRGB(direct[0] / peak, direct[1] / peak, direct[2] / peak);
      /* A directional light's intensity, not a radiance: gi.js multiplies the
       * colour by it, so the magnitude lives here and the chromaticity above.
       * Around 2.6 at noon, which is a white roof at about 0.8 linear. */
      this.sunIntensity = clamp(peak * 0.95, 0, 3.2);
    } else {
      /* Moonlight: cold, and about a fortieth of the sun, which is far more
       * than the real ratio and exactly what a night scene needs to be legible
       * without being lit like an overcast afternoon. */
      this.sunColour.setRGB(0.60, 0.71, 1.0);
      this.sunIntensity = moonUp * nightF * (1 - overcast * 0.8) * 0.20;
    }
    this.ambientColour.setRGB(ambient[0], ambient[1], ambient[2]);
    this.horizonColour.setRGB(horizonW[0], horizonW[1], horizonW[2]);

    /* --- fog ----------------------------------------------------------- */

    /* The fog is the horizon, full stop. Distance fades into the sky it is seen
     * against, so any other colour here is a seam waiting to be noticed. Thick
     * weather pulls it a little toward neutral and a little darker, because a
     * fog bank is lit by the whole sky rather than by the bright part of it. */
    const grey = (horizonW[0] + horizonW[1] + horizonW[2]) / 3;
    /* …at the sky's stop, and through the same highlight desaturation the dome
     * applies, because the whole point of deriving the fog from the horizon is
     * that a distant object and the sky immediately behind it are the same
     * colour. Miss the grade and the seam comes straight back. */
    const stop = skyStop(sunAltDeg);
    const fogLin = horizonW.map(v =>
      mix(v, grey, fogAmt * 0.35 + overcast * 0.20) * (1 - rain * 0.18) * stop);
    const fogL = luma(fogLin);
    /* …and through the dome's shoulder as well, for the same reason: a distant
     * object and the sky immediately behind it have to arrive at the same
     * value, and the dome now compresses its own top end. Miss this and every
     * bright horizon gets its seam back, on the one axis — a far ridge against
     * the sky — where the whole point of this module is to be seamless. */
    const fogRGB = fogLin.map(v => {
      const d = mix(v, fogL, smoothstep(0.42, 2.4, fogL) * 0.34);
      const over = Math.max(d - SKY_KNEE, 0);
      return Math.min(d, SKY_KNEE) + over / (1 + over / (SKY_CEIL - SKY_KNEE));
    });
    this.fogColour.setRGB(fogRGB[0], fogRGB[1], fogRGB[2]);
    if (this._fog) {
      this._fog.color.copy(this.fogColour);
      /* Tuned against the site: one bay is 44m and the whole floor is a few
       * hundred metres across, so clear air has to stay almost invisible over
       * that range while `fog: 0.9` closes it down to about a hundred metres.
       *
       * Re-derived for `FOG_P = 1.5` on 2026-08-07 — a density means something
       * different under a different curve, and carrying the old numbers over
       * would have moved the whole ladder. Two anchors, measured rather than
       * chosen: the clear-air term is set so foliage at 700m reads the
       * reference's blue-minus-red of about +12 in a judged `quality=ultra`
       * frame, and the fog-weather term so `fog: 0.9` still shuts the view
       * down at roughly a hundred metres.
       *
       * The weather exponent went from 1.7 to 2.6 to make those two anchors
       * independent. At 1.7 the fixture's ordinary `fog: 0.10` was contributing
       * two thirds of the total density — so "clear air" was mostly the weather
       * term, the base could not be tuned without the haze slider fighting it,
       * and the difference between a clear day and a slightly damp one was most
       * of the difference between clear and thick. A steeper curve keeps the
       * bottom of the slider close to clear and reaches the same top.
       *
       * Re-derived again on 2026-08-08 for `FOG_P = 3.0` and `FOG_H = 400`, and
       * for the same reason as last time: this number has no meaning apart from
       * the curve and the height profile it is multiplied into, so all three
       * move together or none of them do. The base is now set on a THIRD anchor
       * that the old pair could not see, because there was nothing in the frame
       * far enough away to expose it — the open water between this island and
       * the mainland, at 1.85 km, where two blind critiques independently found
       * "a hard base edge with no haze integration". The base is chosen so the
       * sea at that range carries about 0.39 of air, against the 0.51 the
       * mainland's own material gives itself at the same distance; the join
       * then steps by 27 luminance instead of 47 (`harness/sk-mainedge.mjs`,
       * nine columns across the shoreline, ultra).
       *
       * It looks like a 60% increase and it is not one. `FOG_H` went from 130 m
       * to 400, which raised the height term at sea level from 0.42 to 0.70, so
       * the optical depth at the site is up by about 5% and everything else is
       * the exponent. The waterline measures 0.035 of haze against 0.037 before,
       * and 300-600 m measures 0.010 against 0.018.
       *
       * 0.00060 -> 0.00053 later the same day, and down for the same reason it
       * went up: `FOG_H` moved again, 400 -> 900, and the height term is the
       * other half of this number. At `cam=far` that term goes 0.63 -> 0.81, so
       * holding the density would have raised the optical depth by 28% on top of
       * the shallower exponent.
       *
       * The third anchor is now a hard one instead of a chosen one. terrain.js's
       * mainland rolls its own haze and gives its own foot 0.542 at the join;
       * this density puts the sea directly in front of it at 0.511
       * (`sk-mainedge.mjs`, nine columns, ultra, 2005 m). The two haze models
       * finally agree, and they agree FROM BELOW — any more density here and the
       * water would be hazier than the land standing behind it, which is the one
       * error a viewer reads instantly. The rendered step across that join falls
       * from 25.2 luminance to 17.1 without terrain.js moving at all.
       *
       * What it did not cost, because the whole increase is carried by the
       * height term and the height term is nearly 1 at ground level: `cam=low`,
       * 0-300 m stays at 0.000 of haze and 300-600 m goes 0.012 -> 0.013.
       *
       * What it did cost, stated rather than buried: the open water loses about
       * a fourteenth of its shelf-to-deep spread. `harness/sk-water.py`, p90
       * minus p10 of water luminance over a mask built on a fog-off frame,
       * cam=far, ultra, back-to-back captures repeatable to 0.2:
       *
       *    haze removed entirely   91.5
       *    before                  80.9
       *    after                   74.8
       *
       * That is the price of the far field being continuous with the land
       * standing in it, and it is the smaller of the two errors: a graded sea
       * behind an ungraded island is what got us called a decal.
       *
       * 0.00053 -> 0.000588, 2026-08-08, and for the fourth time this number is
       * not being changed — the curve it is multiplied into is, and this is what
       * it takes to leave the far field where it was. `FOG_P` went 2.75 -> 3.25
       * to take a quarter of the haze off the near plane (see the note above it),
       * and a steeper exponent shrinks the whole map, so the scale has to come
       * up by the amount that puts the far end back. It is SOLVED rather than
       * tuned: `harness/sk-geodump.mjs` dumps every ground sample's true view
       * depth and world height once, and `harness/sk-curve.mjs` then bisects for
       * the density that holds the 1100-1400 m band at 0.1415 under the new
       * exponent. The answer is 6.377e-4 at the fixture's `fog: 0.10`, which is
       * this base plus the weather term's 4.97e-5.
       *
       * The near field is where the change actually lands, and it lands in the
       * operator's frame as well as the art director's: `cam=low`, 0-300 m stays
       * at 0.000 of haze and 300-600 m goes 0.013 -> 0.009 (`sk-haze.mjs`, both
       * curves measured back to back on the same terrain). Thick weather is
       * untouched — at `fog: 0.9` the density is 0.0158, and evaluating the
       * compiled chunk at ground level says a steeper exponent only closes it
       * sooner: 0.868 of air at 100 m against 0.850 before.
       *
       * 2026-08-09: UNCHANGED, AND THAT IS THE DECISION. The clear shell above
       * needs 1.94x the extinction beyond it to leave the far field where it
       * is, and the obvious place for that factor was right here. It went into
       * `FOG_S` inside the shader chunk instead, because this number is not
       * private: labels.js's `dampFog` hardcodes
       * `1 - exp(-fogDensity^2 * vFogDepth^2)` and scales it by 0.55 on the
       * status bars, so multiplying it by 1.94 would have taken a board at
       * 900 m from 0.154 of fog to 0.391 — a 2.5x wash on the one element in
       * this product whose legibility is an acceptance test, caused entirely by
       * a change nothing in this file wanted to make to it. Second reason, in
       * this line specifically: the weather terms are ADDED to the base, so
       * scaling the base would have scaled `fog: 0.9` too and moved the thick
       * preset by the whole factor. Keeping the scale in the chunk leaves the
       * weather curve alone (see the closure table under FOG_T0) and leaves
       * every other module's reading of `scene.fog.density` exactly where it
       * has always been. The cost is that the published density is no longer
       * the extinction; the header comment says so. */
      const density = 0.000588 + Math.pow(fogAmt, 2.6) * 0.0198 + rain * 0.0009;
      this._fog.density = this._fogPin == null ? density : this._fogPin;
    }

    /* --- shader uniforms ------------------------------------------------ */

    const U = this._uniforms;
    if (!U) return;
    U.uSunDir.value.set(sun[0], sun[1], sun[2]);
    U.uMoonDir.value.set(moon[0], moon[1], moon[2]);
    U.uSunTint.value.set(sunTint[0] * A.sunI * 0.055,
                         sunTint[1] * A.sunI * 0.055,
                         sunTint[2] * A.sunI * 0.055);
    /* The clouds are drawn on the dome, so they are lit at the dome's stop and
     * not at the world's — otherwise a correctly exposed sky would carry a
     * three-stop-hot cloud deck across the top of it. */
    U.uSkyStop.value = stop;
    U.uSunLight.value.set(directTop[0] * 2.4, directTop[1] * 2.4, directTop[2] * 2.4);
    U.uSkyLight.value.set(ambient[0] * 1.6, ambient[1] * 1.6, ambient[2] * 1.6);
    /* uFogColour is used inside the dome, upstream of `uSkyStop`, so it goes in
     * at the physical scale the rest of that shader works in. */
    U.uFogColour.value.set(fogLin[0] / stop, fogLin[1] / stop, fogLin[2] / stop);
    U.uCloud.value = cloud;
    U.uRain.value = rain;
    U.uFogAmt.value = fogAmt;
    U.uNight.value = nightF;
    U.uMoonI.value = moonI;
    U.uSunDisc.value = smoothstep(-1.0, 0.25, sunAltDeg);

    /* Storm cloud is thicker, lower and denser; fair-weather cumulus sit high
     * and thin. The base dropping as the weather closes in is most of what
     * makes an overcast sky feel like a lid.
     *
     * The fair-weather base is down from 1750m to 900, which is where a spring
     * cumulus really sits and, more to the point, is the difference between
     * clouds that subtend something and clouds that are a line of specks on the
     * horizon. At 1750m a ground camera looking twenty degrees up is seeing
     * them five kilometres away; at 900 it is two and a half, and they read. */
    U.uCloudBase.value = mix(900, 620, Math.max(overcast, rain));
    U.uCloudThick.value = mix(1500, 3200, Math.max(cloud, rain));
    /* Denser than it was, by about three quarters. A fair-weather cumulus is
     * optically thick — you cannot see the sky through the middle of one — and
     * at the old figure ours ran to an alpha of about 0.5, so every cloud was
     * half the sky behind it and half itself. That halves whatever contrast the
     * lighting manages to build, on top of the phase problem above. */
    U.uCloudDensity.value = 0.0034 + cloud * 0.0030 + rain * 0.0018;

    const wind = clamp(w.wind ?? 0.35, 0, 1.5);
    const wa = w.windAngle ?? 0.6;
    U.uWind.value.set(Math.cos(wa) * (0.4 + wind * 2.2),
                      Math.sin(wa) * (0.4 + wind * 2.2));

    const L = this._lutPass.material.uniforms;
    L.uSunDir.value.set(sun[0], sun[1], sun[2]);
    L.uMoonDir.value.set(moon[0], moon[1], moon[2]);
    L.uMS.value.set(ms[0], ms[1], ms[2]);
    L.uMieMul.value = mieMul;
    L.uSunI.value = A.sunI;
    L.uMoonI.value = moonI;
    L.uNightLift.value = nightF;
    L.uTwiAmp.value = twiAmp;

    this._envDirty = true;
  }

  _renderLut() {
    if (!this._ok || !this._lut) return;
    const r = this.ctx.renderer;
    const prev = r.getRenderTarget();
    r.setRenderTarget(this._lut);
    r.render(this._lutScene, this._lutCam);
    r.setRenderTarget(prev);
  }

  /** Render the sky into a cube and run it through PMREM. This is the light
   *  every metal roof, tank car and puddle in the world reflects, so it has to
   *  be the same dome — not an approximation of it. */
  _buildEnvironment() {
    if (!this._ok || !this._envOk || !this._pmrem) return;
    try {
      const rt = this._pmrem.fromScene(this._envScene, 0.0, 0.1, 10);
      const old = this._envRT;
      this._envRT = rt;
      this.ctx.scene.environment = rt.texture;
      old?.dispose?.();
      this._lastEnvAt = this._t;
      this._envDirty = false;
    } catch (err) {
      /* One failure is enough — a PMREM that throws will throw again, and doing
       * it every frame would be the end of the frame budget. */
      this._envOk = false;
      console.warn('[sky] environment map disabled', err);
    }
  }

  /* ---- lifecycle --------------------------------------------------------- */

  update(dt, t) {
    if (!this._ok) return;
    this._t = t;
    this._uniforms.uTime.value = t;

    /* The dome rides the camera. Its matrix is set by hand because a sphere
     * that is only ever translated does not need a full transform rebuild in
     * the middle of the frame. */
    const cam = this.ctx.camera;
    if (cam) {
      this._dome.matrix.makeTranslation(cam.position.x, cam.position.y, cam.position.z);
      this._dome.matrixWorldNeedsUpdate = true;
    }

    if (this._dirty) {
      this._dirty = false;
      this._recompute();
      this._renderLut();
      this.ctx.emit?.('sky', this);
    }
    /* The environment map costs a few milliseconds. It waits a frame after the
     * LUT so a time change costs two cheap frames instead of one expensive one,
     * and it never runs twice inside a quarter of a second. */
    if (this._envDirty && this._envOk && t - this._lastEnvAt > 0.25) {
      this._buildEnvironment();
    }
  }

  /** Pin `scene.fog.density`, or pass null to hand it back to the weather.
   *
   *  `scene.fog` is a shared object every subsystem and every harness has to
   *  reason about, and this module rewrites its density from the weather on
   *  every recompute — which is not per frame, but is often enough that two
   *  separate attempts to ablate the haze for a measurement silently did
   *  nothing: the assignment came back before the next frame drew, the fog-off
   *  frame was really a fog-on frame, and the conclusion drawn from it was
   *  wrong. Writing `density` from outside is therefore not a supported way to
   *  change it; this is, and it survives a weather or time change.
   *
   *  The shape constants — the chromatic weights, the far-field cap, the
   *  e-folding height, the exponent, the scale, the clear shell and its knee —
   *  are compiled into every material's fog chunk before the first material
   *  exists, so they can only be overridden by setting
   *
   *      globalThis.__lemFog = {k, max, h, p, s, t0, w, density}
   *
   *  before the world builds; `density` there is a pin, applied at build.
   *  `{p: 3.25, s: 1, t0: 0}` reproduces the pre-shell curve EXACTLY — the
   *  shell is emitted only when `t0 > 0` for precisely that reason — and every
   *  "before" row in the fog notes above was taken through it on this build, so
   *  no A/B in this file spans an edit to another module. */
  setFogDensity(v) {
    this._fogPin = Number.isFinite(v) ? v : null;
    if (this._fog) this._dirty = true;
    /* Applied now rather than at the next recompute, so a harness that sets it
     * and screenshots the following frame measures what it asked for. */
    if (this._fog && this._fogPin != null) this._fog.density = this._fogPin;
    return this._fog ? this._fog.density : null;
  }

  onTime(hours) {
    if (!Number.isFinite(hours)) return;
    this.hours = ((hours % 24) + 24) % 24;
    this._dirty = true;
  }

  onWeather() { this._dirty = true; }

  onQuality(tier) {
    this._tier = tier?.name || this._tier;
    if (!this._uniforms) return;
    this._uniforms.uCloudSteps.value = CLOUD_STEPS[this._tier] ?? 8;
    /* The detail octave is a second volume fetch on every marched sample; it is
     * the first thing to go, and losing it costs edge fray, not shape. */
    this._uniforms.uDetailOn.value =
      (this._tier === 'ultra' || this._tier === 'high' || this._tier === 'medium') ? 1 : 0;
    /* At the bottom of the ladder the environment map stops being regenerated:
     * the reflections it feeds are already switched off down there. */
    if (this._tier === 'floor') this._envOk = false;
  }

  dispose() {
    try {
      this.ctx.scene?.remove?.(this._dome);
      this._geometry?.dispose?.();
      this._material?.dispose?.();
      this._envMaterial?.dispose?.();
      this._lut?.dispose?.();
      this._lutPass?.geometry?.dispose?.();
      this._lutPass?.material?.dispose?.();
      this._noise?.dispose?.();
      this._detail?.dispose?.();
      this._envRT?.dispose?.();
      this._pmrem?.dispose?.();
      if (this.ctx.scene) {
        this.ctx.scene.environment = null;
        this.ctx.scene.fog = null;
      }
    } catch { /* teardown is best effort; the page is going away regardless */ }
  }
}

export default Sky;
