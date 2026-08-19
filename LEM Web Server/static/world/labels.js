/* labels.js — the part that keeps this a status board.
 *
 * The old floor was an SVG drawing, and everything an operator needed was on it
 * at a glance: the instrument's name on a plate, a wide lit strip across the
 * head of its bay carrying the overall status, and three small pills — QC, PM,
 * CAL — held apart from each other so "calibration is overdue" could never hide
 * behind "QC passed". A 3D world is a worse status board than an SVG by default:
 * things get small, things get occluded, and the temptation is to give up and
 * float an HTML card over the canvas. That is the failure mode this file exists
 * to avoid.
 *
 * So every one of those readings is rebuilt as physical signage standing in the
 * world, in four lit elements per instrument, each of which answers at a
 * different range:
 *
 *   - the ground spill    — a pool of coloured light under the bay. Readable at
 *                           any distance, through fog, at night, even when the
 *                           sign itself is edge-on or behind a tree.
 *   - the head-of-bay bar — the old wide lit strip, made literal: a light box on
 *                           a frame across the front of the bay.
 *   - the roof beacon     — a lamp on a mast at the building's top, the thing
 *                           you see first when you sweep the site.
 *   - the sign plate      — name, status band and the three pills, painted once
 *                           into a canvas at high resolution and mounted above
 *                           the beacon.
 *
 * Colour is the primary channel and is never reinterpreted: the six status
 * colours are the ones the old floor used. Motion is the second channel, and it
 * is spent carefully — RED pulses, DEAD-LINE flickers under hazard stripes and
 * smoulders, SERVICE breathes slowly because it is a decision somebody made and
 * not a fault, GREEN is steady, UNKNOWN is dim and flat and does not pretend.
 *
 * Two dimming rules that look the same and are not: a module that is not running
 * is dimmed AND marked, because nobody knows whether that instrument is fine; a
 * lab that is simply closed is dimmed and left alone, with the pulse switched
 * off entirely, because an alarm nobody is there to answer is noise.
 */
import * as THREE from 'three';

/* Not open for reinterpretation — these are the floor's colours, and an
 * operator who learned them on the SVG has to be able to read this map without
 * relearning anything. `beat` is how the element is allowed to move; `lift` is
 * how hard it is driven above white, which is what makes it bloom. */
const STATUS = {
  GREEN:       {css: '#21c071', beat: 'steady',  lift: 1.05},
  YELLOW:      {css: '#f5c542', beat: 'soft',    lift: 1.25},
  RED:         {css: '#f85b5b', beat: 'alarm',   lift: 1.55},
  SERVICE:     {css: '#a855f7', beat: 'breathe', lift: 1.20},
  'DEAD-LINE': {css: '#e2483d', beat: 'hazard',  lift: 1.45},
  UNKNOWN:     {css: '#6b7280', beat: 'flat',    lift: 0.62},
};

/* A three-letter verdict beside each pill's colour. Colour alone is enough for
 * anyone with normal vision at ten metres; this is for the printout, the
 * colour-blind operator and the screenshot in a ticket. */
const CODE = {GREEN: 'OK', YELLOW: 'DUE', RED: 'FAIL',
              SERVICE: 'SVC', 'DEAD-LINE': 'STOP', UNKNOWN: '?'};

const PILLS = [['qc', 'QC'], ['pm', 'PM'], ['calibration', 'CAL']];

const CARD_PX_W = 1024, CARD_PX_H = 560;   // the sign's canvas, before scaling
const CARD_W = 15.0;                       // metres wide at scale 1
const CARD_H = CARD_W * CARD_PX_H / CARD_PX_W;

/* The sign grows with distance so its apparent size is nearly constant, but on
 * an exponent below 1 — a card that holds an exactly fixed pixel size stops
 * being a thing in the world and becomes a HUD element pinned to a building. At
 * 0.85 it still shrinks a little as you pull out, which is enough for the eye to
 * keep placing it in space, and it is still legible at the widest preset. */
const CARD_REF_DIST = 102, CARD_EXP = 0.80, CARD_MIN = 0.8, CARD_MAX = 3.1;

/* The head-of-bay strip, in metres. Wide enough to be a piece of the site's
 * infrastructure rather than a marker, narrow enough that two neighbouring
 * bays do not run into one another. */
const BAR_W = 21;

function statusKey(v) {
  const k = String(v == null ? '' : v).trim().toUpperCase().replace(/[\s_]+/g, '-');
  if (k === 'DEADLINE') return 'DEAD-LINE';
  return STATUS[k] ? k : 'UNKNOWN';
}

/* Ink that survives on the status colour behind it. Cheap weighted luminance
 * rather than a proper contrast ratio: the six colours are known, and the split
 * this puts them on (dark ink on green/yellow/red, white on purple/deadline/
 * grey) is the one a designer would pick by eye anyway. */
function inkOn(css) {
  const r = parseInt(css.slice(1, 3), 16) / 255;
  const g = parseInt(css.slice(3, 5), 16) / 255;
  const b = parseInt(css.slice(5, 7), 16) / 255;
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 0.45 ? '#0d1116' : '#ffffff';
}

