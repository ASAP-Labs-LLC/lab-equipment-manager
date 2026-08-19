/* _ablshot.mjs — the same frame with terrain's landform work stubbed out and
 * the world re-graded IN THE PAGE, so the A and the B cannot differ by a file
 * edit, a layout roll or a renderer state. */
import {chromium} from 'playwright';
const out = process.argv[2] || '/tmp/abl.png';
const b = await chromium.launch({headless: true, channel: 'chromium',
  args: ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader']});
const p = await b.newPage({viewport: {width: 1920, height: 1080}});
await p.goto('http://127.0.0.1:5601/static/world/dev/solo.html?mods=sky,gi,terrain&cam=far&time=9&hud=0&quality=ultra&weather=clear',
  {waitUntil:'load',timeout:90000});
await p.waitForFunction(() => window.__worldReady === true, null, {timeout: 90000});
await p.waitForTimeout(9000);
const info = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const plan = t._plan || w.plan || (w.ctx && w.ctx.plan) || null;
  return {hasPlan: !!plan, keys: Object.keys(t).filter(k => /plan|sig/i.test(k))};
});
console.log(JSON.stringify(info));
await p.screenshot({path: out.replace('.png', '-full.png')});
const r = await p.evaluate(() => {
  const w = window.__lemWorld, t = w.subsystems.get('terrain');
  const plan = t._plan || w.plan || (w.ctx && w.ctx.plan);
  if (!plan) return {ok: false};
  t._islandForm = () => 0; t._yardRelief = () => 0;
  t._sig = null;
  try { t._rebuild(plan); t._teardownMeshes && 0; } catch (e) { return {ok:false, err:String(e)}; }
  return {ok: true};
});
console.log(JSON.stringify(r));
await p.waitForTimeout(4000);
await p.screenshot({path: out});
await b.close();
