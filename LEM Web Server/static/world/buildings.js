/* buildings.js — the instruments, as places.
 *
 * Every instrument on the floor is a rail-served petroleum facility: brick or
 * steel-clad processing halls, storage tanks in a bund, pipe racks, a loading
 * gantry over the siding, stair towers, roof plant, conduit. Nothing here is a
 * box with a window texture on it — the site is assembled from a kit of parts
 * by rules, at real dimensions (a door is 2.1m, a storey is 3.6m, a tank is
 * 8-14m across), so the map reads as a place with a scale rather than a chart.
 *
 * Three decisions carry the file:
 *
 *   1. **Everything is merged.** A facility is 200-400 parts, but a part is
 *      never its own mesh — parts are accumulated per material and merged into
 *      one buffer, so a whole site costs eight draw calls instead of three
 *      hundred. `three/addons` is not vendored, so the merge is written here.
 *
 *   2. **Texel density is world-scale, not per-face.** Box UVs from three are
 *      0..1 on every face, which stretches brick across a 40m wall and squashes
 *      it on a 0.4m pilaster. Every box is re-UV'd by triplanar projection at
 *      metres-per-tile after it is placed, so one brick is one brick anywhere.
 *
 *   3. **Status never repaints the building.** A DEAD-LINE facility is shut —
 *      gates across the dock, no lights, dark glass; a SERVICE one has barriers
 *      round the siding. Those are prebuilt, hidden meshes toggled by
 *      `onMachines`, so a status poll every two seconds costs a boolean.
 *      Beacons and signage belong to labels.js and are deliberately absent.
 *
 * What rail.js depends on: `dock(uid)` → `{position, direction}`, the place a
 * train stands when it is at that station, and the direction it faces. Docks
 * are on the north (−Z) face of every site, 26m out from its centre, running
 * east-west; the LabCore terminal's is on its south face. Nothing is built
 * within 3.5m of the dock line below 6m, so a track can be laid straight
 * through it.
 */
import * as THREE from 'three';
import * as TexMod from 'world/textures.js';

/* textures.js exports its helpers both loose and on a `Tex` bag; take either so
 * this file does not care which one it was given. */
const TX = {...TexMod, ...(TexMod.Tex || {})};

const STOREY = 3.6;          // floor to floor
const DOOR_H = 2.1;          // a door, and therefore the unit everything reads against
const DOCK_OFFSET = 26;      // metres from a station's centre to its track centreline
const PLATFORM_EDGE = 2.4;   // track centreline to platform face
const TRACK_CLEAR = 3.6;     // nothing but the gantry inside this of the centreline

const STATUS_COLOUR = {
  GREEN: 0x21c071, YELLOW: 0xf5c542, RED: 0xf85b5b,
  SERVICE: 0xa855f7, 'DEAD-LINE': 0xe2483d, UNKNOWN: 0x6b7280,
};

/* ------------------------------------------------------------------ maths -- */

const saturate = x => (x < 0 ? 0 : x > 1 ? 1 : x);
const mix = (a, b, t) => a + (b - a) * t;
const mixc = (a, b, t) => [mix(a[0], b[0], t), mix(a[1], b[1], t), mix(a[2], b[2], t)];
function smooth(a, b, x) {
  const t = saturate((x - a) / (b - a || 1e-6));
  return t * t * (3 - 2 * t);
}
/** A stable hash for "which brick is this" / "is this pane lit". */
function h1(a, b, s) {
  let h = Math.imul(a | 0, 374761393) + Math.imul(b | 0, 668265263) +
          Math.imul(s | 0, 2246822519);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

/* ------------------------------------------------------- geometry plumbing -- */

/** Merge geometries that share a material into one buffer.
 *
 *  `BufferGeometryUtils` lives in three/addons, which is not vendored, so this
 *  is the small subset the kit actually produces: indexed, with position,
 *  normal and uv. Sources are disposed as they are consumed — at 300 parts per
 *  site the intermediate BoxGeometries are the peak memory of the whole build.
 */
function mergeParts(list) {
  if (!list || !list.length) return null;
  let vTotal = 0, iTotal = 0;
  for (const g of list) {
    if (!g.attributes.normal) g.computeVertexNormals();
    if (!g.getIndex()) {
      const n = g.attributes.position.count;
      const seq = new Uint32Array(n);
      for (let i = 0; i < n; i++) seq[i] = i;
      g.setIndex(new THREE.BufferAttribute(seq, 1));
    }
    vTotal += g.attributes.position.count;
    iTotal += g.getIndex().count;
  }
  const pos = new Float32Array(vTotal * 3);
  const nor = new Float32Array(vTotal * 3);
  const uvs = new Float32Array(vTotal * 2);
  const idx = vTotal > 65000 ? new Uint32Array(iTotal) : new Uint16Array(iTotal);
  let vo = 0, io = 0;
  for (const g of list) {
    const p = g.attributes.position, n = g.attributes.normal, u = g.attributes.uv;
    const count = p.count;
    pos.set(p.array, vo * 3);
    nor.set(n.array, vo * 3);
    if (u) uvs.set(u.array, vo * 2);
    const gi = g.getIndex();
    for (let i = 0; i < gi.count; i++) idx[io + i] = gi.getX(i) + vo;
    vo += count; io += gi.count;
    g.dispose();
  }
  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  out.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  out.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  out.setIndex(new THREE.BufferAttribute(idx, 1));
  out.computeBoundingSphere();
  return out;
}

/** Triplanar UVs at metres-per-tile, applied after the part is in place. This
 *  is what stops a 0.4m pilaster and a 40m wall from having different brick. */
function worldUV(geo, scale) {
  const p = geo.attributes.position, n = geo.attributes.normal;
  const uv = geo.attributes.uv || new THREE.BufferAttribute(
    new Float32Array(p.count * 2), 2);
  const inv = 1 / scale;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const ax = Math.abs(n.getX(i)), ay = Math.abs(n.getY(i)), az = Math.abs(n.getZ(i));
    let u, v;
    if (ay >= ax && ay >= az) { u = x; v = z; }
    else if (ax >= az) { u = -z; v = y; }
    else { u = x; v = y; }
    uv.setXY(i, u * inv, v * inv);
  }
  uv.needsUpdate = true;
  geo.setAttribute('uv', uv);
}

function scaleUV(geo, su, sv) {
  const uv = geo.attributes.uv;
  if (!uv) return;
  for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * su, uv.getY(i) * sv);
  uv.needsUpdate = true;
}

/* Metres per texture tile, per material. Brick is 1.8m (24 courses), cladding
 * 2.0m (8 box-profile ribs), concrete 3.0m — the numbers the samplers below
 * were drawn for. */
const TILE = {
  concrete: 4.5, wall: 1.8, clad: 2.0, roof: 2.4, steel: 2.0, trim: 1.6,
  rust: 3.2, door: 2.0, hazard: 1.4, lamp: 1.0, sign: 1.0, window: 1.0,
};

/** Accumulates parts per material key. Every method places the part in the
 *  site's local frame; nothing is a Mesh until `finish()`. */
class Kit {
  /** `mirror` is -1 for a site built handed the other way. It is applied to the
   *  placement arguments, not to the finished geometry, so winding and normals
   *  never invert and nothing has to be drawn double-sided — and because every
   *  archetype and every helper places through these methods, one flag hands
   *  the whole facility: stack, stair tower, tank bund and all. Four archetypes
   *  across sixteen benches is what made the critics say they saw the same
   *  building twice; this is half the answer to it. */
  /** `pad` is the site's earthworks surface (`makeEarthworks`) in local metres:
   *  what the ground under this facility is, once cut and filled. Every part is
   *  placed relative to `this.base`, and `base` is what the last `stand()` found
   *  under the footprint it was given — so a mast, a rack bent and a hall each
   *  sit on their own ground instead of all inheriting the site origin. Without
   *  a pad it answers zero everywhere, which is exactly the old flat behaviour,
   *  so a site built before terrain exists is unchanged rather than broken. */
  constructor(tileFor, mirror = 1, pad = null) {
    this.groups = new Map();
    this.tileFor = tileFor || (k => TILE[k] || 2.0);
    this.mx = mirror < 0 ? -1 : 1;
    /* Footprints of everything standing above the pad, for `occlusionField`.
     * Collected here rather than scanned off the geometry afterwards because
     * `mergeParts` disposes its sources — by the time there is a mesh to
     * measure, the parts are gone. */
    this.occ = [];
    this.pad = typeof pad === 'function' ? pad : (() => 0);
    this.base = 0;
    /* The founding level of the first thing the archetype stood up — its
     * principal volume. `top` comes back from the archetype in its own local
     * frame, and labels.js hangs off it, so the anchor has to be told which
     * ground that frame was measured from. */
    this.mainBase = null;
    this.founds = [];
  }
  /** Highest and lowest the earthworks reach under a footprint. Sampled on a
   *  small grid rather than at the centre: the whole defect being fixed here is
   *  a part that was placed from one height while its footprint spanned a
   *  grade, and a one-point sample is the same mistake one level down. */
  span(x, z, hx = 1, hz = 1) {
    x *= this.mx;
    let hi = -Infinity, lo = Infinity;
    const n = 2;
    for (let i = -n; i <= n; i++) {
      for (let j = -n; j <= n; j++) {
        const v = this.pad(x + (hx * i) / n, z + (hz * j) / n);
        if (!Number.isFinite(v)) continue;
        if (v > hi) hi = v;
        if (v < lo) lo = v;
      }
    }
    if (!Number.isFinite(hi)) return {hi: 0, lo: 0};
    return {hi, lo};
  }
  /** Found everything placed after this call on the ground under `(x, z)`.
   *  Returns the previous base so a helper that stands on its own can put it
   *  back — a flood mast finding its own ground must not silently move the
   *  building the archetype was halfway through. */
  stand(x, z, hx = 1, hz = 1) {
    const prev = this.base;
    const s = this.span(x, z, hx, hz);
    this.base = s.hi;
    if (this.mainBase === null) this.mainBase = this.base;
    /* Kept so the harness can ask the one question a screenshot answers slowly:
     * which footprint was founded at a level its own low corner does not reach,
     * and by how much. Anything here that is not `footed` is relying on its own
     * buried skirt to bridge the drop. */
    this.founds.push({x, z, hx, hz, hi: s.hi, lo: s.lo, footed: false});
    return prev;
  }
  /** `stand`, plus the plinth that makes it honest. A 60m bund across a 1-in-30
   *  grade stands 2m off the ground at its low end; founding it at the high end
   *  and leaving it there is the "hanging over a graded edge with a gap
   *  beneath" half of the fault. This fills that gap with the structure's own
   *  footing, down past the lowest ground under it. */
  foot(key, x, z, hx, hz, {skirt = 0.9, inset = 0} = {}) {
    const {hi, lo} = this.span(x, z, hx, hz);
    const prev = this.base;
    this.base = hi;
    if (this.mainBase === null) this.mainBase = this.base;
    this.founds.push({x, z, hx, hz, hi, lo, footed: true});
    const drop = hi - lo + skirt;
    if (drop > 0.2) {
      /* Placed through `box`, so it is mirrored and UV'd like everything else —
       * but with `base` temporarily at the bottom of the fill, because `box`
       * adds `base` to the y it is given. */
      this.base = lo - skirt;
      this.box(key, (hx - inset) * 2, drop, (hz - inset) * 2, x, drop / 2, z);
      this.base = hi;
    }
    return prev;
  }
  raw(key, geo) {
    let list = this.groups.get(key);
    if (!list) this.groups.set(key, list = []);
    list.push(geo);
    return geo;
  }
  /** Record a footprint if the part is massive enough to occlude the ground.
   *  The 0.75m floor on `top` is what keeps the podium, the buried skirt and
   *  the apron slabs out of it — they are the ground, and a field that says the
   *  ground occludes the ground darkens the whole site evenly, which is the one
   *  result indistinguishable from no field at all. */
  _occ(x, z, hx, hz, top, h) {
    if (!(top > 0.75) || !(h > 0.9)) return;
    if (hx * hz > 4200) return;          // yard slabs, not buildings
    this.occ.push({x, z, hx, hz, top});
  }
  /** A placed box, triplanar-UV'd. `ry` is the only rotation most parts need. */
  box(key, w, h, d, x, y, z, ry = 0) {
    x *= this.mx; if (this.mx < 0) ry = -ry; y += this.base;
    const g = new THREE.BoxGeometry(w, h, d);
    if (ry) g.applyMatrix4(new THREE.Matrix4().makeRotationY(ry));
    g.translate(x, y, z);
    worldUV(g, this.tileFor(key));
    const c = Math.abs(Math.cos(ry)), s = Math.abs(Math.sin(ry));
    this._occ(x, z, (w * c + d * s) / 2, (w * s + d * c) / 2, y + h / 2, h);
    return this.raw(key, g);
  }
  /** A box tilted about X or Z — roof slopes, braces, loading arms. */
  tilt(key, w, h, d, x, y, z, rx = 0, rz = 0, ry = 0) {
    x *= this.mx; if (this.mx < 0) { rz = -rz; ry = -ry; } y += this.base;
    const g = new THREE.BoxGeometry(w, h, d);
    const e = new THREE.Euler(rx, ry, rz, 'ZYX');
    g.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(e));
    g.translate(x, y, z);
    worldUV(g, this.tileFor(key));
    const lift = Math.max(w, d) * (Math.abs(Math.sin(rx)) + Math.abs(Math.sin(rz)));
    this._occ(x, z, (w + d) / 2 * 0.75, (w + d) / 2 * 0.75, y + (h + lift) / 2, h + lift);
    return this.raw(key, g);
  }
  /** Upright cylinder — tanks, stacks, columns. UVs wrap the circumference. */
  cyl(key, rTop, rBot, h, x, y, z, seg = 20, capped = true) {
    x *= this.mx; y += this.base;
    const g = new THREE.CylinderGeometry(rTop, rBot, h, seg, 1, !capped);
    const s = this.tileFor(key);
    scaleUV(g, (2 * Math.PI * Math.max(rTop, rBot)) / s, h / s);
    g.translate(x, y + h / 2, z);
    const r = Math.max(rTop, rBot);
    this._occ(x, z, r, r, y + h, h);
    return this.raw(key, g);
  }
  /** A pipe between two points. Everything petroleum is made of these. */
  tube(key, a, b, r, seg = 8) {
    /* Always cloned, never mutated: these come in as caller-owned vectors and
     * more than one pipe run reuses the same one. */
    a = new THREE.Vector3(a.x * this.mx, a.y + this.base, a.z);
    b = new THREE.Vector3(b.x * this.mx, b.y + this.base, b.z);
    const dir = new THREE.Vector3().subVectors(b, a);
    const len = dir.length();
    if (len < 1e-4) return null;
    const g = new THREE.CylinderGeometry(r, r, len, seg, 1, true);
    const s = this.tileFor(key);
    scaleUV(g, (2 * Math.PI * r) / s, len / s);
    const q = new THREE.Quaternion().setFromUnitVectors(
      new THREE.Vector3(0, 1, 0), dir.clone().normalize());
    g.applyMatrix4(new THREE.Matrix4().makeRotationFromQuaternion(q));
    g.translate(a.x + dir.x / 2, a.y + dir.y / 2, a.z + dir.z / 2);
    return this.raw(key, g);
  }
  sphere(key, r, x, y, z, seg = 10) {
    x *= this.mx; y += this.base;
    const g = new THREE.SphereGeometry(r, seg, Math.max(4, seg >> 1));
    const s = this.tileFor(key);
    scaleUV(g, (2 * Math.PI * r) / s, (Math.PI * r) / s);
    g.translate(x, y, z);
    return this.raw(key, g);
  }
  cone(key, r, h, x, y, z, seg = 16) {
    x *= this.mx; y += this.base;
    const g = new THREE.ConeGeometry(r, h, seg);
    const s = this.tileFor(key);
    scaleUV(g, (2 * Math.PI * r) / s, h / s);
    g.translate(x, y + h / 2, z);
    return this.raw(key, g);
  }
  torus(key, r, tube, x, y, z, seg = 16) {
    x *= this.mx; y += this.base;
    const g = new THREE.TorusGeometry(r, tube, 6, seg);
    g.rotateX(Math.PI / 2);
    scaleUV(g, (2 * Math.PI * r) / this.tileFor(key), 1);
    g.translate(x, y, z);
    return this.raw(key, g);
  }
  /** A flat panel with explicit tiling — glazing, signs, anything whose texture
   *  is a designed layout rather than a surface. `ry` faces it outward. */
  /** `ou`/`ov` slide the tile. Two window bands of the same width on two
   *  storeys of the same building sample the identical region of the glazing
   *  map otherwise, and stacking one lit office directly above another is the
   *  repeat the eye finds first on a facade. */
  panel(key, w, h, x, y, z, ry, su = 1, sv = 1, ou = 0, ov = 0) {
    x *= this.mx; if (this.mx < 0) ry = -ry; y += this.base;
    const g = new THREE.PlaneGeometry(w, h);
    scaleUV(g, su, sv);
    if (ou || ov) {
      const uv = g.attributes.uv;
      for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) + ou, uv.getY(i) + ov);
      uv.needsUpdate = true;
    }
    if (ry) g.applyMatrix4(new THREE.Matrix4().makeRotationY(ry));
    g.translate(x, y, z);
    return this.raw(key, g);
  }
  finish(materials, nameFor) {
    const out = [];
    for (const [key, list] of this.groups) {
      const mat = materials[key];
      if (!mat || !list.length) { list.forEach(g => g.dispose()); continue; }
      const geo = mergeParts(list);
      if (!geo) continue;
      const mesh = new THREE.Mesh(geo, mat);
      mesh.name = nameFor ? nameFor(key) : key;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.matKey = key;
      out.push(mesh);
    }
    this.groups.clear();
    return out;
  }
}

/* --------------------------------------------------------------- surfaces -- */

/* Every material is drawn at load time: albedo (with crevice AO multiplied in
 * — three's aoMap wants a second UV set this geometry does not carry), a normal
 * map from a height field, and one packed roughness/metalness map. Sizes are
 * chosen per surface: brick and concrete are read from a metre away at street
 * level and get 512; a tank shell never is. */
function surface(sample, {size = 256, detail = 128, strength = 2.2} = {}) {
  const map = TX.makeTexture(TX.paint(size, (x, y, u, v) => {
    const s = sample(u, v);
    const ao = s.ao === undefined ? 1 : s.ao;
    return [s.c[0] * ao, s.c[1] * ao, s.c[2] * ao];
  }), {srgb: true, aniso: 8});
  const height = new Float32Array(detail * detail);
  for (let y = 0; y < detail; y++) {
    for (let x = 0; x < detail; x++) {
      const s = sample((x + 0.5) / detail, (y + 0.5) / detail);
      height[y * detail + x] = s.h === undefined ? 0.5 : s.h;
    }
  }
  const normalMap = TX.makeTexture(TX.normalFromHeight(height, detail, strength),
                                   {aniso: 4});
  const ormMap = TX.makeTexture(TX.packORM(detail, (x, y, u, v) => {
    const s = sample(u, v);
    return {ao: 1, roughness: s.r === undefined ? 0.85 : s.r,
            metalness: s.m === undefined ? 0 : s.m};
  }), {aniso: 4});
  return {map, normalMap, ormMap};
}

function pbr(maps, opts = {}) {
  const {relief = 1, ...rest} = opts;
  const m = new THREE.MeshStandardMaterial({
    map: maps.map, normalMap: maps.normalMap,
    roughnessMap: maps.ormMap, metalnessMap: maps.ormMap,
    roughness: 1, metalness: 1,
    ...rest,
  });
  if (maps.normalMap) m.normalScale = new THREE.Vector2(relief, relief);
  m.userData.baseRoughness = 1;
  return m;
}

/* ------------------------------------------------------ the facade patch --- */

/* Two of the three things every round of critics has named about this file are
 * not expressible in a tile, which is why they live in a shader patch instead
 * of in `surface()`: a wall meets its apron on a bare seam, and a 40m facade
 * shows its 1.8m brick.
 *
 *   `bldField` is per site — a top-down grid of what stands on the pad and how
 *   tall it is, blurred a few metres so coverage bleeds off each footprint onto
 *   the ground around it. Read by local XZ it finds the strip of apron against
 *   every wall; read against local Y it dies away a couple of metres up. That
 *   product is the contact gradient, and it costs one texture fetch.
 *
 *   `bldMacro` is one noise map shared by the whole site list, sampled at 10-40m
 *   — an order of magnitude below the tile — at a per-site offset and scale, so
 *   two facilities of the same archetype do not stain in the same places.
 *   Staining, chalked paint, downwash streaks and a patch-repair tint all come
 *   off it, and none of them costs a triangle or a bigger albedo.
 *
 * The patch is installed before gi.js adopts the material, so gi chains it
 * (`prev?.call` at the top of its own `onBeforeCompile`) rather than replacing
 * it. Installing it later would silently drop the lighting patch instead.
 *
 * Everything this file injects is named `bld*`, and that is not decoration.
 * Two modules splicing the same shader share one scope, and gi's `GI_AO` — the
 * block spliced after `<aomap_fragment>`, immediately above ours — declares its
 * own `float lemContact` there for the direct-light bite. Ours was called
 * `lemContact` too, so the AO term silently read gi's value instead of its own
 * and crushed indirect light across every building on the site to a quarter.
 * It compiled cleanly, warned about nothing, and looked exactly like a lighting
 * bug. Do not use the `lem` prefix in here.
 */

const _missed = new Set();

/** `onBeforeCompile` runs before three expands its `#include`s, so the only
 *  anchors that can be relied on are the include lines themselves — and a
 *  `.replace` that matches nothing returns the source unchanged and says
 *  nothing, which is how this project lost days once already. */
function after(src, anchor, body, tag) {
  const out = src.replace(anchor, anchor + '\n' + body);
  if (out === src && !_missed.has(tag)) {
    _missed.add(tag);
    console.warn(`[buildings] shader anchor "${anchor}" not found (${tag}) — ` +
                 'contact darkening and macro breakup are not being applied.');
  }
  return out;
}

