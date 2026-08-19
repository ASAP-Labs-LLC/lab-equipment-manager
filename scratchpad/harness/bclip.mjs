/* bclip.mjs — measure how far buildings.js sits off the ground, and photograph it.
 *
 *   node bclip.mjs --out ../shots/bclip --layout 0 --at multitek-ns --cam street
 *
 * Two things in one run, because they have to agree: a numeric audit of every
 * site's footprint against ctx.ground(), and a screenshot from the same load.
 * The audit walks a 2m grid over each site's local footprint and reports how
 * far the terrain rises ABOVE the site's flat datum (things buried) and how far
 * it falls BELOW it (things standing in the air).
 *
 * `--layout` picks one of the soak's arrangements so a fix can be shown to hold
 * on more than the tidy demo grid.
 */
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const args = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (!a.startsWith('--')) continue;
  const k = a.slice(2), nxt = process.argv[i + 1];
  if (!nxt || nxt.startsWith('--')) args[k] = true; else { args[k] = nxt; i++; }
}

const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'],
  ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'],
  ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'],
  ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];

/* Same generator as soak.mjs, so a station that reads badly here can be found
 * again by layout index there. */
function layouts(n) {
  const BAY = 2.05;
  const out = [[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]];
  const all = [out];
  let seed = 12345;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let L = 1; L < n; L++) {
    const kind = L % 4;
    const pos = [];
    for (let i = 0; i < FLEET.length; i++) {
      if (kind === 0) pos.push([Math.round(rnd() * 8) * BAY, Math.round(rnd() * 8) * BAY]);
      else if (kind === 1) pos.push([i * BAY, 0]);
      else if (kind === 2) pos.push([0, i * BAY]);
      else pos.push([Math.round(rnd() * 14) * BAY, Math.round(rnd() * 14) * BAY]);
    }
    if (kind === 3) pos[1] = pos[0].slice();
    all.push(pos);
  }
  return all;
}

const LAYOUT = parseInt(args.layout || '0', 10);
const AT = args.at || '';
const CAM = args.cam || 'street';
const TIME = args.time || '16';
const out = path.resolve(args.out || '../shots/bclip');
fs.mkdirSync(path.dirname(out), {recursive: true});

const url = `http://127.0.0.1:5601/static/world/dev/solo.html?mods=${
  args.mods || 'sky,gi,terrain,buildings,rail'}&cam=${CAM}&time=${TIME}` +
  `&weather=${args.weather || 'clear'}&hud=0&quality=ultra`;

