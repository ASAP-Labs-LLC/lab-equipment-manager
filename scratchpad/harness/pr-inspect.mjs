/* pr-inspect.mjs — what props actually built, where, and whether any of it is
 * standing somewhere it must not: on the railway, on hardstanding, under water,
 * or floating above the ground. Also walks the QUALITY LADDER and proves the
 * placement is a STABLE PREFIX — the umbrella that survives to the floor tier
 * must be at the same coordinates it had at ultra.
 *
 *   node pr-inspect.mjs
 */
import {chromium} from 'playwright';

const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const url = q => 'http://127.0.0.1:5601/static/world/dev/solo.html?mods=' + MODS +
  '&cam=far&time=9&weather=clear&hud=0&quality=' + q;

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});

const probe = async (quality) => {
  const p = await (await b.newContext({viewport: {width: 1280, height: 720}})).newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); });
  await p.goto(url(quality), {waitUntil: 'load', timeout: 60000});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
  await p.waitForTimeout(6000);
  const out = await p.evaluate(() => {
    const w = window.__lemWorld;
    const pr = w.subsystems.get('props');
    const t = w.subsystems.get('terrain');
    const rail = w.subsystems.get('rail');
    const meshes = [];
    let propTris = 0, propDraws = 0;
    (pr?.group || {traverse: () => {}}).traverse(o => {
      if (!o.isMesh && !o.isInstancedMesh) return;
      const g = o.geometry;
      const per = (g.index ? g.index.count : g.attributes.position.count) / 3;
      const inst = o.isInstancedMesh ? o.count : 1;
      meshes.push({name: o.name, per, inst, tris: per * inst,
                   castShadow: o.castShadow, receiveShadow: o.receiveShadow});
      propTris += per * inst; propDraws += 1 + (o.castShadow ? 1 : 0);
    });
    /* every umbrella, checked against the world it stands in */
    const railPts = [];
    for (const tr of rail?.tracks || []) {
      const f = tr?.frames; if (!f?.pos) continue;
      for (let i = 0; i < f.count; i += 2) railPts.push(f.pos[i * 3], f.pos[i * 3 + 2]);
    }
    const faults = [];
    const sites = pr?.umbrellaSites || [];
    for (const s of sites) {
      const g = t.heightAt(s.x, s.z);
      const bio = t.biomeAt(s.x, s.z);
      if (!(g > t.waterY)) faults.push(['submerged', s]);
      if (bio.hard > 0.25) faults.push(['hardstanding', s]);
      if (pr.regionAt(s.x, s.z) !== 'beach') faults.push(['not-beach:' + pr.regionAt(s.x, s.z), s]);
      let dr = Infinity;
      for (let i = 0; i < railPts.length; i += 2) {
        const dx = s.x - railPts[i], dz = s.z - railPts[i + 1];
        dr = Math.min(dr, Math.sqrt(dx * dx + dz * dz));
      }
      if (dr < 9) faults.push(['on-rail ' + dr.toFixed(1) + 'm', s]);
      s.groundY = +g.toFixed(2); s.railM = +dr.toFixed(1);
      s.kind = bio.kind;
    }
    const st = w.stats();
    return {
      tier: st.tier, drawCalls: st.drawCalls, triangles: st.triangles,
      failed: st.failed,
      tierShare: pr?._tierShare?.(),
      meshes, propTris, propDraws,
      buildMs: pr?.buildMs,
      sites, faults, candidates: pr?.beachCandidates,
      fraction: pr?.regions?.fraction, warnings: pr?.regions?.warnings,
      shoreW: pr?.regions?.shoreW, ranges: pr?.regions?.ranges,
    };
  });
  out.errors = errs.slice(0, 5);
  await p.context().close();
  return out;
};

const tiers = ['ultra', 'high', 'medium', 'low', 'floor'];
const res = {};
for (const q of tiers) {
  res[q] = await probe(q);
  const r = res[q];
  console.log('\n=== ' + q + ' ===  tier=' + r.tier + ' share=' + r.tierShare +
    '  draws=' + r.drawCalls + ' tris=' + r.triangles.toLocaleString() +
    '  propTris=' + r.propTris + ' propDraws=' + r.propDraws +
    '  buildMs=' + JSON.stringify(r.buildMs) + ' cands=' + r.candidates);
  console.log('  meshes: ' + JSON.stringify(r.meshes));
  console.log('  umbrellas(' + r.sites.length + '): ' +
    r.sites.map(s => '(' + s.x + ',' + s.z + ' y' + s.groundY + ' b' + s.beachness +
      ' sl' + s.slope + ' rail' + s.railM + ' ' + s.kind + ')').join(' '));
  if (r.faults.length) console.log('  FAULTS: ' + JSON.stringify(r.faults));
  if (r.warnings?.length) console.log('  WARN: ' + r.warnings.join(' | '));
  if (r.errors.length) console.log('  ERRORS: ' + r.errors.join(' | '));
}

/* THE STABLE PREFIX. Every tier's site list must be a prefix of ultra's. */
const ref = res.ultra.sites;
let prefixOk = true;
for (const q of tiers) {
  const s = res[q].sites;
  for (let i = 0; i < s.length; i++) {
    if (!ref[i] || s[i].x !== ref[i].x || s[i].z !== ref[i].z) {
      prefixOk = false;
      console.log('PREFIX BROKEN at ' + q + '[' + i + ']: ' +
        JSON.stringify(s[i]) + ' vs ultra ' + JSON.stringify(ref[i]));
    }
  }
}
console.log('\ncounts by tier: ' + tiers.map(q => q + '=' + res[q].sites.length).join(' '));
console.log('STABLE PREFIX: ' + (prefixOk ? 'PASS' : 'FAIL'));
console.log('FAULTS TOTAL: ' + tiers.reduce((a, q) => a + res[q].faults.length, 0));
await b.close();
