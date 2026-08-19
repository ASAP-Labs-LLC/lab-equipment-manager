/* vblob.mjs — WHAT is the dark round mass on the south-east spit, and where.
 *
 *   node vblob.mjs [--px 1416,940] [--cam far] [--time 9]
 *
 * Raycasts named screen pixels of the judged frame into the scene and reports
 * the object hit, its subsystem, and the world position — so "a scatter
 * instance landed outside its mask" becomes a tier name and an (x, z) instead
 * of a guess. Then hides each vegetation tier in turn and re-raycasts, which is
 * the only way to tell a sward card from a clutter bush from a far tree card
 * when all three are dark green at 900 m.
 */
import {chromium} from 'playwright';
import * as THREE from 'file:///Users/rynatical/LAB-lem/LEM%20Web%20Server/static/vendor/three.module.min.js';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const cam = arg('cam', 'far'), time = arg('time', '9');
const W = 1920, H = 1080;

const URL = `http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}` +
  `&time=${time}&hud=0&quality=ultra`;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: W, height: H}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(URL, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(9000);

const pxs = (arg('px', '') || '').split(';').filter(Boolean)
  .map(s => s.split(',').map(Number));
/* A lattice across the blob and across the sand either side of it, so the
 * answer is a map and not one sample. */
const grid = [];
for (let y = 860; y <= 1000; y += 14) for (let x = 1330; x <= 1500; x += 17) grid.push([x, y]);

const out = await p.evaluate(async ({pts, W, H}) => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const THREE = w.THREE || (await import('three'));
  const cam = w.rig ? w.rig.camera : w.camera;
  const camera = cam || w.engine.camera;
  const rc = new THREE.Raycaster();
  rc.far = 1e6;
  const v = new THREE.Vector2();

  /* which subsystem does an object belong to? walk up to a named group. */
  const owner = (o) => {
    for (const [name, s] of w.subsystems) {
      if (!s || !s.group) continue;
      let q = o;
      while (q) { if (q === s.group) return name; q = q.parent; }
    }
    return '?';
  };
  /* which vegetation tier? */
  const tierOf = (o) => {
    for (const e of (veg.trees || [])) {
      if (o === e.near || o === e.far || o === e.trunk) return 'tree';
    }
    for (const g of (veg.groves || [])) if (o === g.mesh) return 'grove';
    for (const c of (veg.clutter || [])) if (o === c.mesh) return 'clutter';
    for (const s of (veg.sward || [])) if (o === s.mesh) return 'sward';
    if (veg.grass && o === veg.grass.mesh) return 'grass';
    return null;
  };

  const shoot = (x, y) => {
    v.set((x / W) * 2 - 1, -(y / H) * 2 + 1);
    rc.setFromCamera(v, camera);
    const hits = rc.intersectObject(w.scene, true);
    for (const h of hits) {
      if (!h.object.visible) continue;
      /* The sky dome and gi's fullscreen quad are both a few metres from the
       * camera and both are hit first. A raycast that returns the camera's own
       * position is the instrument lying, not the scene. */
      if (h.distance < 60) continue;
      const o = h.object;
      if (o.isSprite) continue;
      return {x, y, obj: o.name || o.type, owner: owner(o), tier: tierOf(o),
              wx: +h.point.x.toFixed(1), wz: +h.point.z.toFixed(1),
              wy: +h.point.y.toFixed(2), inst: h.instanceId};
    }
    return {x, y, miss: true};
  };

  const res = pts.map(q => shoot(q[0], q[1]));
  return {
    waterY: +veg.waterY.toFixed(2),
    camPos: [+camera.position.x.toFixed(0), +camera.position.y.toFixed(0), +camera.position.z.toFixed(0)],
    hits: res,
  };
}, {pts: pxs.length ? pxs : grid, W, H});

/* Summarise: which tiers, and the world box each covers. */
const byTier = {};
for (const h of out.hits) {
  const k = h.miss ? 'miss' : (h.tier || h.owner);
  const s = byTier[k] || (byTier[k] = {n: 0, x0: 1e9, x1: -1e9, z0: 1e9, z1: -1e9});
  s.n++;
  if (!h.miss) { s.x0 = Math.min(s.x0, h.wx); s.x1 = Math.max(s.x1, h.wx);
                 s.z0 = Math.min(s.z0, h.wz); s.z1 = Math.max(s.z1, h.wz); }
}
console.log(JSON.stringify({camPos: out.camPos, waterY: out.waterY, byTier,
  hits: out.hits.filter(h => h.tier || h.miss)}, null, 1));
if (errs.length) console.log('errors:', errs.slice(0, 3));
await b.close();
