/* textures.js — every surface in the world, generated on the client.
 *
 * The brief is "conservative poly count, detail from textures". That is only
 * affordable if the textures cost nothing to ship: a lab bench has no internet,
 * the whole page has to stay under 8 MB, and a folder of 2K PBR maps is 40 MB
 * before you have painted a single train. So nothing here is downloaded —
 * albedo, normal, roughness and AO are all drawn into canvases at load time
 * from noise, then uploaded once and shared by every mesh that wants them.
 *
 * Two consequences worth knowing:
 *   - Generation is synchronous and happens once. A 512 map costs ~4ms; the
 *     whole set is budgeted under 400ms, inside the 3s-to-interactive target.
 *   - Every map is tileable. `fbm` wraps on the tile, so a road can run the
 *     length of the map on one 512 texture without a visible repeat seam,
 *     which is where the detail budget actually goes.
 */
import * as THREE from 'three';

/* ---- noise ------------------------------------------------------------- */

function hash2(x, y, seed) {
  let h = x * 374761393 + y * 668265263 + seed * 2246822519;
  h = (h ^ (h >>> 13)) * 1274126177;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

/** Value noise on a wrapping lattice — `period` keeps the tile seamless. */
function valueNoise(x, y, period, seed) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const w = (a, b) => hash2(((a % period) + period) % period,
                            ((b % period) + period) % period, seed);
  const a = w(xi, yi), b = w(xi + 1, yi), c = w(xi, yi + 1), d = w(xi + 1, yi + 1);
  return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v;
}

export function fbm(x, y, {octaves = 5, period = 8, seed = 1,
                           lacunarity = 2, gain = 0.5} = {}) {
  /* The wrapping period has to track the frequency exactly — the moment they
   * drift apart the tile shows a seam, which is the whole point of the lattice
   * being periodic in the first place. */
  let sum = 0, amp = 1, norm = 0, freq = 1, p = period;
  for (let i = 0; i < octaves; i++) {
    sum += valueNoise(x * freq, y * freq, p, seed + i * 131) * amp;
    norm += amp;
    amp *= gain; freq *= lacunarity; p *= lacunarity;
  }
  return sum / norm;
}

/** Worley/cellular, wrapping — the basis for gravel, ballast and bark. */
export function cells(x, y, period, seed) {
  const xi = Math.floor(x), yi = Math.floor(y);
  let best = 1e9, second = 1e9;
  for (let oy = -1; oy <= 1; oy++) {
    for (let ox = -1; ox <= 1; ox++) {
      const cx = xi + ox, cy = yi + oy;
      const wx = ((cx % period) + period) % period;
      const wy = ((cy % period) + period) % period;
      const px = cx + hash2(wx, wy, seed);
      const py = cy + hash2(wx, wy, seed + 977);
      const d = (px - x) * (px - x) + (py - y) * (py - y);
      if (d < best) { second = best; best = d; } else if (d < second) second = d;
    }
  }
  return {f1: Math.sqrt(best), f2: Math.sqrt(second)};
}

/* ---- canvas helpers ----------------------------------------------------- */

/* A global resolution scale, applied to every generated map.
 *
 * This is the quality lever that costs the least to look at. Halving a texture
 * costs detail nobody can resolve at the distance a floor display is watched
 * from; thinning the forest, which is what the ladder used to do first, changes
 * the world itself. Set before anything builds — see Engine's tier.
 *
 * Sizes stay powers of two and never fall below 64: a mip chain on a 37-pixel
 * texture is not a saving, it is an artefact. */
let SCALE = 1;

export function setTextureScale(s) {
  SCALE = Math.max(0.2, Math.min(1, Number(s) || 1));
}

export function textureScale() { return SCALE; }

function scaled(size) {
  if (SCALE >= 1) return size;
  const want = Math.max(64, Math.round(size * SCALE));
  return Math.pow(2, Math.round(Math.log2(want)));
}

function canvas(size) {
  const c = document.createElement('canvas');
  c.width = c.height = scaled(size);
  return c;
}

function makeTexture(cv, {srgb = false, repeat = 1, aniso = 8} = {}) {
  const tex = new THREE.CanvasTexture(cv);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  tex.repeat.set(repeat, repeat);
  tex.anisotropy = aniso;
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  return tex;
}

/** Height field → tangent-space normal map. Sobel, wrapping at the edges. */
export function normalFromHeight(height, size, strength = 2.0) {
  const cv = canvas(size);
  const ctx = cv.getContext('2d');
  const img = ctx.createImageData(size, size);
  const at = (x, y) => height[((y + size) % size) * size + ((x + size) % size)];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1)) -
                 (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1));
      const dy = (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1)) -
                 (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1));
      let nx = dx * strength, ny = dy * strength, nz = 1;
      const len = Math.hypot(nx, ny, nz);
      nx /= len; ny /= len; nz /= len;
      const i = (y * size + x) * 4;
      img.data[i] = (nx * 0.5 + 0.5) * 255;
      img.data[i + 1] = (ny * 0.5 + 0.5) * 255;
      img.data[i + 2] = (nz * 0.5 + 0.5) * 255;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return cv;
}

/** Paint a size×size canvas from a per-pixel function returning [r,g,b] 0..1. */
export function paint(size, fn) {
  const cv = canvas(size);
  size = cv.width;                       // whatever the scale actually gave us
  const ctx = cv.getContext('2d');
  const img = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const c = fn(x, y, x / size, y / size);
      const i = (y * size + x) * 4;
      img.data[i] = Math.max(0, Math.min(255, c[0] * 255));
      img.data[i + 1] = Math.max(0, Math.min(255, c[1] * 255));
      img.data[i + 2] = Math.max(0, Math.min(255, c[2] * 255));
      img.data[i + 3] = c[3] === undefined ? 255 : c[3] * 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return cv;
}

/* Roughness and metalness ride in one texture: three.js reads roughness from
 * G and metalness from B of the same map, so one upload covers both and the
 * material samples it once. R is left for AO, which is the same packing
 * glTF uses — worth matching so nothing here surprises a reader. */
export function packORM(size, fn) {
  return paint(size, (x, y, u, v) => {
    const {ao = 1, roughness = 0.8, metalness = 0} = fn(x, y, u, v);
    return [ao, roughness, metalness];
  });
}

/* ---- the library -------------------------------------------------------- */

const CACHE = new Map();

/** Materials are shared by uid — one asphalt texture set for the whole map. */
export function material(uid, build) {
  if (!CACHE.has(uid)) CACHE.set(uid, build());
  return CACHE.get(uid);
}

export function disposeAll() {
  for (const m of CACHE.values()) {
    m.map?.dispose?.(); m.normalMap?.dispose?.();
    m.roughnessMap?.dispose?.(); m.aoMap?.dispose?.();
    m.dispose?.();
  }
  CACHE.clear();
}

export const Tex = {makeTexture, canvas, paint, packORM, normalFromHeight,
                   fbm, cells, setTextureScale, textureScale};