const FACADE_VERT_PARS = `
varying vec3 vBldLocal;
varying vec3 vBldNrm;`;

const FACADE_FRAG_PARS = `
uniform sampler2D bldField;
uniform vec4 bldFieldXf;
uniform sampler2D bldMacro;
uniform vec4 bldMacroXf;
uniform vec3 bldPatchTint;
/* x macro breakup, y contact darkening, z downwash streaking, w top-face grime. */
uniform vec4 bldFacade;
varying vec3 vBldLocal;
varying vec3 vBldNrm;
float bldContact = 0.0;
float bldMacroV = 0.5;
float bldTopV = 0.0;`;

/* Spliced after `<map_fragment>` so it modulates the albedo three just read,
 * and early enough that the roughness and AO splices below can reuse the two
 * values it leaves behind. */
const FACADE_ALBEDO = `
  {
    vec3 bldN = abs(normalize(vBldNrm));
    vec2 bldP = (bldN.y > max(bldN.x, bldN.z)) ? vBldLocal.xz
              : ((bldN.x > bldN.z) ? vBldLocal.zy : vBldLocal.xy);
    bldP += bldMacroXf.xy;
    float bldUpright = 1.0 - bldN.y;
    float bldA = texture2D(bldMacro, bldP * bldMacroXf.z).r;
    float bldB = texture2D(bldMacro, bldP.yx * bldMacroXf.w).r;
    /* Stretched hard along the fall line: rain leaves a facade streaked
     * vertically, and an isotropic stain is the one kind of dirt that still
     * reads as noise rather than as weather. */
    float bldRun = texture2D(bldMacro, vec2(bldP.x * bldMacroXf.w * 2.3,
                                            bldP.y * bldMacroXf.w * 0.085) + 0.31).r;
    bldMacroV = bldA * 0.58 + bldB * 0.42;
    float bldWash = mix(0.5, bldRun, bldUpright);
    vec2 bldUV = (vBldLocal.xz - bldFieldXf.xy) * bldFieldXf.zw;
    vec2 bldFld = texture2D(bldField, bldUV).rg;
    /* A contact gradient is short. 3.2m of falloff looked like the building
     * was standing in a pool of shade rather than meeting the ground; a metre
     * and a half, stretched a little by how tall the occluder is, is what a
     * wall-to-apron junction actually reads as. */
    float bldFall = 0.50 + 1.45 * bldFld.g;
    bldContact = clamp(bldFld.r * exp(-max(vBldLocal.y, 0.0) / bldFall), 0.0, 1.0)
               * bldFacade.y;
    float bldTone = mix(1.0 - bldFacade.x, 1.0 + bldFacade.x * 0.75, bldMacroV);
    bldTone *= 1.0 - bldFacade.z * bldUpright * (0.60 - bldWash);
    diffuseColor.rgb *= clamp(bldTone, 0.30, 1.7);
    diffuseColor.rgb = mix(diffuseColor.rgb, bldPatchTint * (0.62 + 0.76 * bldMacroV),
                           smoothstep(0.61, 0.90, bldA) * bldFacade.x * 2.3);
    /* Top faces collect what falls out of the sky, and the SIGN of the normal is
     * what says which ones they are — bldN above is an absolute value, so it
     * cannot tell a tank roof from the underside of a canopy.
     *
     * This is here because of what a plan-view camera actually sees. At cam=far
     * the eye is 27 degrees above the horizon and a storage tank presents more
     * roof disc than shell; the roof is the flattest, most sky-facing, least
     * shaded surface on the site, and in bare shell paint it was rendering as a
     * white lid. "Flat white-grey discs" is a description of these, not of the
     * cylinders under them. Clean steel does not stay clean pointing at the
     * weather, so the fix is the same thing that is true. */
    float bldUp = normalize(vBldNrm).y;
    bldTopV = smoothstep(0.30, 0.90, bldUp) * bldFacade.w;
    diffuseColor.rgb = mix(diffuseColor.rgb,
                           diffuseColor.rgb * vec3(0.70, 0.685, 0.635)
                                            * (0.80 + 0.36 * bldA),
                           bldTopV);
    /* The seam itself. A splash zone is darker paint as well as less light —
     * the albedo half is what survives full sun, which is when the bare seam
     * is most obvious. */
    diffuseColor.rgb *= 1.0 - 0.26 * bldContact;
  }`;

/* Dust on a roof is not paint: it scatters. Without this the grimed disc keeps
 * the shell's sheen and reads as a dark mirror rather than as a dirty lid. */
const FACADE_ROUGH = `
  roughnessFactor = clamp(roughnessFactor * mix(0.88, 1.13, bldMacroV)
                          + 0.16 * bldContact + 0.22 * bldTopV, 0.035, 1.0);`;

/* Indirect only, after `<aomap_fragment>`, for the same reason gi.js lands
 * there: a contact shadow is missing bounce, not missing sun. */
const FACADE_AO = `
  {
    /* 0.55, not the 0.86 this started at. Ambient occlusion darkens a seam;
     * past about half the indirect term it stops reading as a seam and starts
     * reading as a hole in the ground, which is worse than no occlusion at all
     * because the eye reads a hole as geometry. */
    float bldAO = 1.0 - 0.55 * bldContact;
    reflectedLight.indirectDiffuse *= bldAO;
    reflectedLight.indirectSpecular *= bldAO;
  }`;

function patchFacade(mat, uniforms) {
  if (!mat || !mat.isMeshStandardMaterial) return mat;
  mat.onBeforeCompile = shader => {
    try {
      Object.assign(shader.uniforms, uniforms);
      let v = shader.vertexShader;
      v = after(v, '#include <common>', FACADE_VERT_PARS, 'vs:common');
      v = after(v, '#include <begin_vertex>', '  vBldLocal = transformed;', 'vs:begin');
      v = after(v, '#include <beginnormal_vertex>', '  vBldNrm = objectNormal;', 'vs:nrm');
      shader.vertexShader = v;
      let f = shader.fragmentShader;
      f = after(f, '#include <common>', FACADE_FRAG_PARS, 'fs:common');
      f = after(f, '#include <map_fragment>', FACADE_ALBEDO, 'fs:map');
      f = after(f, '#include <roughnessmap_fragment>', FACADE_ROUGH, 'fs:rough');
      f = after(f, '#include <aomap_fragment>', FACADE_AO, 'fs:ao');
      shader.fragmentShader = f;
    } catch (err) {
      console.warn('[buildings] facade patch failed', err);
    }
  };
  mat.customProgramCacheKey = () => 'lem-facade-2';
  return mat;
}

/** A top-down occlusion field for one site.
 *
 *  Rasterises the footprint of everything the kit placed that stands above the
 *  pad, then blurs it about three metres so the coverage spills onto the ground
 *  around each wall. R is coverage, G is the occluder's height normalised — the
 *  shader turns G into the falloff length, because a 20m tank should darken the
 *  concrete further up its own shell than a 1.2m platform edge does.
 */
function occlusionField(occ, res) {
  if (!occ || !occ.length) return null;
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (const o of occ) {
    if (o.x - o.hx < minX) minX = o.x - o.hx;
    if (o.x + o.hx > maxX) maxX = o.x + o.hx;
    if (o.z - o.hz < minZ) minZ = o.z - o.hz;
    if (o.z + o.hz > maxZ) maxZ = o.z + o.hz;
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minZ)) return null;
  /* Padded so the blur always has zero to fall into: a field whose coverage
   * runs off the edge clamps to a dark border across the whole apron. */
  const pad = 16;
  const span = Math.max(maxX - minX, maxZ - minZ) + pad * 2;
  const ox = (minX + maxX) / 2 - span / 2;
  const oz = (minZ + maxZ) / 2 - span / 2;
  const N = res;
  const cell = span / N;
  const cov = new Float32Array(N * N);
  const hgt = new Float32Array(N * N);
  for (const o of occ) {
    const i0 = Math.max(0, Math.floor((o.x - o.hx - ox) / cell));
    const i1 = Math.min(N - 1, Math.ceil((o.x + o.hx - ox) / cell));
    const j0 = Math.max(0, Math.floor((o.z - o.hz - oz) / cell));
    const j1 = Math.min(N - 1, Math.ceil((o.z + o.hz - oz) / cell));
    for (let j = j0; j <= j1; j++) {
      for (let i = i0; i <= i1; i++) {
        const k = j * N + i;
        cov[k] = 1;
        if (o.top > hgt[k]) hgt[k] = o.top;
      }
    }
  }
  /* One blur, about two and a half metres wide. Four passes at three metres
   * was tried first and it is the failure this project already has a name for:
   * the halo reached eight metres off every wall, met the halo off the next
   * one, and painted a large soft casterless dark patch across the apron
   * between them. An occlusion halo has to stop before it becomes weather. */
  const r = Math.max(1, Math.round(2.4 / cell));
  /* The height field is dilated before it is blurred. Blurring it straight
   * would drag the falloff length to zero exactly where the coverage is
   * spilling onto the ground — which is the only place it is read. */
  const dil = new Float32Array(N * N);
  for (let j = 0; j < N; j++) {
    for (let i = 0; i < N; i++) {
      let m = 0;
      for (let dj = -r; dj <= r; dj++) {
        const jj = j + dj;
        if (jj < 0 || jj >= N) continue;
        for (let di = -r; di <= r; di++) {
          const ii = i + di;
          if (ii < 0 || ii >= N) continue;
          const v = hgt[jj * N + ii];
          if (v > m) m = v;
        }
      }
      dil[j * N + i] = m;
    }
  }
  const blur = src => {
    let a = src, b = new Float32Array(N * N);
    for (let pass = 0; pass < 1; pass++) {
      for (let j = 0; j < N; j++) {          // horizontal
        for (let i = 0; i < N; i++) {
          let s = 0, n = 0;
          for (let d = -r; d <= r; d++) {
            const ii = i + d;
            if (ii < 0 || ii >= N) continue;
            s += a[j * N + ii]; n++;
          }
          b[j * N + i] = s / n;
        }
      }
      const t = a; a = b; b = t;
      for (let j = 0; j < N; j++) {          // vertical
        for (let i = 0; i < N; i++) {
          let s = 0, n = 0;
          for (let d = -r; d <= r; d++) {
            const jj = j + d;
            if (jj < 0 || jj >= N) continue;
            s += a[jj * N + i]; n++;
          }
          b[j * N + i] = s / n;
        }
      }
      const t2 = a; a = b; b = t2;
    }
    return a;
  };
  const cb = blur(cov), hb = blur(dil);
  const data = new Uint8Array(N * N * 4);
  for (let k = 0; k < N * N; k++) {
    data[k * 4] = Math.min(255, Math.round(saturate(cb[k]) * 255));
    data[k * 4 + 1] = Math.min(255, Math.round(saturate(hb[k] / 14) * 255));
    data[k * 4 + 2] = 0;
    data[k * 4 + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, N, N, THREE.RGBAFormat);
  tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.minFilter = tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return {tex, xf: new THREE.Vector4(ox, oz, 1 / span, 1 / span)};
}

/* --- brick ---------------------------------------------------------------- */
/* 1.8m tile: 24 courses of 75mm, 8 stretchers of 225mm, half-bond. */
function brickSample(u, v) {
  const ROWS = 24, COLS = 8;
  const row = Math.floor(v * ROWS);
  const cx = u * COLS + ((row & 1) ? 0.5 : 0);
  const col = Math.floor(cx);
  const fx = cx - col, fy = v * ROWS - row;
  const mw = 0.055, mh = 0.15;
  const grime = TX.fbm(u * 6, v * 6, {octaves: 3, period: 6, seed: 11});
  const fine = TX.fbm(u * 26, v * 26, {octaves: 2, period: 26, seed: 41});
  if (fx < mw || fx > 1 - mw || fy < mh || fy > 1 - mh) {
    const g = 0.50 + 0.16 * grime + 0.06 * fine;
    return {c: [g * 1.02, g * 0.99, g * 0.94], h: 0.26 + 0.10 * fine,
            r: 0.95, m: 0, ao: 0.70};
  }
  const t = h1(col, row, 7);
  let c;
  if (t < 0.10) c = [0.235, 0.130, 0.115];
  else if (t > 0.93) c = [0.60, 0.44, 0.35];
  else c = mixc([0.395, 0.185, 0.145], [0.585, 0.315, 0.235], (t - 0.10) / 0.83);
  const bright = 0.86 + 0.26 * h1(col, row, 13);
  const dirt = 0.80 + 0.34 * grime;
  const k = bright * dirt * (0.94 + 0.12 * fine);
  const edge = smooth(mw, mw + 0.06, fx) * smooth(mw, mw + 0.06, 1 - fx) *
               smooth(mh, mh + 0.10, fy) * smooth(mh, mh + 0.10, 1 - fy);
  return {c: [c[0] * k, c[1] * k, c[2] * k],
          h: 0.78 + 0.10 * fine + 0.07 * (h1(col, row, 3) - 0.5),
          r: 0.80 + 0.14 * fine, m: 0, ao: 0.80 + 0.20 * edge};
}

/* --- profiled steel cladding ---------------------------------------------- */
/* Box profile, 250mm pitch, 8 ribs on a 2m tile. The albedo is near-neutral on
 * purpose: buildings tint the material, and a saturated base would fight it. */
function cladSample(u, v) {
  const RIBS = 8;
  const t = (u * RIBS) % 1;
  const crest = smooth(0.16, 0.30, t) * smooth(0.84, 0.70, t);
  const streak = TX.fbm(u * 28, 0.37, {octaves: 3, period: 28, seed: 9});
  const blotch = TX.fbm(u * 14, v * 14, {octaves: 3, period: 14, seed: 19});
  const scratch = TX.fbm(u * 60, v * 60, {octaves: 2, period: 60, seed: 29});
  /* One purlin line of fasteners per tile — 2m, which is where a sheeting rail
   * actually is. Five rows per tile banded the wall every 400mm and the tile
   * repeat became the loudest thing on the building. */
  const fRow = Math.abs(v - 0.5);
  const fastener = (1 - smooth(0.44, 0.5, fRow)) * smooth(0.42, 0.5, Math.abs(t - 0.5));
  const dirty = saturate(streak * (0.55 + 0.7 * blotch) * 1.35 - 0.46);
  /* Corrosion has to come from a field that does not know where the tile edge
   * is, or every sheet rusts at the same height. */
  const rustMask = saturate(blotch * 2.2 - 1.58) * 0.62;
  /* Chalking: the binder in a coating breaks down under UV and leaves a
   * powdery, desaturated, much rougher surface in patches. It is the single
   * thing that separates painted sheet from plastic — the critics' words for
   * the loco were "flat diffuse plastic with no metal response", and the same
   * criticism lands here. A chalked patch is lighter, flatter and matt; the
   * paint beside it still has a sheen. */
  const chalk = saturate(TX.fbm(u * 5, v * 5, {octaves: 4, period: 5, seed: 71}) * 1.9 - 0.72);
  let c = [0.80, 0.805, 0.79];
  c = mixc(c, [0.32, 0.33, 0.32], dirty * 0.58);
  c = mixc(c, [0.855, 0.858, 0.850], chalk * 0.55);
  c = mixc(c, [0.44, 0.27, 0.17], rustMask);
  const k = 0.94 + 0.10 * scratch - 0.14 * fastener;
  return {c: [c[0] * k, c[1] * k, c[2] * k],
          h: 0.10 + 0.86 * crest - 0.12 * fastener,
          /* Intact coating is a fairly tight 0.32; chalk and dirt take it to
           * matt, and only corrosion introduces any metal response at all —
           * paint is a dielectric, and the old 0.10 floor gave every clad wall
           * on the site a faint metallic wash it should never have had. */
          r: 0.32 + 0.34 * chalk + 0.26 * dirty + 0.42 * rustMask + 0.06 * scratch,
          m: 0.02 + 0.34 * rustMask,
          ao: 0.76 + 0.24 * crest};
}

/* --- concrete ------------------------------------------------------------- */
/* Deliberately featureless: an apron is 60m across and three metres of tile, so
 * anything with a repeating landmark in it — a form line, a big aggregate
 * blotch — tiles into masonry. Concrete is carried by its roughness break and
 * by what stains it, not by pattern. */
function concreteSample(u, v) {
  const n1 = TX.fbm(u * 3, v * 3, {octaves: 4, period: 3, seed: 3});
  const n2 = TX.fbm(u * 13, v * 13, {octaves: 3, period: 13, seed: 23});
  const n3 = TX.fbm(u * 47, v * 47, {octaves: 2, period: 47, seed: 43});
  const stain = saturate(TX.fbm(u * 7, v * 7, {octaves: 3, period: 7, seed: 31}) * 1.5 - 0.80);
  const g = 0.545 + 0.028 * n1 - 0.040 * n2 + 0.032 * n3 - 0.125 * stain;
  return {c: [g, g * 0.995, g * 0.962],
          h: 0.5 + 0.22 * n3 + 0.08 * n2,
          r: 0.88 + 0.08 * n2 - 0.07 * stain, m: 0, ao: 0.96 + 0.04 * n2};
}

/* --- painted steel plate -------------------------------------------------- */
function steelSample(u, v) {
  const plate = Math.min(Math.abs(((u * 3) % 1) - 0.5), Math.abs(((v * 3) % 1) - 0.5));
  const seam = 1 - smooth(0.46, 0.5, plate);
  const n = TX.fbm(u * 12, v * 12, {octaves: 3, period: 12, seed: 61});
  const fine = TX.fbm(u * 44, v * 44, {octaves: 2, period: 44, seed: 67});
  /* Corrosion is deliberately sparse: a tank shell is 12m across and one
   * texture tile is 2m, so anything more than the odd bloom tiles into
   * camouflage rather than into weathering. */
  const rust = saturate(n * 2.4 - 1.85);
  const drip = smooth(0.45, 1.0, v) * saturate(TX.fbm(
    u * 30, 0.8, {octaves: 2, period: 30, seed: 91}) * 1.5 - 1.02);
  /* 0.60, where this was 0.80. A tank shell is the largest single surface on
   * the site and at 0.80 the sun put 3.8% of its pixels past 200/255 — inside
   * the tone curve's shoulder, which is the one place in the range where no
   * contrast survives at all. That is measured (harness/tk-sweep.mjs, cam=far
   * time=9): albedo x0.62 takes pctOver200 from 3.8 to 0.0 and leaves the shell
   * with somewhere for the terminator to go. It is also the correct number —
   * light industrial paint is a 0.55-0.65 reflector, not chalk.
   *
   * Cool, and deliberately: the dirt this stands on is warm (the critique's
   * "the industrial mass and the dirt are nearly the same value"), so the tank
   * is separated on hue where it cannot be separated on value. */
  let c = mixc([0.596, 0.612, 0.628], [0.500, 0.516, 0.534], 0.6 * fine);
  c = mixc(c, [0.44, 0.27, 0.165], saturate(rust + drip * 0.8));
  /* Painted plate, not a mirror and not a metal: paint is a dielectric, and the
   * metalness that used to sit here took 12-42% of the shell out of the diffuse
   * term the sun actually reaches it through. Bare corroded patches keep some. */
  return {c, h: 0.66 + 0.12 * fine - 0.30 * seam,
          r: 0.54 + 0.16 * fine + 0.34 * saturate(rust + drip),
          m: 0.04 + 0.16 * saturate(rust), ao: 0.88 + 0.12 * (1 - seam)};
}

/* --- corroded steel ------------------------------------------------------- */
function rustSample(u, v) {
  const a = TX.fbm(u * 7, v * 7, {octaves: 4, period: 7, seed: 5});
  const b = TX.fbm(u * 23, v * 23, {octaves: 3, period: 23, seed: 15});
  const pit = TX.cells(u * 30, v * 30, 30, 25);
  /* Kept low-contrast: a 3.2m tile on a 9m tank repeats three times across the
   * shell, and high-contrast corrosion at that rate reads as animal print. */
  const t = saturate(a * 1.15 + b * 0.30 - 0.22);
  let c = mixc([0.335, 0.205, 0.135], [0.505, 0.315, 0.185], t);
  c = mixc(c, [0.285, 0.245, 0.215], saturate(0.72 - a * 1.4) * 0.6);
  const k = 0.92 + 0.14 * b - 0.10 * saturate(1 - pit.f1 * 2.2);
  return {c: [c[0] * k, c[1] * k, c[2] * k],
          h: 0.52 + 0.34 * b - 0.24 * saturate(1 - pit.f1 * 2.0),
          r: 0.86 + 0.12 * b, m: 0.22 + 0.2 * t, ao: 0.84 + 0.16 * b};
}

/* --- flat roof: bitumen and gravel ---------------------------------------- */
function roofSample(u, v) {
  const c1 = TX.cells(u * 22, v * 22, 22, 7);
  const patch = TX.fbm(u * 6, v * 6, {octaves: 3, period: 6, seed: 13});
  const g = 0.295 + 0.145 * saturate(c1.f1 * 1.5) + 0.09 * patch;
  const lap = 1 - smooth(0.46, 0.5, Math.abs(((v * 3) % 1) - 0.5));
  return {c: [g * 1.02, g * 1.0, g * 0.95], h: 0.5 + 0.3 * saturate(c1.f1 * 1.4) - 0.2 * lap,
          r: 0.93 - 0.08 * patch, m: 0.02, ao: 0.88 + 0.12 * saturate(c1.f2 - c1.f1)};
}

/* --- roller shutter / louvre ---------------------------------------------- */
function shutterSample(u, v) {
  const slat = Math.abs(((v * 22) % 1) - 0.5);
  const face = smooth(0.5, 0.16, slat);
  const wear = TX.fbm(u * 20, v * 20, {octaves: 2, period: 20, seed: 37});
  const g = 0.19 + 0.16 * face + 0.06 * wear;
  return {c: [g * 0.95, g * 0.98, g], h: 0.25 + 0.7 * face,
          r: 0.48 + 0.3 * wear, m: 0.35, ao: 0.72 + 0.28 * face};
}

