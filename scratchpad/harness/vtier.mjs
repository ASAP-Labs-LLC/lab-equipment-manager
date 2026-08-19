/* vtier.mjs — walk the quality ladder on one page session and report what the
 * forest actually sheds at each rung: instances per LOD, the outer wood's
 * extent, and the scene's draw and triangle totals.
 *
 * solo.html has no `quality` parameter, and adding one would be an edit to a
 * file this builder does not own — so the tier is stepped through the world's
 * own onQuality path, which is the same call the adaptive ladder makes.
 */
import {chromium} from 'playwright';

const cam = process.argv[2] || 'low';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?cam=${cam}&time=16&hud=0`,
             {waitUntil: 'load', timeout: 60000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(6000);

const out = [];
for (const tier of ['ultra', 'high', 'medium', 'low', 'floor']) {
  await p.evaluate(t => window.__lemWorld.engine.setQualityMode(t), tier);
  await p.waitForTimeout(1800);
  out.push(await p.evaluate(t => {
    const w = window.__lemWorld, v = w.subsystems.get('vegetation');
    let near = 0, far = 0, trunk = 0, grove = 0, gTri = 0;
    for (const e of v.trees) {
      near += e.near.count; far += e.far.count; trunk += e.trunk ? e.trunk.count : 0;
    }
    for (const g of v.groves || []) { grove += g.mesh.count; gTri += g.mesh.count * 8; }
    const s = w.stats();
    return {tier: t, name: v.ctx.quality?.name, q: v.quality, range: v.range,
            near, trunk, far, grove, gTri, draws: s.drawCalls, tris: s.triangles};
  }, tier));
}
console.log(JSON.stringify(out, null, 1));
await b.close();
