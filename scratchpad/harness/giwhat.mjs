/* giwhat.mjs — for a list of screen pixels, say everything the shading of that
 * pixel is made of: what was hit, its world normal, N·L, the probe answer, and
 * the AO buffer's value there.
 *
 * Written because the "caster-less dark region" survived every shadow A/B: it
 * is not in the near map, not in the coarse maps and not in the AO buffer, so
 * the next question is not "which caster" but "which term".
 */
import {chromium} from 'playwright';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), n = process.argv[i + 1];
  if (!n || n.startsWith('--')) args[k] = true; else { args[k] = n; i++; }
}
const PTS = (args.px || '900,470;900,560;620,430;1150,470;500,300')
  .split(';').map(s => s.split(',').map(Number));

const url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
  `?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather` +
  `&cam=${args.cam || 'yard'}&time=${args.time || '16'}&weather=clear&hud=0`;

const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist']});
const ctx = await browser.newContext({viewport: {width: 1280, height: 720},
                                      deviceScaleFactor: 1});
const page = await ctx.newPage();
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 200)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(6000);

const out = await page.evaluate(pts => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const THREE = w.ctx.THREE;
  const rc = new THREE.Raycaster();
  rc.layers.set(0);
  const targets = [];
  w.scene.traverse(o => { if (o.isMesh || o.isInstancedMesh) targets.push(o); });
  const W = w.engine.width || 1280, H = w.engine.height || 720;
  const v = new THREE.Vector2();
  const c = new THREE.Color();
  const n = new THREE.Vector3();
  const rows = [];
  for (const [sx, sy] of pts) {
    v.set(sx / W * 2 - 1, -(sy / H * 2 - 1));
    rc.setFromCamera(v, w.camera);
    const hits = rc.intersectObjects(targets, false);
    const hit = hits.find(h => h.distance > 3 && h.object.visible);
    if (!hit) { rows.push({sx, sy, miss: true}); continue; }
    n.set(0, 1, 0);
    if (hit.normal) n.copy(hit.normal)
      .applyNormalMatrix(new THREE.Matrix3().getNormalMatrix(hit.object.matrixWorld))
      .normalize();
    const p = hit.point;
    const NdL = n.dot(gi.sunDirection);
    gi.irradianceAt(p.x, p.y + 0.05, p.z, n, c);
    const mat = Array.isArray(hit.object.material) ? hit.object.material[0] : hit.object.material;
    rows.push({
      sx, sy, obj: hit.object.name || hit.object.type,
      dist: +hit.distance.toFixed(1),
      p: [p.x, p.y, p.z].map(q => +q.toFixed(1)),
      nrm: [n.x, n.y, n.z].map(q => +q.toFixed(3)),
      NdL: +NdL.toFixed(3),
      probe: [c.r, c.g, c.b].map(q => +q.toFixed(4)),
      probeLum: +(0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b).toFixed(4),
      matColor: mat?.color ? mat.color.getHexString() : null,
      matRough: mat?.roughness, matName: mat?.name || mat?.type,
      recv: hit.object.receiveShadow, cast: hit.object.castShadow,
      groundY: +w.ctx.ground(p.x, p.z).toFixed(2),
    });
  }
  return {
    rows,
    sun: gi.sunDirection.toArray().map(q => +q.toFixed(3)),
    sunColour: gi.sunColour.getHexString(), sunI: gi.sunIntensity,
    giStrength: gi.uniforms.lemGIStrength.value,
    aoStrength: gi.uniforms.lemAOStrength.value,
    skyIrr: gi.uniforms.lemSkyIrradiance.value.toArray().map(q => +q.toFixed(3)),
    gndIrr: gi.uniforms.lemGroundIrradiance.value.toArray().map(q => +q.toFixed(3)),
    grid: gi._grid ? {min: gi._grid.min?.toArray?.().map(q => +q.toFixed(1)),
                      size: gi._grid.size?.toArray?.().map(q => +q.toFixed(1)),
                      dims: [gi._grid.nx, gi._grid.ny, gi._grid.nz]} : null,
  };
}, PTS);
console.log(JSON.stringify(out, null, 1));
await browser.close();
