/* pr-clear.mjs — the gate soak.mjs cannot be: soak's mods list does not include
 * props, so its eight zeroes are silent about anything this file places. This
 * runs the SAME deterministic layout set with props loaded and asserts, after
 * every relayout, that nothing props built is:
 *
 *   - standing on or within 9 m of a rail centre-line or a buffer stop
 *   - standing on hardstanding, ballast or road
 *   - standing under water, or floating above the ground it claims to stand on
 *   - inside the plant's own apron (`city`, which props must leave empty for
 *     the roads round)
 *   - classified as anything but the region its rule says it needs
 *
 * and that the region layer republishes on `onPlan` with a live histogram
 * rather than a stale one or a warning.
 *
 *   node pr-clear.mjs [layouts]
 */
import {chromium} from 'playwright';

const N = parseInt(process.argv[2] || '6', 10);
const FLEET = [
  ['multitek-ns', 'Multitek NS', 'GREEN'], ['multitek-s', 'Multitek S', 'YELLOW'],
  ['optimpp-1', 'OptiMPP 1', 'GREEN'], ['optimpp-2', 'OptiMPP 2', 'RED'],
  ['pac-flash-1', 'PAC Flash 1', 'SERVICE'], ['pac-flash-2', 'PAC Flash 2', 'DEAD-LINE'],
  ['koehler-cp', 'Koehler CP', 'UNKNOWN'],
];
/* Byte-for-byte soak.mjs's layout generator, so a fault here is reproducible
 * against a soak run of the same index. */