/* --- hazard stripe -------------------------------------------------------- */
function hazardSample(u, v) {
  const s = ((u + v) * 5) % 1;
  const on = smooth(0.02, 0.06, s) * smooth(0.52, 0.48, s);
  const wear = TX.fbm(u * 18, v * 18, {octaves: 2, period: 18, seed: 47});
  const c = mixc([0.055, 0.05, 0.05], [0.86, 0.58, 0.05], on);
  const k = 0.82 + 0.28 * wear;
  return {c: [c[0] * k, c[1] * k, c[2] * k], h: 0.5, r: 0.55 + 0.3 * wear, m: 0.1};
}

/* --- glazing --------------------------------------------------------------- */
/* One tile is THREE bays wide and TWO storeys tall — not one bay, which is what
 * it was, and which the critics described exactly: "identical window panels
 * repeat along the facade", "one flat blue tile stamped repeatedly". Both were
 * literally true. The panel was scaled one tile per structural bay, so the same
 * three panes, the same half-dropped blind and the same lit office marched the
 * whole length of every building on the site.
 *
 * Three bays per tile costs nothing but texel density — the same 512 map, a
 * third of the pixels per pane, which glass does not need — and moves the
 * repeat from 3m to 9m. Bay boundaries still land exactly on the physical
 * mullions at any bay count, because each bay owns a third of the tile and the
 * panel is scaled `bays / WBAYS`: the period divides.
 *
 * Inside a bay, what is behind the glass now varies: an empty room, blinds part
 * way down, a plant-room louvre, a lit office. The albedo is dark and cool and
 * the metalness is near 1 — glass is a tinted mirror, and a pale albedo makes a
 * painted rectangle no matter how low the roughness is. */
const WBAYS = 3, WFLOORS = 2;

function windowSample(u, v, seed) {
  const bu = u * WBAYS, bv = v * WFLOORS;
  const bx = Math.min(WBAYS - 1, Math.floor(bu));
  const by = Math.min(WFLOORS - 1, Math.floor(bv));
  const su = bu - bx, sv = bv - by;             // position within this bay
  const s = seed * 977 + bx * 31 + by * 137;

  const PX = 3, PY = 2;
  const grime = TX.fbm(u * 9, v * 9, {octaves: 2, period: 9, seed: 53});
  const fleck = TX.fbm(u * 44, v * 44, {octaves: 2, period: 44, seed: 83});

  const fw = 0.050, fh = 0.058;                 // bay surround
  const outer = su < fw || su > 1 - fw || sv < fh || sv > 1 - fh;
  const px = Math.min(PX - 1, Math.floor(su * PX));
  const py = Math.min(PY - 1, Math.floor(sv * PY));
  const gx = su * PX - px, gy = sv * PY - py;   // gy = 0 at the head of a pane
  const mull = gx < 0.075 || gx > 0.925 || gy < 0.085 || gy > 0.915;
  if (outer || mull) {
    /* Painted steel section, and darker in the outer surround than on the
     * mullions — the surround sits deepest in the reveal, and it is that step
     * in tone that reads as a frame with a thickness rather than a drawn grid. */
    const deep = outer ? 0.72 : 1.0;
    const g = (0.090 + 0.044 * grime + 0.022 * fleck) * deep;
    return {c: [g * 0.96, g, g * 1.10], h: outer ? 0.96 : 0.86,
            r: 0.38 + 0.26 * grime, m: 0.42,
            ao: outer ? 0.56 : 0.78, lit: 0, warm: 0};
  }

  const kind = h1(px, py, s + 3);
  /* A plant room behind the glazing line: a louvre, not a pane. Every real
   * process building has a few, and they break the run of glass the way
   * nothing painted onto glass can. */
  if (kind < 0.11) {
    const slat = Math.abs(((gy * 15) % 1) - 0.5);
    const face = smooth(0.5, 0.13, slat);
    const g = 0.095 + 0.105 * face + 0.035 * fleck;
    return {c: [g * 0.98, g, g * 0.96], h: 0.34 + 0.36 * face,
            r: 0.58 + 0.2 * fleck, m: 0.32, ao: 0.58 + 0.34 * face,
            lit: 0, warm: 0};
  }

  const up = 1 - gy;                            // sky lands hardest at the head
  const tint = h1(px, py, s + 11);
  const dirty = h1(px, py, s + 17);
  /* Dark enough to be a mirror rather than a painted panel, light enough that
   * the sky still lands on it: at metalness ~0.94 the albedo IS the reflected
   * colour, so a near-black pane reflects nothing and reads as a hole, and the
   * old pale one reflected everything and read as the flat blue tile. */
  let c = mixc([0.088, 0.112, 0.146], [0.212, 0.256, 0.318], up * up);
  c = mixc(c, [0.130, 0.148, 0.160], tint * 0.55);
  /* Glass is never flat. A gentle bow per pane is a fraction of a millimetre in
   * reality and it is the whole reason a real curtain wall shimmers pane by
   * pane instead of reflecting the sky as one sheet. */
  const bow = Math.sin(gx * Math.PI) * Math.sin(gy * Math.PI);
  let hgt = 0.22 + 0.13 * bow;
  let rough = 0.040 + 0.055 * grime + 0.10 * dirty * dirty;
  let metal = 0.94;
  let blind = 0;

  if (kind > 0.79) {
    const drop = 0.30 + 0.55 * h1(px, py, s + 29);
    blind = smooth(drop + 0.04, drop - 0.04, gy);
    const slats = 0.66 + 0.34 * Math.abs(((gy * 30) % 1) * 2 - 1);
    c = mixc(c, [0.244 * slats, 0.233 * slats, 0.208 * slats], blind * 0.94);
    rough = mix(rough, 0.66, blind * 0.92);
    metal = mix(metal, 0.04, blind * 0.95);
    hgt = mix(hgt, 0.30, blind);
  }
  /* Whoever cleans these does it a pane at a time. */
  const dust = 1 - 0.34 * dirty * (0.4 + 0.6 * grime);
  const litRoll = h1(px, py, s + 53);
  const lit = litRoll > 0.40 ? (0.28 + 0.72 * h1(px, py, s + 89)) : 0;
  return {c: [c[0] * dust, c[1] * dust, c[2] * dust],
          h: hgt, r: rough, m: metal, ao: 0.96,
          /* A room is brightest at its ceiling and falls off to the cill; a
           * pane lit evenly to the edges is a light box, not a room. */
          lit: lit * (1 - blind * 0.62) * (0.45 + 0.55 * up * up),
          warm: h1(px, py, s + 131)};
}

function windowSurface(seed) {
  const s = (u, v) => windowSample(u, v, seed);
  const maps = surface(s, {size: 512, detail: 384, strength: 1.9});
  maps.emissiveMap = TX.makeTexture(TX.paint(384, (x, y, u, v) => {
    const w = windowSample(u, v, seed);
    /* Some rooms are on fluorescent and some on the warm lamps over a bench.
     * A whole facility lit in one colour temperature is the tell that the
     * lights are a texture rather than rooms. */
    const warm = w.warm || 0;
    const r = mix(0.80, 1.0, warm), g = mix(0.88, 0.86, warm);
    const b = mix(1.0, 0.60, warm);
    return [w.lit * r, w.lit * g, w.lit * b];
  }), {srgb: true, aniso: 4});
  return maps;
}

/** The LABCORE sign — drawn with real text, because the terminal has to be
 *  legible from the wide camera and a painted-noise "sign" is not. */
function signTextures(text) {
  const W = 1024, H = 256;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const c = cv.getContext('2d');
  c.fillStyle = '#141a22'; c.fillRect(0, 0, W, H);
  const grad = c.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, 'rgba(255,255,255,0.06)');
  grad.addColorStop(1, 'rgba(0,0,0,0.22)');
  c.fillStyle = grad; c.fillRect(0, 0, W, H);
  c.strokeStyle = '#2b3644'; c.lineWidth = 10;
  c.strokeRect(5, 5, W - 10, H - 10);
  c.fillStyle = '#0f1620'; c.fillRect(28, 196, W - 56, 8);
  const lit = document.createElement('canvas');
  lit.width = W; lit.height = H;
  const l = lit.getContext('2d');
  l.fillStyle = '#000'; l.fillRect(0, 0, W, H);
  for (const g of [c, l]) {
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.font = '700 132px "Helvetica Neue", Helvetica, Arial, sans-serif';
    g.letterSpacing = '18px';
    g.fillStyle = g === c ? '#f2f5f8' : '#ffe3ac';
    g.fillText(text, W / 2, 108);
    g.font = '600 34px "Helvetica Neue", Helvetica, Arial, sans-serif';
    g.letterSpacing = '13px';
    g.fillStyle = g === c ? '#8fa2b6' : '#7d6a44';
    g.fillText('RECEIVING TERMINAL', W / 2, 224);
  }
  /* Bolt heads on the frame — the difference between a sign and a decal. */
  c.fillStyle = '#0c1119';
  for (let i = 0; i < 14; i++) {
    const x = 34 + i * ((W - 68) / 13);
    c.beginPath(); c.arc(x, 22, 5, 0, 7); c.fill();
    c.beginPath(); c.arc(x, H - 22, 5, 0, 7); c.fill();
  }
  return {map: TX.makeTexture(cv, {srgb: true, aniso: 8}),
          emissiveMap: TX.makeTexture(lit, {srgb: true, aniso: 8})};
}

/* --------------------------------------------------------------- materials -- */

function buildLibrary() {
  const lib = {};
  lib.concrete = pbr(surface(concreteSample, {size: 512, detail: 256, strength: 0.9}));
  lib.brick = pbr(surface(brickSample, {size: 512, detail: 384, strength: 3.4}),
                  {relief: 1.5});
  lib.clad = pbr(surface(cladSample, {size: 256, detail: 256, strength: 2.6}),
                 {relief: 1.25});
  lib.steel = pbr(surface(steelSample, {size: 256, detail: 128, strength: 1.6}));
  lib.rust = pbr(surface(rustSample, {size: 256, detail: 128, strength: 2.0}));
  lib.roof = pbr(surface(roofSample, {size: 256, detail: 128, strength: 1.8}));
  lib.door = pbr(surface(shutterSample, {size: 256, detail: 128, strength: 2.2}));
  lib.hazard = pbr(surface(hazardSample, {size: 128, detail: 128, strength: 1.0}));
  /* Five, not three. Sixteen benches over four archetypes means every facade
   * shape appears four times, and a shared glazing pattern is the detail that
   * makes two of them read as the same building. */
  lib.windowSets = [1, 2, 3, 4, 5].map(windowSurface);
  lib.signMaps = signTextures('LABCORE');
  /* The macro field. One map for the whole site list, read at 10-40m through a
   * per-site offset and scale — see `patchFacade`. Deliberately low-contrast
   * and low-frequency: it is not a texture, it is what stops the textures from
   * looking like textures. */
  lib.macro = TX.makeTexture(TX.paint(256, (x, y, u, v) => {
    const a = TX.fbm(u * 4, v * 4, {octaves: 5, period: 4, seed: 77});
    const b = TX.fbm(u * 11, v * 11, {octaves: 3, period: 11, seed: 131});
    const g = saturate(0.5 + (a - 0.5) * 1.62 + (b - 0.5) * 0.52);
    return [g, g, g];
  }), {aniso: 4});
  return lib;
}

/* ------------------------------------------------------------- kit of parts -- */

/** Handrail: posts, top rail, knee rail. Runs along X or Z. */
function handrail(kit, key, x0, z0, x1, z1, y, {h = 1.08, spacing = 2.1} = {}) {
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  if (len < 0.6) return;
  const n = Math.max(2, Math.round(len / spacing));
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    kit.box(key, 0.07, h, 0.07, x0 + dx * t, y + h / 2, z0 + dz * t);
  }
  const ang = Math.atan2(dx, dz);
  for (const ry of [h - 0.04, h * 0.55]) {
    kit.box(key, 0.06, 0.06, len, x0 + dx / 2, y + ry, z0 + dz / 2, ang);
  }
}

/** One flight of steel stair, running in Z: treads, both stringers, and a rake
 *  handrail. Restricted to the Z axis on purpose — every stair on this site is
 *  in a tower against a gable, and a general one needs a frame stack to keep
 *  the tilt honest. */
function flight(kit, key, x, z0, y0, z1, y1, width = 1.1) {
  const dz = z1 - z0, dy = y1 - y0;
  if (dy < 0.8 || Math.abs(dz) < 0.8) return;
  const len = Math.hypot(dz, dy);
  const ang = Math.atan2(dy, Math.abs(dz));
  const rx = dz > 0 ? -ang : ang;
  const steps = Math.max(4, Math.round(dy / 0.19));
  for (let i = 0; i < steps; i++) {
    const t = (i + 0.5) / steps;
    kit.box(key, width, 0.05, 0.27, x, y0 + dy * t, z0 + dz * t);
  }
  for (const s of [-1, 1]) {
    kit.tilt(key, 0.07, 0.28, len, x + s * width / 2, (y0 + y1) / 2 - 0.17,
             (z0 + z1) / 2, rx);
    kit.tilt(key, 0.05, 0.05, len, x + s * width / 2, (y0 + y1) / 2 + 0.98,
             (z0 + z1) / 2, rx);
    kit.tilt(key, 0.05, 0.05, len, x + s * width / 2, (y0 + y1) / 2 + 0.52,
             (z0 + z1) / 2, rx);
    for (let i = 0; i <= 3; i++) {
      const t = i / 3;
      kit.box(key, 0.05, 1.0, 0.05, x + s * width / 2, y0 + dy * t + 0.5, z0 + dz * t);
    }
  }
}

/** A caged ladder — cheaper than a stair and reads at any distance. */
function ladder(kit, key, x, z, yBot, yTop, face = 1) {
  const h = yTop - yBot;
  if (h < 1.4) return;
  kit.box(key, 0.05, h, 0.05, x - 0.24, yBot + h / 2, z);
  kit.box(key, 0.05, h, 0.05, x + 0.24, yBot + h / 2, z);
  const rungs = Math.floor(h / 0.32);
  for (let i = 1; i < rungs; i++) {
    kit.box(key, 0.52, 0.035, 0.035, x, yBot + i * 0.32, z);
  }
  const hoops = Math.floor(h / 1.6);
  for (let i = 1; i <= hoops; i++) {
    kit.torus(key, 0.42, 0.03, x, yBot + i * 1.6, z + face * 0.34, 10);
  }
}

/** Vertical storage tank: shell, cone roof, wind girder, top rail, manway,
 *  nozzles and either a spiral stair or a caged ladder. 8-14m across. */
function storageTank(kit, x, z, r, h, rng, {stairs = true, weathered = false} = {}) {
  const shell = weathered ? 'rust' : 'steel';
  /* 32/24 where this was 26/20. The shell is the one surface in the frame whose
   * whole job is to carry a smooth terminator around a curve, and at 20 sides a
   * 5m tank steps every 18 degrees — which is exactly the width of the band the
   * light is turning through. It costs 12 triangles a tank. */
  kit.cyl(shell, r, r * 1.005, h, x, 0.35, z, r > 6 ? 32 : 24, false);
  kit.cyl('steel', r * 0.16, r * 1.02, Math.max(1.1, r * 0.22), x, 0.35 + h, z, 28);
  kit.torus('steel', r * 1.01, 0.09, x, 0.35 + h * 0.62, z, 24);   // wind girder
  kit.torus('steel', r * 1.02, 0.11, x, 0.35 + h - 0.05, z, 24);   // curb angle
  kit.cyl('concrete', r + 0.55, r + 0.75, 0.42, x, 0, z, 24);      // ringwall
  /* Top rail around the roof edge — the thing that gives a tank its scale. */
  const posts = 16;
  for (let i = 0; i < posts; i++) {
    const a = (i / posts) * Math.PI * 2;
    kit.box('steel', 0.06, 1.0, 0.06,
            x + Math.cos(a) * r * 0.96, 0.35 + h + 0.12, z + Math.sin(a) * r * 0.96);
  }
  kit.torus('steel', r * 0.96, 0.045, x, 0.35 + h + 0.58, z, 24);
  kit.torus('steel', r * 0.96, 0.04, x, 0.35 + h + 0.3, z, 24);
  /* Nozzles and a manway on the shell. */
  const a0 = rng() * Math.PI * 2;
  kit.cyl('steel', 0.42, 0.42, 0.3, x + Math.cos(a0) * r, 1.1, z + Math.sin(a0) * r, 10);
  kit.box('steel', 0.9, 0.9, 0.24, x + Math.cos(a0 + 1.2) * r, 1.6,
          z + Math.sin(a0 + 1.2) * r, -a0 - 1.2);
  if (stairs) {
    /* A spiral stair wrapped on the shell: 26 treads and a helical rail. */
    const turns = 1.15, treads = 26;
    let px = null;
    for (let i = 0; i <= treads; i++) {
      const t = i / treads;
      const a = a0 + 2.4 + t * turns * Math.PI * 2;
      const cx = x + Math.cos(a) * (r + 0.55), cz = z + Math.sin(a) * (r + 0.55);
      const cy = 0.35 + t * h;
      kit.box('steel', 0.95, 0.05, 0.34, cx, cy, cz, -a);
      kit.box('steel', 0.05, 1.0, 0.05, cx + Math.cos(a) * 0.45, cy + 0.5,
              cz + Math.sin(a) * 0.45);
      if (px) {
        kit.tube('steel', new THREE.Vector3(px.x, px.y + 1.0, px.z),
                 new THREE.Vector3(cx + Math.cos(a) * 0.45, cy + 1.0,
                                   cz + Math.sin(a) * 0.45), 0.035, 5);
      }
      px = {x: cx + Math.cos(a) * 0.45, y: cy, z: cz + Math.sin(a) * 0.45};
    }
  } else {
    ladder(kit, 'steel', x + Math.cos(a0 + 3) * (r + 0.3), z + Math.sin(a0 + 3) * (r + 0.3),
           0.4, 0.35 + h + 0.6);
  }
  return 0.35 + h + Math.max(1.1, r * 0.22) + 0.6;
}

/** Horizontal bullet tank on saddles — LPG/product storage, unmistakably
 *  petroleum and cheap to draw. */
function bulletTank(kit, x, z, r, len, ry = 0) {
  const dir = new THREE.Vector3(Math.cos(ry), 0, Math.sin(ry));
  /* The vessel is one rigid thing, so it is founded once over its whole length;
   * each saddle is then drawn tall enough to reach the ground under itself. A
   * 22m bullet with both saddles cut at the same height is the "hanging over a
   * graded edge" fault in miniature, and there are five of them at the
   * terminal, on the part of the yard that grades hardest. */
  const prev = kit.stand(x, z, Math.abs(dir.x) * len / 2 + r,
                         Math.abs(dir.z) * len / 2 + r);
  const a = new THREE.Vector3(x - dir.x * len / 2, r + 1.5, z - dir.z * len / 2);
  const b = new THREE.Vector3(x + dir.x * len / 2, r + 1.5, z + dir.z * len / 2);
  kit.tube('steel', a, b, r, 18);
  kit.sphere('steel', r, a.x, a.y, a.z, 12);
  kit.sphere('steel', r, b.x, b.y, b.z, 12);
  const top = kit.base;
  for (const t of [-0.3, 0.3]) {
    const px = x + dir.x * len * t, pz = z + dir.z * len * t;
    const lo = Math.min(top, kit.span(px, pz, 0.8, r * 1.1).lo) - 0.5;
    kit.base = lo;
    kit.box('concrete', 1.4, top + 1.5 - lo, r * 2.1, px, (top + 1.5 - lo) / 2, pz, ry);
    kit.base = top;
    kit.box('steel', 1.5, 0.3, r * 1.9, px, 1.55, pz, ry);
  }
  kit.tube('steel', new THREE.Vector3(x, r * 2 + 1.5, z),
           new THREE.Vector3(x, r * 2 + 2.6, z), 0.12, 8);
  kit.base = prev;
}

/** A pipe rack: portal bents at 6m carrying 3-5 lines. Petroleum sites are
 *  mostly this, and it is what ties the separate volumes into one facility. */
function pipeRack(kit, x0, z0, x1, z1, height, count, rng) {
  const dx = x1 - x0, dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  if (len < 4) return;
  const ang = Math.atan2(dx, dz);
  const across = new THREE.Vector3(Math.cos(ang), 0, -Math.sin(ang));
  const bents = Math.max(2, Math.round(len / 6.5));
  const width = 0.55 + count * 0.42;
  const prev = kit.base;
  /* Every bent finds its own ground and is stretched to reach the pipes, which
   * stay at one level: a rack crossing a grade is a straight run of pipe on
   * legs of different lengths, not a pipe that follows the hill. The run is set
   * from the highest bent so no leg is ever short of its own beam. */
  const feet = [];
  let deck = -Infinity;
  for (let i = 0; i <= bents; i++) {
    const t = i / bents;
    const cx = x0 + dx * t, cz = z0 + dz * t;
    const y = kit.span(cx, cz, width * 0.6 + 0.6, width * 0.6 + 0.6).hi;
    feet.push([cx, cz, y]);
    if (y + height > deck) deck = y + height;
  }
  for (const [cx, cz, y] of feet) {
    kit.base = y;
    const hh = deck - y;
    for (const s of [-1, 1]) {
      kit.box('steel', 0.26, hh, 0.26,
              cx + across.x * s * width / 2, hh / 2, cz + across.z * s * width / 2);
    }
    kit.box('steel', width + 0.4, 0.3, 0.24, cx, hh - 0.15, cz, ang + Math.PI / 2);
    kit.box('concrete', 1.0, 0.35, 1.0, cx + across.x * width / 2, 0.17,
            cz + across.z * width / 2);
    kit.box('concrete', 1.0, 0.35, 1.0, cx - across.x * width / 2, 0.17,
            cz - across.z * width / 2);
  }
  kit.base = 0;
  for (let p = 0; p < count; p++) {
    const off = (p - (count - 1) / 2) * (width / Math.max(1, count));
    const r = 0.10 + rng() * 0.18;
    const y = deck - 0.32 - (p % 2) * 0.34;
    kit.tube(p % 3 === 2 ? 'rust' : 'steel',
             new THREE.Vector3(x0 + across.x * off, y, z0 + across.z * off),
             new THREE.Vector3(x1 + across.x * off, y, z1 + across.z * off), r, 8);
  }
  kit.base = prev;
}

