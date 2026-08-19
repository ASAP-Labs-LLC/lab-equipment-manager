/* giwho.mjs — who is in / out of the coarse cascades, by name. */
import {chromium} from 'playwright';
const url = process.argv[2] ||
  'http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,vegetation,weather&cam=low&time=13&weather=clear&hud=0';
const browser = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
page.on('pageerror', e => console.log('pageerror', String(e).slice(0, 300)));
await page.goto(url, {waitUntil: 'load', timeout: 60000});
await page.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await page.waitForTimeout(5000);
const out = await page.evaluate(() => {
  const w = window.__lemWorld, gi = w.subsystems.get('gi');
  const rows = [];
  const tag = o => {
    let p = o, path = [];
    while (p && path.length < 4) { path.push(p.name || p.type); p = p.parent; }
    return path.join('<');
  };
  w.scene.traverse(o => {
    if (!(o.isMesh || o.isInstancedMesh || o.isBatchedMesh)) return;
    const m = o.userData?.lemCast;
    const mat = Array.isArray(o.material) ? o.material[0] : o.material;
    rows.push({t: tag(o), inst: o.count | 0,
      base: o.userData?.lemCastBase, cast: o.castShadow, recv: o.receiveShadow,
      size: m ? +m.size.toFixed(1) : null, rise: m ? +m.rise.toFixed(2) : null,
      slab: m?.slab, l6: o.layers.isEnabled(6), l7: o.layers.isEnabled(7),
      depth: !!gi._depthFor(o), cdm: !!o.customDepthMaterial,
      matT: mat ? [mat.type, mat.transparent, mat.alphaTest, mat.depthWrite].join('/') : 'none'});
  });
  return {rows, csm: gi._csm.map(c => ({i: c.i, casters: c.casters.length, ready: c.ready,
    runs: c.runs, cost: c.cost, radius: c.radius,
    box: c.i === 0 ? gi.uniforms.lemCsmBox0.value.toArray().map(n => +n.toFixed(1)) : null})),
    nearR: gi.uniforms.lemNearRadius.value,
    nearC: gi.uniforms.lemNearCentre.value.toArray().map(n => +n.toFixed(1))};
});
console.log(JSON.stringify(out.csm), 'nearR', out.nearR, 'nearC', out.nearC);
for (const r of out.rows) console.log(JSON.stringify(r));
await browser.close();