function rgba(css, a) {
  const r = parseInt(css.slice(1, 3), 16);
  const g = parseInt(css.slice(3, 5), 16);
  const b = parseInt(css.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${a})`;
}

const FONT = '"Inter", "Helvetica Neue", Helvetica, Arial, sans-serif';
const FONT_NARROW = '"Arial Narrow", "Inter", "Helvetica Neue", Helvetica, Arial, sans-serif';

/** Let the scene's fog touch a material without swallowing it.
 *
 *  Fog is right for the world and wrong for the signage: at `weather=fog` the
 *  scene's own mix takes a status plate to nothing at two hundred metres, which
 *  is precisely the visibility in which somebody most needs to know which bay
 *  is dead-lined. Damping the factor keeps the sign inside the atmosphere —
 *  it greys, it recedes, it never disappears.
 *
 *  Patched through `#include <fog_fragment>`, which is a stable marker: if a
 *  future three renames it the replace is a no-op and the material simply gets
 *  ordinary fog, which is a worse sign rather than a broken one. */
function dampFog(material, keep) {
  try {
    material.onBeforeCompile = shader => {
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <fog_fragment>', `
        #ifdef USE_FOG
          #ifdef FOG_EXP2
            float fogFactor = 1.0 - exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
          #else
            float fogFactor = smoothstep(fogNear, fogFar, vFogDepth);
          #endif
          gl_FragColor.rgb = mix(gl_FragColor.rgb, fogColor, fogFactor * ${keep.toFixed(2)});
        #endif`);
    };
    material.customProgramCacheKey = () => 'lem-dampfog-' + keep;
  } catch { /* leave the material with the scene's own fog */ }
}

function roundRect(g, x, y, w, h, r) {
  const k = Math.min(r, w * 0.5, h * 0.5);
  g.beginPath();
  g.moveTo(x + k, y);
  g.arcTo(x + w, y, x + w, y + h, k);
  g.arcTo(x + w, y + h, x, y + h, k);
  g.arcTo(x, y + h, x, y, k);
  g.arcTo(x, y, x + w, y, k);
  g.closePath();
}

/** Shrink a font until the string fits, then draw it. Instrument names are free
 *  text typed by whoever installed the module, so "PAC Flash 2" and
 *  "Koehler Cloud Point Automatic K90000" both have to land on the same plate. */
function fitText(g, text, x, y, maxW, startPx, weight, family) {
  let px = startPx;
  for (let i = 0; i < 18; i++) {
    g.font = `${weight} ${px}px ${family}`;
    if (g.measureText(text).width <= maxW || px <= startPx * 0.42) break;
    px -= Math.max(2, px * 0.06);
  }
  g.fillText(text, x, y);
  return px;
}

function ellipsize(g, text, maxW) {
  if (g.measureText(text).width <= maxW) return text;
  let s = text;
  while (s.length > 1 && g.measureText(s + '…').width > maxW) s = s.slice(0, -1);
  return s + '…';
}

/* ---- the sign plate ------------------------------------------------------ */

/** Paint one instrument's whole sign into a 2D context. Called once per status
 *  change, which on a real floor is a few times an hour — so it can afford to
 *  be this detailed, and the result is a texture with mipmaps that stays crisp
 *  from the street and readable from the far corner of the site. */
function paintCard(g, info, steel) {
  const W = CARD_PX_W, H = CARD_PX_H;
  const st = STATUS[info.status];
  const ink = inkOn(st.css);
  g.clearRect(0, 0, W, H);

  /* The plate, in four coats, because a flat dark rectangle floating over a
   * landscape reads as a browser element every single time and no amount of
   * good typography rescues it:
   *   1. a near-black bezel that gives the sign an edge against a bright sky;
   *   2. painted steel, light enough to be a surface rather than a hole;
   *   3. brushed grain, tiled from one 256px noise pattern shared by every sign;
   *   4. a bevel — light on the top edge, dark on the bottom — which is what
   *      actually persuades the eye that this thing has thickness.
   * The material cuts on alpha rather than blending, so all of it is opaque. */
  g.fillStyle = '#04060a';
  roundRect(g, 0, 0, W, H, 30); g.fill();
  const body = g.createLinearGradient(0, 10, 0, H - 10);
  body.addColorStop(0, '#39434f');
  body.addColorStop(0.30, '#2a323c');
  body.addColorStop(0.62, '#1d242c');
  body.addColorStop(1, '#161c23');
  g.fillStyle = body;
  roundRect(g, 10, 10, W - 20, H - 20, 24); g.fill();

  if (steel) {
    g.save();
    roundRect(g, 10, 10, W - 20, H - 20, 24); g.clip();
    g.globalAlpha = 0.5;
    g.fillStyle = steel;
    g.fillRect(10, 10, W - 20, H - 20);
    g.globalAlpha = 1;
    g.restore();
  }

  g.strokeStyle = 'rgba(186,206,232,0.42)'; g.lineWidth = 3;
  g.beginPath(); g.moveTo(36, 12); g.lineTo(W - 36, 12); g.stroke();
  g.strokeStyle = 'rgba(0,0,0,0.55)'; g.lineWidth = 3;
  g.beginPath(); g.moveTo(36, H - 12); g.lineTo(W - 36, H - 12); g.stroke();
  g.strokeStyle = 'rgba(150,172,198,0.22)'; g.lineWidth = 2;
  roundRect(g, 11, 11, W - 22, H - 22, 23); g.stroke();

  /* A wash of the status colour up from the bottom: the plate is lit by its own
   * band, so it should not read as a flat swatch in front of the world. */
  const wash = g.createLinearGradient(0, H * 0.42, 0, H - 12);
  wash.addColorStop(0, rgba(st.css, 0));
  wash.addColorStop(1, rgba(st.css, info.dim ? 0.06 : 0.16));
  g.fillStyle = wash;
  roundRect(g, 12, 12, W - 24, H - 24, 22); g.fill();

  /* Rivets, and the two mounting lugs the plate hangs from. The single cheapest
   * thing that stops it reading as a div. */
  for (const [rx, ry] of [[34, 34], [W - 34, 34], [34, H - 34], [W - 34, H - 34]]) {
    const sh = g.createRadialGradient(rx - 2, ry - 2, 1, rx, ry, 9);
    sh.addColorStop(0, 'rgba(226,238,252,0.55)');
    sh.addColorStop(1, 'rgba(10,14,20,0.55)');
    g.fillStyle = sh;
    g.beginPath(); g.arc(rx, ry, 8, 0, Math.PI * 2); g.fill();
  }
  g.fillStyle = 'rgba(9,12,17,0.9)';
  for (const lx of [W * 0.34, W * 0.66]) {
    roundRect(g, lx - 30, H - 20, 60, 14, 5); g.fill();
  }

  /* ---- title row ---- */
  const tagW = 190;
  g.textBaseline = 'alphabetic';
  g.textAlign = 'left';
  g.fillStyle = '#eaf1f8';
  try { g.letterSpacing = '0px'; } catch { /* older canvas */ }
  fitText(g, info.title, 44, 120, W - 88 - tagW - 24, 92, '700', FONT);

  /* The module tag. This is the "dimmed and marked" half of the not-running
   * rule: the plate goes dark, and this says which kind of dark it is. */
  const tag = info.stateTag;
  if (tag) {
    const tx = W - 44 - tagW;
    g.fillStyle = rgba(tag.css, 0.16);
    roundRect(g, tx, 52, tagW, 62, 14); g.fill();
    g.strokeStyle = rgba(tag.css, 0.75); g.lineWidth = 2.5;
    roundRect(g, tx, 52, tagW, 62, 14); g.stroke();
    g.fillStyle = tag.css;
    g.textAlign = 'center';
    g.font = `700 34px ${FONT}`;
    try { g.letterSpacing = '2px'; } catch { /* older canvas */ }
    g.fillText(tag.label, tx + tagW / 2, 95);
    try { g.letterSpacing = '0px'; } catch { /* older canvas */ }
    g.textAlign = 'left';
  }

  /* ---- the status band: the old floor's wide lit strip ---- */
  const bx = 34, by = 150, bw = W - 68, bh = 168;
  g.save();
  roundRect(g, bx, by, bw, bh, 18); g.clip();
  g.fillStyle = st.css;
  g.fillRect(bx, by, bw, bh);

  if (info.status === 'DEAD-LINE') {
    /* Hazard stripes. Not decoration: DEAD-LINE and RED are two reds three
     * hundred metres away, and the stripes are what tells them apart before the
     * word is legible. */
    g.fillStyle = '#1a1210';
    for (let s = -bh; s < bw + bh; s += 96) {
      g.beginPath();
      g.moveTo(bx + s, by + bh);
      g.lineTo(bx + s + 48, by + bh);
      g.lineTo(bx + s + 48 + bh, by);
      g.lineTo(bx + s + bh, by);
      g.closePath(); g.fill();
    }
  } else if (info.status === 'SERVICE') {
    /* A fine administrative hatch — SERVICE is a decision, so it gets a texture
     * that reads as "marked out of use by a person", not as a fault pattern. */
    g.strokeStyle = 'rgba(255,255,255,0.13)'; g.lineWidth = 3;
    for (let s = -bh; s < bw + bh; s += 26) {
      g.beginPath();
      g.moveTo(bx + s, by + bh); g.lineTo(bx + s + bh, by); g.stroke();
    }
  }

  /* Lamp gradient across the strip: brighter at the top where a real light box
   * bleeds through the diffuser. */
  const lamp = g.createLinearGradient(0, by, 0, by + bh);
  lamp.addColorStop(0, 'rgba(255,255,255,0.26)');
  lamp.addColorStop(0.45, 'rgba(255,255,255,0.02)');
  lamp.addColorStop(1, 'rgba(0,0,0,0.20)');
  g.fillStyle = lamp;
  g.fillRect(bx, by, bw, bh);
  g.restore();

  g.strokeStyle = 'rgba(6,9,13,0.55)'; g.lineWidth = 3;
  roundRect(g, bx, by, bw, bh, 18); g.stroke();

  /* A miniature signal head on the right of the strip. Three aspects, one lit —
   * the same information as the colour, in a shape that survives being 12 px
   * tall and in a form the rest of this world is already speaking. */
  const px0 = bx + bw - 22 - 92, py0 = by + 14, pw = 92, ph = bh - 28;
  g.fillStyle = 'rgba(6,9,13,0.72)';
  roundRect(g, px0, py0, pw, ph, 16); g.fill();
  g.strokeStyle = 'rgba(6,9,13,0.9)'; g.lineWidth = 2;
  roundRect(g, px0, py0, pw, ph, 16); g.stroke();
  const aspect = {'DEAD-LINE': 0, RED: 0, SERVICE: 1, YELLOW: 1, UNKNOWN: 1,
                  GREEN: 2}[info.status];
  const lampCss = ['#f85b5b', info.status === 'SERVICE' ? '#a855f7' :
                   (info.status === 'UNKNOWN' ? '#6b7280' : '#f5c542'), '#21c071'];
  for (let i = 0; i < 3; i++) {
    const cx = px0 + pw / 2, cy = py0 + ph * (0.2 + i * 0.3);
    const on = i === aspect;
    if (on) {
      const glow = g.createRadialGradient(cx, cy, 2, cx, cy, 34);
      glow.addColorStop(0, rgba(lampCss[i], 0.85));
      glow.addColorStop(1, rgba(lampCss[i], 0));
      g.fillStyle = glow;
      g.beginPath(); g.arc(cx, cy, 34, 0, Math.PI * 2); g.fill();
    }
    g.fillStyle = on ? lampCss[i] : 'rgba(255,255,255,0.09)';
    g.beginPath(); g.arc(cx, cy, 17, 0, Math.PI * 2); g.fill();
  }

  /* The word. Behind it on the striped statuses, a plaque — otherwise the
   * stripes eat the letters exactly when the letters matter most. */
  g.font = `800 108px ${FONT_NARROW}`;
  try { g.letterSpacing = '5px'; } catch { /* older canvas */ }
  const wordW = g.measureText(info.status).width;
  if (info.status === 'DEAD-LINE' || info.status === 'SERVICE') {
    g.fillStyle = 'rgba(8,10,14,0.55)';
    roundRect(g, bx + 20, by + 26, wordW + 44, bh - 52, 12); g.fill();
  }
  g.fillStyle = info.status === 'DEAD-LINE' ? '#ffffff' : ink;
  g.fillText(info.status, bx + 42, by + bh / 2 + 38);
  try { g.letterSpacing = '0px'; } catch { /* older canvas */ }

  /* ---- the three pills ---- */
  const py = 332, phh = 150, gap = 16;
  const pwid = (bw - gap * 2) / 3;
  for (let i = 0; i < 3; i++) {
    const key = statusKey(info.sub[PILLS[i][0]]);
    const s = STATUS[key];
    const x = bx + i * (pwid + gap);
    const bad = key !== 'GREEN' && key !== 'UNKNOWN';

    /* A problem pill is filled and a healthy one is outlined. That difference
     * is legible at a range where the hue is only a smear, which is the whole
     * point of keeping the three apart in the first place. */
    if (bad) {
      const grad = g.createLinearGradient(0, py, 0, py + phh);
      grad.addColorStop(0, s.css);
      grad.addColorStop(1, rgba(s.css, 0.82));
      g.fillStyle = grad;
    } else {
      g.fillStyle = rgba(s.css, 0.13);
    }
    roundRect(g, x, py, pwid, phh, 20); g.fill();
    g.strokeStyle = bad ? 'rgba(6,9,13,0.55)' : rgba(s.css, 0.85);
    g.lineWidth = bad ? 3 : 3.5;
    roundRect(g, x, py, pwid, phh, 20); g.stroke();

    /* A solid tab down the left edge, so the pill still shows its colour when
     * it is outlined and the fill is nearly transparent. */
    g.save();
    roundRect(g, x, py, pwid, phh, 20); g.clip();
    g.fillStyle = bad ? 'rgba(6,9,13,0.35)' : s.css;
    g.fillRect(x, py, 16, phh);
    g.restore();

    const fg = bad ? inkOn(s.css) : s.css;
    g.fillStyle = fg;
    g.textAlign = 'left';
    g.font = `800 74px ${FONT}`;
    g.fillText(PILLS[i][1], x + 40, py + 102);
    g.textAlign = 'right';
    g.font = `700 38px ${FONT}`;
    g.globalAlpha = bad ? 0.92 : 0.85;
    g.fillText(CODE[key], x + pwid - 24, py + 100);
    g.globalAlpha = 1;
    g.textAlign = 'left';
  }

  /* ---- footer: why ---- */
  if (info.reason) {
    g.fillStyle = 'rgba(140,158,180,0.85)';
    g.font = `500 32px ${FONT}`;
    g.fillText(ellipsize(g, info.reason, bw - 8), bx + 4, 534);
  }

  /* ---- the two kinds of dark ---- */
  if (info.dim) {
    g.fillStyle = info.closed ? 'rgba(6,9,13,0.42)' : 'rgba(6,9,13,0.34)';
    roundRect(g, 8, 8, W - 16, H - 16, 24); g.fill();
    if (!info.closed) {
      /* Stopped, not closed: nobody is watching this instrument and nobody
       * decided that. It gets a hatch, because the reading on the plate is
       * as old as the module has been down and must not be trusted. */
      g.save();
      roundRect(g, 8, 8, W - 16, H - 16, 24); g.clip();
      g.strokeStyle = 'rgba(245,197,66,0.16)'; g.lineWidth = 14;
      for (let s = -H; s < W + H; s += 52) {
        g.beginPath(); g.moveTo(s, H); g.lineTo(s + H, 0); g.stroke();
      }
      g.restore();
    }
    /* Dimmed is not the same as unreadable. The name and the word come back at
     * part strength on top of the overlay: an operator has to be able to tell
     * WHICH instrument is down and what it last said, or the dimming has
     * removed the information instead of qualifying it. */
    g.globalAlpha = 0.62;
    g.fillStyle = '#dfe8f2';
    fitText(g, info.title, 44, 120, W - 88 - 214, 92, '700', FONT);
    g.globalAlpha = 0.48;
    g.fillStyle = inkOn(st.css);
    g.font = `800 108px ${FONT_NARROW}`;
    try { g.letterSpacing = '5px'; } catch { /* older canvas */ }
    g.fillText(info.status, bx + 42, by + bh / 2 + 38);
    try { g.letterSpacing = '0px'; } catch { /* older canvas */ }
    g.globalAlpha = 1;
  }
}

/* ---- shared procedural textures ------------------------------------------ */

function makeTex(cv, {srgb = false, aniso = 16} = {}) {
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.anisotropy = aniso;
  t.generateMipmaps = true;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  t.needsUpdate = true;
  return t;
}

function canvasOf(w, h) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  return c;
}