/** Roof plant: air handlers, extract fans, ducting, a dunnage frame. */
function roofPlant(kit, cx, cz, w, d, y, rng) {
  const units = 2 + Math.floor(rng() * 2);
  for (let i = 0; i < units; i++) {
    const uw = 2.4 + rng() * 2.2, ud = 1.6 + rng() * 1.4, uh = 1.3 + rng() * 0.9;
    const px = cx + (rng() - 0.5) * (w - uw - 2);
    const pz = cz + (rng() - 0.5) * (d - ud - 2);
    kit.box('concrete', uw + 0.5, 0.25, ud + 0.5, px, y + 0.12, pz);
    kit.box('trim', uw, uh, ud, px, y + 0.25 + uh / 2, pz);
    kit.box('door', uw * 0.7, uh * 0.6, 0.06, px, y + 0.25 + uh / 2, pz + ud / 2 + 0.03);
    if (rng() > 0.4) {
      kit.cyl('steel', 0.55, 0.55, 0.5, px + uw * 0.25, y + 0.25 + uh, pz, 12);
      kit.cyl('steel', 0.58, 0.58, 0.12, px + uw * 0.25, y + 0.25 + uh + 0.5, pz, 12);
    }
  }
  for (let i = 0; i < 2; i++) {
    const px = cx + (rng() - 0.5) * (w - 3), pz = cz + (rng() - 0.5) * (d - 3);
    kit.cyl('steel', 0.5, 0.62, 0.9, px, y, pz, 12);
    kit.cone('steel', 0.62, 0.5, px, y + 0.9, pz, 12);
  }
  /* A duct run tying the plant to the parapet — the silhouette detail that
   * stops a flat roof reading as a lid. */
  const dx0 = cx - w * 0.28, dx1 = cx + w * 0.3;
  kit.box('steel', dx1 - dx0, 0.85, 0.85, (dx0 + dx1) / 2, y + 1.0, cz + d * 0.22);
  kit.box('steel', 0.9, 1.7, 0.9, dx1, y + 0.85, cz + d * 0.22);
}

/** A yard flood mast. The lens is a separate material so nightfall is a
 *  uniform change, not a rebuild. */
function floodMast(kit, x, z, h) {
  const prev = kit.stand(x, z, 0.7, 0.7);
  kit.cyl('steel', 0.16, 0.24, h, x, 0, z, 8);
  kit.box('concrete', 1.0, 0.5, 1.0, x, 0.25, z);
  kit.box('steel', 2.6, 0.16, 0.6, x, h + 0.12, z);
  for (const s of [-0.86, 0, 0.86]) {
    kit.box('steel', 0.86, 0.5, 0.66, x + s, h - 0.16, z);
    /* The lens is tilted down the way a yard floodlight is aimed, and it is
     * 20cm deep rather than a sliver — an edge-on emissive plate is invisible
     * from every camera that matters. */
    kit.tilt('lamp', 0.78, 0.2, 0.58, x + s, h - 0.42, z + 0.06, 0.5);
  }
  kit.base = prev;
}

/** The rail-served face: platform, canopy, loading gantry with arms, bollards
 *  and a small dispatch hut. Everything sits clear of the track centreline at
 *  local z = −DOCK_OFFSET, which is what `dock()` promises rail.js. */
function sidingFace(kit, halfWidth, rng, {gantry = true} = {}) {
  const tz = -DOCK_OFFSET;
  const pz = tz + PLATFORM_EDGE;               // platform face
  const pw = 5.0;                              // platform depth
  const len = Math.min(56, halfWidth * 2 + 14);
  const ph = 1.15;
  /* The platform is one casting, so it is founded once and its footing reaches
   * whatever the ground does under the other end. Everything standing ON it —
   * canopy, hut, bollards — then inherits that one level, which is what a
   * platform is for. */
  const plat = kit.foot('concrete', 0, pz + pw / 2, len / 2, pw / 2, {skirt: 1.4});
  kit.box('concrete', len, ph, pw, 0, ph / 2, pz + pw / 2);
  kit.box('concrete', len, 0.12, 0.5, 0, ph + 0.02, pz + 0.25);   // edge nosing
  kit.box('hazard', len, 0.02, 0.36, 0, ph + 0.09, pz + 0.55);    // edge line
  const platBase = kit.base;
  /* The apron is tarmac, not more concrete. A site whose every horizontal
   * surface is the same pale slab reads as a model on a board; the dark strip
   * between the platform and the doors is what gives the yard a floor. It is a
   * conforming slab and not a box: seventy metres of tarmac laid at one height
   * is a metre into the ground at one end of a station and a metre over it at
   * the other, and that is most of what "clipping through the ground" looked
   * like from the street camera. */
  kit.base = 0;
  slabGrid(kit, 'roof', -(len + 14) / 2, (len + 14) / 2,
           pz + pw - 6.75 + 6.5, pz + pw + 13.25, 4.0, 0.19);
  slabGrid(kit, 'concrete', -(len + 15) / 2, (len + 15) / 2,
           pz + pw + 12.8, pz + pw + 14.0, 4.0, 0.19);
  for (let i = 0; i < 4; i++) {
    const x = (-len * 0.34 + i * 3.4) * kit.mx;
    slabGrid(kit, 'hazard', x - 0.11, x + 0.11, pz + pw + 1.5, pz + pw + 6.5, 3.0, 0.22);
  }
  slabGrid(kit, 'hazard', -len * 0.25, len * 0.25,
           pz + pw + 1.09, pz + pw + 1.31, 4.0, 0.22);
  kit.base = platBase;

  /* Canopy on the platform: posts, beams, a shallow ridge. */
  const posts = Math.max(3, Math.round(len / 7));
  for (let i = 0; i <= posts; i++) {
    const px = -len / 2 + 1.5 + (len - 3) * (i / posts);
    kit.box('steel', 0.22, 3.5, 0.22, px, ph + 1.75, pz + pw - 0.9);
    kit.tilt('steel', 0.16, 0.16, 2.6, px, ph + 4.05, pz + pw - 2.1, 0.42);
  }
  kit.box('steel', len - 2, 0.3, 0.3, 0, ph + 3.6, pz + pw - 0.9);
  kit.tilt('roof', len - 1, 0.16, pw + 1.4, 0, ph + 4.28, pz + pw / 2 - 0.4, 0.12);
  kit.box('steel', len - 1, 0.22, 0.16, 0, ph + 3.98, pz - 0.2);   // gutter

  /* Loading gantry straddling the siding, with drop arms over the tank car. */
  if (gantry) {
    const gy = 6.9;
    const legs = [];
    for (const gx of [-len * 0.22, len * 0.22]) {
      for (const gz of [tz - 4.6, tz + 4.4]) legs.push([gx, gz]);
    }
    /* A gantry leg finds its own ground. The four of them straddle 45m of yard
     * and the beam they carry is level by definition, so the legs come out
     * different lengths — which is the point, and is why the deck height is
     * taken from the highest of them rather than from the site origin. */
    let gDeck = -Infinity;
    const legY = legs.map(([gx, gz]) => {
      const y = kit.span(gx, gz, 1.0, 1.0).hi;
      if (y + gy > gDeck) gDeck = y + gy;
      return y;
    });
    kit.base = gDeck - gy;
    for (let i = 0; i < legs.length; i++) {
      const [gx, gz] = legs[i];
      const foot = legY[i] - kit.base;                 // relative to the deck frame
      kit.box('steel', 0.42, gy - foot, 0.42, gx, (gy + foot) / 2, gz);
      kit.box('concrete', 1.2, 0.5, 1.2, gx, foot + 0.25, gz);
      kit.tilt('steel', 0.18, 0.18, 2.4, gx, gy - 1.1,
               gz + (gz < tz ? 1.0 : -1.0), gz < tz ? -0.7 : 0.7);
    }
    for (const gx of [-len * 0.22, len * 0.22]) {
      kit.box('steel', 0.5, 0.45, 9.4, gx, gy + 0.2, tz - 0.1);
    }
    kit.box('steel', len * 0.44 + 0.6, 0.42, 0.42, 0, gy + 0.2, tz - 4.6);
    kit.box('steel', len * 0.44 + 0.6, 0.42, 0.42, 0, gy + 0.2, tz + 4.4);
    /* Walkway along the gantry, above loading gauge. */
    kit.box('steel', len * 0.44, 0.1, 1.2, 0, gy + 0.45, tz + 1.6);
    handrail(kit, 'steelDetail', -len * 0.22, tz + 2.2, len * 0.22, tz + 2.2, gy + 0.5);
    handrail(kit, 'steelDetail', -len * 0.22, tz + 1.0, len * 0.22, tz + 1.0, gy + 0.5);
    for (const ax of [-len * 0.22 + 3.5, 0, len * 0.22 - 3.5]) {
      kit.tube('steel', new THREE.Vector3(ax, gy + 0.1, tz + 1.4),
               new THREE.Vector3(ax, gy + 0.1, tz), 0.13, 8);
      kit.tube('steel', new THREE.Vector3(ax, gy + 0.1, tz),
               new THREE.Vector3(ax, 5.0, tz), 0.13, 8);
      kit.tube('rust', new THREE.Vector3(ax, 5.0, tz),
               new THREE.Vector3(ax + 0.9, 4.3, tz), 0.10, 6);
      kit.sphere('steel', 0.16, ax, 5.0, tz, 8);
    }
    /* The header the arms hang off, and its run back to the plant. */
    kit.tube('steel', new THREE.Vector3(-len * 0.24, gy + 0.55, tz + 1.4),
             new THREE.Vector3(len * 0.24, gy + 0.55, tz + 1.4), 0.16, 8);
    kit.tube('steel', new THREE.Vector3(len * 0.22, gy + 0.55, tz + 1.4),
             new THREE.Vector3(len * 0.22, gy + 0.55, pz + pw + 2), 0.16, 8);
  }

  /* Dispatch hut, bollards, a stack of pallets — all on the platform, so all
   * back on the platform's level. */
  kit.base = platBase;
  const hx = -len / 2 + 4.2;
  kit.box('concrete', 3.4, 0.35, 3.0, hx, ph + 0.17, pz + pw - 1.6);
  kit.box('door', 3.0, 2.6, 2.6, hx, ph + 0.35 + 1.3, pz + pw - 1.6);
  kit.box('window', 1.5, 1.0, 0.06, hx, ph + 2.0, pz + pw - 2.92, Math.PI, 0.5, 0.3);
  kit.tilt('roof', 3.7, 0.16, 3.3, hx, ph + 3.05, pz + pw - 1.6, 0.06);
  for (let i = 0; i < 4; i++) {
    kit.cyl('hazard', 0.14, 0.16, 0.9, len / 2 - 3 - i * 2.4, ph, pz + pw - 0.5, 8);
  }
  for (let i = 0; i < 3 + Math.floor(rng() * 3); i++) {
    kit.box('rust', 1.1, 0.9, 1.1, len * 0.18 + i * 1.35, ph + 0.45,
            pz + pw - 1.4 + (rng() - 0.5), rng());
  }
  kit.base = plat;
  return ph;
}

/** Conduit, downpipes, vents and a junction box on a wall face. Small, and the
 *  first thing that makes a wall look built rather than extruded. */
function wallServices(kit, w, h, zFace, rng, faceSign = -1) {
  const n = 2 + Math.floor(rng() * 3);
  for (let i = 0; i < n; i++) {
    const x = (rng() - 0.5) * (w - 2);
    const top = 1.0 + rng() * (h - 2.5);
    kit.tube('steelDetail', new THREE.Vector3(x, 0.2, zFace + faceSign * 0.14),
             new THREE.Vector3(x, top, zFace + faceSign * 0.14), 0.055 + rng() * 0.04, 6);
    kit.box('steelDetail', 0.34, 0.5, 0.2, x, top + 0.3, zFace + faceSign * 0.14);
  }
  for (let i = 0; i < 2; i++) {
    const x = (rng() - 0.5) * (w - 3);
    kit.tube('steelDetail', new THREE.Vector3(x, 0.1, zFace + faceSign * 0.2),
             new THREE.Vector3(x, h - 0.3, zFace + faceSign * 0.2), 0.075, 6);
  }
  const vx = (rng() - 0.5) * (w - 4);
  kit.box('door', 1.5, 1.1, 0.16, vx, h - 1.6, zFace + faceSign * 0.1);
  kit.box('steelDetail', 1.7, 0.16, 0.4, vx, h - 1.0, zFace + faceSign * 0.2);
}

/** Window band with a real reveal.
 *
 *  The glass sits just proud of the wall and every piece of frame around it
 *  stands 0.24m in front of that, so the head, the mullions and the transoms
 *  all cast onto the glazing instead of being drawn on it. It was 0.10m before,
 *  which is under the width of the mullion itself: at that depth the sun never
 *  puts a frame shadow anywhere and the whole band reads as a decal — which is
 *  what "one flat blue tile stamped repeatedly" describes as much as the tiling
 *  does. Transoms per storey are new for the same reason.
 */
const REVEAL = 0.26;

function windowBand(kit, w, h, y, zFace, faceSign, {bay = 3.0, ou = 0, ov = 0} = {}) {
  const bays = Math.max(1, Math.round(w / bay));
  const floors = Math.max(1, Math.round(h / STOREY));
  const ry = faceSign < 0 ? Math.PI : 0;
  const zg = zFace + faceSign * 0.02, zf = zFace + faceSign * REVEAL;
  kit.panel('window', w, h, 0, y, zg, ry, bays / WBAYS, floors / WFLOORS, ou, ov);
  const t = 0.18;
  kit.box('trim', w + 0.28, t, 0.30, 0, y + h / 2 + t / 2, zf);
  kit.box('trim', w + 0.28, t, 0.30, 0, y - h / 2 - t / 2, zf);
  for (let i = 0; i <= bays; i++) {
    const x = -w / 2 + (w * i) / bays;
    kit.box('trim', 0.13, h, 0.34, x, y, zf);
  }
  for (let i = 1; i < floors; i++) {
    kit.box('trim', w, 0.14, 0.30, 0, y - h / 2 + (h * i) / floors, zf);
  }
  /* The cill projects further than the frame and is the one part of a window
   * that throws a shadow onto the wall below it. */
  kit.box('concrete', w + 0.6, 0.14, 0.44, 0, y - h / 2 - 0.26,
          zFace + faceSign * 0.24);
}

function windowBandX(kit, w, h, y, xFace, faceSign, {bay = 3.0, ou = 0, ov = 0} = {}) {
  const bays = Math.max(1, Math.round(w / bay));
  const floors = Math.max(1, Math.round(h / STOREY));
  const ry = faceSign > 0 ? Math.PI / 2 : -Math.PI / 2;
  const xg = xFace + faceSign * 0.02, xf = xFace + faceSign * REVEAL;
  kit.panel('window', w, h, xg, y, 0, ry, bays / WBAYS, floors / WFLOORS, ou, ov);
  const t = 0.18;
  kit.box('trim', 0.30, t, w + 0.28, xf, y + h / 2 + t / 2, 0);
  kit.box('trim', 0.30, t, w + 0.28, xf, y - h / 2 - t / 2, 0);
  for (let i = 0; i <= bays; i++) {
    const z = -w / 2 + (w * i) / bays;
    kit.box('trim', 0.34, h, 0.13, xf, y, z);
  }
  for (let i = 1; i < floors; i++) {
    kit.box('trim', 0.30, 0.14, w, xf, y - h / 2 + (h * i) / floors, 0);
  }
  kit.box('concrete', 0.44, 0.14, w + 0.6, xFace + faceSign * 0.24,
          y - h / 2 - 0.26, 0);
}

/** Roller shutter door in a steel surround. */
function rollerDoor(kit, w, h, x, zFace, faceSign) {
  kit.box('door', w, h, 0.14, x, h / 2, zFace + faceSign * 0.09);
  kit.box('trim', w + 0.5, 0.34, 0.36, x, h + 0.17, zFace + faceSign * 0.16);
  kit.box('trim', 0.26, h + 0.35, 0.36, x - w / 2 - 0.13, (h + 0.35) / 2,
          zFace + faceSign * 0.16);
  kit.box('trim', 0.26, h + 0.35, 0.36, x + w / 2 + 0.13, (h + 0.35) / 2,
          zFace + faceSign * 0.16);
  kit.box('concrete', w + 3.0, 0.14, 3.2, x, 0.09, zFace + faceSign * 1.7);
}

function personnelDoor(kit, x, zFace, faceSign) {
  kit.box('door', 1.05, DOOR_H, 0.12, x, DOOR_H / 2, zFace + faceSign * 0.08);
  kit.box('trim', 1.35, DOOR_H + 0.3, 0.2, x, (DOOR_H + 0.3) / 2, zFace + faceSign * 0.04);
  kit.box('trim', 1.9, 0.14, 1.0, x, DOOR_H + 0.55, zFace + faceSign * 0.5);
  kit.box('concrete', 1.9, 0.16, 1.2, x, 0.08, zFace + faceSign * 0.7);
}

/* ------------------------------------------------------------ earthworks -- */

/** The surface a facility stands on, in site-local metres.
 *
 *  Terrain grades one tilted design plane across the whole yard and cuts a
 *  level pad at each station on it; the site origin is a single point on that
 *  plane. A facility built flat from its origin is therefore buried at its
 *  uphill corner and hanging over its downhill one, and it was: measured before
 *  this existed, the ground crossed a station's datum by +1.38m / −1.23m over
 *  its 86×80 footprint, and the terminal's by +3.70m / −24.37m over its
 *  300×150 yard, with 39% of both footprints more than 0.25m under ground.
 *
 *  The rule is what a works does with a sloping site, in two clauses:
 *
 *    - Ground at or above the design level is FOLLOWED. A level pad cannot be
 *      wished over a hill, and the alternative — raising the whole facility to
 *      its highest corner — puts the platform a metre and a half above the
 *      track it serves. The design plane's gradient is capped at 1.8% per axis,
 *      so following it is a slope nobody can see and a guarantee nothing is
 *      buried.
 *    - Ground below it is FILLED, but only over the core. Past the core the
 *      fill dies away over `band` metres so the rim of a very large yard meets
 *      natural ground instead of standing on a four-storey retaining wall.
 *
 *  A station's core covers its whole apron, so a station stays a level platform
 *  on fill and nothing about it changes on flat ground. The terminal's core is
 *  the pad terrain actually grades for it, and its outer sixty metres conform.
 *
 *  The returned function carries `.gl`, the ungraded local ground, because the
 *  skirt has to reach below the real terrain rather than below the fill.
 *
 *  `APRON` is the make-up the finished surface stands proud of the earthworks,
 *  and it is not decoration. Where the ground is at or above the design level
 *  the rule above returns the ground EXACTLY, which puts the yard slab coplanar
 *  with the terrain mesh — and coplanar is z-fighting: the terminal's tarmac
 *  came out blotched with patches of hillside showing through it, most of the
 *  way across the yard. A third of a metre is also simply what a concrete apron
 *  on a formation is, and it clears the difference between `heightAt`, which is
 *  analytic, and the mesh, which is bilinear on a 3.6m grid inside the core and
 *  a 14m one outside it.
 */
const APRON = 0.35;

function makeEarthworks(ground, y0, ox, oz, {hx, hz, cz = 0, band = 30}) {
  const gl = (x, z) => {
    const g = ground(ox + x, oz + z);
    return Number.isFinite(g) ? g - y0 : 0;
  };
  const pad = (x, z) => {
    const g = gl(x, z);
    if (g >= 0) return g + APRON;
    const dx = Math.abs(x) - hx, dz = Math.abs(z - cz) - hz;
    const d = Math.hypot(Math.max(dx, 0), Math.max(dz, 0)) +
              Math.min(Math.max(dx, dz), 0);
    return g * smooth(0, band, d) + APRON;
  };
  pad.gl = gl;
  return pad;
}

/** A slab that follows the earthworks: a grid, normals from the height field,
 *  UVs in world metres so it tiles like every other surface. Pushed straight
 *  into the kit's material group, so a conforming yard costs no draw call. */
