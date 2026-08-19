/* grassfit.mjs — does ground cover actually sit on the ground?
 *
 * Ryan: grass "won't stick to the floor". Two things could mean: the instance
 * origin is at the wrong height (terrain.heightAt lying), or the card's own
 * geometry hangs off its origin so the blade's foot is not at y=0. This walks
 * every InstancedMesh vegetation owns, pulls each instance's world translation
 * AND the geometry's own y range, and compares the foot of the card against
 * both terrain.heightAt and a raycast onto the drawn terrain meshes. */
import {chromium} from 'playwright';
const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i]; if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const url = args.url || 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=terrain,vegetation&cam=street&time=16&hud=0';
const b = await chromium.launch({args: ['--use-angle=metal']});
const p = await b.newPage();
p.on('console', m => { if (m.type() === 'error') console.error('CONSOLE', m.text()); });
await p.goto(url, {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(6000);
console.log(JSON.stringify(await p.evaluate(() => {
  const w = window.__lemWorld;
  const THREE = window.THREE || window.__THREE || w?.ctx?.THREE;
  const t = w.subsystems.get('terrain'), v = w.subsystems.get('vegetation');
  if (!t || !v) return {error: 'missing subsystem'};
  const grp = v.group || v.root;
  const terr = t.group;
  const out = {meshes: [], waterY: t.waterY};
  const M = new THREE.Matrix4();
  const rc = new THREE.Raycaster();
  const down = new THREE.Vector3(0, -1, 0);
  const targets = [];
  terr.traverse(o => { if (o.isMesh && o.name !== 'terrain-water') targets.push(o); });
  grp.traverse(o => {
    if (!o.isInstancedMesh || !o.count) return;
    o.updateMatrixWorld(true);
    const g = o.geometry;
    g.computeBoundingBox();
    const gy0 = g.boundingBox.min.y, gy1 = g.boundingBox.max.y;
    const n = Math.min(o.count, 260);
    const step = Math.max(1, Math.floor(o.count / n));
    const errs = [], rerr = [];
    for (let i = 0; i < o.count; i += step) {
      o.getMatrixAt(i, M);
      const e = M.elements;
      const x = e[12], y = e[13], z = e[14];
      const sy = Math.hypot(e[4], e[5], e[6]);
      const foot = y + gy0 * sy;
      const gh = t.heightAt(x, z);
      errs.push(foot - gh);
      rc.set(new THREE.Vector3(x, y + 400, z), down);
      const hit = rc.intersectObjects(targets, false)[0];
      if (hit) rerr.push(foot - hit.point.y);
    }
    if (!errs.length) return;
    errs.sort((a, c) => a - c); rerr.sort((a, c) => a - c);
    const q = (arr, f) => arr.length ? +arr[Math.floor(f * (arr.length - 1))].toFixed(2) : null;
    out.meshes.push({
      name: o.name || '(unnamed)', count: o.count, sampled: errs.length,
      geoY: [+gy0.toFixed(2), +gy1.toFixed(2)],
      vsHeightAt: {min: q(errs, 0), p50: q(errs, 0.5), max: q(errs, 1)},
      vsDrawn: {min: q(rerr, 0), p50: q(rerr, 0.5), max: q(rerr, 1), n: rerr.length},
    });
  });
  return out;
}, null)));
await b.close();