/* ---- instanced billboards (glow, smoke, embers) -------------------------- */

/* One draw call for every soft sprite in the subsystem. Three's InstancedMesh
 * cannot vary opacity per instance — instanceColor multiplies rgb only — and
 * smoke that cannot fade is not smoke, so these carry their own tiny shader
 * with an alpha attribute and billboard in view space. */
const SPRITE_VS = /* glsl */`
  in vec3 aOffset;
  in float aScale;
  in float aRot;
  in float aAlpha;
  in vec3 aTint;
  out vec2 vUv;
  out float vAlpha;
  out vec3 vTint;
  out float vDepth;
  void main() {
    vec4 c = modelViewMatrix * vec4(aOffset, 1.0);
    float s = sin(aRot), co = cos(aRot);
    vec2 p = vec2(position.x * co - position.y * s,
                  position.x * s + position.y * co) * aScale;
    c.xy += p;
    vUv = uv; vAlpha = aAlpha; vTint = aTint; vDepth = -c.z;
    gl_Position = projectionMatrix * c;
  }`;

const SPRITE_FS = /* glsl */`
  precision highp float;
  in vec2 vUv;
  in float vAlpha;
  in vec3 vTint;
  in float vDepth;
  uniform sampler2D tMap;
  uniform vec3 uFogColor;
  uniform float uFogDensity;
  uniform float uAdditive;
  layout(location = 0) out vec4 outColor;
  void main() {
    vec4 t = texture(tMap, vUv);
    float a = t.a * vAlpha;
    if (a <= 0.004) discard;
    vec3 c = t.rgb * vTint;
    /* Additive sprites (beacon glow, embers) fade INTO the fog rather than
     * toward its colour — light does not turn grey in mist, it just stops
     * reaching you. Smoke does the opposite and is tinted. */
    float f = clamp(1.0 - exp(-uFogDensity * uFogDensity * vDepth * vDepth), 0.0, 1.0);
    if (uAdditive > 0.5) a *= (1.0 - f * 0.85);
    else c = mix(c, uFogColor, f);
    outColor = vec4(c * a, a);
  }`;

class SpriteField {
  constructor(count, texture, {additive = true, depthWrite = false} = {}) {
    const base = new THREE.PlaneGeometry(1, 1);
    const geo = new THREE.InstancedBufferGeometry();
    geo.setAttribute('position', base.getAttribute('position'));
    geo.setAttribute('normal', base.getAttribute('normal'));
    geo.setAttribute('uv', base.getAttribute('uv'));
    geo.setIndex(base.getIndex());
    base.dispose();
    this.offset = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);
    this.scale = new THREE.InstancedBufferAttribute(new Float32Array(count), 1);
    this.rot = new THREE.InstancedBufferAttribute(new Float32Array(count), 1);
    this.alpha = new THREE.InstancedBufferAttribute(new Float32Array(count), 1);
    this.tint = new THREE.InstancedBufferAttribute(new Float32Array(count * 3), 3);
    geo.setAttribute('aOffset', this.offset);
    geo.setAttribute('aScale', this.scale);
    geo.setAttribute('aRot', this.rot);
    geo.setAttribute('aAlpha', this.alpha);
    geo.setAttribute('aTint', this.tint);
    geo.instanceCount = count;
    /* The instances move every frame and the geometry's own bounds describe a
     * 1x1 quad at the origin, so culling would be wrong in both directions. */
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);

    const mat = new THREE.ShaderMaterial({
      vertexShader: SPRITE_VS, fragmentShader: SPRITE_FS,
      glslVersion: THREE.GLSL3,
      uniforms: {
        tMap: {value: texture},
        uFogColor: {value: new THREE.Color(0x8fa4bb)},
        uFogDensity: {value: 0},
        uAdditive: {value: additive ? 1 : 0},
      },
      transparent: true, depthWrite, depthTest: true,
      blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
      /* Premultiplied, because both branches of the shader output `c * a`:
       * one path can then serve additive light and ordinary smoke. */
      premultipliedAlpha: true,
    });
    this.count = count;
    this.mesh = new THREE.Mesh(geo, mat);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = additive ? 14 : 12;
    for (let i = 0; i < count; i++) this.alpha.array[i] = 0;
  }

  set(i, x, y, z, scale, alpha, rot, r, g, b) {
    const o = this.offset.array;
    o[i * 3] = x; o[i * 3 + 1] = y; o[i * 3 + 2] = z;
    this.scale.array[i] = scale;
    this.alpha.array[i] = alpha;
    this.rot.array[i] = rot;
    const t = this.tint.array;
    t[i * 3] = r; t[i * 3 + 1] = g; t[i * 3 + 2] = b;
  }

  hide(i) { this.alpha.array[i] = 0; }

  flush() {
    this.offset.needsUpdate = true; this.scale.needsUpdate = true;
    this.rot.needsUpdate = true; this.alpha.needsUpdate = true;
    this.tint.needsUpdate = true;
  }

  fog(scene) {
    const u = this.mesh.material.uniforms;
    const f = scene && scene.fog;
    if (!f) { u.uFogDensity.value = 0; return; }
    u.uFogColor.value.copy(f.color);
    u.uFogDensity.value = f.density !== undefined
      ? f.density
      /* Linear fog, approximated: match the half-way point of the range so a
       * sprite disappears roughly where the geometry behind it does. */
      : 1.4 / Math.max(1, f.far);
  }

  dispose() {
    this.mesh.geometry.dispose();
    this.mesh.material.uniforms.tMap.value?.dispose?.();
    this.mesh.material.dispose();
  }
}

