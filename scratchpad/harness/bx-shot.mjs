/* bx-shot.mjs — the judged frame, with and without the benches, in ONE page
 * load so the A and the B cannot differ by a file edit, a layout roll or a
 * renderer state. B is taken second by nulling `terrain._terrace` and rebuilding
 * the heightfield the way `_onBenches` does.
 *
 *   node bx-shot.mjs /tmp/bx/bench    (writes -on.png and -off.png)
 */
import {chromium} from 'playwright';
const stem = process.argv[2] || '/tmp/bx/bench';
const time = process.argv[3] || '9';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1600, height: 900}});
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(`http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain,buildings,rail,trains,vegetation,weather&cam=wide&time=${time}&weather=clear&hud=0&quality=ultra`,
  {waitUntil: 'load', timeout: 120000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 120000});
await p.waitForTimeout(9000);
console.log(JSON.stringify(await p.evaluate(() => ({
  buildStable: window.__buildStable !== false,
  terrace: !!window.__lemWorld.subsystems.get('terrain')._terrace}))));
await p.screenshot({path: stem + '-on.png'});
const r = await p.evaluate(() => {
  const t = window.__lemWorld.subsystems.get('terrain');
  t._terrace = null;
  try {
    t._teardownMeshes(); t._buildField(); t._buildCore();
    t._buildRing(t.ringSize, t.ringSeg, t.coreSize, 40);
    t._buildOcean(); t._buildHorizon(); t._buildMainland(); t._syncEnvironment();
  } catch (e) { return {ok: false, err: String(e)}; }
  return {ok: true};
});
console.log(JSON.stringify(r));
await p.waitForTimeout(6000);
await p.screenshot({path: stem + '-off.png'});
if (errs.length) console.log('errors', errs.slice(0, 4));
await b.close();
