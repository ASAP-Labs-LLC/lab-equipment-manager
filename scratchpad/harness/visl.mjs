/* visl.mjs — the island round's own probe.
 *
 *   node visl.mjs [cam] [--quality ultra] [--time 16] [--tag before]
 *
 * Two things at once, because they are the same session and the round is judged
 * on both: what vegetation costs (draws / triangles / instances, ablated in
 * place) and whether the four faults Ryan reported are still in the frame.
 *
 * The fault checks walk EVERY tier's own placement list rather than the drawn
 * InstancedMesh transforms. The previous probe walked transforms, found the
 * instanced tiers clean, and reported a pass on a fault that lives in a tier it
 * could not see — and it ran from one camera, so anything culled from that
 * camera was never in the sample at all. Lists are camera-independent.
 */
import {chromium} from 'playwright';

const arg = k => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : null; };
const cam = (process.argv[2] && !process.argv[2].startsWith('--')) ? process.argv[2] : 'wide';
const quality = arg('quality') || 'ultra';
const time = arg('time') || '16';
const tag = arg('tag') || 'run';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errs.push(m.text().slice(0, 220)); });
p.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 220)));

await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}&time=${time}&hud=0`,
             {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(5000);
await p.evaluate(t => window.__lemWorld.engine.setQualityMode(t), quality);
await p.waitForTimeout(3000);

const sample = async () => {
  const s = [];
  for (let i = 0; i < 10; i++) { await p.waitForTimeout(300); s.push(await p.evaluate(() => window.__lemWorld.stats())); }
  const d = s.map(x => x.drawCalls).sort((a, c) => a - c);
  const t = s.map(x => x.triangles).sort((a, c) => a - c);
  return {draws: d[d.length - 1], tris: t[t.length - 1], fps: s[s.length - 1].fps, tier: s[s.length - 1].quality};
};
const on = await sample();
await p.evaluate(() => { const v = window.__lemWorld.subsystems.get('vegetation');
  if (v) { v.group.visible = false; window.__lemWorld.engine.shadowNeedsUpdate = true; } });
const off = await sample();
await p.evaluate(() => { const v = window.__lemWorld.subsystems.get('vegetation'); if (v) v.group.visible = true; });

const facts = await p.evaluate(() => {
  const W = window.__lemWorld;
  const v = W.subsystems.get('vegetation');
  const t = W.subsystems.get('terrain');
  const bl = W.subsystems.get('buildings');
  if (!v) return {error: 'no vegetation subsystem'};
  const ground = (x, z) => { try { const h = t?.heightAt ? t.heightAt(x, z) : W.ground(x, z); return Number.isFinite(h) ? h : 0; } catch { return 0; } };
  const wy = Number.isFinite(t?.waterY) ? t.waterY : (Number.isFinite(t?.waterLevel) ? t.waterLevel : -1e6);

  /* Building footprints, straight off buildings.js — the same source vegetation
   * reads, so a disagreement here is vegetation ignoring it rather than a
   * difference of opinion about where the halls are. */
  const blds = [];
  try { bl?.sites?.forEach(s => { const q = s?.root?.position;
    if (q && Number.isFinite(s.radius)) blds.push({x: q.x, z: q.z, r: s.radius}); }); } catch {}

  /* r is the plant's reach: what has to clear the water and the walls, not the
   * stem. A grove card is 58 m of painted canopy and a stem test says nothing
   * about it. */
  const tiers = [];
  const push = (name, pts, reach) => tiers.push({name, pts, reach});
  const trees = [], groves = [], clut = [];
  for (const e of (v.trees || [])) {
    const s = e.spec?.refH || 18;
    for (let i = 0; i < e.list.length; i++) trees.push([e.xs[i], e.zs[i], s * 0.34]);
  }
  for (const g of (v.groves || [])) for (let i = 0; i < g.count; i++) groves.push([g.xs[i], g.zs[i], 26]);
  for (const c of (v.clutter || [])) for (let i = 0; i < c.count; i++) clut.push([c.xs[i], c.zs[i], 1.1]);
  /* The sward's reach is half its card, for the reason the grove's was 26 and
   * not 0: what has to clear the water and the walls is the painting, not the
   * point it is anchored at. */
  const sward = [];
  for (const w of (v.sward || [])) for (let i = 0; i < w.count; i++) sward.push([w.xs[i], w.zs[i], 7.8]);
  push('tree', trees); push('grove', groves); push('clutter', clut); push('sward', sward);
  const G = v.grass;
  const grass = [];
  if (G) { for (let i = 0; i < G.count; i++) grass.push([G.mats[i * 16 + 12], G.mats[i * 16 + 14], 0.5, G.mats[i * 16 + 13]]); }
  push('grass', grass);

  const out = {waterY: wy, buildings: blds.length, tiers: {}};
  for (const T of tiers) {
    let inWater = 0, inBuilding = 0, floatMax = 0, floatBad = 0;
    for (const q of T.pts) {
      const [x, z, r] = q;
      if (ground(x, z) < wy) inWater++;
      else { for (let k = 0; k < 6; k++) { const a = k * 1.0472;
          if (ground(x + Math.cos(a) * r, z + Math.sin(a) * r) < wy) { inWater++; break; } } }
      for (const B of blds) { const dx = x - B.x, dz = z - B.z;
        if (dx * dx + dz * dz < (B.r + r * 0.5) * (B.r + r * 0.5)) { inBuilding++; break; } }
      if (q.length > 3) { const gap = q[3] - ground(x, z);
        if (gap > 0.25) { floatBad++; if (gap > floatMax) floatMax = gap; } }
    }
    out.tiers[T.name] = {n: T.pts.length, inWater, inBuilding,
                         floatBad: floatBad || undefined, floatMax: +floatMax.toFixed(2) || undefined};
  }
  let near = 0, trunk = 0, far = 0, grove = 0, clutter = 0;
  for (const e of (v.trees || [])) { near += e.near?.count || 0; trunk += e.trunk?.count || 0; far += e.far?.count || 0; }
  for (const g of (v.groves || [])) grove += g.mesh?.count || 0;
  for (const c of (v.clutter || [])) clutter += c.mesh?.count || 0;
  let swardDrawn = 0;
  for (const w of (v.sward || [])) swardDrawn += w.mesh?.count || 0;
  out.drawn = {near, trunk, far, grove, clutter, sward: swardDrawn,
               grass: G?.count || 0, meshes: v.meshes.length};
  out.island = v.island || null;
  out.buildMs = v._buildMs | 0;
  return out;
});

console.log(JSON.stringify({tag, cam, quality, tier: on.tier, fps: on.fps,
  scene: {draws: on.draws, tris: on.tris},
  veg: {draws: on.draws - off.draws, tris: on.tris - off.tris},
  facts, errors: errs}, null, 1));
await b.close();
