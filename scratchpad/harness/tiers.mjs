/* tiers.mjs — step the quality ladder down through every tier and report any
 * console error, so a shader that only compiles at ultra is caught. */
import {chromium} from 'playwright';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1280, height: 720}});
const errs = [];
p.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 300)));
p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text())) errs.push(m.text().slice(0, 300)); });
await p.goto(process.argv[2], {waitUntil: 'load'});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 60000});
await p.waitForTimeout(4000);
for (const i of [1, 2, 3, 4, 0]) {
  const r = await p.evaluate(async idx => {
    const w = window.__lemWorld, e = w.engine;
    e.setTier(idx, {force: true});
    await new Promise(res => setTimeout(res, 3000));
    const gi = w.subsystems.get('gi');
    return {idx, tier: e.tier.name, cascades: gi._csm.length,
            calls: e.drawCalls, tris: e.triangles, ready: gi._csm.map(c => c.ready),
            casters: gi._csm.map(c => c.casters.length)};
  }, i);
  console.log(JSON.stringify(r));
}
console.log('ERRORS', JSON.stringify(errs, null, 1));
await b.close();