/* ---- the subsystem -------------------------------------------------------- */

export class Labels {
  constructor(ctx) {
    this.ctx = ctx;
    this.group = new THREE.Group();
    this.group.name = 'labels';
    this.entries = [];
    this.byUid = new Map();
    this.selected = null;
    this.hovered = null;
    this.tier = ctx.quality || {name: 'ultra'};
    this.night = 0;
    this._anchorsPending = true;
    this._t = 0;
    this._tmp = new THREE.Vector3();
    this._colour = new THREE.Color();
    this._m4 = new THREE.Matrix4();
    this._q = new THREE.Quaternion();
    this._euler = new THREE.Euler();
    this._hazScale = new THREE.Vector3(1, 1, 1);
    this._scratch = new THREE.Vector3(1, 1, 1);
  }

  async build(plan) {
    try {
      /* Every glyph on the sign is measured before it is drawn, so a font that
       * has not finished loading would be measured against a fallback and then
       * painted with something wider. One await, once. */
      await document.fonts?.ready;
    } catch { /* no font loading API — the metrics will be the fallback's */ }
    try {
      this._makeTextures();
      this._makeShared();
      this.ctx.scene.add(this.group);
      this.onTime(this.ctx.world?.timeOfDay);
      this.onPlan(plan || this.ctx.plan);
      this.onQuality(this.ctx.quality);
      this.selected = this.ctx.world?.selected || null;
    } catch (err) {
      /* A subsystem that throws in build() is skipped by index.js and the map
       * carries on — but this is the subsystem that says which instrument is
       * RED, so it complains loudly on the way down. */
      console.error('[labels] signage failed to build', err);
    }
  }

  /* ---- textures ---------------------------------------------------------- */