function slabGrid(kit, key, x0, x1, z0, z1, step, lift, {yAt = null, flip = false} = {}) {
  const pad = yAt || kit.pad;
  const nx = Math.max(1, Math.round((x1 - x0) / step));
  const nz = Math.max(1, Math.round((z1 - z0) / step));
  const vw = nx + 1, vh = nz + 1;
  const pos = new Float32Array(vw * vh * 3);
  const nor = new Float32Array(vw * vh * 3);
  const uv = new Float32Array(vw * vh * 2);
  const inv = 1 / kit.tileFor(key);
  const e = 1.2;
  for (let j = 0; j < vh; j++) {
    for (let i = 0; i < vw; i++) {
      const x = x0 + ((x1 - x0) * i) / nx, z = z0 + ((z1 - z0) * j) / nz;
      const k = j * vw + i;
      pos[k * 3] = x; pos[k * 3 + 1] = pad(x, z) + lift; pos[k * 3 + 2] = z;
      const gx = -(pad(x + e, z) - pad(x - e, z)) / (2 * e);
      const gz = -(pad(x, z + e) - pad(x, z - e)) / (2 * e);
      const len = Math.hypot(gx, 1, gz) || 1;
      const s = flip ? -1 : 1;
      nor[k * 3] = (gx / len) * s; nor[k * 3 + 1] = s / len; nor[k * 3 + 2] = (gz / len) * s;
      uv[k * 2] = x * inv; uv[k * 2 + 1] = z * inv;
    }
  }
  const idx = new Uint32Array(nx * nz * 6);
  let o = 0;
  for (let j = 0; j < nz; j++) {
    for (let i = 0; i < nx; i++) {
      const a = j * vw + i, b = a + 1, c = a + vw, d = c + 1;
      if (flip) { idx[o++] = a; idx[o++] = b; idx[o++] = c;
                  idx[o++] = b; idx[o++] = d; idx[o++] = c; }
      else      { idx[o++] = a; idx[o++] = c; idx[o++] = b;
                  idx[o++] = b; idx[o++] = c; idx[o++] = d; }
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  return kit.raw(key, g);
}

/** The retaining edge. One quad per step around the slab, from the earthworks
 *  surface down past the natural ground under that very point — which is the
 *  whole reason the skirt is a strip and not a box: a box has one bottom, and a
 *  yard on a grade needs its downhill edge four metres deeper than its uphill
 *  one to meet the ground on both. */
function slabSkirt(kit, key, x0, x1, z0, z1, step, lift, depth = 2.6) {
  const pad = kit.pad;
  const ring = [];
  const push = (x, z) => ring.push([x, z]);
  const nx = Math.max(1, Math.round((x1 - x0) / step));
  const nz = Math.max(1, Math.round((z1 - z0) / step));
  for (let i = 0; i < nx; i++) push(x0 + ((x1 - x0) * i) / nx, z0);
  for (let j = 0; j < nz; j++) push(x1, z0 + ((z1 - z0) * j) / nz);
  for (let i = nx; i > 0; i--) push(x0 + ((x1 - x0) * i) / nx, z1);
  for (let j = nz; j > 0; j--) push(x0, z0 + ((z1 - z0) * j) / nz);
  const n = ring.length;
  const pos = new Float32Array(n * 2 * 3);
  const nor = new Float32Array(n * 2 * 3);
  const uv = new Float32Array(n * 2 * 2);
  const inv = 1 / kit.tileFor(key);
  let run = 0;
  for (let i = 0; i < n; i++) {
    const [x, z] = ring[i];
    const [px, pz] = ring[(i + n - 1) % n];
    const [qx, qz] = ring[(i + 1) % n];
    if (i) run += Math.hypot(x - px, z - pz);
    /* Outward normal from the ring's own direction, so a corner reads as a
     * corner rather than as two faces lit the same way. */
    const tx = qx - px, tz = qz - pz;
    const tl = Math.hypot(tx, tz) || 1;
    const ox2 = tz / tl, oz2 = -tx / tl;
    const top = pad(x, z) + lift;
    const bot = Math.min(top, pad.gl ? pad.gl(x, z) : top) - depth;
    for (const [s, y] of [[0, top], [1, bot]]) {
      const k = i * 2 + s;
      pos[k * 3] = x; pos[k * 3 + 1] = y; pos[k * 3 + 2] = z;
      nor[k * 3] = ox2; nor[k * 3 + 1] = 0; nor[k * 3 + 2] = oz2;
      uv[k * 2] = run * inv; uv[k * 2 + 1] = y * inv;
    }
  }
  const idx = new Uint32Array(n * 6);
  let o = 0;
  for (let i = 0; i < n; i++) {
    const a = i * 2, b = a + 1;
    const c = ((i + 1) % n) * 2, d = c + 1;
    idx[o++] = a; idx[o++] = b; idx[o++] = c;
    idx[o++] = c; idx[o++] = b; idx[o++] = d;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  return kit.raw(key, g);
}

/** The platform every site stands on, replacing the flat six-metre box this
 *  used to be. Concrete to the edge with a retaining skirt, tarmac inside the
 *  kerb — a yard whose every horizontal surface is the same pale slab as its
 *  plinths has no ground plane and everything on it reads as floating. */
function podium(kit, w, d, z, {step = 3.6} = {}) {
  const x0 = -w / 2, x1 = w / 2, z0 = z - d / 2, z1 = z + d / 2;
  slabGrid(kit, 'concrete', x0, x1, z0, z1, step, -0.10);
  slabSkirt(kit, 'concrete', x0, x1, z0, z1, step, -0.10, 3.2);
  slabGrid(kit, 'roof', x0 + 1.3, x1 - 1.3, z0 + 1.3, z1 - 1.3, step, 0);
}

/* ------------------------------------------------------------- archetypes -- */

/** Brick process hall: pilastered brick, banded glazing, flat roof behind a
 *  parapet, roof plant, a brick stack, an annex and two product tanks. */
function brickHall(kit, rng, scale) {
  const w = (30 + rng() * 10) * scale;
  const d = (20 + rng() * 6) * scale;
  const storeys = 2 + (rng() > 0.55 ? 1 : 0);
  const h = storeys * STOREY + 1.0;
  const nz = -12, sz = nz + d;
  const cz = (nz + sz) / 2;

  /* Founded on the ground under the hall's own footprint, not on the site
   * origin. The buried skirt below is the footing that takes up whatever slope
   * is left inside that footprint. Every volume from here down does the same:
   * one `stand` per thing that is rigid, so a stack, an annex and a bund are
   * three separate answers to "what is the ground here" instead of one. */
  kit.stand(0, cz, w / 2 + 0.6, d / 2 + 0.6);
  kit.box('concrete', w + 1.2, 3.4, d + 1.2, 0, -1.5, cz);        // buried skirt
  kit.box('concrete', w + 0.9, 1.0, d + 0.9, 0, 0.5, cz);         // plinth
  kit.box('brick', w, h, d, 0, 1.0 + h / 2, cz);

  /* Pilasters at 4.5m: the whole reason a brick wall reads as brick. */
  const bays = Math.max(4, Math.round(w / 4.5));
  for (let i = 0; i <= bays; i++) {
    const x = -w / 2 + (w * i) / bays;
    kit.box('brick', 0.85, h - 0.4, 0.42, x, 1.0 + (h - 0.4) / 2, nz - 0.21);
    kit.box('brick', 0.85, h - 0.4, 0.42, x, 1.0 + (h - 0.4) / 2, sz + 0.21);
  }
  kit.box('brick', w + 1.1, 0.5, d + 1.1, 0, 1.0 + h + 0.05, cz);   // cornice
  kit.box('concrete', w + 1.3, 0.18, d + 1.3, 0, 1.0 + h + 0.36, cz);
  const roofY = 1.0 + h + 0.45;
  kit.box('roof', w - 0.4, 0.3, d - 0.4, 0, roofY, cz);
  for (const [pw, pd, px, pz] of [[w + 0.6, 0.5, 0, nz - 0.05], [w + 0.6, 0.5, 0, sz + 0.05],
                                  [0.5, d + 0.6, -w / 2 - 0.05, cz],
                                  [0.5, d + 0.6, w / 2 + 0.05, cz]]) {
    kit.box('brick', pw, 1.0, pd, px, roofY + 0.5, pz);
    kit.box('concrete', pw + 0.2, 0.14, pd + 0.2, px, roofY + 1.03, pz);
  }

  /* Glazing: a band per upper storey on both long faces, and gable windows. */
  for (let s = 1; s < storeys; s++) {
    const y = 1.0 + s * STOREY + STOREY * 0.55;
    windowBand(kit, w - 4.5, 2.15, y, nz, -1, {ou: s * 0.31, ov: s * 0.5});
    windowBand(kit, w - 4.5, 2.15, y, sz, 1, {ou: s * 0.63 + 0.34, ov: s * 0.5 + 0.5});
  }
  windowBandX(kit, d - 5, 2.15, 1.0 + STOREY * 1.55, w / 2, 1);
  /* Ground floor: two roller doors on the track face and a door. */
  rollerDoor(kit, 4.6, 4.4, -w * 0.24, nz, -1);
  rollerDoor(kit, 4.6, 4.4, w * 0.16, nz, -1);
  personnelDoor(kit, w * 0.40, nz, -1);
  windowBand(kit, w * 0.20, 1.9, 2.6, nz, -1, {bay: 2.6});
  wallServices(kit, w, h, sz, rng, 1);
  wallServices(kit, w * 0.5, h, nz, rng, -1);
  roofPlant(kit, 0, cz, w - 3, d - 3, roofY + 0.15, rng);

  /* Brick stack with steel bands — the tallest thing on a process site. Not
   * every hall has one, and that is the point: this archetype is drawn four
   * times over sixteen benches, so which optional volumes a facility owns is
   * doing as much work here as its dimensions are. */
  const hasStack = rng() > 0.28;
  const stackH = hasStack ? 16 + rng() * 9 : 0;
  if (hasStack) {
    const sx = -w / 2 - 3.4, szz = sz - 4;
    kit.foot('concrete', sx, szz, 1.8, 1.8, {skirt: 1.0});
    kit.box('concrete', 3.6, 0.9, 3.6, sx, 0.45, szz);
    kit.cyl('brick', 1.05, 1.65, stackH, sx, 0.9, szz, 14);
    for (let i = 1; i <= 4; i++) {
      kit.torus('steel', 1.62 - i * 0.14, 0.07, sx, 0.9 + (stackH * i) / 5, szz, 14);
    }
    kit.cyl('concrete', 1.28, 1.15, 0.5, sx, 0.9 + stackH, szz, 14);
    ladder(kit, 'steelDetail', sx + 1.5, szz, 2.0, 0.9 + stackH, 1);
  } else {
    /* A riveted water tower on legs instead, so the silhouette still has
     * something above the parapet. */
    const tx = -w / 2 - 4.0, tzz = sz - 5, th2 = 13 + rng() * 4;
    kit.stand(tx, tzz, 2.5, 2.5);
    /* Each leg is drawn from its own ground up to the one tank it carries, so
     * a water tower on a cross-fall stands on four different lengths of steel
     * instead of on one leg and three gaps. */
    for (const ox of [-2.2, 2.2]) {
      for (const oz of [-2.2, 2.2]) {
        const lo = Math.min(kit.base, kit.span(tx + ox, tzz + oz, 0.4, 0.4).lo) - 0.3;
        const hh = th2 + (kit.base - lo);
        const b = kit.base; kit.base = lo;
        kit.box('steel', 0.30, hh, 0.30, tx + ox, hh / 2, tzz + oz);
        kit.box('concrete', 0.9, 0.5, 0.9, tx + ox, 0.25, tzz + oz);
        kit.base = b;
      }
    }
    for (let i = 1; i <= 3; i++) {
      kit.box('steel', 5.0, 0.14, 0.14, tx, (th2 * i) / 4, tzz - 2.2);
      kit.box('steel', 5.0, 0.14, 0.14, tx, (th2 * i) / 4, tzz + 2.2);
    }
    kit.cyl('rust', 3.0, 3.0, 5.2, tx, th2, tzz, 16);
    kit.cone('steel', 3.15, 1.5, tx, th2 + 5.2, tzz, 16);
    ladder(kit, 'steelDetail', tx + 3.2, tzz, 1.0, th2 + 5.4);
  }

  /* Single-storey annex on the east flank. */
  const aw = 11 + rng() * 3, ad = 12;
  const ax = w / 2 + aw / 2 + 0.4, az = cz + 2;
  kit.stand(ax, az, aw / 2 + 0.4, ad / 2 + 0.4);
  kit.box('concrete', aw + 0.6, 2.6, ad + 0.6, ax, -0.6, az);
  kit.box('brick', aw, STOREY + 0.6, ad, ax, (STOREY + 0.6) / 2 + 0.3, az);
  kit.box('brick', aw + 0.7, 0.4, ad + 0.7, ax, STOREY + 1.05, az);
  kit.box('roof', aw + 0.3, 0.26, ad + 0.3, ax, STOREY + 1.3, az);
  kit.panel('window', aw - 3, 1.5, ax, 2.4, az - ad / 2 - 0.03, Math.PI,
            Math.round((aw - 3) / 3) / WBAYS, 1 / WFLOORS);
  kit.box('trim', aw - 2.6, 0.2, 0.3, ax, 3.2, az - ad / 2 - 0.12);
  personnelDoor(kit, ax - aw * 0.32, az - ad / 2, -1);
  roofPlant(kit, ax, az, aw - 2, ad - 2, STOREY + 1.4, rng);

  /* Two product tanks in a bund, fed off a rack from the hall. */
  const tr = 4.4 + rng() * 1.6, th = 9 + rng() * 3;
  const bx = -w / 2 - 12, bz = cz + 6;
  const pair = rng() > 0.40;
  /* A bund is a watertight concrete tray thirty metres across; on a grade it is
   * cast level and the ground is made up to it, which is what the footing does.
   * Its 1.1m wall is nowhere near deep enough to be that footing itself. */
  kit.foot('concrete', bx - tr - 1, bz, tr * 2 + 5, tr + 3, {skirt: 0.8, inset: 0.4});
  kit.box('concrete', 2 * (tr * 2 + 5), 1.1, tr * 2 + 6, bx - tr - 1, 0.55, bz);
  kit.box('concrete', 2 * (tr * 2 + 5) - 1.6, 0.9, tr * 2 + 4.4, bx - tr - 1, 0.6, bz);
  const t1 = storageTank(kit, bx - tr * 1.15, bz, tr, th, rng, {stairs: true});
  const t2 = pair
    ? storageTank(kit, bx - tr * 3.6, bz, tr * 0.85, th * 0.9, rng,
                  {stairs: false, weathered: true})
    : (bulletTank(kit, bx - tr * 3.4, bz, 2.0, tr * 3.4, Math.PI / 2), 0);
  pipeRack(kit, bx - tr * 1.15, bz - tr - 1.5, -w / 2 - 1, nz + 4, 5.2, 4, rng);
  pipeRack(kit, -w * 0.1, nz - 2.5, -w * 0.1, -DOCK_OFFSET + 8.5, 5.6, 3, rng);
  floodMast(kit, w / 2 + 6, nz - 6, 11);
  floodMast(kit, -w / 2 - 8, nz - 4, 11);
  return Math.max(1.0 + h + 1.1, hasStack ? 0.9 + stackH + 0.5 : 0, t1, t2);
}

/** Steel-framed plant: a tall clad hall with a monitor roof, an external stair
 *  tower, roof extract and a bunded tank group. */
function steelPlant(kit, rng, scale) {
  const w = (28 + rng() * 9) * scale;
  const d = (19 + rng() * 6) * scale;
  const h = 11.5 + rng() * 4.5;
  const nz = -12, sz = nz + d, cz = (nz + sz) / 2;

  kit.stand(0, cz, w / 2 + 0.7, d / 2 + 0.7);
  kit.box('concrete', w + 1.4, 3.6, d + 1.4, 0, -1.6, cz);
  kit.box('concrete', w + 1.0, 1.3, d + 1.0, 0, 0.4, cz);
  kit.box('clad', w, h, d, 0, 1.0 + h / 2, cz);
  /* Sheeting rails and a frame line at every portal — clad walls read wrong
   * without the horizontal banding. */
  const frames = Math.max(4, Math.round(w / 5.5));
  for (let i = 0; i <= frames; i++) {
    const x = -w / 2 + (w * i) / frames;
    kit.box('trim', 0.26, h, 0.14, x, 1.0 + h / 2, nz - 0.07);
    kit.box('trim', 0.26, h, 0.14, x, 1.0 + h / 2, sz + 0.07);
  }
  for (let s = 1; s * 3.5 < h; s++) {
    kit.box('trim', w + 0.2, 0.12, 0.09, 0, 1.0 + s * 3.5, nz - 0.06);
    kit.box('trim', w + 0.2, 0.12, 0.09, 0, 1.0 + s * 3.5, sz + 0.06);
  }
  /* Gable roof with a raised monitor and a ridge vent. */
  const pitch = 0.22;
  const half = d / 2 + 0.7;
  const ridge = 1.0 + h + Math.tan(pitch) * half;
  for (const s of [-1, 1]) {
    kit.tilt('roof', w + 1.2, 0.28, half / Math.cos(pitch), 0,
             1.0 + h + Math.tan(pitch) * half / 2, cz + s * half / 2, s * pitch);
  }
  kit.box('clad', w + 1.2, Math.tan(pitch) * half, 0.5, 0,
          1.0 + h + Math.tan(pitch) * half / 2, nz - 0.35);
  kit.box('clad', w + 1.2, Math.tan(pitch) * half, 0.5, 0,
          1.0 + h + Math.tan(pitch) * half / 2, sz + 0.35);
  kit.box('steel', w * 0.6, 1.5, 3.4, 0, ridge + 0.6, cz);
  kit.panel('window', w * 0.58, 0.9, 0, ridge + 0.8, cz - 1.72, Math.PI,
            Math.round(w * 0.58 / 3) / WBAYS, 1 / WFLOORS);
  kit.panel('window', w * 0.58, 0.9, 0, ridge + 0.8, cz + 1.72, 0,
            Math.round(w * 0.58 / 3) / WBAYS, 1 / WFLOORS);
  kit.tilt('roof', w * 0.62, 0.2, 4.2, 0, ridge + 1.45, cz, 0);
  for (let i = 0; i < 3; i++) {
    const px = (i - 1) * w * 0.26;
    kit.cyl('steel', 0.62, 0.72, 1.0, px, ridge + 1.55, cz, 12);
    kit.cone('steel', 0.78, 0.55, px, ridge + 2.55, cz, 12);
  }

  /* Clerestory glazing high on both long faces, plus a low band. */
  windowBand(kit, w - 5, 2.4, 1.0 + h - 2.6, nz, -1, {ou: 0.17});
  windowBand(kit, w - 5, 2.4, 1.0 + h - 2.6, sz, 1, {ou: 0.58, ov: 0.5});
  windowBand(kit, w * 0.34, 1.9, 4.4, sz, 1, {bay: 2.8});
  rollerDoor(kit, 6.2, 5.4, -w * 0.20, nz, -1);
  personnelDoor(kit, w * 0.30, nz, -1);
  wallServices(kit, w, h, sz, rng, 1);

  /* External stair tower up the west gable: four columns, half-landings at
   * each end, and a flight between them. The half-landing switchback is what
   * makes a tower read as a stair rather than as a ladder. */
  const tx = -w / 2 - 2.2;
  const tz0 = cz - 3.4, tz1 = cz + 3.4;
  const levels = Math.max(3, Math.round((h - 1.0) / 3.2));
  for (let i = 0; i <= levels; i++) {
    const y = 1.0 + (h - 1.0) * (i / levels);
    const near = (i % 2) === 0;
    const lz = near ? tz0 + 1.0 : tz1 - 1.0;
    kit.box('steel', 3.2, 0.12, 2.1, tx, y, lz);
    handrail(kit, 'steelDetail', tx - 1.5, near ? tz0 : tz1, tx + 1.5,
             near ? tz0 : tz1, y);
    handrail(kit, 'steelDetail', tx - 1.5, lz - 1.0, tx - 1.5, lz + 1.0, y);
    if (i < levels) {
      const y2 = 1.0 + (h - 1.0) * ((i + 1) / levels);
      flight(kit, 'steel', tx, near ? tz0 + 2.0 : tz1 - 2.0, y,
             near ? tz1 - 2.0 : tz0 + 2.0, y2, 1.2);
    }
  }
  for (const cxp of [tx - 1.7, tx + 1.7]) {
    for (const czp of [tz0, tz1]) {
      kit.box('steel', 0.26, h + 0.4, 0.26, cxp, (h + 0.4) / 2, czp);
      for (let b = 1; b * 4 < h; b++) {
        kit.box('steel', 0.16, 0.16, tz1 - tz0, cxp, 1.0 + b * 4, cz);
      }
    }
  }
  kit.box('steel', 3.4, 0.12, 2.4, tx, 1.0 + h + 0.1, cz);
  flight(kit, 'steel', tx + 1.0, cz + 1.2, 1.0 + h + 0.1, cz - 1.2, ridge - 0.1, 1.0);
  handrail(kit, 'steelDetail', -w / 2, cz - 0.5, w / 2, cz - 0.5, ridge + 0.2);

  /* Tank group and the rack that feeds the gantry. */
  const tr = 4.6 + rng() * 2.2, th = 11 + rng() * 4;
  const bx = w / 2 + tr + 8, bz = cz - 1;
  const group = 2 + Math.floor(rng() * 2);
  const span = tr * (1.4 + group * 1.9);
  const hallBase = kit.base;
  kit.foot('concrete', bx + tr, bz, span / 2, tr + 3, {skirt: 0.8, inset: 0.4});
  kit.box('concrete', span, 1.2, tr * 2 + 6, bx + tr, 0.6, bz);
  kit.box('concrete', span - 1.8, 1.0, tr * 2 + 4.4, bx + tr, 0.65, bz);
  let top = 0;
  for (let i = 0; i < group; i++) {
    const r = tr * (i === 1 ? 1 : 0.82);
    top = Math.max(top, storageTank(kit, bx + i * tr * 2.2, bz + (i === 1 ? -0.4 : 0.6),
                                    r, th * (i === 1 ? 1 : 0.85), rng,
                                    {stairs: i === 1, weathered: i === 2}));
  }
  pipeRack(kit, bx, bz - tr - 2.5, bx * 0.15, nz - 3, 6.0, 5, rng);
  pipeRack(kit, bx * 0.15, nz - 3, bx * 0.15, -DOCK_OFFSET + 8.5, 6.0, 4, rng);
  /* A vent stack: thin, tall, and the reason the silhouette has a spike.
   * A fractionating column instead on one plant in three — the same spike,
   * eight times the girth, and it changes which building this reads as from
   * the wide camera. */
  kit.base = hallBase;
  if (rng() > 0.66) {
    top = Math.max(top, processColumn(kit, w / 2 + 5.0, sz - 3,
                                      2.0 + rng() * 0.8, 20 + rng() * 8, rng));
  }
  const vh = 16 + rng() * 7;
  kit.stand(w / 2 + 3.4, sz - 3, 1.4, 1.4);
  kit.cyl('steel', 0.5, 0.62, vh, w / 2 + 3.2, 1.0, sz - 3, 12);
  kit.cyl('rust', 0.56, 0.5, 0.7, w / 2 + 3.2, 1.0 + vh, sz - 3, 12);
  for (let i = 1; i <= 3; i++) {
    kit.torus('steelDetail', 0.7, 0.05, w / 2 + 3.2, 1.0 + (vh * i) / 4, sz - 3, 10);
  }
  ladder(kit, 'steelDetail', w / 2 + 4.0, sz - 3, 1.2, 1.0 + vh);
  floodMast(kit, -w / 2 - 7, nz - 5, 12);
  floodMast(kit, w / 2 + 4, nz - 7, 12);
  return Math.max(ridge + 2.6, top, 1.0 + vh + 0.8);
}

/** Tank farm: the tanks are the building. A small brick control room, a
 *  pumphouse under an open frame, bullets, and a manifold. */
function tankFarm(kit, rng, scale) {
  const nz = -12;
  const tr = 5.0 + rng() * 2.0;
  const th = 11 + rng() * 5;
  const count = 4;
  const bundW = count * tr * 2.35 + 4;
  const bundD = tr * 2 + 9;
  const bz = nz + bundD / 2 + 1;
  kit.stand(0, bz, bundW / 2 + 1.5, bundD / 2 + 1.5);
  kit.box('concrete', bundW + 3, 3.4, bundD + 3, 0, -1.5, bz);
  kit.box('concrete', bundW, 1.35, bundD, 0, 0.67, bz);
  kit.box('concrete', bundW - 2.2, 1.15, bundD - 2.2, 0, 0.72, bz);
  let top = 0;
  for (let i = 0; i < count; i++) {
    const x = -bundW / 2 + tr * 1.5 + i * tr * 2.35;
    const r = tr * (0.82 + 0.24 * ((i * 7919) % 3) / 2) * scale * 0.98;
    const hh = th * (0.86 + 0.2 * ((i * 104729) % 5) / 4);
    top = Math.max(top, storageTank(kit, x, bz + (i % 2 ? -1.2 : 1.2), r, hh, rng,
                                    {stairs: i === 1 || i === 2, weathered: i === 3}));
    /* Every tank ties into the manifold along the bund's north wall. */
    kit.tube('steel', new THREE.Vector3(x, 1.5, bz - tr - 1.0),
             new THREE.Vector3(x, 1.5, nz + 1.5), 0.18, 8);
    kit.tube('steel', new THREE.Vector3(x, 1.5, bz - tr - 1.0),
             new THREE.Vector3(x, 1.5, bz + (i % 2 ? -1.2 : 1.2) - r), 0.18, 8);
  }
  kit.tube('steel', new THREE.Vector3(-bundW / 2 + 1, 1.5, nz + 1.5),
           new THREE.Vector3(bundW / 2 - 1, 1.5, nz + 1.5), 0.24, 10);
  kit.tube('steel', new THREE.Vector3(-bundW / 2 + 1, 2.2, nz + 2.4),
           new THREE.Vector3(bundW / 2 - 1, 2.2, nz + 2.4), 0.18, 10);

  /* Control room: brick, one storey, banded windows facing the yard. */
  const cw = 12, cd = 8.5;
  const cx = -bundW / 2 - cw / 2 - 5, ccz = nz + 5;
  kit.stand(cx, ccz, cw / 2 + 0.6, cd / 2 + 0.6);
  kit.box('concrete', cw + 1.2, 3.0, cd + 1.2, cx, -1.0, ccz);
  kit.box('concrete', cw + 0.8, 0.7, cd + 0.8, cx, 0.35, ccz);
  kit.box('brick', cw, STOREY + 0.4, cd, cx, 0.7 + (STOREY + 0.4) / 2, ccz);
  kit.box('brick', cw + 0.8, 0.36, cd + 0.8, cx, 0.7 + STOREY + 0.58, ccz);
  kit.box('roof', cw + 0.4, 0.3, cd + 0.4, cx, 0.7 + STOREY + 0.85, ccz);
  kit.panel('window', cw - 3.2, 1.6, cx, 2.5, ccz - cd / 2 - 0.03, Math.PI,
            Math.round((cw - 3.2) / 3) / WBAYS, 1 / WFLOORS);
  kit.box('trim', cw - 2.8, 0.2, 0.32, cx, 3.35, ccz - cd / 2 - 0.14);
  kit.box('trim', cw - 2.8, 0.2, 0.32, cx, 1.68, ccz - cd / 2 - 0.14);
  personnelDoor(kit, cx + cw * 0.34, ccz - cd / 2, -1);
  wallServices(kit, cw, STOREY, ccz + cd / 2, rng, 1);
  roofPlant(kit, cx, ccz, cw - 3, cd - 3, 0.7 + STOREY + 1.0, rng);

  /* Pumphouse: an open steel frame over pumps and their suction lines. */
  const px = bundW / 2 + 8, pz = nz + 9;
  kit.foot('concrete', px, pz, 5.5, 4.0, {skirt: 0.7});
  kit.box('concrete', 11, 0.5, 8, px, 0.25, pz);
  for (const gx of [px - 4.6, px + 4.6]) {
    for (const gz of [pz - 3.2, pz + 3.2]) {
      kit.box('steel', 0.28, 5.2, 0.28, gx, 2.6, gz);
    }
    kit.box('steel', 0.3, 0.34, 7.0, gx, 5.2, pz);
  }
  kit.box('steel', 10.0, 0.34, 0.3, px, 5.2, pz - 3.2);
  kit.box('steel', 10.0, 0.34, 0.3, px, 5.2, pz + 3.2);
  kit.tilt('roof', 11.2, 0.2, 8.0, px, 5.5, pz, 0.08);
  for (let i = 0; i < 3; i++) {
    const mx = px - 3.4 + i * 3.4;
    kit.box('concrete', 2.4, 0.6, 1.6, mx, 0.75, pz);
    kit.cyl('steel', 0.5, 0.5, 1.3, mx - 0.5, 1.05, pz, 12);
    kit.box('steel', 1.5, 0.9, 0.9, mx + 0.6, 1.5, pz);
    kit.tube('steel', new THREE.Vector3(mx - 0.5, 2.35, pz),
             new THREE.Vector3(mx - 0.5, 3.4, pz), 0.16, 8);
    kit.tube('steel', new THREE.Vector3(mx - 0.5, 3.4, pz),
             new THREE.Vector3(mx - 0.5, 3.4, nz + 2.4), 0.16, 8);
  }
  bulletTank(kit, px + 1, pz + 11, 1.9, 12, Math.PI / 2);
  bulletTank(kit, px + 1, pz + 16, 1.9, 12, Math.PI / 2);

  pipeRack(kit, 0, nz + 1.0, 0, -DOCK_OFFSET + 8.5, 5.6, 4, rng);
  floodMast(kit, -bundW / 2 - 3, nz - 4, 13);
  floodMast(kit, bundW / 2 + 3, nz - 4, 13);
  floodMast(kit, 0, bz + bundD / 2 + 4, 13);
  return Math.max(top, 0.7 + STOREY + 1.6);
}

/** A long corrugated shed with a brick office on the end: the small
 *  instruments, and the archetype that keeps the site from being all towers. */
function shedYard(kit, rng, scale) {
  const w = (34 + rng() * 10) * scale;
  const d = 14 + rng() * 4;
  const h = 6.6 + rng() * 1.6;
  const nz = -12, sz = nz + d, cz = (nz + sz) / 2;
  kit.stand(0, cz, w / 2 + 0.7, d / 2 + 0.7);
  kit.box('concrete', w + 1.4, 3.2, d + 1.4, 0, -1.5, cz);
  kit.box('concrete', w + 1.0, 0.75, d + 1.0, 0, 0.35, cz);
  kit.box('clad', w, h, d, 0, 0.7 + h / 2, cz);
  const frames = Math.max(5, Math.round(w / 5));
  for (let i = 0; i <= frames; i++) {
    const x = -w / 2 + (w * i) / frames;
    kit.box('trim', 0.24, h, 0.13, x, 0.7 + h / 2, nz - 0.07);
    kit.box('trim', 0.24, h, 0.13, x, 0.7 + h / 2, sz + 0.07);
  }
  const pitch = 0.26, half = d / 2 + 0.8;
  const ridge = 0.7 + h + Math.tan(pitch) * half;
  for (const s of [-1, 1]) {
    kit.tilt('roof', w + 1.3, 0.26, half / Math.cos(pitch), 0,
             0.7 + h + Math.tan(pitch) * half / 2, cz + s * half / 2, s * pitch);
  }
  kit.box('clad', w + 1.3, Math.tan(pitch) * half, 0.45, 0,
          0.7 + h + Math.tan(pitch) * half / 2, nz - 0.3);
  kit.box('clad', w + 1.3, Math.tan(pitch) * half, 0.45, 0,
          0.7 + h + Math.tan(pitch) * half / 2, sz + 0.3);
  kit.box('steel', w + 1.3, 0.3, 0.6, 0, ridge + 0.18, cz);
  kit.box('steel', w + 1.4, 0.2, 0.16, 0, 0.7 + h + 0.1, nz - 0.45);   // gutter
  kit.box('steel', w + 1.4, 0.2, 0.16, 0, 0.7 + h + 0.1, sz + 0.45);
  for (const x of [-w * 0.42, 0, w * 0.42]) {
    kit.tube('steelDetail', new THREE.Vector3(x, 0.1, sz + 0.5),
             new THREE.Vector3(x, 0.7 + h + 0.1, sz + 0.5), 0.08, 6);
  }

  /* Two to four doors, and where they start varies: a run of evenly spaced
   * identical doors down the middle of every shed is the same repeat as the
   * window band, one storey down. */
  const doors = 2 + Math.floor(rng() * 3);
  const dStart = -w * (0.34 - rng() * 0.10);
  for (let i = 0; i < doors; i++) {
    rollerDoor(kit, 4.4, 4.6, dStart + i * w * (0.62 / doors), nz, -1);
  }
  personnelDoor(kit, w * 0.42, nz, -1);
  windowBand(kit, w * 0.5, 1.5, h - 0.9, nz, -1, {bay: 2.6, ou: 0.24});
  windowBand(kit, w * 0.7, 1.5, h - 0.9, sz, 1, {bay: 2.6, ou: 0.66, ov: 0.5});
  /* A canopy on the loading side, on raking props — on about two sheds in
   * three, and only over the part of the face with doors under it. */
  if (rng() > 0.34) {
    const cwid = w * (0.55 + rng() * 0.2);
    kit.tilt('roof', cwid, 0.16, 4.6, 0, 5.35, nz - 2.2, 0.14);
    const props = Math.max(3, Math.round(cwid / 7));
    for (let i = 0; i <= props; i++) {
      const x = -cwid / 2 + cwid * (i / props);
      kit.box('steel', 0.2, 4.8, 0.2, x, 2.4, nz - 4.2);
      kit.tilt('steel', 0.15, 0.15, 3.4, x, 4.1, nz - 2.2, 0.75);
    }
    kit.box('steel', cwid, 0.26, 0.26, 0, 4.85, nz - 4.2);
  }

  /* Brick office on the east end. */
  const ow = 12, od = d + 2;
  const ox = w / 2 + ow / 2 + 0.5;
  kit.stand(ox, cz, ow / 2 + 0.5, od / 2 + 0.5);
  kit.box('concrete', ow + 1.0, 3.0, od + 1.0, ox, -1.1, cz);
  kit.box('concrete', ow + 0.7, 0.9, od + 0.7, ox, 0.45, cz);
  kit.box('brick', ow, STOREY * 2, od, ox, 0.9 + STOREY, cz);
  kit.box('brick', ow + 0.8, 0.4, od + 0.8, ox, 0.9 + STOREY * 2 + 0.2, cz);
  kit.box('roof', ow + 0.4, 0.3, od + 0.4, ox, 0.9 + STOREY * 2 + 0.5, cz);
  windowBandX(kit, od - 3.5, 1.7, 0.9 + STOREY * 1.5, ox + ow / 2, 1);
  kit.panel('window', ow - 3, 1.7, ox, 0.9 + STOREY * 1.5, nz - 0.03, Math.PI,
            Math.round((ow - 3) / 3) / WBAYS, 1 / WFLOORS, 0.33, 0);
  kit.panel('window', ow - 3, 1.7, ox, 0.9 + STOREY * 0.5, nz - 0.03, Math.PI,
            Math.round((ow - 3) / 3) / WBAYS, 1 / WFLOORS, 0, 0.5);
  kit.box('trim', ow - 2.6, 0.22, 0.3, ox, 0.9 + STOREY * 1.5 + 0.95, nz - 0.14);
  kit.box('trim', ow - 2.6, 0.22, 0.3, ox, 0.9 + STOREY * 0.5 + 0.95, nz - 0.14);
  personnelDoor(kit, ox - ow * 0.3, nz, -1);
  wallServices(kit, ow, STOREY * 2, cz + od / 2, rng, 1);
  roofPlant(kit, ox, cz, ow - 3, od - 4, 0.9 + STOREY * 2 + 0.65, rng);

  /* Drum racks and a day tank — the yard clutter that sells the scale. */
  const dx = -w / 2 - 7;
  kit.stand(dx, cz + 2, 2.0, 2.0);
  for (let r = 0; r < 2; r++) {
    for (let i = 0; i < 5; i++) {
      kit.cyl('rust', 0.32, 0.32, 0.9, dx + (i % 3) * 0.8, 0.3 + r * 1.0,
              cz + 2 + Math.floor(i / 3) * 0.85, 10);
    }
  }
  kit.box('steel', 3.6, 0.14, 2.6, dx + 0.8, 0.25, cz + 2.4);
  kit.foot('concrete', dx - 1, cz - 5, 3.9, 3.9, {skirt: 0.6});
  const tk = storageTank(kit, dx - 1, cz - 5, 3.2, 7.5, rng, {stairs: false});
  pipeRack(kit, dx - 1, cz - 8.5, dx - 1, -DOCK_OFFSET + 8.5, 4.8, 3, rng);
  floodMast(kit, w / 2 + 8, nz - 6, 10);
  floodMast(kit, -w / 2 - 6, nz - 6, 10);
  return Math.max(ridge + 0.4, 0.9 + STOREY * 2 + 1.2, tk);
}

const ARCHETYPES = {brick_hall: brickHall, steel_plant: steelPlant,
                    tank_farm: tankFarm, shed_yard: shedYard};

const FAMILY = [
  [/multitek|element|sulfur|sulphur|\bns\b/, 'brick_hall'],
  [/optimpp|\bmpp\b|distil|\bd86\b|simdis/, 'steel_plant'],
  [/flash|pensky|cleveland|\bpac\b|\bfp\b/, 'tank_farm'],
  [/koehler|viscos|cloud|pour|\bcp\b|density/, 'shed_yard'],
];

function characterOf(station) {
  const title = String(station.title || station.uid || '').toLowerCase();
  for (const [re, arch] of FAMILY) if (re.test(title)) return arch;
  const keys = Object.keys(ARCHETYPES);
  let h = 0;
  for (const ch of String(station.uid)) h = (Math.imul(h, 31) + ch.charCodeAt(0)) | 0;
  return keys[Math.abs(h) % keys.length];
}

/* ---------------------------------------------------------- status fittings -- */

/** Barriers and cones round the siding — SERVICE, prebuilt and hidden. */
function serviceBarriers(kit, halfWidth) {
  const z = -DOCK_OFFSET + PLATFORM_EDGE + 6.6;
  const len = Math.min(50, halfWidth * 2 + 12);
  /* A run of pedestrian barriers the length of the siding face. Short of that
   * the state does not survive the wide camera, and a status the operator has
   * to zoom in to read is not a status. */
  const bays = 8;
  for (let i = 0; i < bays; i++) {
    const x = -len / 2 + (len / bays) * (i + 0.5);
    const w = (len / bays) * 0.86;
    /* Per bay, not per run. Fifty metres of barrier set from one height sinks
     * into the apron at one end of the siding and floats at the other, and it
     * is the object an operator looks straight at when a bench goes to
     * SERVICE. */
    kit.stand(x, z, w / 2, 0.5);
    kit.box('hazard', w, 0.22, 0.09, x, 1.12, z);
    kit.box('hazard', w, 0.22, 0.09, x, 0.62, z);
    kit.box('steel', 0.09, 1.3, 0.09, x - w / 2, 0.65, z);
    kit.box('steel', 0.09, 1.3, 0.09, x + w / 2, 0.65, z);
    kit.box('steel', 0.7, 0.07, 0.7, x - w / 2, 0.035, z);
    kit.box('steel', 0.7, 0.07, 0.7, x + w / 2, 0.035, z);
  }
  for (let i = 0; i < 10; i++) {
    const x = -len / 2 + 2 + i * ((len - 4) / 9);
    kit.stand(x, z - 3.4, 0.4, 0.4);
    kit.cone('hazard', 0.30, 0.78, x, 0.02, z - 3.4, 8);
    kit.box('hazard', 0.66, 0.05, 0.66, x, 0.03, z - 3.4);
  }
  /* A works cabin and a spoil skip: a bench under service has kit on the apron
   * and that is what makes it read as work rather than as decoration. */
  kit.stand(-len * 0.30, z + 4.2, 1.8, 1.3);
  kit.box('hazard', 3.4, 2.5, 2.4, -len * 0.30, 1.25, z + 4.2);
  kit.box('steel', 3.6, 0.2, 2.6, -len * 0.30, 2.6, z + 4.2);
  kit.stand(len * 0.22, z + 4.0, 2.2, 1.1);
  kit.box('rust', 4.4, 1.5, 2.2, len * 0.22, 0.75, z + 4.0);
  kit.box('hazard', 4.5, 0.4, 2.3, len * 0.22, 1.35, z + 4.0);
  for (let i = 0; i < 3; i++) {
    kit.stand(len * 0.34 + i * 0.75, z + 3.2, 0.35, 0.35);
    kit.cyl('rust', 0.32, 0.32, 0.9, len * 0.34 + i * 0.75, 0, z + 3.2, 10);
  }
}

/** Gates across the dock, boarding on the ground floor: DEAD-LINE. A shut
 *  facility should look shut from the wide camera without reading its colour. */
function deadlineShutters(kit, halfWidth) {
  const z = -DOCK_OFFSET + PLATFORM_EDGE + 6.2;
  const len = Math.min(48, halfWidth * 2 + 10);
  /* Gates the length of the siding face, not a token pair: from the wide
   * camera the shut state has to be a change in the silhouette, and one small
   * fence in the middle of a 40m apron is not one. */
  for (const s of [-1, 1]) {
    const cx = s * len * 0.25;
    const w = len * 0.5;
    kit.stand(cx, z, w / 2, 0.5);
    kit.box('steel', 0.18, 3.0, 0.18, cx - w / 2, 1.5, z);
    kit.box('steel', 0.18, 3.0, 0.18, cx + w / 2, 1.5, z);
    kit.box('steel', w, 0.14, 0.14, cx, 2.85, z);
    kit.box('steel', w, 0.14, 0.14, cx, 0.4, z);
    kit.box('steel', w, 0.14, 0.14, cx, 1.6, z);
    for (let i = 0; i <= 18; i++) {
      kit.box('steel', 0.06, 2.5, 0.06, cx - w / 2 + (w * i) / 18, 1.6, z);
    }
    kit.box('hazard', w * 0.62, 0.7, 0.07, cx, 1.75, z - 0.12);
    kit.tilt('steel', 0.09, 0.09, Math.hypot(w, 2.4), cx, 1.6, z + 0.1, 0, 0, 0);
  }
  /* Jersey barriers across the ramp, and the buffer stop the siding gets when
   * nothing is coming to it any more. */
  for (const bx of [-len * 0.34, -len * 0.12, len * 0.12, len * 0.34]) {
    kit.stand(bx, z - 2.6, 1.5, 0.5);
    kit.box('concrete', 3.0, 0.95, 1.0, bx, 0.48, z - 2.6);
    kit.box('concrete', 3.0, 0.35, 0.7, bx, 1.12, z - 2.6);
    kit.box('hazard', 3.02, 0.34, 1.02, bx, 0.8, z - 2.6);
  }
  kit.stand(-len * 0.42, -DOCK_OFFSET, 1.4, 0.9);
  kit.box('rust', 2.6, 1.2, 1.6, -len * 0.42, 0.9, -DOCK_OFFSET);
  kit.box('hazard', 2.7, 0.9, 0.1, -len * 0.42, 1.5, -DOCK_OFFSET + 0.85);
}

/** A fractionating column: a tall vessel on a skirt, with tray rings, platforms
 *  and a caged ladder. Nothing else says "petroleum" from 300m away. */
function processColumn(kit, x, z, r, h, rng) {
  const prev = kit.foot('concrete', x, z, r + 0.9, r + 0.9, {skirt: 0.7});
  kit.cyl('concrete', r + 0.5, r + 0.9, 2.2, x, 0, z, 16);
  kit.cyl('steel', r, r * 1.02, h, x, 2.2, z, 18, false);
  kit.sphere('steel', r, x, 2.2 + h, z, 14);
  kit.cyl('steel', r * 0.28, r * 0.28, 1.6, x, 2.2 + h + r * 0.5, z, 10);
  const decks = Math.max(3, Math.round(h / 7));
  for (let i = 1; i <= decks; i++) {
    const y = 2.2 + (h * i) / (decks + 0.4);
    kit.torus('steel', r + 1.05, 0.1, x, y, z, 18);
    kit.cyl('steel', r + 1.1, r + 1.1, 0.1, x, y - 0.05, z, 18, true);
    for (let k = 0; k < 12; k++) {
      const a = (k / 12) * Math.PI * 2;
      kit.box('steelDetail', 0.05, 1.05, 0.05,
              x + Math.cos(a) * (r + 1.0), y + 0.55, z + Math.sin(a) * (r + 1.0));
    }
    kit.torus('steelDetail', r + 1.0, 0.04, x, y + 1.05, z, 18);
  }
  for (let i = 0; i < 5; i++) {
    const a = rng() * 6.28;
    const y = 4 + rng() * (h - 6);
    kit.tube('steel', new THREE.Vector3(x + Math.cos(a) * r, y, z + Math.sin(a) * r),
             new THREE.Vector3(x + Math.cos(a) * (r + 2.5), y, z + Math.sin(a) * (r + 2.5)),
             0.16, 8);
  }
  ladder(kit, 'steelDetail', x + r + 1.6, z, 2.2, 2.2 + h);
  const top = kit.base + 2.2 + h + r + 2.0;
  kit.base = prev;
  return top - prev;
}

/* ------------------------------------------------------------- the terminal -- */

/** LabCore: the receiving yard every train ends up at, and the biggest thing on
 *  the map by a wide margin.
 *
 *  The layout is the whole point. The first attempt put the tank farm directly
 *  behind the process hall, where — from every camera the floor actually uses,
 *  all of which look at the terminal from the south — it was completely hidden
 *  and the terminal read as smaller than the instruments feeding it. The tanks
 *  are now in two bunds flanking the hall and south of it, between the rack and
 *  the loading gantry, which is also where a real receiving yard puts them.
 */
function labcoreTerminal(kit, rng) {
  const dockZ = DOCK_OFFSET;                   // the terminal faces south
  const tops = [];

  /* Yard slab and the arrival siding.
   *
   * This is 300 x 150 metres and terrain grades a 128 x 96 pad for it, so three
   * quarters of it stood on whatever the landscape happened to be doing: it was
   * 3.7m under the natural ground at its north-west corner and 24.4m over it at
   * its south-east, and from the wide camera the west half of the terminal —
   * flare stack, coolers, one whole tank bund — was standing on grass with the
   * slab nowhere in sight. A conforming slab is the only shape that can be both
   * a level yard where the pad is and a real surface out at the fence line. */
  const yz0 = dockZ - 141, yz1 = dockZ + 9;
  slabGrid(kit, 'concrete', -150, 150, yz0, yz1, 6.0, -0.10);
  slabSkirt(kit, 'concrete', -150, 150, yz0, yz1, 6.0, -0.10, 4.0);
  slabGrid(kit, 'roof', -146.5, 146.5, yz0 + 3.5, yz1 - 3.5, 6.0, 0);
  /* And the underside of the embankment. Over a yard this size the fill runs to
   * twelve metres in places, and a one-sided surface with twelve metres of air
   * under it is back-face culled from below — a camera that dips under the yard
   * edge sees straight through it to the landscape. This closes the volume
   * against the natural ground for the cost of one more grid. */
  slabGrid(kit, 'concrete', -150, 150, yz0, yz1, 6.0, 0,
           {yAt: (x, z) => Math.min(kit.pad(x, z), kit.pad.gl(x, z)) - 0.4, flip: true});
  const pz = dockZ - PLATFORM_EDGE - 5.0;
  kit.foot('concrete', 0, pz + 2.5, 75, 2.5, {skirt: 1.4});
  kit.box('concrete', 150, 1.15, 5.0, 0, 0.58, pz + 2.5);
  kit.box('concrete', 150, 0.12, 0.5, 0, 1.17, pz + 4.9);
  kit.box('hazard', 150, 0.02, 0.36, 0, 1.19, pz + 4.6);
  const yard = kit.base;

  /* A 130m loading gantry over the siding: seven portals, a walkway the length
   * of it, and a drop arm per tank car. */
  const gy = 7.6;
  /* Fourteen legs across 130m of yard, each on its own ground, all carrying one
   * level deck — the deck is set from the highest of them so no leg is short. */
  const gLegs = [];
  let gDeck = -Infinity;
  for (let i = 0; i < 7; i++) {
    const gx = -66 + i * 22;
    for (const gz of [dockZ + 4.8, dockZ - 4.8]) {
      const y = kit.span(gx, gz, 1.2, 1.2).hi;
      gLegs.push([gx, gz, y]);
      if (y + gy > gDeck) gDeck = y + gy;
    }
  }
  kit.base = gDeck - gy;
  for (const [gx, gz, y] of gLegs) {
    const foot = y - kit.base;
    kit.box('steel', 0.55, gy - foot, 0.55, gx, (gy + foot) / 2, gz);
    kit.box('concrete', 1.6, 0.5, 1.6, gx, foot + 0.25, gz);
    kit.tilt('steel', 0.22, 0.22, 3.4, gx, gy - 1.5, gz + (gz > dockZ ? -1.5 : 1.5),
             gz > dockZ ? 0.75 : -0.75);
  }
  for (let i = 0; i < 7; i++) {
    kit.box('steel', 0.65, 0.55, 10.2, -66 + i * 22, gy + 0.28, dockZ);
  }
  kit.box('steel', 134, 0.55, 0.55, 0, gy + 0.28, dockZ + 4.8);
  kit.box('steel', 134, 0.55, 0.55, 0, gy + 0.28, dockZ - 4.8);
  kit.box('steel', 134, 0.14, 1.5, 0, gy + 0.6, dockZ - 2.1);
  handrail(kit, 'steelDetail', -67, dockZ - 1.35, 67, dockZ - 1.35, gy + 0.66);
  handrail(kit, 'steelDetail', -67, dockZ - 2.85, 67, dockZ - 2.85, gy + 0.66);
  for (let i = 0; i < 11; i++) {
    const ax = -62 + i * 12.4;
    kit.tube('steel', new THREE.Vector3(ax, gy + 0.25, dockZ - 1.9),
             new THREE.Vector3(ax, gy + 0.25, dockZ), 0.16, 8);
    kit.tube('steel', new THREE.Vector3(ax, gy + 0.25, dockZ),
             new THREE.Vector3(ax, 5.3, dockZ), 0.16, 8);
    kit.tube('rust', new THREE.Vector3(ax, 5.3, dockZ),
             new THREE.Vector3(ax + 1.2, 4.4, dockZ), 0.12, 6);
    kit.sphere('steel', 0.2, ax, 5.3, dockZ, 8);
  }
  kit.tube('steel', new THREE.Vector3(-68, gy + 1.0, dockZ - 1.9),
           new THREE.Vector3(68, gy + 1.0, dockZ - 1.9), 0.22, 10);
  tops.push(kit.base + gy + 1.4);

  /* The process hall, on the yard's centre line and set back from the rack. */
  const hw = 68, hd = 34, hh = 19.5;
  const hx = -5, hz = dockZ - 60;
  kit.stand(hx, hz, hw / 2 + 1.2, hd / 2 + 1.2);
  kit.box('concrete', hw + 2.4, 6.0, hd + 2.4, hx, -2.6, hz);
  kit.box('concrete', hw + 1.6, 1.5, hd + 1.6, hx, 0.55, hz);
  kit.box('clad', hw, hh, hd, hx, 1.3 + hh / 2, hz);
  for (let i = 0; i <= 12; i++) {
    const x = hx - hw / 2 + (hw * i) / 12;
    kit.box('trim', 0.34, hh, 0.16, x, 1.3 + hh / 2, hz - hd / 2 - 0.08);
    kit.box('trim', 0.34, hh, 0.16, x, 1.3 + hh / 2, hz + hd / 2 + 0.08);
  }
  for (let s = 1; s * 3.6 < hh; s++) {
    kit.box('trim', hw + 0.2, 0.13, 0.09, hx, 1.3 + s * 3.6, hz - hd / 2 - 0.06);
    kit.box('trim', hw + 0.2, 0.13, 0.09, hx, 1.3 + s * 3.6, hz + hd / 2 + 0.06);
  }
  const pitch = 0.2, half = hd / 2 + 0.9;
  const ridge = 1.3 + hh + Math.tan(pitch) * half;
  for (const s of [-1, 1]) {
    kit.tilt('roof', hw + 1.8, 0.34, half / Math.cos(pitch), hx,
             1.3 + hh + Math.tan(pitch) * half / 2, hz + s * half / 2, s * pitch);
  }
  kit.box('clad', hw + 1.8, Math.tan(pitch) * half, 0.7, hx,
          1.3 + hh + Math.tan(pitch) * half / 2, hz - hd / 2 - 0.45);
  kit.box('clad', hw + 1.8, Math.tan(pitch) * half, 0.7, hx,
          1.3 + hh + Math.tan(pitch) * half / 2, hz + hd / 2 + 0.45);
  kit.box('steel', hw * 0.7, 2.0, 4.4, hx, ridge + 0.9, hz);
  kit.panel('window', hw * 0.68, 1.2, hx, ridge + 1.1, hz - 2.22, Math.PI,
            15 / WBAYS, 1 / WFLOORS);
  kit.panel('window', hw * 0.68, 1.2, hx, ridge + 1.1, hz + 2.22, 0,
            15 / WBAYS, 1 / WFLOORS);
  kit.tilt('roof', hw * 0.74, 0.26, 5.2, hx, ridge + 1.95, hz, 0);
  for (let i = 0; i < 5; i++) {
    kit.cyl('steel', 0.8, 0.9, 1.3, hx + (i - 2) * 12, ridge + 2.1, hz, 12);
    kit.cone('steel', 0.98, 0.65, hx + (i - 2) * 12, ridge + 3.4, hz, 12);
  }
  windowBand(kit, hw - 10, 2.8, 1.3 + hh - 3.2, hz + hd / 2, 1, {ou: 0.11});
  windowBand(kit, hw - 10, 2.8, 1.3 + hh - 3.2, hz - hd / 2, -1, {ou: 0.72, ov: 0.5});
  windowBand(kit, hw * 0.30, 2.2, 5.4, hz + hd / 2, 1, {bay: 2.8});
  rollerDoor(kit, 8.0, 6.4, hx - hw * 0.28, hz + hd / 2, 1);
  rollerDoor(kit, 8.0, 6.4, hx + hw * 0.10, hz + hd / 2, 1);
  personnelDoor(kit, hx + hw * 0.34, hz + hd / 2, 1);
  wallServices(kit, hw, hh, hz - hd / 2, rng, -1);
  const hallBase = kit.base;
  tops.push(hallBase + ridge + 3.6);

  /* The sign: a hoarding on the hall's south eaves, 40m across, raked forward
   * on struts so it faces the yard rather than the sky. */
  const signW = 40, signH = 10;
  const sy = 1.3 + hh + 6.0;
  kit.panel('sign', signW, signH, hx, sy, hz + hd / 2 + 1.5, 0, 1, 1);
  kit.box('steel', signW + 1.6, 0.5, 0.6, hx, sy + signH / 2 + 0.25, hz + hd / 2 + 1.35);
  kit.box('steel', signW + 1.6, 0.5, 0.6, hx, sy - signH / 2 - 0.25, hz + hd / 2 + 1.35);
  for (let i = 0; i <= 6; i++) {
    const x = hx - signW / 2 + (signW * i) / 6;
    kit.box('steel', 0.34, signH + 1.2, 0.45, x, sy, hz + hd / 2 + 1.28);
    kit.tilt('steel', 0.24, 0.24, 7.0, x, sy - signH / 2 - 2.0, hz + hd / 2 - 0.6, -0.72);
  }
  for (const lx of [hx - signW * 0.32, hx, hx + signW * 0.32]) {
    kit.box('steel', 1.4, 0.34, 1.2, lx, sy - signH / 2 - 1.2, hz + hd / 2 + 2.4);
    kit.box('lamp', 1.2, 0.18, 0.6, lx, sy - signH / 2 - 1.4, hz + hd / 2 + 2.5);
  }
  tops.push(hallBase + sy + signH / 2);

  /* Two bunded tank groups flanking the hall, forward of it so they are the
   * first thing read from the south. */
  const bundZ = dockZ - 34;
  const GROUPS = [
    {cx: -66, tanks: [[-98, -2, 9.0, 16.5, true], [-76, -13, 10.0, 18.0, false],
                      [-53, -1, 8.0, 15.0, false]], w: 62},
    {cx: 74, tanks: [[45, -2, 8.5, 16.0, true], [67, -13, 9.5, 17.5, false],
                     [89, -1, 7.5, 14.0, false], [107, -13, 6.5, 12.5, false]], w: 82},
  ];
  for (const g of GROUPS) {
    kit.foot('concrete', g.cx, bundZ - 4, g.w / 2, 21, {skirt: 1.0, inset: 0.6});
    const bundBase = kit.base;
    kit.box('concrete', g.w, 1.6, 42, g.cx, 0.8, bundZ - 4);
    kit.box('concrete', g.w - 3.0, 1.4, 39, g.cx, 0.85, bundZ - 4);
    for (const [tx, tz, r, th, sp] of g.tanks) {
      tops.push(bundBase + storageTank(kit, tx, bundZ + tz, r, th, rng, {stairs: sp}));
      kit.tube('steel', new THREE.Vector3(tx, 2.2, bundZ + tz - r),
               new THREE.Vector3(tx, 2.2, bundZ - 26), 0.22, 8);
    }
  }
  kit.base = yard;
  kit.tube('steel', new THREE.Vector3(-104, 2.2, bundZ - 26),
           new THREE.Vector3(112, 2.2, bundZ - 26), 0.3, 10);

  /* Fractionating columns beside the hall: the terminal's skyline. */
  tops.push(kit.base + processColumn(kit, hx - hw / 2 - 13, hz + 4, 2.6, 30, rng));
  tops.push(kit.base + processColumn(kit, hx - hw / 2 - 21, hz - 2, 2.0, 24, rng));
  tops.push(kit.base + processColumn(kit, hx + hw / 2 + 14, hz + 2, 2.3, 27, rng));

  /* Flare stack at the far west, on its own pad and well clear of everything. */
  const fx = -132, fz = dockZ - 52, fh = 48;
  kit.foot('concrete', fx, fz, 5.5, 5.5, {skirt: 1.0});
  kit.box('concrete', 11, 1.0, 11, fx, 0.5, fz);
  for (let i = 1; i <= 8; i++) {
    const y = 1.0 + (fh * 0.76 * i) / 9;
    const t = 1 - (i / 9) * 0.5;
    kit.box('steel', 7.4 * t, 0.2, 0.2, fx, y, fz - 3.4 * t);
    kit.box('steel', 7.4 * t, 0.2, 0.2, fx, y, fz + 3.4 * t);
    kit.box('steel', 0.2, 0.2, 6.9 * t, fx - 3.4 * t, y, fz);
    kit.box('steel', 0.2, 0.2, 6.9 * t, fx + 3.4 * t, y, fz);
  }
  for (const sx of [-1, 1]) {
    for (const sz of [-1, 1]) {
      kit.tilt('steel', 0.38, fh * 0.78, 0.38, fx + sx * 2.6, 1.0 + fh * 0.39,
               fz + sz * 2.6, sz * -0.032, sx * 0.032);
    }
  }
  kit.cyl('steel', 0.7, 1.0, fh, fx, 1.0, fz, 14);
  kit.cyl('rust', 0.85, 0.74, 2.6, fx, 1.0 + fh, fz, 14);
  kit.cone('lamp', 1.05, 3.6, fx, 1.0 + fh + 2.6, fz, 10);
  ladder(kit, 'steelDetail', fx + 1.9, fz, 1.2, 1.0 + fh * 0.76);
  tops.push(kit.base + 1.0 + fh + 6.2);

  /* Fin-fan cooler bank and a knockout drum in the west yard. */
  for (let i = 0; i < 4; i++) {
    const cxp = -112 + i * 11;
    const czp = dockZ - 78;
    kit.stand(cxp, czp, 4.6, 3.2);
    for (const s of [-4.0, 4.0]) {
      for (const t of [-2.6, 2.6]) {
        const lo = Math.min(kit.base, kit.span(cxp + s, czp + t, 0.4, 0.4).lo) - 0.3;
        const hh = 5.4 + (kit.base - lo);
        const b = kit.base; kit.base = lo;
        kit.box('steel', 0.26, hh, 0.26, cxp + s, hh / 2, czp + t);
        kit.base = b;
      }
    }
    kit.box('steel', 9.2, 1.2, 6.0, cxp, 5.9, czp);
    kit.cyl('steel', 2.1, 2.1, 0.55, cxp, 6.5, czp, 14);
    kit.cyl('rust', 2.15, 2.15, 0.18, cxp, 7.05, czp, 14);
  }
  bulletTank(kit, 120, dockZ - 62, 2.8, 22, 0);
  bulletTank(kit, 120, dockZ - 70, 2.8, 22, 0);
  bulletTank(kit, -46, dockZ - 78, 2.4, 16, 0);

  /* Admin block: brick, three storeys, at the yard gate. */
  const aw = 26, ad = 15, ax = 130, az = dockZ - 18;
  kit.stand(ax, az, aw / 2 + 1, ad / 2 + 1);
  kit.box('concrete', aw + 2, 4.0, ad + 2, ax, -1.8, az);
  kit.box('concrete', aw + 1.2, 1.0, ad + 1.2, ax, 0.5, az);
  kit.box('brick', aw, STOREY * 3, ad, ax, 1.0 + STOREY * 1.5, az);
  for (let i = 0; i <= 6; i++) {
    const x = ax - aw / 2 + (aw * i) / 6;
    kit.box('brick', 0.9, STOREY * 3 - 0.4, 0.4, x, 1.0 + STOREY * 1.5, az + ad / 2 + 0.2);
    kit.box('brick', 0.9, STOREY * 3 - 0.4, 0.4, x, 1.0 + STOREY * 1.5, az - ad / 2 - 0.2);
  }
  kit.box('brick', aw + 1.2, 0.5, ad + 1.2, ax, 1.0 + STOREY * 3 + 0.1, az);
  kit.box('concrete', aw + 1.4, 0.18, ad + 1.4, ax, 1.0 + STOREY * 3 + 0.42, az);
  kit.box('roof', aw - 0.4, 0.3, ad - 0.4, ax, 1.0 + STOREY * 3 + 0.5, az);
  kit.box('brick', aw + 0.8, 1.0, 0.5, ax, 1.0 + STOREY * 3 + 1.0, az + ad / 2 + 0.1);
  kit.box('brick', aw + 0.8, 1.0, 0.5, ax, 1.0 + STOREY * 3 + 1.0, az - ad / 2 - 0.1);
  for (let s = 0; s < 3; s++) {
    const y = 1.0 + s * STOREY + STOREY * 0.58;
    kit.panel('window', aw - 4.5, 2.15, ax, y, az + ad / 2 + 0.42, 0,
              Math.round((aw - 4.5) / 3) / WBAYS, 1 / WFLOORS, s * 0.37, s * 0.5);
    kit.panel('window', aw - 4.5, 2.15, ax, y, az - ad / 2 - 0.42, Math.PI,
              Math.round((aw - 4.5) / 3) / WBAYS, 1 / WFLOORS, s * 0.71 + 0.2, s * 0.5);
    kit.box('trim', aw - 4.2, 0.22, 0.34, ax, y + 1.25, az + ad / 2 + 0.32);
    kit.box('trim', aw - 4.2, 0.22, 0.34, ax, y - 1.25, az + ad / 2 + 0.32);
    kit.box('concrete', aw - 4.0, 0.16, 0.55, ax, y - 1.45, az + ad / 2 + 0.4);
  }
  windowBandX(kit, ad - 4, 2.15, 1.0 + STOREY * 1.58, ax + aw / 2, 1);
  personnelDoor(kit, ax - aw * 0.28, az + ad / 2, 1);
  kit.box('concrete', 7.0, 0.4, 3.0, ax - aw * 0.28, 0.2, az + ad / 2 + 1.6);
  roofPlant(kit, ax, az, aw - 4, ad - 4, 1.0 + STOREY * 3 + 0.68, rng);
  tops.push(kit.base + 1.0 + STOREY * 3 + 2.0);

  /* Racks stitching the yard together. */
  pipeRack(kit, -104, bundZ - 26, -104, dockZ - 13, 6.6, 5, rng);
  pipeRack(kit, -104, dockZ - 13, 112, dockZ - 13, 6.6, 5, rng);
  pipeRack(kit, 74, dockZ - 13, 74, bundZ - 26, 6.6, 4, rng);
  pipeRack(kit, hx, hz + hd / 2 + 2, hx, dockZ - 15, 6.2, 4, rng);
  pipeRack(kit, -112, dockZ - 74, -112, bundZ - 26, 5.8, 3, rng);

  /* Yard lighting on tall masts: the terminal works at night. */
  for (const [mx, mz] of [[-118, dockZ - 8], [-72, dockZ - 8], [-24, dockZ - 8],
                          [24, dockZ - 8], [72, dockZ - 8], [118, dockZ - 8],
                          [-100, dockZ - 92], [-20, dockZ - 92], [60, dockZ - 92],
                          [128, dockZ - 60]]) {
    floodMast(kit, mx, mz, 18);
  }

  /* Gatehouse and the perimeter fence along the yard's south edge. */
  kit.foot('concrete', 146, dockZ + 14, 3.5, 3.0, {skirt: 0.6});
  kit.box('concrete', 7, 0.4, 6, 146, 0.2, dockZ + 14);
  kit.box('brick', 5.4, 3.2, 4.4, 146, 1.8, dockZ + 14);
  kit.box('roof', 6.6, 0.3, 5.6, 146, 3.55, dockZ + 14);
  kit.panel('window', 3.6, 1.4, 146, 2.5, dockZ + 11.77, 0, 1 / WBAYS, 1 / WFLOORS);
  kit.box('hazard', 8, 0.24, 0.24, 138, 2.0, dockZ + 11);
  /* 300m of palisade along the fence line, well outside anything terrain grades
   * for the hub. Set from one height it was a fence half buried at one end and
   * on stilts at the other; each bay now finds the ground under its own two
   * posts and its rails follow. */
  for (let i = 0; i < 38; i++) {
    const x = -148 + i * 8;
    if (Math.abs(x) < 70) continue;
    kit.stand(x + 4, dockZ + 11, 4.2, 0.4);
    const a = kit.span(x, dockZ + 11, 0.3, 0.3).hi - kit.base;
    const b = kit.span(x + 8, dockZ + 11, 0.3, 0.3).hi - kit.base;
    kit.box('steel', 0.13, 2.8 - a, 0.13, x, a + (2.8 - a) / 2, dockZ + 11);
    const mid = (a + b) / 2;
    kit.box('steel', 8, 0.08, 0.08, x + 4, 2.7, dockZ + 11);
    kit.box('steel', 8, 0.08, 0.08, x + 4, mid + 0.6, dockZ + 11);
    kit.box('steel', 8, 0.08, 0.08, x + 4, mid + 1.65, dockZ + 11);
  }
  return Math.max(...tops);
}


/* ------------------------------------------------------------------ module -- */

export class Buildings {
  constructor(ctx) {
    this.ctx = ctx;
    this.group = new THREE.Group();
    this.group.name = 'buildings';
    this.sites = new Map();          // uid → {root, meshes, materials, …}
    this.docks = new Map();
    this.lib = null;
    this.night = 0;
    this.selected = null;
    this.hovered = null;
    this._t = 0;
  }

  async build(plan) {
    const ctx = this.ctx;
    ctx.scene.add(this.group);
    /* anchors is written by this module and read by labels.js; it may not have
     * been created yet depending on who booted first. */
    if (ctx.world && !ctx.world.anchors) ctx.world.anchors = new Map();

    try {
      this.lib = TX.material('buildings:library', buildLibrary);
    } catch (err) {
      console.warn('[buildings] texture library failed; using flat surfaces', err);
      this.lib = null;
    }
    this._makeMarkers();
    this._fallbackLighting();
    if (plan) this._rebuild(plan);
  }

  /* ---- lifecycle ---- */

  onPlan(plan) { this._rebuild(plan); }

  onMachines(machines, plan) {
    if (!Array.isArray(machines)) return;
    for (const m of machines) {
      const site = this.sites.get(m.machine_uid);
      if (site) this._applyStatus(site, m);
    }
  }

  onSelected(uid) {
    this.selected = uid || null;
    this._placeMarker(this.selMarker, uid, 1.0);
  }

  onHover(uid) {
    this.hovered = uid && uid !== this.selected ? uid : null;
    this._placeMarker(this.hoverMarker, this.hovered, 0.34);
  }

  onTime(hours) {
    /* Interiors come on before the sun is properly down and stay on into the
     * early shift — a lab runs 24 hours and an unlit site at 05:00 reads as
     * abandoned rather than as night. */
    const h = Number.isFinite(hours) ? hours : 12;
    const night = 1 - smooth(4.6, 7.4, h) * smooth(21.4, 18.2, h);
    this.night = saturate(night);
    for (const [, site] of this.sites) this._applyLight(site);
  }

  onWeather(weather) {
    const wet = saturate(weather?.wetness ?? 0);
    for (const [, site] of this.sites) {
      for (const key of ['concrete', 'roof', 'steel', 'hazard']) {
        const m = site.materials[key];
        if (m) m.roughness = 1 - 0.42 * wet;
      }
    }
  }

  /** At the bottom of the ladder the handrails, conduit, cage hoops and column
   *  platforms go. They are a third of the triangles on a site and none of them
   *  survives a 512 shadow map or a 0.6 render scale anyway — the silhouette,
   *  which is what the floor is read by, is untouched. */
  onQuality(tier) {
    const lean = !!tier && (tier.name === 'low' || tier.name === 'floor');
    for (const [, site] of this.sites) {
      for (const mesh of site.meshes) {
        if (mesh.userData.matKey !== 'steelDetail') continue;
        mesh.visible = !lean;
      }
    }
  }

  update(dt) {
    this._t += dt;
    /* The selection ring breathes slowly. Anything faster reads as an alarm,
     * and alarms on this floor mean something specific. */
    const k = 0.72 + 0.28 * Math.sin(this._t * 1.7);
    if (this.selMarker?.visible) this.selMarker.material.opacity = 0.15 + 0.11 * k;
    if (this.hoverMarker?.visible) this.hoverMarker.material.opacity = 0.055 + 0.035 * k;
  }

  dispose() {
    this._clear();
    this.group.removeFromParent();
    for (const m of [this.selMarker, this.hoverMarker]) {
      m?.geometry.dispose(); m?.material.map?.dispose(); m?.material.dispose();
    }
  }

  /* ---- what rail.js asks for ---- */

  /** Where a train stands when it is at this station, and which way it faces.
   *  Station docks are 26m north of the site centre; the terminal's is on its
   *  south face. Both run east-west, and nothing is built within 3.6m of the
   *  line below 6m so a track can be laid straight through. */
  dock(uid) {
    const d = this.docks.get(uid);
    if (!d) return null;
    return {position: d.position.clone(), direction: d.direction.clone()};
  }

  /* ---- construction ---- */

  _rebuild(plan) {
    if (!plan) return;
    this._clear();
    const ctx = this.ctx;
    const mats = this.lib;
    for (const station of plan.stations || []) {
      try { this._site(station, mats); }
      catch (err) { console.warn('[buildings] station skipped', station?.uid, err); }
    }
    if (plan.hub) {
      try { this._hub(plan.hub, mats); }
      catch (err) { console.warn('[buildings] terminal skipped', err); }
    }
    this.onTime(ctx.world?.timeOfDay ?? ctx.timeOfDay ?? 12);
    this.onWeather(ctx.weather);
    this.onQuality(ctx.quality);
    if (ctx.world?.machines?.length) this.onMachines(ctx.world.machines, plan);
    if (ctx.engine) ctx.engine.shadowNeedsUpdate = true;
  }

  /** Per-site materials: cloned so a building can be tinted, lit and shut on
   *  its own. The textures behind them are shared — this costs uniforms, not
   *  uploads. */
  /* How hard the facade patch hits each surface: macro breakup, contact
   * darkening, downwash streaking. Cladding takes the most macro because a
   * coated sheet is the flattest thing on the site; tarmac takes almost no
   * streaking because it is horizontal and there is nothing above it to run
   * off. Glass, signage and lamp lenses are not patched at all — their UVs are
   * a designed layout, not a world-scale surface. */
  /* The fourth number is top-face grime, and it is scaled by how much of the
   * surface a plan-view camera is looking down at and how little it self-cleans.
   * Steel takes the most: tank roofs and gantry decks are the site's sky-facing
   * area and were its brightest pixels. Concrete takes least of the flat
   * surfaces — an apron is walked and driven, and the yard's value is what the
   * cast shadows are read against, so it is not a place to spend contrast. */
  static FACADE = {
    concrete: [0.14, 1.00, 0.30, 0.20], brick: [0.19, 1.00, 0.40, 0.34],
    clad: [0.24, 1.00, 0.46, 0.52], roof: [0.15, 1.00, 0.10, 0.26],
    steel: [0.15, 0.85, 0.32, 0.85], trim: [0.08, 0.70, 0.18, 0.30],
    rust: [0.15, 0.80, 0.30, 0.50], door: [0.10, 0.90, 0.24, 0.14],
    hazard: [0.07, 0.70, 0.12, 0.10],
  };

  _materialsFor(rng, arch, field) {
    const lib = this.lib;
    if (!lib) {
      const flat = c => new THREE.MeshStandardMaterial({color: c, roughness: 0.85});
      return {concrete: flat(0x9a9a94), wall: flat(0x8a6a58), clad: flat(0x8f9aa0),
              brick: flat(0x8a6a58), roof: flat(0x3a3c3a), steel: flat(0xb0b2ae),
              steelDetail: flat(0xb0b2ae), trim: flat(0x4b5158),
              rust: flat(0x6a4128), door: flat(0x33383c),
              hazard: flat(0xb0842a), window: flat(0x1a222c), lamp: flat(0x222222),
              sign: flat(0x1a222c)};
    }
    const CLAD_TINTS = [0x9aa4a8, 0x86928a, 0xa88b76, 0xc3bcac, 0x7f8d9c, 0x93957f];
    const BRICK_TINTS = [0xffffff, 0xe2d3c4, 0xcfc4b8, 0xffd9c6, 0xd8c6b0];
    const ROOF_TINTS = [0xffffff, 0xc8ccc8, 0xb4bcc0];
    const out = {};
    out.concrete = lib.concrete.clone();
    out.concrete.color.setHex(0xffffff).multiplyScalar(0.92 + rng() * 0.16);
    out.brick = lib.brick.clone();
    out.brick.color.setHex(BRICK_TINTS[Math.floor(rng() * BRICK_TINTS.length)]);
    out.clad = lib.clad.clone();
    out.clad.color.setHex(CLAD_TINTS[Math.floor(rng() * CLAD_TINTS.length)]);
    out.roof = lib.roof.clone();
    out.roof.color.setHex(ROOF_TINTS[Math.floor(rng() * ROOF_TINTS.length)]);
    out.steel = lib.steel.clone();
    out.steel.color.setHex(0xffffff).multiplyScalar(0.84 + rng() * 0.22);
    out.steelDetail = out.steel;
    /* Window surrounds, mullions and door frames are dark painted steel. In
     * bright galvanised they read as a white grid stuck on the wall, which is
     * exactly the PS1 look the glazing is here to avoid. */
    out.trim = lib.steel.clone();
    out.trim.color.setHex(0x3e444b);
    /* Cloned now, where they used to be shared straight off the library. The
     * facade patch carries its occlusion field in per-material uniforms, and a
     * material shared between two sites would light one of them with the
     * other's building footprints. */
    out.rust = lib.rust.clone();
    out.door = lib.door.clone();
    out.hazard = lib.hazard.clone();

    if (field) {
      const uni = {
        bldField: {value: field.tex},
        bldFieldXf: {value: field.xf},
        bldMacro: {value: lib.macro},
        /* Offset and two scales per site, both well below the tile: the same
         * noise map, but no two facilities stain in the same places. */
        bldMacroXf: {value: new THREE.Vector4(
          rng() * 240 - 120, rng() * 240 - 120,
          1 / (26 + rng() * 20), 1 / (9.5 + rng() * 6))},
        bldPatchTint: {value: new THREE.Color().setHSL(
          0.035 + rng() * 0.10, 0.10 + rng() * 0.16, 0.30 + rng() * 0.22)},
      };
      for (const key in Buildings.FACADE) {
        const m = out[key];
        if (!m) continue;
        patchFacade(m, {...uni, bldFacade: {value:
          new THREE.Vector4(...Buildings.FACADE[key])}});
      }
    }

    const wset = lib.windowSets[Math.floor(rng() * lib.windowSets.length)];
    out.window = new THREE.MeshStandardMaterial({
      map: wset.map, normalMap: wset.normalMap,
      roughnessMap: wset.ormMap, metalnessMap: wset.ormMap,
      emissiveMap: wset.emissiveMap, emissive: new THREE.Color(0xffe6c4),
      emissiveIntensity: 0, roughness: 1, metalness: 1,
      /* 2.2 put a blown white rectangle on every pane the sun happened to
        * favour, and a hard-edged white rectangle is the flat blue tile with a
        * different colour. 1.5 keeps the sky legible in the glass and lets the
        * per-pane tint and dirt survive it. */
      envMapIntensity: 1.5, side: THREE.DoubleSide,
    });
    out.lamp = new THREE.MeshStandardMaterial({
      color: 0x2a2c30, roughness: 0.4, metalness: 0.6,
      emissive: new THREE.Color(0xffe0ac), emissiveIntensity: 0,
    });
    out.sign = new THREE.MeshStandardMaterial({
      map: lib.signMaps.map, emissiveMap: lib.signMaps.emissiveMap,
      emissive: new THREE.Color(0xffffff), emissiveIntensity: 0,
      roughness: 0.55, metalness: 0.3, side: THREE.DoubleSide,
    });
    out.arch = arch;
    return out;
  }

  _site(station, lib) {
    const ctx = this.ctx;
    /* Deterministic per instrument, and independent of `station.rng` — that
     * stream is shared with every other subsystem, so drawing from it would
     * make this building depend on who else ran first. */
    const rng = ctx.seededRandom(`${station.uid}|buildings`);
    const arch = characterOf(station);
    const scale = 0.86 + rng() * 0.30;
    /* Handed before anything is placed, so the whole facility comes out the
     * other way round — stack, stair tower, annex and tank bund together. Four
     * archetypes over sixteen benches is why the critics reported seeing the
     * same building twice; this halves how often two sites of one archetype
     * present the same silhouette. */
    const y = this._padHeight(station.x, station.z, 30, 30);
    /* The site's own earthworks, sampled off the terrain at this station's
     * position. Its core covers the whole apron, so a station is still a level
     * platform — what changes is that the level is now the top of the ground
     * under each thing rather than the ground under the origin. */
    const pad = makeEarthworks(
      typeof ctx.ground === 'function' ? ctx.ground : () => y,
      y, station.x, station.z, {hx: 46, hz: 44, cz: 6, band: 26});
    const kit = new Kit(null, rng() > 0.5 ? -1 : 1, pad);
    podium(kit, 86, 80, 6);
    const top = (ARCHETYPES[arch] || shedYard)(kit, rng, scale);
    const halfWidth = 22;
    sidingFace(kit, halfWidth, rng);
    /* The field has to be built from the finished kit and handed to the
     * materials before `finish()` consumes and disposes the parts. */
    const field = occlusionField(kit.occ, 96);
    const materials = this._materialsFor(rng, arch, field);
    const meshes = kit.finish(materials, k => `${station.uid}:${k}`);

    /* The status fittings stand on the same earthworks as the site they belong
     * to — a barrier run that ignored it would float in front of a siding that
     * does not. */
    const barrierKit = new Kit(null, kit.mx, pad);
    serviceBarriers(barrierKit, halfWidth);
    const barriers = barrierKit.finish(materials, k => `${station.uid}:svc:${k}`);
    const shutKit = new Kit(null, kit.mx, pad);
    deadlineShutters(shutKit, halfWidth);
    const shut = shutKit.finish(materials, k => `${station.uid}:shut:${k}`);

    const root = new THREE.Group();
    root.name = `site:${station.uid}`;
    root.position.set(station.x, y, station.z);
    root.userData.machineUid = station.uid;
    for (const m of meshes) root.add(m);
    const svc = new THREE.Group(); svc.visible = false;
    for (const m of barriers) svc.add(m);
    const dead = new THREE.Group(); dead.visible = false;
    for (const m of shut) dead.add(m);
    root.add(svc, dead);
    this.group.add(root);

    /* `top` came back in the principal volume's own frame, and that frame now
     * starts at whatever ground was under it — labels.js hangs its plate off
     * this, so the offset has to be added back or every label on a site cut
     * into a slope sits a metre inside its own roof. */
    const anchorTop = top + (kit.mainBase || 0);
    const site = {uid: station.uid, root, meshes, materials, top: anchorTop,
                  field, pad, founds: kit.founds,
                  extent: {x0: -46, x1: 46, z0: -36, z1: 48},
                  barriers: svc, shut: dead, lit: true, radius: 0};
    site.radius = Math.max(30, halfWidth + 16);
    this.sites.set(station.uid, site);

    if (ctx.world?.pickables) ctx.world.pickables.push(root);
    if (ctx.world?.anchors) ctx.world.anchors.set(station.uid, {top: anchorTop});
    this.docks.set(station.uid, {
      position: new THREE.Vector3(station.x, y, station.z - DOCK_OFFSET),
      direction: new THREE.Vector3(1, 0, 0),
    });
    this._applyStatus(site, station.machine);
  }

  _hub(hub, lib) {
    const ctx = this.ctx;
    const rng = ctx.seededRandom('__labcore__|buildings');
    const y = this._padHeight(hub.x, hub.z, 120, 70);
    /* A far wider core and a 70m band, because the terminal is 300m across and
     * terrain grades 128 x 96 of it. Inside the core it is a level yard on
     * fill; past it the fill runs out over seventy metres so the fence line and
     * the flare pad meet natural ground instead of standing on a retaining wall
     * the height of the process hall. */
    const pad = makeEarthworks(
      typeof ctx.ground === 'function' ? ctx.ground : () => y,
      y, hub.x, hub.z, {hx: 80, hz: 56, cz: 0, band: 70});
    const kit = new Kit(null, 1, pad);
    const top = labcoreTerminal(kit, rng);
    /* 128 rather than 64: the terminal's field spans 300m, and at 64 a cell is
     * nearly five metres — wider than the contact gradient it is meant to
     * describe, which turns every seam on the yard into a soft grey wash. */
    const field = occlusionField(kit.occ, 160);
    const materials = this._materialsFor(rng, 'terminal', field);
    const meshes = kit.finish(materials, k => `labcore:${k}`);
    const root = new THREE.Group();
    root.name = 'site:labcore';
    root.position.set(hub.x, y, hub.z);
    root.userData.machineUid = hub.uid;
    for (const m of meshes) root.add(m);
    this.group.add(root);
    const site = {uid: hub.uid, root, meshes, materials, top, field, pad,
                  founds: kit.founds,
                  extent: {x0: -152, x1: 152, z0: -118, z1: 45},
                  barriers: null, shut: null, lit: true, radius: 120};
    this.sites.set(hub.uid, site);
    if (ctx.world?.anchors) ctx.world.anchors.set(hub.uid, {top});
    this.docks.set(hub.uid, {
      position: new THREE.Vector3(hub.x, y, hub.z + DOCK_OFFSET),
      direction: new THREE.Vector3(1, 0, 0),
    });
  }

  /** The elevation a site's platform is cut to. Terrain grades a level pad
   *  under every station, so the design elevation is what it answers at the
   *  centre; the samples around it only guard against landing on a lone spike.
   *  Taking the maximum over the whole footprint was tried and is wrong — one
   *  hill inside the sample radius jacks the whole facility several metres into
   *  the air on a plinth, which looks far worse than a metre of fill.
   */
  _padHeight(cx, cz, hx, hz) {
    const g = this.ctx.ground;
    if (typeof g !== 'function') return 0;
    const hs = [g(cx, cz), g(cx - hx * 0.4, cz), g(cx + hx * 0.4, cz),
                g(cx, cz - hz * 0.4), g(cx, cz + hz * 0.4)]
      .filter(Number.isFinite).sort((a, b) => a - b);
    return hs.length ? hs[Math.floor(hs.length / 2)] : 0;
  }

  _clear() {
    const pick = this.ctx.world?.pickables;
    for (const [, site] of this.sites) {
      if (pick) {
        const i = pick.indexOf(site.root);
        if (i >= 0) pick.splice(i, 1);
      }
      site.root.traverse(o => { if (o.isMesh) o.geometry.dispose(); });
      site.field?.tex?.dispose();
      for (const key in site.materials) {
        const m = site.materials[key];
        /* Only clones are ours to dispose; the library's own materials are
         * shared across every site and owned by the texture cache. */
        if (m && m.isMaterial && !Object.values(this.lib || {}).includes(m)) m.dispose();
      }
      site.root.removeFromParent();
      this.ctx.world?.anchors?.delete(site.uid);
    }
    this.sites.clear();
    this.docks.clear();
  }

  /* ---- state ---- */

  _applyStatus(site, machine) {
    const status = String(machine?.status || 'UNKNOWN').toUpperCase();
    site.status = status;
    if (site.barriers) site.barriers.visible = status === 'SERVICE';
    if (site.shut) site.shut.visible = status === 'DEAD-LINE';
    /* A dead-lined bench is not merely red: it is dark. Nothing runs, nothing
     * is lit, and that reads at any distance without a legend. */
    site.lit = status !== 'DEAD-LINE';
    this._applyLight(site);
  }

  _applyLight(site) {
    const n = site.lit ? this.night : 0;
    const w = site.materials.window;
    if (w) w.emissiveIntensity = n * 1.05;
    const l = site.materials.lamp;
    if (l) l.emissiveIntensity = site.lit ? 0.12 + this.night * 2.6 : 0;
    const s = site.materials.sign;
    if (s) s.emissiveIntensity = n * 1.15;
  }

  /* ---- selection ---- */

  _makeMarkers() {
    const size = 128;
    const cv = TX.paint(size, (x, y, u, v) => {
      const d = Math.hypot(u - 0.5, v - 0.5) * 2;
      const ring = smooth(0.855, 0.935, d) * smooth(1.0, 0.955, d);
      const glow = smooth(0.70, 0.98, d) * 0.13;
      const a = saturate(ring + glow);
      return [a, a, a, a];
    });
    const tex = TX.makeTexture(cv, {srgb: true, aniso: 4});
    const make = colour => {
      const m = new THREE.Mesh(
        new THREE.PlaneGeometry(1, 1),
        new THREE.MeshBasicMaterial({
          map: tex, color: colour, transparent: true, opacity: 0,
          depthWrite: false, blending: THREE.AdditiveBlending,
          side: THREE.DoubleSide, toneMapped: false,
        }));
      m.rotation.x = -Math.PI / 2;
      m.visible = false;
      m.renderOrder = 3;
      m.frustumCulled = false;
      this.group.add(m);
      return m;
    };
    this.selMarker = make(0x7fd4ff);
    this.hoverMarker = make(0xbfd8e8);
  }

  _placeMarker(marker, uid, strength) {
    if (!marker) return;
    const site = uid && this.sites.get(uid);
    if (!site) { marker.visible = false; return; }
    const r = site.radius * 1.35;
    marker.scale.set(r, r, 1);
    marker.position.copy(site.root.position);
    marker.position.y += 0.34;
    marker.material.opacity = strength * 0.22;
    marker.visible = true;
  }

  /** Lighting belongs to sky.js and gi.js. When neither is present — a solo
   *  harness run, or both failed to load — the floor would render black, and
   *  the floor is a status display before it is a rendering. This is the
   *  minimum that keeps it readable, and it removes itself the moment anyone
   *  else puts a light in the scene. */
  _fallbackLighting() {
    const scene = this.ctx.scene;
    let hasLight = false;
    scene.traverse(o => { if (o.isLight) hasLight = true; });
    if (hasLight) return;
    try {
      const sun = new THREE.DirectionalLight(0xffeedd, 4.4);
      sun.position.set(190, 220, 130);
      sun.castShadow = true;
      sun.shadow.mapSize.set(2048, 2048);
      const c = sun.shadow.camera;
      c.left = -280; c.right = 280; c.top = 280; c.bottom = -280;
      c.near = 20; c.far = 700;
      sun.shadow.bias = -0.0009;
      scene.add(sun);
      scene.add(new THREE.HemisphereLight(0xa8c8ea, 0x6a6050, 1.9));
      if (!scene.environment) {
        const eq = TX.paint(128, (x, y, u, v) => {
          const sky = mixc([0.72, 0.79, 0.90], [0.42, 0.60, 0.94], smooth(0.55, 0.0, v));
          const ground = [0.30, 0.28, 0.24];
          const t = smooth(0.48, 0.54, v);
          return mixc(sky, ground, t);
        });
        const env = new THREE.CanvasTexture(eq);
        env.mapping = THREE.EquirectangularReflectionMapping;
        env.colorSpace = THREE.SRGBColorSpace;
        scene.environment = env;
        this._ownEnv = env;
      }
      this._ownLights = true;
    } catch (err) {
      console.warn('[buildings] fallback lighting failed', err);
    }
  }
}

export default Buildings;
