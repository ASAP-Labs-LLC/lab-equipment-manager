/* csmprobe.mjs — what is in the shadow passes, and what they cost.
 *   node csmprobe.mjs URL */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
p.on('pageerror', e => console.log('PAGEERROR', String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 300)); });
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(5000);

console.log(JSON.stringify(await p.evaluate(async () => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi'), e = w.engine, r = e.renderer;
  const sleep = ms => new Promise(res => setTimeout(res, ms));

  /* Draw calls / triangles with and without a shadow-map refresh in the frame. */
  e.shadowNeedsUpdate = false;
  await sleep(300);
  const quiet = {calls: e.drawCalls, tris: e.triangles};
  e.shadowNeedsUpdate = true;
  await sleep(60);
  const loud = {calls: e.drawCalls, tris: e.triangles};

  /* Who casts, by owner. */
  const byOwner = {};
  let casters = 0, castTris = 0;
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
    if (!o.castShadow || !o.visible) return;
    let n = o, own = '?';
    while (n) { if (n.name) own = n.name; n = n.parent; }
    const g = o.geometry;
    const idx = g?.index ? g.index.count : (g?.attributes?.position?.count || 0);
    const inst = o.isInstancedMesh ? o.count : 1;
    const t = (idx / 3) * inst;
    casters++; castTris += t;
    const k = (o.name || own || 'unnamed').replace(/[0-9]+$/, '');
    byOwner[k] = byOwner[k] || {n: 0, tris: 0};
    byOwner[k].n++; byOwner[k].tris += t;
  });
  const top = Object.entries(byOwner).sort((a, c) => c[1].tris - a[1].tris)
    .slice(0, 18).map(([k, v]) => `${k} n=${v.n} tris=${Math.round(v.tris)}`);

  const cascades = (gi._csm || []).map(c => ({
    i: c.i, size: c.rt?.width, casters: c.casters.length, cost: c.cost, tris: c.tris,
    runs: c.runs, ready: c.ready, radius: c.radius,
    sample: c.casters.slice(0, 6).map(o => `${o.name || '?'}/${o.isInstancedMesh ? o.count : 1}/${(o.userData.lemCast?.size || 0).toFixed(0)}`),
  }));

  return {
    quiet, loud,
    shadowPassCalls: loud.calls - quiet.calls, shadowPassTris: loud.tris - quiet.tris,
    casters, castTris: Math.round(castTris), topCasters: top,
    cascades,
    nearRadius: gi.uniforms.lemNearRadius.value,
    shadowCam: (() => { const c = gi.sun.shadow.camera;
      return {l: c.left, r: c.right, t: c.top, b: c.bottom, near: c.near, far: c.far}; })(),
    shadowSize: gi.sun.shadow.mapSize.x,
    sunDir: gi.sunDirection.toArray().map(v => +v.toFixed(3)),
    tier: e.tier.name,
  };
}), null, 1));
await b.close();
