/* _vrip.mjs — the riparian work, at a camera that can see it.
 *
 *   node _vrip.mjs --out /path/prefix
 *
 * The gate cameras all frame the site, and the site is hardstanding and pads —
 * every one of the four is a camera pointed at the one part of the island where
 * the drainage rules are switched off by `_openness`. So this finds the subject
 * rather than being told it: the strongest inland run of `biomeAt().flow` that
 * is clear of the plan's own bounds, and stands the rig on the shoulder above
 * it looking down the low line.
 *
 * A probe that chooses its own subject has to say which one it chose, so it
 * prints the coordinate, the flow there, and how far it is from the site — the
 * lesson `vsward.mjs` learned by twice measuring a headland and reporting,
 * correctly, that nothing had changed.
 *
 * Ablation is the same stub pair as `_vabl15.mjs`: one page, one instant, only
 * the two readers change.
 */
import {chromium} from 'playwright';

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const out = arg('out', '/tmp/vrip');

const URL = 'http://127.0.0.1:5601/static/world/dev/solo.html?cam=low' +
  '&time=9&quality=ultra&hud=0';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
await p.goto(URL, {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(14000);

const subject = await p.evaluate(() => {
  const w = window.__lemWorld;
  const veg = w.subsystems.get('vegetation');
  const ter = w.subsystems.get('terrain');
  const isl = veg.island;
  const bb = veg.plan?.bounds;
  /* Away from the pads: the site's own apron is most of the open ground and
     every planting rule is muted there, so a channel inside it proves nothing. */
  const farFromSite = (x, z) => {
    if (!bb || !Number.isFinite(bb.minX)) return true;
    const dx = Math.max(bb.minX - x, 0, x - bb.maxX);
    const dz = Math.max(bb.minZ - z, 0, z - bb.maxZ);
    return Math.hypot(dx, dz) > 90;
  };
  let best = null;
  for (let z = isl.cz - isl.r; z <= isl.cz + isl.r; z += 8) {
    for (let x = isl.cx - isl.r; x <= isl.cx + isl.r; x += 8) {
      if ((x - isl.cx) ** 2 + (z - isl.cz) ** 2 > isl.r * isl.r) continue;
      if (!farFromSite(x, z)) continue;
      const site = veg._site(x, z, 9.0);
      if (!site || site.coast < 60) continue;
      const bi = ter.biomeAt(x, z);
      if (!bi) continue;
      /* Score the NEIGHBOURHOOD, not the cell: one bright cell is a speck and
         what has to be photographable is a run of channel. */
      let s = 0;
      for (let k = 0; k < 8; k++) {
        const a = k * Math.PI / 4;
        const q = ter.biomeAt(x + Math.cos(a) * 14, z + Math.sin(a) * 14);
        s += q ? q.flow : 0;
      }
      const score = bi.flow * 2 + s / 8;
      if (!best || score > best.score) best = {x, z, score, flow: bi.flow, ring: s / 8,
                                              coast: site.coast, h: site.h};
    }
  }
  if (!best) return null;
  /* Stand off up the slope so the low line runs away from the lens. */
  const rig = w.rig;
  rig.goalTarget.set(best.x, best.h + 2, best.z);
  rig.goalDistance = 78;
  rig.goalPitch = 0.24;
  rig.goalYaw = -0.9;
  rig.apply(1);
  return best;
});

const grab = async (tag) => {
  await p.waitForTimeout(3000);
  await p.screenshot({path: `${out}-${tag}.png`});
  return p.evaluate(() => {
    const veg = window.__lemWorld.subsystems.get('vegetation');
    return {placed: veg._scatterStats.placed, gully: veg._scatterStats.gully,
            bank: veg._scatterStats.bank, mouth: veg._scatterStats.mouth,
            closed: veg._scatterStats.closed};
  });
};

await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  veg.__expo = veg._exposure; veg.__rip = veg._riparian;
  veg._exposure = () => 0.5;
  veg._riparian = () => ({gully: 0, bank: 0, channel: 0});
  veg._regrow();
});
const before = await grab('before');
await p.evaluate(() => {
  const veg = window.__lemWorld.subsystems.get('vegetation');
  veg._exposure = veg.__expo; veg._riparian = veg.__rip;
  veg._regrow();
});
const after = await grab('after');

console.log(JSON.stringify({subject, before, after, errs: errs.slice(0, 4)}, null, 1));
await b.close();
