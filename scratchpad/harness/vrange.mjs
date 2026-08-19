/* vrange.mjs — the scene's cost at the three judged cameras, and vegetation's
 * own share of it, in one page session per camera.
 *
 *   node vrange.mjs [cam,cam,...] [--time 16] [--quality ultra]
 *
 * vcost2.mjs drives the rig by hand, which is right when you are comparing two
 * builds on one framing and wrong here: the question is what `cam=wide` costs,
 * and that is the camera the harness itself picks. Samples over four seconds
 * because the shadow cascades rebuild on demand — a single read is as likely to
 * catch the cheap frame as the dear one — and reports the max as well as the
 * median, because the budget is a ceiling and a ceiling is about the max.
 */
import {chromium} from 'playwright';

const cams = (process.argv[2] && !process.argv[2].startsWith('--')
              ? process.argv[2] : 'wide,yard,low').split(',');
const arg = k => {
  const i = process.argv.indexOf('--' + k);
  return i > 0 ? process.argv[i + 1] : null;
};
const time = arg('time') || '16';
const quality = arg('quality') || '';

const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});

const out = {};
for (const cam of cams) {
  const p = await b.newPage({viewport: {width: 1920, height: 1080}});
  const errs = [];
  p.on('console', m => { if (m.type() === 'error' &&
      !/favicon/.test(m.text())) errs.push(m.text().slice(0, 200)); });
  let url = `http://127.0.0.1:5601/static/world/dev/solo.html` +
            `?cam=${cam}&time=${time}&hud=0`;
  await p.goto(url, {waitUntil: 'load', timeout: 60000});
  await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
  await p.waitForTimeout(6000);
  const sample = async () => {
    const s = [];
    for (let i = 0; i < 12; i++) {
      await p.waitForTimeout(340);
      s.push(await p.evaluate(() => window.__lemWorld.stats()));
    }
    const d = s.map(x => x.drawCalls).sort((a, c) => a - c);
    const t = s.map(x => x.triangles).sort((a, c) => a - c);
    return {draws: d[d.length - 1], tris: t[t.length - 1],
            drawMed: d[6], triMed: t[6], fps: s[s.length - 1].fps,
            tier: s[s.length - 1].quality || null};
  };
  /* The budget is written against the ultra tier, and headless chromium is slow
   * enough that the adaptive ladder settles wherever the machine's load happens
   * to put it — two runs of the same build an hour apart came back at `high` and
   * at `floor`, which makes every number in between incomparable. Pin it. */
  const tier0 = await p.evaluate(t => {
    const e = window.__lemWorld.engine;
    const was = e.tier.name;
    if (t) e.setQualityMode(t);
    return {settled: was, pinned: e.tier.name};
  }, quality || null);
  await p.waitForTimeout(2500);
  const on = await sample();
  await p.evaluate(() => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    if (v) { v.group.visible = false; window.__lemWorld.engine.shadowNeedsUpdate = true; }
  });
  const off = await sample();
  const counts = await p.evaluate(() => {
    const v = window.__lemWorld.subsystems.get('vegetation');
    if (!v || !v.trees) return null;
    let near = 0, trunk = 0, far = 0, grove = 0;
    for (const e of v.trees) {
      near += e.near?.count || 0;
      trunk += e.trunk?.count || 0;
      far += e.far?.count || 0;
    }
    for (const g of (v.groves || [])) grove += g.mesh?.count || 0;
    return {near, trunk, far, grove, meshes: v.meshes.length};
  });
  out[cam] = {tier0, on, off, vegDraws: on.draws - off.draws,
              vegTris: on.tris - off.tris, counts, errors: errs};
  await p.close();
}
console.log(JSON.stringify(out, null, 1));
await b.close();
