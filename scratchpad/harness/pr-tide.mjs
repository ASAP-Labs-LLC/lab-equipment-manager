/* pr-tide.mjs — the measurements the shade/tide-line round needs, taken from
 * the live world rather than assumed:
 *
 *   - the sun's direction and elevation at time=9 (the judged hour)
 *   - waterY, and the elevation distribution of the beach candidates, so a
 *     tide-line gate can be set without silently emptying the set
 *   - what terrain publishes that a tide line could be derived from
 *   - which props are enrolled to cast, per tier
 *
 *   node pr-tide.mjs [quality]
 */
import {chromium} from 'playwright';
const Q = process.argv[2] || 'ultra';
const MODS = 'sky,gi,terrain,buildings,rail,trains,vegetation,props,weather';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await (await b.newContext({viewport: {width: 1280, height: 720}})).newPage();
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=' + MODS +
  '&cam=far&time=9&weather=clear&hud=0&quality=' + Q, {waitUntil: 'load', timeout: 90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(6000);

const out = await p.evaluate(() => {
  const w = window.__lemWorld;
  const pr = w.subsystems.get('props');
  const t = w.subsystems.get('terrain');
  const sky = w.subsystems.get('sky');
  const gi = w.subsystems.get('gi');
  const sd = sky?.sunDirection;
  const td = sky?.trueSunDirection;
  const deg = v => v == null ? null : +(v * 180 / Math.PI).toFixed(2);
  const dir = v => v ? {x: +v.x.toFixed(4), y: +v.y.toFixed(4), z: +v.z.toFixed(4),
                        elevDeg: deg(Math.asin(Math.max(-1, Math.min(1, v.y)))),
                        azDeg: deg(Math.atan2(v.x, -v.z))} : null;

  /* every beach candidate the ordering saw, with its elevation */
  const m = pr._mask;
  const alts = [], wet = [];
  for (let j = 0; j < m.N; j++) {
    for (let i = 0; i < m.N; i++) {
      if (!m.land[j * m.N + i]) continue;
      const x = m.x0 + i * m.cell, z = m.z0 + j * m.cell;
      const dw = m.d[j * m.N + i] * m.cell;
      if (dw > pr._shoreW) continue;
      const s = t.biomeAt(x, z);
      if (!s || s.hard > 0.25 || s.kind === 'hardstanding') continue;
      if (pr.beachnessAt(x, z, s) < 0.28) continue;
      alts.push(+s.altitude.toFixed(2));
      wet.push({a: +s.altitude.toFixed(2), k: s.kind});
    }
  }
  alts.sort((a, b) => a - b);
  const q = f => alts.length ? alts[Math.min(alts.length - 1, Math.floor(alts.length * f))] : null;
  const kinds = {};
  for (const e of wet) kinds[e.k] = (kinds[e.k] || 0) + 1;

  /* how many survive each candidate tide-line gate */
  const survive = {};
  for (const g of [0.9, 1.5, 2.0, 2.5, 2.95, 3.5, 4.0, 4.87]) {
    survive[g] = alts.filter(a => a >= g).length;
  }

  const casters = [];
  pr.group.traverse(o => { if (o.isMesh || o.isInstancedMesh)
    casters.push([o.name, o.castShadow, o.receiveShadow, o.layers.mask]); });

  return {
    tier: w.stats().tier,
    sun: dir(sd), trueSun: dir(td),
    sunIntensity: gi?.sunIntensity, expNow: gi?._expNow,
    waterY: t.waterY, beachW: t.beachW, cliffW: t.cliffW, islandR: t.islandR,
    terrainKeys: Object.keys(t).filter(k => typeof t[k] === 'number').sort(),
    terrainStrandFields: {strandH: t.strandH, toeH: t.toeH, wetH: t.wetH},
    shoreW: pr._shoreW, cityR: pr._cityR,
    bandAlt: pr._bandAltRange, bandSlope: pr._bandSlopeRange, dWater: pr._dWaterRange,
    nCand: alts.length, altP: [q(0.05), q(0.25), q(0.5), q(0.75), q(0.95)],
    altMin: alts[0], altMax: alts[alts.length - 1],
    survive, kinds,
    anchor: pr.beachAnchor, pier: pr.pier, pierRefusal: pr.pierRefusal,
    casters,
  };
});
console.log(JSON.stringify(out, null, 2));
await b.close();