const browser = await chromium.launch({
  headless: true, channel: 'chromium',
  args: ['--ignore-gpu-blocklist', '--enable-gpu-rasterization', '--use-angle=metal',
         '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({viewport: {width: 1920, height: 1080}});
const errors = [];
page.on('console', m => {
  if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text().slice(0, 300));
});
await page.goto(url, {waitUntil: 'load', timeout: 90000});
await page.waitForFunction('window.__worldReady === true', null, {timeout: 90000});

if (LAYOUT > 0) {
  const pos = layouts(LAYOUT + 1)[LAYOUT];
  await page.evaluate(([fleet, pos]) => {
    const machines = fleet.map(([uid, title, status], i) => ({
      machine_uid: uid, title, status, pos: pos[i], reason: 'Fixture.',
      sub_statuses: {qc: status, pm: status, calibration: status},
      module_running: true, module_state: 'running',
      effective_specs: [], qc_targets: [], maintenance: [],
    }));
    window.__lemWorld.setMachines(machines);
  }, [FLEET, pos]);
  await page.waitForTimeout(2500);
}

/* `--synth` replaces ctx.ground with a slope of our own and draws it, so the
 * building/ground contact can be photographed while terrain.js is mid-refactor
 * and rendering a flat fallback plane. This is not a cheat: ctx.ground IS the
 * contract buildings.js is written against, and the synthetic grade is harsher
 * than the real design plane (3% per axis plus a long roll against terrain's
 * 1.8% cap), so anything that holds here holds on the real site. */
if (args.synth) {
  await page.evaluate(async () => {
    const THREE = await import('three');
    const w = window.__lemWorld;
    const g = (x, z) => 0.030 * x - 0.024 * z +
      1.6 * Math.sin(x / 55) * Math.cos(z / 71) + 0.9 * Math.sin((x + z) / 130);
    w.ctx.ground = g;
    /* Hide whatever terrain managed to build, then lay our own ground under the
     * whole plan so the contact line is visible from a street camera. */
    w.scene.traverse(o => { if (o.isMesh && /terrain|ocean|water/i.test(o.name)) o.visible = false; });
    const t = w.subsystems.get('terrain');
    if (t && t.group) t.group.visible = false;
    const b = w.plan.bounds;
    const x0 = b.minX - 420, x1 = b.maxX + 420, z0 = b.minZ - 420, z1 = b.maxZ + 420;
    const step = 4;
    const nx = Math.round((x1 - x0) / step), nz = Math.round((z1 - z0) / step);
    const geo = new THREE.PlaneGeometry(x1 - x0, z1 - z0, nx, nz);
    geo.rotateX(-Math.PI / 2);
    geo.translate((x0 + x1) / 2, 0, (z0 + z1) / 2);
    const p = geo.attributes.position;
    for (let i = 0; i < p.count; i++) p.setY(i, g(p.getX(i), p.getZ(i)));
    geo.computeVertexNormals();
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial(
      {color: 0x6b7a4a, roughness: 0.95}));
    mesh.name = 'synthGround';
    mesh.receiveShadow = true; mesh.castShadow = true;
    w.scene.add(mesh);
    const bld = w.subsystems.get('buildings');
    if (bld && bld.onPlan) bld.onPlan(w.plan);
    const rail = w.subsystems.get('rail');
    if (rail && rail.onPlan) { try { rail.onPlan(w.plan); } catch (e) {} }
  });
  await page.waitForTimeout(2500);
}

if (AT) {
  await page.evaluate(([dx, dz]) => { window.__dx = dx; window.__dz = dz; },
                      [parseFloat(args.dx || '0'), parseFloat(args.dz || '0')]);
  await page.evaluate(uid => {
    const w = window.__lemWorld;
    const s = uid === 'labcore' ? w.plan?.hub : w.plan?.byUid.get(uid);
    /* Framed off the ground under the station, not off y=4. On a graded site the
     * pad can be twenty metres up and a camera aimed at absolute 4m ends up
     * underneath the landscape looking at the back of it. */
    const gy = w.ctx.ground ? w.ctx.ground(s.x, s.z) : 0;
    if (s) { w.rig.goalTarget.set(s.x + (window.__dx || 0), gy + 6, s.z + (window.__dz || 0));
             w.rig.apply(1); w.rig.idleDrift = false; }
  }, AT);
}
await page.waitForTimeout(3500);

const report = await page.evaluate(() => {
  const w = window.__lemWorld;
  const b = w.subsystems.get('buildings');
  const g = w.ctx ? w.ctx.ground : null;
  const ground = g || ((x, z) => {
    const t = w.subsystems.get('terrain');
    return t && t.heightAt ? t.heightAt(x, z) : 0;
  });
  if (!b || !b.sites) return {error: 'no buildings subsystem'};
  const rows = [];
  for (const [uid, site] of b.sites) {
    /* Footprint the kit actually covers, in site-local metres. `extent` is
     * recorded by the Kit once the fix lands; before that, the archetypes'
     * hand-measured reach. */
    const e = site.extent ||
      (uid === '__labcore__' ? {x0: -152, x1: 152, z0: -118, z1: 45}
                             : {x0: -46, x1: 46, z0: -36, z1: 48});
    const y0 = site.root.position.y;
    const ox = site.root.position.x, oz = site.root.position.z;
    let up = -1e9, dn = 1e9, n = 0, above = 0, sum = 0;
    let upAt = null, dnAt = null;
    const step = 2;
    for (let x = e.x0; x <= e.x1; x += step) {
      for (let z = e.z0; z <= e.z1; z += step) {
        const h = ground(ox + x, oz + z);
        if (!isFinite(h)) continue;
        const d = h - y0;
        n++; sum += d;
        if (d > 0.25) above++;
        if (d > up) { up = d; upAt = [x, z]; }
        if (d < dn) { dn = d; dnAt = [x, z]; }
      }
    }
    /* What the fix is actually judged on: the site's own ground surface — the
     * level everything is founded on — against the terrain under it. Negative
     * anywhere means the yard is UNDER the ground and whatever stands on it is
     * buried; the maximum is the fill the perimeter skirt has to cover, and if
     * that exceeds the skirt the rim hangs in the air. */
    let sunk = 1e9, fill = -1e9, sunkAt = null, fillAt = null, bad = 0, m = 0;
    if (site.pad && site.pad.gl) {
      for (let x = e.x0; x <= e.x1; x += step) {
        for (let z = e.z0; z <= e.z1; z += step) {
          const p = site.pad(x, z), g = site.pad.gl(x, z);
          if (!isFinite(p) || !isFinite(g)) continue;
          const d = p - g;
          m++;
          if (d < -0.05) bad++;
          if (d < sunk) { sunk = d; sunkAt = [x, z]; }
          if (d > fill) { fill = d; fillAt = [x, z]; }
        }
      }
    }
    /* Every founding decision the kit made, and the drop its own footprint has
     * to bridge. A footed one builds a plinth down to `lo`; an un-footed one is
     * relying on whatever buried skirt its archetype already draws, so the
     * worst un-footed drop is the number that says whether anything can hang. */
    let worstFooted = 0, worstBare = 0, bareAt = null;
    for (const f of site.founds || []) {
      const drop = f.hi - f.lo;
      if (f.footed) worstFooted = Math.max(worstFooted, drop);
      else if (drop > worstBare) { worstBare = drop; bareAt = [f.x, f.z, f.hx, f.hz]; }
    }
    rows.push({uid, y0: +y0.toFixed(2), samples: n,
               founds: (site.founds || []).length,
               worstFootedDrop: +worstFooted.toFixed(2),
               worstBareDrop: +worstBare.toFixed(2), bareAt,
               terrainRise: +up.toFixed(2), riseAt: upAt,
               terrainFall: +dn.toFixed(2), fallAt: dnAt,
               terrainMean: +(sum / Math.max(1, n)).toFixed(2),
               oldBuriedPct: +(100 * above / Math.max(1, n)).toFixed(1),
               padUnderGround: m ? +sunk.toFixed(2) : null, sunkAt,
               padUnderGroundPct: m ? +(100 * bad / m).toFixed(1) : null,
               maxFill: m ? +fill.toFixed(2) : null, fillAt});
  }
  const s = w.stats ? w.stats() : {};
  return {rows, stats: {fps: s.fps, draws: s.drawCalls, tris: s.triangles, tier: s.tier}};
});

await page.screenshot({path: out + '.png'});
fs.writeFileSync(out + '.json', JSON.stringify({url, layout: LAYOUT, at: AT, cam: CAM,
                                                errors, ...report}, null, 2));
console.log(JSON.stringify({layout: LAYOUT, at: AT, errors, ...report}, null, 2));
await browser.close();
