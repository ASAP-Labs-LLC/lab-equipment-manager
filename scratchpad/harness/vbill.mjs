/* vbill.mjs — what vegetation costs, per tier of its own LOD, at one camera.
 *
 *   node vbill.mjs [--cam wide] [--quality ultra]
 *
 * Ablation in-session rather than arithmetic: the scene is sampled, then each
 * group of meshes is hidden in turn and the renderer's own counters re-read, so
 * the figure is what the driver was actually asked to draw and not a sum over
 * geometry that may or may not have been culled.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const cam = arg('cam', 'wide');
const quality = arg('quality', 'ultra');
const mods = arg('mods', 'sky,gi,terrain,buildings,rail,trains,vegetation,weather');

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text())) errs.push(m.text().slice(0, 200)); });
p.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 200)));

await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=${mods}&cam=${cam}&time=16&hud=0&quality=${quality}`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(5000);

const out = await p.evaluate(async () => {
  const W = window.__lemWorld, v = W.subsystems.get('vegetation');
  const R = W.engine?.renderer || W.ctx?.renderer || W.renderer;
  const wait = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const read = async () => { await wait(); await wait();
    return {draws: R.info.render.calls, tris: R.info.render.triangles}; };
  const sets = {
    near: (v.trees || []).map(e => e.near),
    trunk: (v.trees || []).map(e => e.trunk).filter(Boolean),
    far: (v.trees || []).map(e => e.far),
    grove: (v.groves || []).map(g => g.mesh),
    clutter: (v.clutter || []).map(c => c.mesh),
    sward: (v.sward || []).map(s => s.mesh),
    grass: v.grass ? [v.grass.mesh] : [],
  };
  const all = await read();
  const rows = {};
  for (const [k, ms] of Object.entries(sets)) {
    if (!ms.length) { rows[k] = {draws: 0, tris: 0, insts: 0}; continue; }
    const insts = ms.reduce((a, m) => a + (m.count ?? 0), 0);
    for (const m of ms) m.visible = false;
    const off = await read();
    for (const m of ms) m.visible = true;
    rows[k] = {draws: all.draws - off.draws, tris: all.tris - off.tris, insts};
  }
  const grp = v.group;
  grp.visible = false;
  const noveg = await read();
  grp.visible = true;
  return {scene: all, vegetation: {draws: all.draws - noveg.draws, tris: all.tris - noveg.tris},
          rows, tier: v.tier, quality: v.quality, range: v.range,
          island: v.island, landR: v.landR, groveR: v.groveR};
});
await b.close();
console.log(JSON.stringify({cam, quality, ...out, errs}, null, 1));
