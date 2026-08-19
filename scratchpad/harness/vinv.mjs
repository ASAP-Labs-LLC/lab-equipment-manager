/* vinv.mjs — is the forest the same forest from every distance?
 *
 *   node vinv.mjs [--quality ultra] [--time 16] [--radius 130]
 *
 * The acceptance test for "zooming in and out should not make it more/less
 * barren", done as a count rather than as a photograph, because a photograph
 * of a hillside at two ranges differs in framing, in haze and in mip level and
 * only the first of those can be normalised away.
 *
 * One fixed patch of ground is chosen from the tree list itself — a disc that
 * genuinely has a stand on it. The camera is then flown to that patch from a
 * series of distances, the partition is forced, and everything DRAWN whose
 * centre falls in the disc is counted: near canopies, trunks, far cards,
 * groves (weighted by their coverage alpha, since a grove at 2% is not a
 * grove), undergrowth and grass. Population is invariant if those totals,
 * converted to stems, are flat across the row.
 *
 * The camera is placed at a fixed elevation angle so the patch is always in
 * frame; the frustum is a legitimate reason to drop an instance and a probe
 * that let the patch slide out of view would measure the frustum instead.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const quality = arg('quality', 'ultra');
const time = arg('time', '16');
const R = parseFloat(arg('radius', '130'));
const DISTS = (arg('dists', '160,320,640,1200,2200')).split(',').map(Number);

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errs.push(m.text().slice(0, 200)); });
p.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 200)));

await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,vegetation&cam=wide&time=${time}&hud=0`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(4000);
await p.evaluate(t => window.__lemWorld.engine.setQualityMode(t), quality);
await p.waitForTimeout(2500);

/* The patch: the densest 130 m disc among a sample of the placed stems, so the
 * measurement is taken on a wood rather than on a meadow that is empty at every
 * range for honest reasons. */
const patch = await p.evaluate(({R}) => {
  const v = window.__lemWorld.subsystems.get('vegetation');
  const xs = [], zs = [];
  for (const e of (v.trees || [])) for (let i = 0; i < e.list.length; i++) { xs.push(e.xs[i]); zs.push(e.zs[i]); }
  let best = null;
  for (let k = 0; k < xs.length; k += Math.max(1, (xs.length / 400) | 0)) {
    let n = 0;
    for (let j = 0; j < xs.length; j += 3) {
      const dx = xs[j] - xs[k], dz = zs[j] - zs[k];
      if (dx * dx + dz * dz < R * R) n++;
    }
    if (!best || n > best.n) best = {x: xs[k], z: zs[k], n: n * 3};
  }
  return {...best, total: xs.length, island: v.island, landR: v.landR, groveR: v.groveR,
          treeBudget: v._treeBudget, quality: v.quality, range: v.range};
}, {R});

const rows = [];
for (const d of DISTS) {
  await p.evaluate(({x, z, d}) => {
    const w = window.__lemWorld, r = w.rig;
    r.maxDistance = Math.max(r.maxDistance || 0, 6000);
    r.goalTarget.set(x, w.ground ? w.ground(x, z) : 0, z);
    r.target.copy(r.goalTarget);
    r.goalDistance = d; r.distance = d;
    r.goalPitch = 0.55; r.pitch = 0.55;
    r.apply(1);
  }, {x: patch.x, z: patch.z, d});
  await p.waitForTimeout(900);
  const row = await p.evaluate(({x, z, R, d}) => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    v._repartition(true);
    const inP = (px, pz) => { const dx = px - x, dz = pz - z; return dx * dx + dz * dz < R * R; };
    const walk = mesh => { let n = 0; if (!mesh) return 0;
      const a = mesh.instanceMatrix.array;
      for (let i = 0; i < mesh.count; i++) if (inP(a[i * 16 + 12], a[i * 16 + 14])) n++;
      return n; };
    let near = 0, trunk = 0, far = 0;
    for (const e of (v.trees || [])) { near += walk(e.near); trunk += walk(e.trunk); far += walk(e.far); }
    let grove = 0, groveA = 0;
    for (const g of (v.groves || [])) {
      const a = g.mesh.instanceMatrix.array;
      const al = g.mesh.geometry.getAttribute('aVegAlpha').array;
      for (let i = 0; i < g.mesh.count; i++)
        if (inP(a[i * 16 + 12], a[i * 16 + 14])) { grove++; groveA += al[i]; }
    }
    let clut = 0; for (const c of (v.clutter || [])) clut += walk(c.mesh);
    const grass = v.grass ? walk(v.grass.mesh || v.grass) : 0;
    /* The sward, weighted by its coverage alpha for the same reason the groves
     * were: a patch at 2% is not a patch. `swardPlaced` is the same disc taken
     * off the scatter list, which no camera can move, so the pair is a direct
     * read of population against representation. */
    let sward = 0, swardA = 0, swardPlaced = 0;
    for (const s of (v.sward || [])) {
      const a = s.mesh.instanceMatrix.array;
      const al = s.mesh.geometry.getAttribute('aVegAlpha').array;
      for (let i = 0; i < s.mesh.count; i++)
        if (inP(a[i * 16 + 12], a[i * 16 + 14])) { sward++; swardA += al[i]; }
      for (let i = 0; i < s.count; i++)
        if (inP(s.xs[i], s.zs[i]) && s.rank[i] <= v.quality * v._treeBudget) swardPlaced++;
    }
    /* Placed, not drawn: the same disc measured against the scatter lists, which
     * no camera can move. Anything the row does not reach is a tree the LOD
     * deleted. */
    let placed = 0;
    for (const e of (v.trees || [])) for (let i = 0; i < e.list.length; i++)
      if (inP(e.xs[i], e.zs[i]) && e.rank[i] <= v.quality * v._treeBudget) placed++;
    return {d, near, trunk, far, grove, groveA: +groveA.toFixed(1), clut, grass,
            sward, swardA: +swardA.toFixed(1), swardPlaced, placed,
            stems: near + far + +(groveA * 15).toFixed(0)};
  }, {x: patch.x, z: patch.z, R, d});
  rows.push(row);
}
await b.close();
console.log(JSON.stringify({quality, patch, R, rows, errs}, null, 1));