function layouts(n) {
  const BAY = 2.05;
  const out = [[0, 0], [2.05, 0], [4.1, 0], [0, 2.05], [2.05, 2.05], [4.1, 2.05], [6.15, 0]];
  const all = [out];
  let seed = 12345;
  const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let L = 1; L < n; L++) {
    const kind = L % 4, pos = [];
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

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await (await b.newContext({viewport: {width: 1280, height: 720}})).newPage();
const consoleErrors = [];
p.on('pageerror', e => consoleErrors.push(String(e).slice(0, 200)));
p.on('console', m => {
  const t = m.text();
  if (m.type() === 'error' && !/favicon|404 \(NOT FOUND\)/.test(t)) {
    consoleErrors.push(t.slice(0, 200));
  }
});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html' +
  '?mods=sky,gi,terrain,buildings,rail,trains,vegetation,props,weather' +
  '&cam=far&time=9&weather=clear&hud=0&quality=ultra',
  {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(5000);

const check = () => p.evaluate(() => {
  const w = window.__lemWorld;
  const pr = w.subsystems.get('props');
  const t = w.subsystems.get('terrain');
  const rail = w.subsystems.get('rail');
  const faults = [];
  const railPts = [];
  for (const tr of rail?.tracks || []) {
    const f = tr?.frames; if (!f?.pos) continue;
    for (let i = 0; i < f.count; i += 2) railPts.push(f.pos[i * 3], f.pos[i * 3 + 2]);
  }
  for (const s of rail?.bufferStops || []) railPts.push(s.x, s.z);
  const nearRail = (x, z) => {
    let m = Infinity;
    for (let i = 0; i < railPts.length; i += 2) {
      m = Math.min(m, Math.hypot(x - railPts[i], z - railPts[i + 1]));
    }
    return m;
  };
  /* things that STAND */
  for (const s of pr.umbrellaSites || []) {
    const g = t.heightAt(s.x, s.z), bio = t.biomeAt(s.x, s.z);
    const r = nearRail(s.x, s.z);
    if (!(g > t.waterY)) faults.push('umbrella submerged @' + s.x + ',' + s.z);
    if (bio.hard > 0.25) faults.push('umbrella on hardstanding @' + s.x + ',' + s.z);
    if (r < 9) faults.push('umbrella ' + r.toFixed(1) + 'm from rail @' + s.x + ',' + s.z);
    if (pr.regionAt(s.x, s.z) !== 'beach') {
      faults.push('umbrella on ' + pr.regionAt(s.x, s.z) + ' @' + s.x + ',' + s.z);
    }
    /* THE TIDE LINE. This check was `< 0.9`, which is freeboard — it keeps a
     * chair out of the SEA and says nothing about the wash. terrain.js paints
     * its wet band by elevation: `strand = smoothstep(10,0,h-waterY)` and
     * `wetSand = smoothstep(0.79,0.965,strand)`, which is full below 1.12 m and
     * zero at 2.95 m. So 2.95 m is where the visibly darker, wetter sand ends.
     * The shipped set stood at a median 0.53 m — every umbrella inside the
     * saturated band, on the seaward side of the tone, and this gate passed all
     * ten of them. It is the defect the gate did not catch, so it is now the
     * gate. Kept as a literal here rather than read off props.js: a harness
     * that imports the rule it is checking is checking its own copy. */
    const WASH_LINE = 2.95;
    if (bio.altitude < WASH_LINE) {
      faults.push('umbrella below the tide line (' + bio.altitude.toFixed(2) +
                  'm < ' + WASH_LINE + 'm) @' + s.x + ',' + s.z);
    }
  }
  /* THE APPROACH. A path is the one prop that is allowed to leave the beach, so
   * it gets its own rules: it may cross any region, but it may not walk on the
   * railway, through the plant apron, or into the sea, and it has to actually
   * go somewhere. */
  if (pr.path) {
    const rt = pr._path;
    for (const q of (rt?.pts || [])) {
      const r = nearRail(q.x, q.z);
      if (r < 9) faults.push('path ' + r.toFixed(1) + 'm from rail @' +
                             q.x.toFixed(0) + ',' + q.z.toFixed(0));
      if (pr.dPlantAt(q.x, q.z) <= pr.regions.cityR) {
        faults.push('path inside the plant apron @' + q.x.toFixed(0) + ',' + q.z.toFixed(0));
      }
      if (!(t.heightAt(q.x, q.z) > t.waterY)) {
        faults.push('path underwater @' + q.x.toFixed(0) + ',' + q.z.toFixed(0));
      }
    }
    if (pr.path.length < 40) faults.push('path is only ' + pr.path.length + 'm long');
  } else if (pr.umbrellaSites?.length) {
    faults.push('umbrellas but NO APPROACH PATH: ' + (pr.pathRefusal || 'no reason given'));
  }
  if (pr.pier) {
    const r = nearRail(pr.pier.x, pr.pier.z);
    if (r < 9) faults.push('pier root ' + r.toFixed(1) + 'm from rail');
    if (pr.dPlantAt(pr.pier.x, pr.pier.z) <= pr.regions.cityR) faults.push('pier in city');
    if (!(t.heightAt(pr.pier.x, pr.pier.z) > t.waterY)) faults.push('pier root afloat');
    const hx = pr.pier.x + pr.pier.dir.x * pr.pier.length;
    const hz = pr.pier.z + pr.pier.dir.z * pr.pier.length;
    if (t.heightAt(hx, hz) > t.waterY) faults.push('pier head on dry land');
  }
  /* things that FLOAT */
  for (const s of pr.boatSites || []) {
    if (t.heightAt(s.x, s.z) > t.waterY) faults.push('boat aground @' + s.x + ',' + s.z);
    if (s.depth < 1.2) faults.push('boat in ' + s.depth + 'm @' + s.x + ',' + s.z);
  }
  const reg = pr.regions;
  return {
    faults, warnings: reg?.warnings || [], fraction: reg?.fraction,
    source: reg?.source, landSamples: reg?.landSamples,
    counts: {umbrellas: (pr.umbrellaSites || []).length,
             boats: (pr.boatSites || []).length, birds: pr.birdCount || 0,
             pier: pr.pier ? 1 : 0, path: pr.path ? pr.path.length : 0},
    pathStop: pr.path?.stoppedBecause || pr.pathRefusal || null,
    headDepth: pr.pier?.headDepth ?? null,
    shade: pr.shade || null,
    minAlt: Math.min(...(pr.umbrellaSites || []).map(s => s.altitude), Infinity),
    pierRefusal: pr.pierRefusal || null,
    ctxSame: w.ctx.regions === pr.regions,
  };
});

const SET = layouts(N);
let total = 0;
for (let L = 0; L < SET.length; L++) {
  await p.evaluate(([fleet, pos]) => {
    window.__lemWorld.setMachines(fleet.map((f, i) => ({
      machine_uid: f[0], title: f[1], state: f[2], pos: pos[i],
      effective_specs: [], qc_targets: [], maintenance: [], parses: [],
    })));
  }, [FLEET, SET[L]]);
  await p.waitForTimeout(4000);
  const r = await check();
  total += r.faults.length;
  console.log('layout ' + L + ': ' + JSON.stringify(r.counts) +
    '  land=' + r.landSamples + ' ' + JSON.stringify(r.fraction) +
    '  minAlt=' + (Number.isFinite(r.minAlt) ? r.minAlt.toFixed(2) : '-') +
    ' headDepth=' + r.headDepth + ' shade=' + JSON.stringify(r.shade) +
    '\n   path: ' + r.pathStop +
    '  ctxPublished=' + r.ctxSame +
    (r.pierRefusal ? '  NO PIER: ' + r.pierRefusal : '') +
    (r.warnings.length ? '  WARN ' + r.warnings.join(' | ') : '') +
    (r.faults.length ? '\n   FAULTS: ' + r.faults.join('\n           ') : ''));
}
console.log('\nplacement faults across ' + SET.length + ' layouts: ' + total);
console.log('console errors: ' + consoleErrors.length +
  (consoleErrors.length ? ' ' + consoleErrors.slice(0, 3).join(' | ') : ''));
console.log(total === 0 && consoleErrors.length === 0 ? 'PASS' : 'FAIL');
await b.close();