  _makeTextures() {
    const T = this.ctx.Tex || {};
    const fbm = T.fbm || (() => 0.5);

    /* Radial falloff, used at three sizes: the beacon's halo, the pool of light
     * on the ground, and the soft end of everything else. Squared falloff, not
     * linear — a linear gradient reads as a flat disc the moment it is big. */
    const radial = (size, power, edge) => {
      const cv = canvasOf(size, size);
      const g = cv.getContext('2d');
      const img = g.createImageData(size, size);
      const c = (size - 1) / 2;
      for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
          const d = Math.hypot(x - c, y - c) / c;
          let a = Math.max(0, 1 - d);
          a = Math.pow(a, power);
          if (edge) a *= 0.75 + 0.25 * fbm(x / size * 4, y / size * 4,
                                           {octaves: 3, period: 4, seed: 12});
          const i = (y * size + x) * 4;
          img.data[i] = img.data[i + 1] = img.data[i + 2] = 255;
          img.data[i + 3] = Math.max(0, Math.min(255, a * 255));
        }
      }
      g.putImageData(img, 0, 0);
      return makeTex(cv);
    };

    this.texGlow = radial(128, 2.2, false);
    this.texSpill = radial(256, 1.8, true);
    this.texEmber = radial(32, 1.4, false);

    /* Smoke: a lumpy blob rather than a clean gaussian, so a handful of puffs
     * read as one rolling column instead of a string of circles. */
    const sc = canvasOf(128, 128);
    {
      const g = sc.getContext('2d');
      const img = g.createImageData(128, 128);
      for (let y = 0; y < 128; y++) {
        for (let x = 0; x < 128; x++) {
          const d = Math.hypot(x - 63.5, y - 63.5) / 63.5;
          const n = fbm(x / 128 * 3, y / 128 * 3, {octaves: 4, period: 3, seed: 7});
          let a = Math.max(0, 1 - d) * (0.45 + 0.85 * n);
          a = Math.pow(Math.min(1, a), 1.5);
          const v = 150 + n * 70;
          const i = (y * 128 + x) * 4;
          img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
          img.data[i + 3] = Math.max(0, Math.min(255, a * 255));
        }
      }
      g.putImageData(img, 0, 0);
    }
    this.texSmoke = makeTex(sc, {srgb: true});

    /* The light box across the head of the bay. Ribbed diffuser with hot ends
     * dimmed off — a real strip fitting is never uniform, and the ribbing is
     * what stops a 16 m glowing rectangle looking like an untextured plane. */
    const bc = canvasOf(512, 64);
    {
      const g = bc.getContext('2d');
      g.fillStyle = '#000'; g.fillRect(0, 0, 512, 64);
      for (let x = 0; x < 512; x++) {
        const end = Math.min(1, Math.min(x, 511 - x) / 26);
        const rib = 0.80 + 0.20 * Math.pow(Math.abs(Math.sin(x * Math.PI / 21)), 0.6);
        for (let y = 0; y < 64; y++) {
          const v = Math.pow(1 - Math.abs(y - 31.5) / 31.5, 0.45);
          const a = Math.max(0, Math.min(1, end * rib * (0.30 + 0.70 * v)));
          g.fillStyle = `rgba(255,255,255,${a})`;
          g.fillRect(x, y, 1, 1);
        }
      }
    }
    this.texBar = makeTex(bc, {srgb: true});

    /* Hazard overlay for the strip, laid over the bar on a DEAD-LINE bench. */
    const hc = canvasOf(512, 64);
    {
      const g = hc.getContext('2d');
      g.clearRect(0, 0, 512, 64);
      g.fillStyle = 'rgba(14,10,9,0.94)';
      for (let s = -64; s < 512 + 64; s += 56) {
        g.beginPath();
        g.moveTo(s, 64); g.lineTo(s + 28, 64);
        g.lineTo(s + 28 + 64, 0); g.lineTo(s + 64, 0);
        g.closePath(); g.fill();
      }
    }
    this.texHazard = makeTex(hc, {srgb: true});

    /* Selection: a survey ring, ticked like a rail gauge. It reads as a mark
     * somebody put on the ground rather than a game's target reticle. */
    const rc = canvasOf(512, 512);
    {
      const g = rc.getContext('2d');
      g.clearRect(0, 0, 512, 512);
      g.translate(256, 256);
      g.strokeStyle = 'rgba(255,255,255,0.92)';
      g.lineWidth = 7; g.beginPath(); g.arc(0, 0, 226, 0, Math.PI * 2); g.stroke();
      g.lineWidth = 3; g.globalAlpha = 0.5;
      g.beginPath(); g.arc(0, 0, 206, 0, Math.PI * 2); g.stroke();
      g.globalAlpha = 1;
      g.lineWidth = 9;
      for (let i = 0; i < 48; i++) {
        const a = i / 48 * Math.PI * 2;
        const long = i % 4 === 0;
        g.globalAlpha = long ? 0.95 : 0.42;
        g.beginPath();
        g.moveTo(Math.cos(a) * 226, Math.sin(a) * 226);
        g.lineTo(Math.cos(a) * (long ? 186 : 208), Math.sin(a) * (long ? 186 : 208));
        g.stroke();
      }
    }
    this.texRing = makeTex(rc, {srgb: true});

    /* Brushed grain for the sign plates. One 256px tile, painted once and
     * handed to every plate as a CanvasPattern — running fbm per pixel over
     * seven 1024x560 plates is half a second of the boot budget for a texture
     * that is identical on all of them. */
    const gc = canvasOf(256, 256);
    {
      const g = gc.getContext('2d');
      const img = g.createImageData(256, 256);
      for (let y = 0; y < 256; y++) {
        for (let x = 0; x < 256; x++) {
          /* Stretched along x so it reads as a brush direction, not as static. */
          const n = fbm(x / 256 * 22, y / 256 * 3, {octaves: 3, period: 22, seed: 31});
          const v = 128 + (n - 0.5) * 96;
          const i = (y * 256 + x) * 4;
          img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
          img.data[i + 3] = 40;
        }
      }
      g.putImageData(img, 0, 0);
    }
    /* Kept as a canvas, not as a pattern: a CanvasPattern belongs to the
     * context that made it, and every sign has its own. */
    this._steelTile = gc;
  }

  /* ---- shared geometry ---------------------------------------------------- */

  _makeShared() {
    /* Structure is lit by whatever the gi subsystem puts in the scene, but a
     * dim self-emission keeps a mast from being a pure black cut-out if the
     * lighting subsystem is absent or still building. */
    this.matSteel = new THREE.MeshStandardMaterial({
      color: 0x555f6b, roughness: 0.62, metalness: 0.75,
      emissive: 0x0a0d11, emissiveIntensity: 1,
    });
    this.matDark = new THREE.MeshStandardMaterial({
      color: 0x39414b, roughness: 0.74, metalness: 0.30,
      emissive: 0x0a0e13, emissiveIntensity: 1,
    });

    /* Slim on purpose. A mast is opaque and correctly occludes whatever is
     * behind it — including, from a low camera in a dense yard, the next bay's
     * sign. Thin is the cheapest way to lose less of it. */
    this.geoMast = new THREE.CylinderGeometry(0.20, 0.32, 1, 8, 1, false);
    this.geoMast.translate(0, 0.5, 0);
    this.geoHousing = new THREE.CylinderGeometry(1.05, 1.25, 1.3, 10);
    this.geoLens = new THREE.SphereGeometry(1, 12, 8);
    this.geoStalk = new THREE.CylinderGeometry(0.16, 0.16, 1, 6);
    this.geoStalk.translate(0, 0.5, 0);
    this.geoBar = new THREE.BoxGeometry(1, 1, 1);
    this.geoFrame = new THREE.BoxGeometry(1, 1, 1);

    this.matLens = new THREE.MeshBasicMaterial({toneMapped: true});
    /* The strip is an opaque lens, not an additive smear. Additive alone
     * disappears against a bright sky — exactly the daytime case where an
     * operator still has to see which bay is red — so the colour lives in a
     * solid panel and the glow that blooms at dusk is a separate quad in front
     * of it. */
    this.matBar = new THREE.MeshBasicMaterial({
      map: this.texBar, toneMapped: true,
    });
    dampFog(this.matBar, 0.55);
    this.matHazard = new THREE.MeshBasicMaterial({
      map: this.texHazard, transparent: true, depthWrite: false, toneMapped: true,
    });
    /* `fog: false` on every additive element, and it is not a style choice.
     * Three's fog mixes the fragment TOWARD the fog colour; on an additive
     * material that turns a nearly-black glow at three hundred metres into a
     * full-strength lump of fog colour ADDED to the scene, and the site grows a
     * white pool under every sign. Light in fog attenuates, it does not take on
     * the fog's colour — so the distance falloff is the `haze` term in
     * `_drive`, computed where the weather is already known. */
    this.matSpill = new THREE.MeshBasicMaterial({
      map: this.texSpill, transparent: true, depthWrite: false, fog: false,
      blending: THREE.AdditiveBlending, toneMapped: true,
    });
    this.matHalo = new THREE.MeshBasicMaterial({
      map: this.texGlow, transparent: true, depthWrite: false, fog: false,
      blending: THREE.AdditiveBlending, toneMapped: true,
      /* The beacon's halo is billboarded and only ever seen from the front; the
       * strip's is a fixed panel on a roof that the camera orbits right past. */
      side: THREE.DoubleSide,
    });

    /* The selection and hover marks. One of each exists and is moved, because
     * exactly one instrument can be selected and one hovered. */
    const ring = new THREE.PlaneGeometry(1, 1);
    ring.rotateX(-Math.PI / 2);
    this.selRing = new THREE.Mesh(ring, new THREE.MeshBasicMaterial({
      map: this.texRing, transparent: true, depthWrite: false, fog: false,
      blending: THREE.AdditiveBlending, toneMapped: true,
      color: new THREE.Color(0.55, 0.85, 1.25),
    }));
    this.selRing.visible = false;
    this.selRing.renderOrder = 6;
    this.hovRing = new THREE.Mesh(ring, this.selRing.material.clone());
    this.hovRing.material.color.setRGB(0.75, 0.82, 0.92);
    this.hovRing.material.opacity = 0.5;
    this.hovRing.visible = false;
    this.hovRing.renderOrder = 6;
    this.group.add(this.selRing, this.hovRing);

    /* A soft column of light standing over the selected bay — the only element
     * here whose job is "find this one again after you looked away". */
    const beam = new THREE.CylinderGeometry(2.2, 3.4, 1, 12, 1, true);
    beam.translate(0, 0.5, 0);
    this.selBeam = new THREE.Mesh(beam, new THREE.MeshBasicMaterial({
      color: new THREE.Color(0.30, 0.55, 0.95), transparent: true, opacity: 0.16,
      depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.BackSide,
      toneMapped: true, fog: false,
    }));
    this.selBeam.visible = false;
    this.selBeam.renderOrder = 5;
    this.group.add(this.selBeam);

    this.smoke = new SpriteField(60, this.texSmoke, {additive: false});
    this.embers = new SpriteField(96, this.texEmber, {additive: true});
    this.group.add(this.smoke.mesh, this.embers.mesh);
    this._puffs = [];
    for (let i = 0; i < 60; i++) this._puffs.push({life: -1, entry: null});
    this._sparks = [];
    for (let i = 0; i < 96; i++) this._sparks.push({life: -1, entry: null});
  }

  /* ---- layout ------------------------------------------------------------- */

  onPlan(plan) {
    if (!plan || !Array.isArray(plan.stations)) return;
    try {
      /* index.js re-plans once more after every subsystem has built, and will
       * re-plan on any payload whose positions differ. Rebuilding seven signs
       * (and seven 1024px canvases) for a plan that describes the same site in
       * the same places is pure waste, so the layout is fingerprinted. */
      const sig = plan.stations.map(s => `${s.uid}@${s.x},${s.z}`).join('|');
      if (sig === this._planSig && this.entries.length) {
        this.onMachines(this.ctx.world?.machines || [], plan);
        return;
      }
      this._planSig = sig;
      this._teardownEntries();
      const hub = plan.hub || {x: 0, z: 0};
      for (const st of plan.stations) this.entries.push(this._makeEntry(st, hub));
      this._buildInstances();
      this._anchorsPending = true;
      this.onMachines(this.ctx.world?.machines || [], plan);
    } catch (err) {
      console.error('[labels] could not lay out the signage', err);
    }
  }

  _makeEntry(st, hub) {
    const rng = st.rng || this.ctx.seededRandom(st.uid);
    const ground = this.ctx.ground(st.x, st.z) || 0;
    /* The head of the bay is the side the line runs past, i.e. the side facing
     * the LabCore terminal. Every bar in the site therefore faces the same
     * aisle, which is what makes a row of them readable as a row. */
    const dx = hub.x - st.x, dz = hub.z - st.z;
    const len = Math.hypot(dx, dz) || 1;
    const facing = Math.atan2(dx / len, dz / len);

    const entry = {
      uid: st.uid, title: st.title || st.uid, station: st,
      x: st.x, z: st.z, ground, facing,
      top: 16,
      /* Three heights in rotation. Two signs at the same height, seen from a
       * low camera, sit exactly on top of each other and each hides half of
       * the other's pills — which is the one failure this whole subsystem
       * exists to prevent. */
      mast: 6 + ((st.index || 0) % 3) * 4.2 + rng() * 1.3,
      phase: rng() * Math.PI * 2,
      vis: null, painted: '',
      card: null, ctx2d: null, texture: null,
      /* The band rides the building's front parapet and the pool of light it
       * throws lands on the apron in front. Both are placed off the station
       * origin and the direction of the terminal, because that is all this
       * subsystem knows: `ctx.world.anchors` publishes each building's height
       * and nothing about its footprint, so anything measured from a guessed
       * width would end up inside a wall on the first instrument that is not
       * the size I assumed. */
      /* Both sit on the station's own origin, give or take a couple of metres.
       * `ctx.world.anchors` publishes each building's height and nothing about
       * its footprint or which way it faces, so anything placed at a guessed
       * offset ends up floating off the back of the first building that is not
       * the size I assumed — which is what the first cut of this did. Over the
       * origin, the band is on the roof and the pool is around the walls for
       * any building the plan actually put there. */
      barX: st.x + Math.sin(facing) * 2,
      barZ: st.z + Math.cos(facing) * 2,
      beat: 1, glowBeat: 1,
    };
    entry.barGround = ground;

    const cv = canvasOf(this._cardPixels(), Math.round(this._cardPixels() *
                                                       CARD_PX_H / CARD_PX_W));
    entry.canvas = cv;
    entry.ctx2d = cv.getContext('2d');
    entry.texture = makeTex(cv, {srgb: true});

    const geo = new THREE.PlaneGeometry(CARD_W, CARD_H);
    /* The sign grows upward from a fixed bottom edge. Scaling about the centre
     * would push a distant sign down through the roof it is mounted on. */
    geo.translate(0, CARD_H / 2, 0);
    const mat = new THREE.MeshBasicMaterial({
      map: entry.texture, transparent: false, alphaTest: 0.5,
      side: THREE.DoubleSide, toneMapped: true,
    });
    /* Above white on purpose: the composite tone-maps, and a plate left at 1.0
     * comes out of ACES looking like grey card. */
    mat.color.setScalar(1.28);
    dampFog(mat, 0.45);
    entry.card = new THREE.Mesh(geo, mat);
    entry.card.renderOrder = 10;
    entry.card.frustumCulled = false;
    this.group.add(entry.card);

    /* The selection outline lives under the card so it inherits the card's
     * billboard rotation and its distance scaling for free. */
    const og = new THREE.PlaneGeometry(CARD_W * 1.11, CARD_H * 1.20);
    og.translate(0, CARD_H / 2 - CARD_H * 0.02, 0);
    entry.outline = new THREE.Mesh(og, new THREE.MeshBasicMaterial({
      color: new THREE.Color(0.5, 0.8, 1.2), transparent: true, opacity: 0,
      depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: true,
      side: THREE.DoubleSide, fog: false,
    }));
    entry.outline.position.z = -0.12;
    entry.outline.renderOrder = 9;
    entry.card.add(entry.outline);

    this.byUid.set(entry.uid, entry);
    return entry;
  }

  _cardPixels() {
    const n = this.tier?.name;
    return (n === 'low' || n === 'floor') ? 512 : CARD_PX_W;
  }

  /** The static structure, one instanced mesh per part. Rebuilt only when the
   *  fleet's layout changes, which is roughly never. */
  _buildInstances() {
    const n = this.entries.length;
    if (!n) return;
    const mk = (geo, mat, colour) => {
      const m = new THREE.InstancedMesh(geo, mat, n);
      m.frustumCulled = false;
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      if (colour) m.instanceColor = new THREE.InstancedBufferAttribute(
        new Float32Array(n * 3).fill(1), 3);
      this.group.add(m);
      return m;
    };
    this.iMast = mk(this.geoMast, this.matSteel);
    this.iStalk = mk(this.geoStalk, this.matSteel);
    this.iHousing = mk(this.geoHousing, this.matDark);
    this.iLens = mk(this.geoLens, this.matLens, true);
    this.iHalo = mk(new THREE.PlaneGeometry(1, 1), this.matHalo, true);
    this.iBar = mk(this.geoBar, this.matBar, true);
    this.iHaz = mk(this.geoBar, this.matHazard, true);
    /* The strip's own throw: a soft additive panel hanging in front of it, so
     * the light box has a halo at three hundred metres instead of collapsing to
     * a six-pixel line the bloom cannot find. */
    this.iBarGlow = mk(new THREE.PlaneGeometry(1, 1), this.matHalo, true);
    this.iFrame = mk(this.geoFrame, this.matDark);
    const spill = new THREE.PlaneGeometry(1, 1);
    spill.rotateX(-Math.PI / 2);
    this.iSpill = mk(spill, this.matSpill, true);
    this.iHalo.renderOrder = 13;
    this.iBarGlow.renderOrder = 7;
    this.iBar.renderOrder = 8;
    this.iHaz.renderOrder = 9;
    this.iSpill.renderOrder = 4;
    this._placeInstances();
    /* The instanced meshes are new objects, so whatever the quality ladder had
     * already switched off has to be switched off again on them. */
    this.onQuality(this.tier);
  }

  /** Positions only — colour and pulse are set every frame in `update`. */
  _placeInstances() {
    if (!this.iMast) return;
    const M = this._m4, Q = this._q, E = this._euler, S = new THREE.Vector3();
    const P = new THREE.Vector3();
    const put = (mesh, i, px, py, pz, sx, sy, sz, ry, rx) => {
      E.set(rx || 0, ry || 0, 0, 'YXZ');
      Q.setFromEuler(E);
      P.set(px, py, pz); S.set(sx, sy, sz);
      M.compose(P, Q, S);
      mesh.setMatrixAt(i, M);
    };
    this.entries.forEach((e, i) => {
      const base = e.ground + e.top;
      e.lensY = base + e.mast + 1.1;
      e.cardY = e.lensY + 3.4;
      put(this.iMast, i, e.x, base, e.z, 1, e.mast, 1, e.facing);
      put(this.iHousing, i, e.x, base + e.mast + 0.2, e.z, 1, 1, 1, e.facing);
      put(this.iLens, i, e.x, e.lensY, e.z, 1.05, 0.85, 1.05, 0);
      put(this.iHalo, i, e.x, e.lensY, e.z, 1, 1, 1, 0);
      put(this.iStalk, i, e.x, base + e.mast + 0.9, e.z, 1, e.cardY - base - e.mast - 0.9,
          1, e.facing);
      /* The light box leans back a little, the way a real one does so its
       * throw lands on the apron in front of it rather than in your eyes. */
      /* Light box on the parapet, its housing a shade behind it, and the
       * throw landing on the apron. Leaning back so the strip faces a camera
       * that is almost always above it. */
      /* A light box standing upright on the roof, lit on BOTH long faces and
       * with nothing in front of it. The first cut had a housing behind a
       * forward-mounted lens, which looks right from one side of the site and
       * shows a blank grey slab from the other — and the default camera happens
       * to be on the blank side. A status board does not get to have a bad
       * angle. */
      /* Straddling the roof line rather than hovering over it. `anchors` gives
       * the top of the building and different buildings put different amounts
       * of rooftop plant into that number, so a band placed cleanly above it
       * floats on some of them. Sunk a little, it reads as mounted on every
       * one, and clipping into a parapet is the failure nobody notices. */
      const by = e.ground + e.top - 0.2;
      put(this.iBar, i, e.barX, by, e.barZ, BAR_W, 1.9, 0.6, e.facing);
      put(this.iHaz, i, e.barX, by, e.barZ, BAR_W, 1.9, 0.64, e.facing);
      put(this.iBarGlow, i, e.barX, by, e.barZ, BAR_W * 1.3, 7, 1, e.facing);
      /* A hood over the box: it takes the sun, gives the strip a dark top edge
       * so it does not read as a floating slab of colour from above, and is the
       * only unlit thing in the assembly. */
      put(this.iFrame, i, e.barX, by + 1.15, e.barZ, BAR_W + 1.1, 0.4, 1.2,
          e.facing);
      /* Wide enough to reach past the walls on every side: the middle of it is
       * under the building and never seen, and the ring of it that shows is the
       * light spilling onto the apron. */
      /* Held a little clear of the terrain, because the site's aprons and
       * hardstanding are laid ON the terrain and a pool at ground level gets
       * cut into rectangles by the slabs it is supposed to be lighting. */
      put(this.iSpill, i, e.x, e.ground + 0.85, e.z, 86, 1, 86, e.facing);
    });
    this._placeHazard();
    for (const m of [this.iMast, this.iStalk, this.iHousing, this.iLens, this.iHalo,
                     this.iBar, this.iHaz, this.iBarGlow, this.iFrame, this.iSpill]) {
      m.instanceMatrix.needsUpdate = true;
      m.computeBoundingSphere?.();
    }
  }

  /** The hazard stripes darken what is under them, so they cannot be faded out
   *  with `instanceColor` the way every other element here can — an unlit black
   *  is still black. A bench that is not dead-lined gets its stripe instance
   *  collapsed to zero size instead. */
  _placeHazard() {
    if (!this.iHaz) return;
    const M = this._m4, E = this._euler, Q = this._q;
    this.entries.forEach((e, i) => {
      const on = !!e.vis?.hazard;
      E.set(0, e.facing, 0, 'YXZ');
      Q.setFromEuler(E);
      M.compose(this._tmp.set(e.barX, e.ground + e.top - 0.2, e.barZ), Q,
                on ? this._hazScale.set(BAR_W, 1.9, 0.64)
                   : this._hazScale.set(0, 0, 0));
      this.iHaz.setMatrixAt(i, M);
    });
    this.iHaz.instanceMatrix.needsUpdate = true;
  }

  /* ---- status ------------------------------------------------------------- */

  onMachines(machines, plan) {
    try {
      const rows = new Map();
      for (const m of machines || []) rows.set(m.machine_uid, m);
      let hazardBefore = '';
      for (const e of this.entries) hazardBefore += e.vis?.hazard ? '1' : '0';
      for (const e of this.entries) {
        const m = rows.get(e.uid) || e.station?.machine || {};
        e.vis = this._read(m, e);
        const sig = JSON.stringify([e.vis.status, e.vis.sub, e.vis.stateTag?.label,
                                    e.vis.dim, e.vis.reason, e.title]);
        if (sig !== e.painted) {
          e.painted = sig;
          this._repaint(e);
        }
      }
      let hazardAfter = '';
      for (const e of this.entries) hazardAfter += e.vis?.hazard ? '1' : '0';
      if (hazardAfter !== hazardBefore) this._placeHazard();
      void plan;
    } catch (err) {
      console.error('[labels] could not restate the signage', err);
    }
  }

  _read(m, entry) {
    const status = statusKey(m.status);
    const subs = m.sub_statuses || {};
    const state = String(m.module_state || '').toLowerCase();
    const running = m.module_running !== false && state !== 'stopped' &&
                    state !== 'closed';
    const closed = state === 'closed';
    /* Closed is dim and quiet; stopped is dim and marked. Both dim, and the
     * difference between them is the whole point — one is a lab that went home,
     * the other is an instrument nobody is watching. */
    const stateTag = closed ? {label: 'LAB CLOSED', css: '#8b97a8'}
      : (!running ? {label: 'MODULE DOWN', css: '#f5c542'}
        : (state === 'unknown' ? {label: 'NO SIGNAL', css: '#6b7280'} : null));
    entry.title = m.title || entry.station?.title || entry.uid;
    return {
      status,
      sub: {qc: subs.qc, pm: subs.pm, calibration: subs.calibration},
      title: entry.title,
      reason: typeof m.reason === 'string' ? m.reason : '',
      dim: !running, closed, running, stateTag,
      /* Only a lab that is open gets an alarm. */
      alarm: !closed,
      smoulder: status === 'DEAD-LINE' && !closed,
      hazard: status === 'DEAD-LINE',
    };
  }

  _repaint(e) {
    try {
      const px = e.canvas.width;
      const g = e.ctx2d;
      if (e.steel === undefined) {
        try { e.steel = this._steelTile
          ? g.createPattern(this._steelTile, 'repeat') : null; }
        catch { e.steel = null; }
      }
      g.save();
      g.setTransform(px / CARD_PX_W, 0, 0, px / CARD_PX_W, 0, 0);
      paintCard(g, {...e.vis, title: e.title}, e.steel);
      g.restore();
      e.texture.needsUpdate = true;
    } catch (err) {
      console.error('[labels] could not paint the sign for', e.uid, err);
    }
  }

  /* ---- events ------------------------------------------------------------- */

  onSelected(uid) { this.selected = uid || null; }
  onHover(uid) { this.hovered = uid || null; }

  onWeather() { /* read live in update — the weather changes mid-frame anyway */ }

  onTime(hours) {
    /* Night is when the lit elements do the work. 0 at midday, 1 in the small
     * hours, with the transition sitting on civil twilight rather than on the
     * hour, so the site does not snap on at 18:00 sharp. */
    const h = Number.isFinite(hours) ? hours : 12;
    const dusk = 1 - Math.max(0, Math.min(1, (h - 5.2) / 2.4)) *
                     Math.max(0, Math.min(1, (20.4 - h) / 2.4));
    this.night = Math.max(0, Math.min(1, dusk));
  }

  onQuality(tier) {
    this.tier = tier || this.tier;
    const lean = tier?.name === 'low' || tier?.name === 'floor';
    if (this.smoke) this.smoke.mesh.visible = !lean;
    if (this.embers) this.embers.mesh.visible = !lean;
    if (this.iSpill) this.iSpill.visible = !lean || this.night > 0.4;
  }

  /* ---- the frame ---------------------------------------------------------- */

  update(dt, t) {
    if (!this.entries.length) return;
    this._t = t;
    try {
      this._resolveAnchors(dt);
      this._drive(t);
      this._smoulder(dt, t);
      this._marks(dt, t);
    } catch (err) {
      /* A raise here would kill the render loop and blank the floor, and the
       * floor is a status display before it is a rendering. */
      if (!this._warned) { this._warned = true; console.error('[labels]', err); }
    }
  }

  /** buildings.js publishes each building's height into `ctx.world.anchors`, and
   *  it may finish after this subsystem does. Poll until every sign has found
   *  its roof, then stop looking. */
  _resolveAnchors(dt) {
    if (!this._anchorsPending) return;
    this._anchorAge = (this._anchorAge || 0) + dt;
    if (this._anchorAge < 0.4) return;
    this._anchorAge = 0;
    const anchors = this.ctx.world?.anchors;
    let missing = 0, moved = false;
    for (const e of this.entries) {
      const top = anchors?.get?.(e.uid)?.top;
      if (typeof top === 'number' && Number.isFinite(top)) {
        if (Math.abs(top - e.top) > 0.05) { e.top = top; moved = true; }
      } else missing++;
      const gy = this.ctx.ground(e.x, e.z);
      if (Number.isFinite(gy) && Math.abs(gy - e.ground) > 0.05) {
        e.ground = gy;
        e.barGround = this.ctx.ground(e.barX, e.barZ) || gy;
        moved = true;
      }
    }
    if (moved) this._placeInstances();
    /* Give up after ten seconds: no anchors means buildings.js is not loaded,
     * and the fallback height is a perfectly good answer. */
    this._anchorsPending = missing > 0 && (this._anchorTotal = (this._anchorTotal || 0) + 0.4) < 10;
  }

  /** Colour, pulse and the sign's distance scaling — everything that changes
   *  per frame. Seven instruments, so this is a few hundred float writes. */
  _drive(t) {
    const cam = this.ctx.camera;
    const w = this.ctx.weather || {};
    const wet = Math.max(0, Math.min(1, w.wetness || 0));
    const fog = Math.max(0, Math.min(1, w.fog || 0));
    const night = this.night;
    const c = this._colour;

    for (let i = 0; i < this.entries.length; i++) {
      const e = this.entries[i];
      const vis = e.vis;
      if (!vis) continue;
      const st = STATUS[vis.status];

      /* The beat. RED is a hard two-per-second throb you cannot not see;
       * DEAD-LINE adds an irregular flicker on top, like something failing;
       * SERVICE breathes at a quarter of the rate, which reads as deliberate;
       * YELLOW ripples; GREEN and UNKNOWN sit still. */
      const ph = t * 1 + e.phase;
      let beat = 1;
      if (!vis.alarm) beat = 1;                        // a closed lab does not flash
      else if (st.beat === 'alarm') beat = 0.42 + 0.58 * Math.pow(
        0.5 + 0.5 * Math.sin(ph * 6.6), 1.6) * 1.55;
      else if (st.beat === 'hazard') beat = (0.5 + 0.5 * Math.sin(ph * 5.0)) * 1.35 +
        0.35 + 0.22 * Math.sin(ph * 27.3) * Math.sin(ph * 11.1);
      else if (st.beat === 'breathe') beat = 0.72 + 0.28 * Math.sin(ph * 1.4);
      else if (st.beat === 'soft') beat = 0.86 + 0.14 * Math.sin(ph * 2.2);
      else if (st.beat === 'steady') beat = 0.97 + 0.03 * Math.sin(ph * 0.9);
      else beat = 1;
      const dim = vis.dim ? (vis.closed ? 0.26 : 0.34) : 1;
      const drive = st.lift * beat * dim;
      e.beat = beat;

      /* Lamp, halo, strip and pool all take the same colour and the same drive,
       * so the four elements read as one instrument's light rather than four
       * unrelated glows that happen to be near each other. */
      c.set(st.css);
      this._setColour(this.iLens, i, c, drive * (0.9 + night * 1.5));
      this._setColour(this.iBar, i, c, drive * (1.00 + night * 0.85 + fog * 0.15));
      /* Wet ground throws the pool back at you; fog makes the halo bloom. */
      /* Fog is halation at night and a white smear by day, because additive
       * light added to a bright grey background is just white. So the glows
       * grow with fog only once it is dark enough for them to be the light in
       * the scene rather than a wash over it. */
      /* Fog eats light with distance; it does not brighten it. The lamps only
       * grow with fog once night has made them the light in the scene. */
      const haze = 1 - fog * 0.42 * (1 - night);
      this._setColour(this.iSpill, i, c,
                      (0.08 + night * 0.90) * (1 + wet * 0.7) * drive * 0.55 * haze);
      this._setColour(this.iHalo, i, c, drive * (0.38 + night * 1.05) * haze);
      this._setColour(this.iBarGlow, i, c, drive * (0.15 + night * 0.50) * haze);

      /* The halo swells with the beat, which is what makes a RED beacon read as
       * pulsing rather than merely brightening. */
      const halo = (7 + night * 5 + fog * 6) * (0.8 + beat * 0.35) * (vis.dim ? 0.6 : 1);
      this._scaleAt(this.iHalo, i, e.x, e.lensY, e.z, halo, halo);

      /* The sign. Yaw-billboard only: it stays vertical like a real sign, and
       * pitching it to face a high camera would immediately look like UI. */
      const card = e.card;
      card.position.set(e.x, e.cardY, e.z);
      const d = cam.position.distanceTo(card.position);
      let s = Math.pow(Math.max(1, d) / CARD_REF_DIST, CARD_EXP);
      s = Math.max(CARD_MIN, Math.min(CARD_MAX, s));
      const focus = (this.selected === e.uid ? 1.08 : 1) *
                    (this.hovered === e.uid ? 1.05 : 1);
      card.scale.setScalar(s * focus);
      card.rotation.y = Math.atan2(cam.position.x - e.x, cam.position.z - e.z);
      /* The plate is unlit and the composite tone-maps, so it needs driving
       * above white to come out white — and it throbs with its own status,
       * which is what makes RED unmissable at the wide preset. */
      const cardDrive = 1.28 * (vis.dim ? 0.62 : 1) *
        (st.beat === 'alarm' || st.beat === 'hazard' ? 0.86 + beat * 0.16 : 1);
      card.material.color.setScalar(cardDrive);

      const sel = this.selected === e.uid, hov = this.hovered === e.uid;
      const wantOutline = sel ? 0.34 + 0.10 * Math.sin(t * 2.4) : (hov ? 0.13 : 0);
      const o = e.outline.material;
      o.opacity += (wantOutline - o.opacity) * 0.18;
      e.outline.visible = o.opacity > 0.005;
      if (sel) o.color.setRGB(0.34, 0.74, 1.40);
      else o.color.setRGB(0.52, 0.60, 0.74);
    }

    for (const m of [this.iLens, this.iBar, this.iBarGlow, this.iSpill, this.iHalo]) {
      if (m?.instanceColor) m.instanceColor.needsUpdate = true;
    }
    if (this.iHalo) this.iHalo.instanceMatrix.needsUpdate = true;
  }

  _setColour(mesh, i, colour, gain) {
    if (!mesh?.instanceColor) return;
    const a = mesh.instanceColor.array;
    a[i * 3] = colour.r * gain;
    a[i * 3 + 1] = colour.g * gain;
    a[i * 3 + 2] = colour.b * gain;
  }

  /** A camera-facing quad written straight into an instance matrix — cheaper
   *  than a Sprite per beacon and it keeps the halos in one draw call. */
  _scaleAt(mesh, i, x, y, z, sx, sy) {
    if (!mesh) return;
    const cam = this.ctx.camera;
    this._euler.set(0, Math.atan2(cam.position.x - x, cam.position.z - z), 0, 'YXZ');
    this._q.setFromEuler(this._euler);
    this._m4.compose(this._tmp.set(x, y, z), this._q,
                     this._scratch.set(sx, sy, 1));
    mesh.setMatrixAt(i, this._m4);
  }

  /* ---- DEAD-LINE smoulders -------------------------------------------------- */

  /** The old floor drew embers and rolling smoke over a dead-lined instrument.
   *  Same idea, one draw call each: a shared pool of puffs, handed out to
   *  whichever benches are dead-lined this minute. */
  _smoulder(dt, t) {
    if (!this.smoke) return;
    const lean = this.tier?.name === 'low' || this.tier?.name === 'floor';
    const sources = lean ? [] : this.entries.filter(e => e.vis?.smoulder);
    const w = this.ctx.weather || {};
    const wind = (w.wind ?? 0.35) * 2.4;
    const wa = w.windAngle ?? 0.6;
    const wx = Math.sin(wa) * wind, wz = Math.cos(wa) * wind;

    this.smoke.fog(this.ctx.scene);
    this.embers.fog(this.ctx.scene);

    for (let i = 0; i < this._puffs.length; i++) {
      const p = this._puffs[i];
      p.life -= dt;
      if (p.life <= 0) {
        if (!sources.length) { this.smoke.hide(i); continue; }
        const e = sources[i % sources.length];
        p.entry = e;
        p.life = p.max = 4.6 + Math.random() * 3.2;
        p.x = e.x + (Math.random() - 0.5) * 7;
        p.y = e.ground + e.top * (0.35 + Math.random() * 0.4);
        p.z = e.z + (Math.random() - 0.5) * 7;
        p.rise = 2.6 + Math.random() * 2.2;
        p.rot = Math.random() * Math.PI * 2;
        p.spin = (Math.random() - 0.5) * 0.35;
        p.size = 5 + Math.random() * 5;
      }
      const k = 1 - p.life / p.max;
      p.x += wx * dt * (0.4 + k); p.z += wz * dt * (0.4 + k);
      p.y += p.rise * dt;
      p.rot += p.spin * dt;
      /* Fades in fast and out slowly: a puff that appears at full opacity pops,
       * and one that vanishes at full opacity pops twice. */
      const a = Math.min(1, k * 6) * Math.pow(1 - k, 1.4) * 0.42;
      const soot = 0.30 + 0.22 * (1 - k);
      this.smoke.set(i, p.x, p.y, p.z, p.size * (0.6 + k * 1.9), a, p.rot,
                     soot, soot * 0.96, soot * 0.94);
    }
    this.smoke.flush();

    for (let i = 0; i < this._sparks.length; i++) {
      const s = this._sparks[i];
      s.life -= dt;
      if (s.life <= 0) {
        if (!sources.length) { this.embers.hide(i); continue; }
        const e = sources[i % sources.length];
        s.life = s.max = 1.1 + Math.random() * 1.6;
        s.x = e.x + (Math.random() - 0.5) * 5;
        s.y = e.ground + e.top * (0.3 + Math.random() * 0.35);
        s.z = e.z + (Math.random() - 0.5) * 5;
        s.rise = 3.4 + Math.random() * 3.6;
        s.size = 0.5 + Math.random() * 0.8;
      }
      const k = 1 - s.life / s.max;
      s.x += wx * dt * 0.7; s.z += wz * dt * 0.7;
      s.y += s.rise * dt * (1 - k * 0.6);
      /* Embers cool as they climb: hot white-orange at the base, deep red at
       * the top of the column, and they wink because they tumble. */
      const flick = 0.55 + 0.45 * Math.sin(t * 21 + i * 2.3);
      const a = Math.pow(1 - k, 1.8) * flick;
      this.embers.set(i, s.x, s.y, s.z, s.size, a, 0,
                      2.2, 0.95 - k * 0.55, 0.30 - k * 0.22);
    }
    this.embers.flush();
  }

  /* ---- selection and hover -------------------------------------------------- */

  _marks(dt, t) {
    const sel = this.selected && this.byUid.get(this.selected);
    const hov = this.hovered && this.hovered !== this.selected &&
                this.byUid.get(this.hovered);

    if (sel) {
      const y = sel.ground + 0.18;
      const cx = sel.x, cz = sel.z;
      this.selRing.position.set(cx, y, cz);
      const pulse = 1 + 0.012 * Math.sin(t * 1.9);
      this.selRing.scale.set(96 * pulse, 1, 96 * pulse);
      this.selRing.rotation.y = t * 0.10;
      this.selRing.visible = true;
      this.selRing.material.opacity = 0.55 + 0.15 * Math.sin(t * 1.9);
      /* The column stops at the beacon. Run it past the sign and it washes
       * over the plate from the front, which costs legibility to say something
       * the ring on the ground has already said. */
      this.selBeam.position.set(sel.x, sel.ground, sel.z);
      this.selBeam.scale.set(1, sel.top + sel.mast, 1);
      this.selBeam.visible = true;
      this.selBeam.material.opacity = 0.05 + 0.025 * Math.sin(t * 1.9) +
                                      this.night * 0.07;
    } else {
      this.selRing.visible = false;
      this.selBeam.visible = false;
    }

    if (hov) {
      const y = hov.ground + 0.16;
      this.hovRing.position.set(hov.x, y, hov.z);
      this.hovRing.scale.set(88, 1, 88);
      this.hovRing.rotation.y = -t * 0.06;
      this.hovRing.visible = true;
      /* Hover is a whisper and selection is a statement. They use the same
       * mark, so the only thing keeping them apart is that this one is faint —
       * a hover that shouts reads as "this is the selected one" and the
       * operator loses track of what the left rail is actually showing. */
      this.hovRing.material.opacity = 0.18;
    } else {
      this.hovRing.visible = false;
    }
    void dt;
  }

  /* ---- teardown ------------------------------------------------------------ */

  _teardownEntries() {
    for (const e of this.entries) {
      e.card?.geometry.dispose();
      e.card?.material.dispose();
      e.outline?.geometry.dispose();
      e.outline?.material.dispose();
      e.texture?.dispose();
      if (e.card) this.group.remove(e.card);
    }
    this.entries.length = 0;
    this.byUid.clear();
    for (const m of [this.iMast, this.iStalk, this.iHousing, this.iLens, this.iHalo,
                     this.iBar, this.iHaz, this.iBarGlow, this.iFrame, this.iSpill]) {
      if (!m) continue;
      this.group.remove(m);
      m.dispose?.();
      if (m.geometry !== this.geoMast && m.geometry !== this.geoStalk &&
          m.geometry !== this.geoHousing && m.geometry !== this.geoLens &&
          m.geometry !== this.geoBar && m.geometry !== this.geoFrame) {
        m.geometry.dispose();
      }
    }
    this.iMast = this.iStalk = this.iHousing = this.iLens = this.iHalo =
      this.iBar = this.iHaz = this.iBarGlow = this.iFrame = this.iSpill = null;
  }

  dispose() {
    try {
      this._teardownEntries();
      this.smoke?.dispose();
      this.embers?.dispose();
      for (const g of [this.geoMast, this.geoHousing, this.geoLens, this.geoStalk,
                       this.geoBar, this.geoFrame]) g?.dispose();
      for (const m of [this.matSteel, this.matDark, this.matLens, this.matBar,
                       this.matHazard, this.matSpill, this.matHalo]) m?.dispose();
      for (const t of [this.texGlow, this.texSpill, this.texEmber, this.texSmoke,
                       this.texBar, this.texHazard, this.texRing]) t?.dispose();
      this.selRing?.geometry.dispose();
      this.selRing?.material.dispose();
      this.hovRing?.material.dispose();
      this.selBeam?.geometry.dispose();
      this.selBeam?.material.dispose();
      this.ctx.scene.remove(this.group);
    } catch (err) {
      console.error('[labels] dispose', err);
    }
  }
}

export default Labels;
